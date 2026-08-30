"""P2 §6 -- the four new Berth Reality Engine endpoints, against the real
FastAPI app and real data on disk (no mocking, same philosophy as
test_backend.py)."""
from __future__ import annotations

from fastapi.testclient import TestClient

from backend.main import app

client = TestClient(app)


class TestPortReality:
    def test_a_registered_port_returns_a_real_verdict(self) -> None:
        r = client.get(
            "/ports/GANGAVARAM/reality",
            params={"vessel_dwt": 60_000, "draft_m": 13.0, "loa_m": 230.0, "beam_m": 30.0},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["port"] == "GANGAVARAM"
        assert body["verdict"] in ("FEASIBLE", "INFEASIBLE", "CANNOT_VERIFY")
        # PortEnum flattened to its symbolic code, never an embedded Port object.
        assert isinstance(body["port"], str)

    def test_a_fallback_port_still_returns_a_verdict_not_an_error(self) -> None:
        r = client.get(
            "/ports/NEWCASTLE_AU/reality",
            params={"vessel_dwt": 80_000, "draft_m": 14.0, "loa_m": 280.0, "beam_m": 45.0},
        )
        assert r.status_code == 200
        assert r.json()["limit_source"] == "PORTENUM_FALLBACK"

    def test_unknown_port_returns_422(self) -> None:
        r = client.get(
            "/ports/NOT_A_REAL_PORT/reality",
            params={"vessel_dwt": 1, "draft_m": 1, "loa_m": 1, "beam_m": 1},
        )
        assert r.status_code == 422

    def test_missing_required_query_param_returns_422(self) -> None:
        r = client.get("/ports/GANGAVARAM/reality", params={"vessel_dwt": 60_000})
        assert r.status_code == 422

    def test_repeated_calls_are_cached_and_return_identical_results(self) -> None:
        params = {"vessel_dwt": 60_000, "draft_m": 13.0, "loa_m": 230.0, "beam_m": 30.0}
        r1 = client.get("/ports/GANGAVARAM/reality", params=params)
        r2 = client.get("/ports/GANGAVARAM/reality", params=params)
        assert r1.json() == r2.json()


class TestPortBerths:
    def test_a_registered_port_lists_real_berths(self) -> None:
        r = client.get("/ports/GANGAVARAM/berths")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "OK"
        assert len(body["berths"]) == 9  # the real, seeded Gangavaram berth count
        assert all(b["source_doc_id"] for b in body["berths"])  # every row cites a source

    def test_an_unregistered_port_is_explicitly_not_available(self) -> None:
        r = client.get("/ports/NEWCASTLE_AU/berths")
        assert r.status_code == 200  # explicit payload, not a 404/empty-200 masquerade
        body = r.json()
        assert body["status"] == "NOT_AVAILABLE"
        assert body["berths"] == []
        assert body["reason"]


class TestPortCalls:
    def test_paradip_returns_real_paginated_rows(self) -> None:
        r = client.get("/ports/PARADIP/calls", params={"limit": 5})
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "OK"
        assert body["total"] > 100
        assert len(body["rows"]) == 5
        assert body["rows"][0]["vessel_name"]

    def test_pagination_offset_moves_the_window(self) -> None:
        page1 = client.get("/ports/PARADIP/calls", params={"limit": 5, "offset": 0}).json()
        page2 = client.get("/ports/PARADIP/calls", params={"limit": 5, "offset": 5}).json()
        assert page1["rows"] != page2["rows"]

    def test_a_port_with_no_ingested_history_is_not_available(self) -> None:
        r = client.get("/ports/SINGAPORE/calls")
        assert r.status_code == 200
        assert r.json()["status"] == "NOT_AVAILABLE"

    def test_invalid_limit_returns_422(self) -> None:
        r = client.get("/ports/PARADIP/calls", params={"limit": 0})
        assert r.status_code == 422
        r2 = client.get("/ports/PARADIP/calls", params={"limit": 1000})
        assert r2.status_code == 422


class TestPortWaits:
    def test_paradip_reports_real_sufficient_distributions(self) -> None:
        r = client.get("/ports/PARADIP/waits")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "OK"
        arrival_to_berth = body["distributions"]["ARRIVAL_TO_BERTH"]
        assert arrival_to_berth["is_sufficient"] is True
        assert arrival_to_berth["p90_hours"] >= arrival_to_berth["p50_hours"]

    def test_a_thin_port_reports_baseline_only_not_a_fabricated_percentile(self) -> None:
        r = client.get("/ports/GANGAVARAM/waits")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "BASELINE_ONLY"
        for dist in body["distributions"].values():
            assert dist["p50_hours"] is None

    def test_muara_pantai_discloses_its_portwatch_proxy_status(self) -> None:
        r = client.get("/ports/MUARA_PANTAI/waits")
        assert r.status_code == 200
        assert r.json()["portwatch_mapping_status"] == "PROXY"
