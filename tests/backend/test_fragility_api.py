"""P5 -- POST /fragility against the real FastAPI app and real engines (no
mocking, same philosophy as test_reality_api.py / test_tonnage_api.py)."""
from __future__ import annotations

import time

from fastapi.testclient import TestClient

from backend.main import app

client = TestClient(app)

_BASE_BODY = {
    "cargo_volume_dwt": 75_000,
    "origin_port": "GANGAVARAM",
    "dest_port": "GANGAVARAM",
    "laycan_start": "2026-09-15",
    "laycan_end": "2026-09-25",
}


class TestRealPayload:
    def test_real_sweep_returns_findings_with_fragility_ranking(self) -> None:
        r = client.post("/fragility", json={**_BASE_BODY, "variables": ["vessel_draft_m", "permissible_draft_m"]})
        assert r.status_code == 200
        body = r.json()
        assert len(body["findings"]) == 2
        for f in body["findings"]:
            assert f["fragility_category"] in ("FRAGILE", "STABLE", "UNAVAILABLE")
            assert isinstance(f["fragility_rank"], int)

    def test_unavailable_variables_are_present_with_reasons_not_hidden(self) -> None:
        r = client.post("/fragility", json={**_BASE_BODY, "variables": ["origin_wait_days", "dest_wait_days"]})
        body = r.json()
        assert len(body["findings"]) == 2
        for f in body["findings"]:
            assert f["flip_found"] is False
            assert f["unavailable_reason"] is not None
            assert f["fragility_category"] == "UNAVAILABLE"
            # P5: empirical wait context is still real and present even though unavailable for search.
            assert f["berth_truth_context"] is not None
            assert "empirical_wait_sample_n" in f["berth_truth_context"]

    def test_draft_findings_carry_real_tide_and_draft_status_context(self) -> None:
        r = client.post("/fragility", json={**_BASE_BODY, "variables": ["vessel_draft_m"]})
        finding = r.json()["findings"][0]
        ctx = finding["berth_truth_context"]
        assert ctx is not None  # Gangavaram has a real register entry -- binding_constraint is not None
        assert ctx["draft_source"] is not None

    def test_current_decision_is_a_real_signature(self) -> None:
        r = client.post("/fragility", json={**_BASE_BODY, "variables": ["cargo_volume_dwt"]})
        body = r.json()
        assert body["current_decision"]["target_vessel_class"] in ("Handysize", "Supramax", "Panamax", "Capesize")

    def test_malformed_port_returns_422(self) -> None:
        r = client.post("/fragility", json={**_BASE_BODY, "origin_port": "NOT_A_REAL_PORT"})
        assert r.status_code == 422

    def test_unknown_variable_name_returns_422(self) -> None:
        r = client.post("/fragility", json={**_BASE_BODY, "variables": ["not_a_real_variable"]})
        assert r.status_code == 422

    def test_inverted_laycan_returns_422(self) -> None:
        r = client.post(
            "/fragility",
            json={**_BASE_BODY, "laycan_start": "2026-09-25", "laycan_end": "2026-09-15", "variables": ["cargo_volume_dwt"]},
        )
        assert r.status_code == 422


class TestLatencyBudget:
    def test_a_scoped_subset_sweep_stays_within_a_sane_latency_budget(self) -> None:
        t0 = time.perf_counter()
        r = client.post("/fragility", json={**_BASE_BODY, "variables": ["cargo_volume_dwt", "vessel_draft_m"]})
        elapsed = time.perf_counter() - t0
        assert r.status_code == 200
        assert elapsed < 15.0  # Tier 1 only for both -- should be fast, well under a full 8-variable sweep

    def test_default_full_sweep_completes_and_covers_all_eight_variables(self) -> None:
        t0 = time.perf_counter()
        r = client.post("/fragility", json=_BASE_BODY)
        elapsed = time.perf_counter() - t0
        assert r.status_code == 200
        body = r.json()
        assert len(body["findings"]) == 8
        # A real, sane upper bound -- not tuned tight, just catching a real
        # regression (an accidental uncapped loop, a lost memoisation cache).
        assert elapsed < 60.0
