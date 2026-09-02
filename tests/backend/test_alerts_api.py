"""The standing-alerts HTTP surface.

`alerts/` carries the reasoning and its own tests; these cover the wiring, the
role gating, and the one claim the API makes about itself that must stay true:
that it does not deliver notifications.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from alerts.store import AlertStore
from auth.store import AuthStore
from backend import main

_ADMIN_PW = "an-admin-length-passphrase"
_USER_PW = "a-user-length-passphrase"


@pytest.fixture
def stores(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[AuthStore, AlertStore]]:
    auth = AuthStore(":memory:")
    alerts = AlertStore(":memory:")
    monkeypatch.setattr(main, "_auth_store", auth)
    monkeypatch.setattr(main, "_alert_store", alerts)
    yield auth, alerts


@pytest.fixture
def client(stores: tuple[AuthStore, AlertStore]) -> TestClient:
    return TestClient(main.app)


def _watch_body(**over: object) -> dict:
    body = {
        "kind": "rate_crosses",
        "label": "Supramax under 22k",
        "vessel_class": "Supramax",
        "threshold_usd_per_day": 22_000,
        "direction": "below",
    }
    body.update(over)
    return body


class TestShape:
    def test_alerts_reports_the_kinds_it_can_evaluate(self, client: TestClient) -> None:
        body = client.get("/alerts").json()
        assert {k["value"] for k in body["kinds"]} == {
            "rate_crosses",
            "rate_moves",
            "outcome_overdue",
        }
        assert all(k["description"].strip() for k in body["kinds"])

    def test_it_does_not_claim_to_deliver_notifications(self, client: TestClient) -> None:
        """Nothing in this stack emails, texts or calls anyone. The bell was
        removed once (F-79) precisely for promising a capability that did not
        exist; the replacement must not repeat it."""
        assert client.get("/alerts").json()["delivers_notifications"] is False

    def test_a_fresh_deployment_has_no_watches_and_no_unread(
        self, client: TestClient
    ) -> None:
        body = client.get("/alerts").json()
        assert body["watches"] == []
        assert body["firings"] == []
        assert body["unread"] == 0


class TestCreation:
    def test_a_valid_watch_is_created(self, client: TestClient) -> None:
        r = client.post("/alerts/watches", json=_watch_body())
        assert r.status_code == 200
        assert r.json()["watch"]["label"] == "Supramax under 22k"

    def test_an_unevaluable_watch_is_refused_with_the_reason(
        self, client: TestClient
    ) -> None:
        """Stored, it would silently never fire -- which reads to its owner
        exactly like a market that never moved."""
        r = client.post("/alerts/watches", json={"kind": "rate_crosses", "label": "broken"})
        assert r.status_code == 422
        assert "vessel class" in r.json()["detail"]

    def test_the_creator_is_recorded_when_someone_is_signed_in(
        self, client: TestClient
    ) -> None:
        client.post("/auth/bootstrap", json={"username": "desk.admin", "password": _ADMIN_PW})
        r = client.post("/alerts/watches", json=_watch_body())
        assert r.json()["watch"]["created_by"] == "desk.admin"

    def test_an_open_deployment_records_no_creator_rather_than_a_fake_one(
        self, client: TestClient
    ) -> None:
        assert client.post("/alerts/watches", json=_watch_body()).json()["watch"]["created_by"] is None


class TestEvaluation:
    def test_evaluating_twice_fires_at_most_once_for_one_transition(
        self, client: TestClient, stores: tuple[AuthStore, AlertStore]
    ) -> None:
        _auth, alerts = stores
        wid = client.post(
            "/alerts/watches", json=_watch_body(threshold_usd_per_day=1_000_000)
        ).json()["watch"]["watch_id"]

        # First look never fires: the condition already held when it was made.
        assert client.post("/alerts/evaluate").json()["fired"] == []
        # Force the edge, then confirm exactly one firing and no repeat.
        alerts.record_state(wid, "out")
        assert len(client.post("/alerts/evaluate").json()["fired"]) == 1
        assert client.post("/alerts/evaluate").json()["fired"] == []

    def test_a_firing_carries_the_real_observation_date(
        self, client: TestClient, stores: tuple[AuthStore, AlertStore]
    ) -> None:
        _auth, alerts = stores
        wid = client.post(
            "/alerts/watches", json=_watch_body(threshold_usd_per_day=1_000_000)
        ).json()["watch"]["watch_id"]
        alerts.record_state(wid, "out")
        fired = client.post("/alerts/evaluate").json()["fired"]
        assert fired[0]["observed_on"] is not None
        # fired_at is when this system noticed; observed_on is when the number
        # was published. They are different and both are reported.
        assert fired[0]["observed_on"] < fired[0]["fired_at"]

    def test_unread_clears_but_the_firings_remain(
        self, client: TestClient, stores: tuple[AuthStore, AlertStore]
    ) -> None:
        _auth, alerts = stores
        wid = client.post(
            "/alerts/watches", json=_watch_body(threshold_usd_per_day=1_000_000)
        ).json()["watch"]["watch_id"]
        alerts.record_state(wid, "out")
        client.post("/alerts/evaluate")
        assert client.get("/alerts").json()["unread"] == 1
        assert client.post("/alerts/read").json()["marked_read"] == 1
        body = client.get("/alerts").json()
        assert body["unread"] == 0
        assert len(body["firings"]) == 1


class TestDeletion:
    def test_deleting_a_watch_removes_its_firings_from_the_bell(
        self, client: TestClient, stores: tuple[AuthStore, AlertStore]
    ) -> None:
        _auth, alerts = stores
        wid = client.post(
            "/alerts/watches", json=_watch_body(threshold_usd_per_day=1_000_000)
        ).json()["watch"]["watch_id"]
        alerts.record_state(wid, "out")
        client.post("/alerts/evaluate")
        assert client.delete(f"/alerts/watches/{wid}").status_code == 200
        body = client.get("/alerts").json()
        assert body["watches"] == []
        assert body["unread"] == 0

    def test_an_unknown_watch_is_a_404(self, client: TestClient) -> None:
        assert client.delete("/alerts/watches/no-such-id").status_code == 404
        assert (
            client.patch("/alerts/watches/no-such-id", json={"is_active": False}).status_code
            == 404
        )


class TestRoles:
    """A watch puts a number on a bell every user of the deployment sees, so
    creating one sits with the role that already carries responsibility for
    what goes on the record."""

    @pytest.fixture
    def closed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(main, "AUTH_ENFORCED", True)

    def _as_viewer(self, client: TestClient) -> None:
        client.post("/auth/bootstrap", json={"username": "desk.admin", "password": _ADMIN_PW})
        client.post(
            "/auth/users", json={"username": "v.iyer", "password": _USER_PW, "role": "viewer"}
        )
        client.post("/auth/logout")
        client.post("/auth/login", json={"username": "v.iyer", "password": _USER_PW})

    def test_a_viewer_can_read_alerts(self, client: TestClient, closed: None) -> None:
        self._as_viewer(client)
        assert client.get("/alerts").status_code == 200
        assert client.post("/alerts/read").status_code == 200

    def test_a_viewer_cannot_create_or_evaluate(
        self, client: TestClient, closed: None
    ) -> None:
        self._as_viewer(client)
        assert client.post("/alerts/watches", json=_watch_body()).status_code == 403
        assert client.post("/alerts/evaluate").status_code == 403

    def test_a_manager_can(self, client: TestClient, closed: None) -> None:
        client.post("/auth/bootstrap", json={"username": "desk.admin", "password": _ADMIN_PW})
        client.post(
            "/auth/users",
            json={"username": "m.rao", "password": _USER_PW, "role": "chartering_manager"},
        )
        client.post("/auth/logout")
        client.post("/auth/login", json={"username": "m.rao", "password": _USER_PW})
        assert client.post("/alerts/watches", json=_watch_body()).status_code == 200
        assert client.post("/alerts/evaluate").status_code == 200
