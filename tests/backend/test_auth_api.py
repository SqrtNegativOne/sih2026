"""Sign-in, roles and the two enforcement modes.

The design worth pinning here is the one most likely to be mistaken for a
mistake: enforcement is OFF unless ``DESK_REQUIRE_AUTH`` is set. A fresh clone
of this repository has to run end to end with no setup, and an API that
returns 401 to every call until somebody finds the bootstrap endpoint fails
that on the first click. The login system is fully built and usable in either
mode; a deployment that wants the desk closed sets one variable.

Two things are NOT relaxed by the open mode, and both are tested below:
account management (a username is half a credential) and the last-admin guard.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from auth.store import AuthStore
from backend import main

_ADMIN_PW = "an-admin-length-passphrase"
_USER_PW = "a-user-length-passphrase"


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> Iterator[AuthStore]:
    """A fresh in-memory account store for each test.

    The app holds its store as a module-level singleton built at import time,
    pointing at the real on-disk database. Swapping the attribute keeps every
    test isolated from that file and from every other test -- both the route
    dependencies and the middleware read the module global at call time, so
    one patch covers both paths.
    """
    fresh = AuthStore(":memory:")
    monkeypatch.setattr(main, "_auth_store", fresh)
    yield fresh


@pytest.fixture
def client(store: AuthStore) -> TestClient:
    return TestClient(main.app)


@pytest.fixture
def closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Put the desk in enforced mode for one test."""
    monkeypatch.setattr(main, "AUTH_ENFORCED", True)


def _bootstrap(client: TestClient) -> dict:
    r = client.post(
        "/auth/bootstrap",
        json={"username": "desk.admin", "password": _ADMIN_PW, "display_name": "Desk Admin"},
    )
    assert r.status_code == 200, r.text
    return r.json()["user"]


class TestStatus:
    def test_a_fresh_deployment_reports_that_it_needs_an_admin(self, client: TestClient) -> None:
        body = client.get("/auth/status").json()
        assert body["needs_bootstrap"] is True
        assert body["user"] is None

    def test_status_says_which_mode_the_desk_is_in(self, client: TestClient) -> None:
        """The frontend needs this to render honestly -- an open desk should
        say it is open, not show a padlock over an open door."""
        assert client.get("/auth/status").json()["enforced"] is False

    def test_status_describes_every_role(self, client: TestClient) -> None:
        roles = {r["value"]: r["description"] for r in client.get("/auth/status").json()["roles"]}
        assert set(roles) == {"viewer", "chartering_manager", "admin"}
        assert all(d.strip() for d in roles.values())


class TestBootstrap:
    def test_the_first_account_is_an_admin_and_is_signed_in(self, client: TestClient) -> None:
        user = _bootstrap(client)
        assert user["role"] == "admin"
        assert client.get("/auth/status").json()["user"]["username"] == "desk.admin"

    def test_bootstrap_closes_once_used(self, client: TestClient) -> None:
        _bootstrap(client)
        r = client.post("/auth/bootstrap", json={"username": "x", "password": _ADMIN_PW})
        assert r.status_code == 409

    def test_a_short_password_is_refused_with_the_reason(self, client: TestClient) -> None:
        r = client.post("/auth/bootstrap", json={"username": "x", "password": "short"})
        assert r.status_code == 422
        assert "12 characters" in r.json()["detail"]

    def test_no_response_anywhere_carries_a_password_hash(self, client: TestClient) -> None:
        body = client.post(
            "/auth/bootstrap", json={"username": "desk.admin", "password": _ADMIN_PW}
        ).text
        assert "scrypt" not in body
        assert _ADMIN_PW not in body


class TestLogin:
    def test_correct_credentials_sign_in(self, client: TestClient) -> None:
        _bootstrap(client)
        client.post("/auth/logout")
        r = client.post("/auth/login", json={"username": "desk.admin", "password": _ADMIN_PW})
        assert r.status_code == 200
        assert client.get("/auth/status").json()["user"]["username"] == "desk.admin"

    @pytest.mark.parametrize(
        ("username", "password"),
        [("desk.admin", "the-wrong-passphrase"), ("nobody", _ADMIN_PW)],
    )
    def test_failures_are_indistinguishable(
        self, client: TestClient, username: str, password: str
    ) -> None:
        _bootstrap(client)
        client.post("/auth/logout")
        r = client.post("/auth/login", json={"username": username, "password": password})
        assert r.status_code == 401
        assert r.json()["detail"] == "That username and password do not match an active account."

    def test_the_session_cookie_is_httponly_and_samesite(self, client: TestClient) -> None:
        """HttpOnly so an XSS bug cannot read the session; SameSite=Lax so a
        cross-site form post or image tag will not carry it."""
        r = client.post(
            "/auth/bootstrap", json={"username": "desk.admin", "password": _ADMIN_PW}
        )
        cookie = r.headers["set-cookie"].lower()
        assert "httponly" in cookie
        assert "samesite=lax" in cookie

    def test_logout_clears_the_session(self, client: TestClient) -> None:
        _bootstrap(client)
        assert client.post("/auth/logout").status_code == 200
        assert client.get("/auth/status").json()["user"] is None


class TestAccountManagementIsAlwaysClosed:
    """Never relaxed by the open mode.

    "This deployment is open" is a statement about the desk -- anyone may
    price a cargo -- and never about the account system. Handing the user list
    to an unauthenticated caller would be a real disclosure however open the
    rest is.
    """

    def test_listing_users_needs_an_admin_even_with_auth_off(self, client: TestClient) -> None:
        assert client.get("/auth/users").status_code == 401
        _bootstrap(client)
        assert client.get("/auth/users").status_code == 200

    def test_creating_users_needs_an_admin_even_with_auth_off(self, client: TestClient) -> None:
        r = client.post("/auth/users", json={"username": "x", "password": _USER_PW})
        assert r.status_code == 401

    def test_a_manager_cannot_manage_accounts(self, client: TestClient) -> None:
        _bootstrap(client)
        client.post(
            "/auth/users",
            json={"username": "m.rao", "password": _USER_PW, "role": "chartering_manager"},
        )
        client.post("/auth/logout")
        client.post("/auth/login", json={"username": "m.rao", "password": _USER_PW})
        assert client.get("/auth/users").status_code == 403

    def test_the_last_admin_cannot_be_demoted_through_the_api(self, client: TestClient) -> None:
        admin = _bootstrap(client)
        r = client.patch(f"/auth/users/{admin['user_id']}", json={"role": "viewer"})
        assert r.status_code == 409
        assert "only active admin" in r.json()["detail"]

    def test_an_unknown_user_id_is_a_404(self, client: TestClient) -> None:
        _bootstrap(client)
        assert client.patch("/auth/users/no-such-id", json={"role": "viewer"}).status_code == 404


class TestOpenMode:
    def test_the_desk_itself_is_reachable_without_signing_in(self, client: TestClient) -> None:
        """The fresh-clone property. This is the test that fails if anyone
        makes enforcement the default."""
        assert client.get("/ports").status_code == 200
        assert client.get("/meta").status_code == 200
        assert client.get("/health").status_code == 200


class TestClosedMode:
    def test_every_desk_route_needs_a_session(self, client: TestClient, closed: None) -> None:
        assert client.get("/ports").status_code == 401
        assert client.get("/meta").status_code == 401

    def test_the_way_in_stays_open(self, client: TestClient, closed: None) -> None:
        """Enforcement must not lock out the endpoints needed to get in: you
        cannot sign in if /auth/login itself demands a signed-in user."""
        assert client.get("/health").status_code == 200
        assert client.get("/auth/status").status_code == 200
        assert client.post("/auth/bootstrap", json={"username": "a", "password": _ADMIN_PW}).status_code == 200

    def test_signing_in_opens_the_desk(self, client: TestClient, closed: None) -> None:
        _bootstrap(client)
        assert client.get("/ports").status_code == 200
        client.post("/auth/logout")
        assert client.get("/ports").status_code == 401

    def test_a_viewer_cannot_record_an_outcome(self, client: TestClient, closed: None) -> None:
        """The reason the chartering-manager role exists. The ledger scores
        this system's own recommendations; if everyone who can read a quote
        can also write an outcome, the performance figures mean nothing."""
        _bootstrap(client)
        client.post("/auth/users", json={"username": "v.iyer", "password": _USER_PW, "role": "viewer"})
        client.post("/auth/logout")
        client.post("/auth/login", json={"username": "v.iyer", "password": _USER_PW})
        r = client.post(
            "/ledger/outcome",
            json={
                "entry_id": "does-not-exist",
                "realized_rate_usd_per_day": 20000,
                "realized_at_date": "2026-08-20",
            },
        )
        assert r.status_code == 403
        assert "chartering_manager" in r.json()["detail"]

    def test_a_viewer_cannot_reset_the_ledger(self, client: TestClient, closed: None) -> None:
        _bootstrap(client)
        client.post("/auth/users", json={"username": "v.iyer", "password": _USER_PW, "role": "viewer"})
        client.post("/auth/logout")
        client.post("/auth/login", json={"username": "v.iyer", "password": _USER_PW})
        assert client.delete("/ledger/live").status_code == 403

    def test_a_manager_passes_the_role_check_on_outcomes(
        self, client: TestClient, closed: None
    ) -> None:
        """404 rather than 403: the role was accepted and the entry simply
        does not exist, which is the distinction that proves the gate opened."""
        _bootstrap(client)
        client.post(
            "/auth/users",
            json={"username": "m.rao", "password": _USER_PW, "role": "chartering_manager"},
        )
        client.post("/auth/logout")
        client.post("/auth/login", json={"username": "m.rao", "password": _USER_PW})
        r = client.post(
            "/ledger/outcome",
            json={
                "entry_id": "does-not-exist",
                "realized_rate_usd_per_day": 20000,
                "realized_at_date": "2026-08-20",
            },
        )
        assert r.status_code == 404

    def test_a_disabled_account_is_locked_out_immediately(
        self, client: TestClient, closed: None
    ) -> None:
        """Not at the next expiry -- the session dies with the account, which
        is why sessions are server-side rather than self-contained tokens."""
        # Two clients, so the two sessions are genuinely concurrent. Reusing
        # one client and swapping its cookies does not work: logging out to
        # switch identity destroys the first session server-side, which is
        # precisely the behaviour under test.
        admin = client
        _bootstrap(admin)
        created = admin.post(
            "/auth/users", json={"username": "v.iyer", "password": _USER_PW, "role": "viewer"}
        ).json()["user"]

        viewer = TestClient(main.app)
        viewer.post("/auth/login", json={"username": "v.iyer", "password": _USER_PW})
        assert viewer.get("/ports").status_code == 200

        assert admin.patch(
            f"/auth/users/{created['user_id']}", json={"is_active": False}
        ).status_code == 200
        assert viewer.get("/ports").status_code == 401
