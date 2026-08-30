"""P6 -- POST /backhaul."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.main import app

client = TestClient(app)

_VESSEL = {
    "vessel_id": "V1", "vessel_class": "Supramax", "current_port": "PARADIP",
    "status": "idle", "available_from": "2026-09-01",
    "dwt": 55_000, "draft_m": 12.0, "loa_m": 190.0, "beam_m": 32.0, "speed_kn": 12.0,
    "laden_fuel_consumption_tpd": 30.0, "ballast_fuel_consumption_tpd": 25.0,
}


class TestBackhaulEndpoint:
    def test_explicit_candidate_ports_are_scored_and_sorted(self) -> None:
        r = client.post("/backhaul", json={
            "vessel": _VESSEL, "discharge_port": "PARADIP",
            "candidate_load_ports": ["DHAMRA", "GANGAVARAM", "HAMPTON_ROADS"],
        })
        assert r.status_code == 200
        body = r.json()
        assert body["vessel_id"] == "V1"
        assert body["discharge_port"] == "PARADIP"
        assert len(body["results"]) == 3
        scores = [row["score"] for row in body["results"]]
        assert scores == sorted(scores, reverse=True)
        assert {row["candidate_load_port"] for row in body["results"]} == {"DHAMRA", "GANGAVARAM", "HAMPTON_ROADS"}

    def test_credit_is_always_null_with_a_real_reason(self) -> None:
        r = client.post("/backhaul", json={
            "vessel": _VESSEL, "discharge_port": "PARADIP", "candidate_load_ports": ["DHAMRA"],
        })
        row = r.json()["results"][0]
        assert row["credit_usd_per_mt"] is None
        assert "no rate/freight/$ field" in row["credit_evidence_reason"]

    def test_far_port_is_timing_infeasible_and_scores_zero(self) -> None:
        r = client.post("/backhaul", json={
            "vessel": _VESSEL, "discharge_port": "PARADIP",
            "candidate_load_ports": ["HAMPTON_ROADS"], "assumed_window_days": 1,
        })
        row = r.json()["results"][0]
        assert row["timing_feasible"] is False
        assert row["score"] == 0.0

    def test_unknown_port_is_422(self) -> None:
        r = client.post("/backhaul", json={
            "vessel": _VESSEL, "discharge_port": "NOT_A_REAL_PORT",
        })
        assert r.status_code == 422

    def test_missing_vessel_is_422(self) -> None:
        r = client.post("/backhaul", json={"discharge_port": "PARADIP"})
        assert r.status_code == 422

    def test_score_usd_is_a_real_dollar_figure_backed_by_a_real_quote(self) -> None:
        """F-18: score used to be a bare probability. The endpoint should now
        also thread a real, non-fabricated TC quote through into score_usd."""
        r = client.post("/backhaul", json={
            "vessel": _VESSEL, "discharge_port": "PARADIP", "candidate_load_ports": ["DHAMRA"],
        })
        row = r.json()["results"][0]
        assert row["base_tce_usd_per_day"] is not None
        assert row["base_tce_usd_per_day"] > 0.0
        assert row["ballast_cost_usd"] >= 0.0
        assert row["score_usd"] is not None
        expected = row["cargo_probability"] * row["base_tce_usd_per_day"] * row["assumed_window_days"] - row["ballast_cost_usd"]
        assert row["score_usd"] == pytest.approx(expected)

    def test_results_are_sorted_by_the_real_dollar_score_not_bare_probability(self) -> None:
        r = client.post("/backhaul", json={
            "vessel": _VESSEL, "discharge_port": "PARADIP",
            "candidate_load_ports": ["DHAMRA", "GANGAVARAM", "HAMPTON_ROADS"],
        })
        rows = r.json()["results"]
        usd_scores = [row["score_usd"] for row in rows]
        assert usd_scores == sorted(usd_scores, reverse=True)
