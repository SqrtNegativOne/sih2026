"""End-to-end tests for the FastAPI backend, against real market data on
disk -- same real-data-only testing philosophy as the rest of this repo,
now exercised through the actual HTTP layer teammates will build against.

``POST /quote`` returns a typed envelope:
``{"status": "feasible" | "structural_infeasible" | "contingent_infeasible", "quote": {...} | null, ...}``.
Malformed *requests* still return 4xx/503 -- the envelope is only for a
well-formed request that the optimizer cannot (or can only partly) solve.
"""
from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from ml.live_forecast import latest_available_date
from opt import ledger
from opt.network import PortEnum

client = TestClient(app)


@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path, monkeypatch):
    """P6 fix: every test in this file calls POST /quote against the real
    FastAPI app, and _run_quote_envelope records a real Live Decision
    Ledger entry as a side effect on every feasible/contingent_infeasible
    call -- confirmed as a real, reproducible pollution source (a full
    `pytest -q` run left genuine dated entries in
    raw_data/ledger/ledger_entries.jsonl, timestamped from this file's own
    origin/dest/commodity combinations). autouse so every existing and
    future test here is isolated by construction, not by remembering to
    request a fixture -- same isolation pattern (and same three patched
    attributes) as tests/backend/test_ledger_api.py's isolated_ledger."""
    monkeypatch.setattr(ledger, "LEDGER_DIR", tmp_path / "ledger")
    monkeypatch.setattr(ledger, "ENTRIES_LOG", tmp_path / "ledger" / "ledger_entries.jsonl")
    monkeypatch.setattr(ledger, "OUTCOMES_LOG", tmp_path / "ledger" / "ledger_outcomes.jsonl")


def _laycan():
    today = latest_available_date()
    return (today + timedelta(days=5)).isoformat(), (today + timedelta(days=18)).isoformat()


def _feasible_quote(payload: dict) -> dict:
    """POST /quote, assert a feasible envelope, return the inner quote."""
    r = client.post("/quote", json=payload)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "feasible", body
    assert body["quote"] is not None
    return body["quote"]


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_meta_reports_latest_data_date():
    r = client.get("/meta")
    assert r.status_code == 200
    assert r.json()["latest_data_date"] == latest_available_date().isoformat()


def test_list_ports_matches_real_port_enum_and_carries_coordinates():
    r = client.get("/ports")
    assert r.status_code == 200
    rows = r.json()
    assert {row["code"] for row in rows} == {p.name for p in PortEnum}
    for row in rows:
        assert -90.0 <= row["lat"] <= 90.0
        assert -180.0 <= row["lon"] <= 180.0


def test_quote_happy_path_real_route():
    start, end = _laycan()
    data = _feasible_quote({
        "cargo_volume_dwt": 70_000,
        "origin_port": "NEWCASTLE_AU",
        "dest_port": "PARADIP",
        "laycan_start": start,
        "laycan_end": end,
        "contract_term_days": 30,
        "commodity": "Thermal Coal",
    })

    # Port fields must be plain request-ready code strings, not embedded objects.
    assert data["origin_port"] == "NEWCASTLE_AU"
    assert data["dest_port"] == "PARADIP"
    assert data["origin_port_check"]["port"] == "NEWCASTLE_AU"
    assert data["dest_port_check"]["port"] == "PARADIP"

    assert data["lock_action"] in ("LOCK", "WAIT")
    assert data["today_quote_usd_per_day"] > 0
    assert data["rate_forecast"], "expected at least one real forecast horizon"
    for h in data["rate_forecast"]:
        assert h["p10_usd_per_day"] <= h["p50_usd_per_day"] <= h["p90_usd_per_day"]

    assert data["origin_port_check"]["congestion_label"] in ("LOW", "MODERATE", "HIGH")
    assert data["dest_port_check"]["congestion_label"] in ("LOW", "MODERATE", "HIGH")

    # Every solver route carries at least one leg with a real polyline.
    assert data["route_exploration"], "expected the solver to record the routes it explored"
    for route in data["route_exploration"]:
        assert route["status"] in ("chosen", "considered", "rejected")
        assert route["legs"]
        for leg in route["legs"]:
            assert len(leg["polyline"]) >= 2

    # A caller must be able to round-trip the port codes it got back.
    assert PortEnum[data["origin_port"]] == PortEnum.NEWCASTLE_AU


def test_quote_lowercase_port_code_is_accepted():
    start, end = _laycan()
    data = _feasible_quote({
        "cargo_volume_dwt": 70_000,
        "origin_port": "newcastle_au",
        "dest_port": "paradip",
        "laycan_start": start,
        "laycan_end": end,
    })
    assert data["origin_port"] == "NEWCASTLE_AU"


def test_quote_unknown_port_returns_422_with_valid_options_listed():
    start, end = _laycan()
    r = client.post("/quote", json={
        "cargo_volume_dwt": 70_000,
        "origin_port": "ATLANTIS",
        "dest_port": "PARADIP",
        "laycan_start": start,
        "laycan_end": end,
    })
    assert r.status_code == 422
    assert "PARADIP" in r.json()["detail"]  # a real, valid code is listed to help the caller


def test_quote_inverted_laycan_returns_422():
    today = latest_available_date()
    r = client.post("/quote", json={
        "cargo_volume_dwt": 70_000,
        "origin_port": "NEWCASTLE_AU",
        "dest_port": "PARADIP",
        "laycan_start": (today + timedelta(days=18)).isoformat(),
        "laycan_end": (today + timedelta(days=5)).isoformat(),
    })
    assert r.status_code == 422


@pytest.mark.parametrize("bad_dwt", [0, -50])
def test_quote_non_positive_cargo_returns_422(bad_dwt):
    start, end = _laycan()
    r = client.post("/quote", json={
        "cargo_volume_dwt": bad_dwt,
        "origin_port": "NEWCASTLE_AU",
        "dest_port": "PARADIP",
        "laycan_start": start,
        "laycan_end": end,
    })
    assert r.status_code == 422


def test_quote_risk_tolerance_out_of_range_returns_422():
    start, end = _laycan()
    r = client.post("/quote", json={
        "cargo_volume_dwt": 70_000,
        "origin_port": "NEWCASTLE_AU",
        "dest_port": "PARADIP",
        "laycan_start": start,
        "laycan_end": end,
        "risk_tolerance": 1.5,
    })
    assert r.status_code == 422


def test_quote_structurally_impossible_tonnage_is_reported_not_priced():
    start, end = _laycan()
    r = client.post("/quote", json={
        "cargo_volume_dwt": 1_000_000_000,
        "origin_port": "NEWCASTLE_AU",
        "dest_port": "PARADIP",
        "laycan_start": start,
        "laycan_end": end,
    })
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "structural_infeasible"
    assert body["quote"] is None
    fields = {p["field"] for p in body["structural_problems"]}
    assert "cargo_volume_dwt" in fields


def test_quote_laycan_before_charter_date_is_structural():
    today = latest_available_date()
    r = client.post("/quote", json={
        "cargo_volume_dwt": 70_000,
        "origin_port": "NEWCASTLE_AU",
        "dest_port": "PARADIP",
        "laycan_start": (today - timedelta(days=40)).isoformat(),
        "laycan_end": (today - timedelta(days=20)).isoformat(),
        "as_of": today.isoformat(),
    })
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "structural_infeasible"
    assert "laycan" in {p["field"] for p in body["structural_problems"]}


def test_quote_contingent_infeasible_is_relaxed_and_flagged():
    """Every vessel class is ruled out of Beira on berth limits (draft 8.0 m,
    max 30,000 dwt). The optimizer must not silently price it: it reports the
    original blockers, loosens the constraints, and returns the relaxed solve."""
    start, end = _laycan()
    r = client.post("/quote", json={
        "cargo_volume_dwt": 70_000,
        "origin_port": "RICHARDS_BAY",
        "dest_port": "BEIRA",
        "laycan_start": start,
        "laycan_end": end,
    })
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "contingent_infeasible"
    assert body["original_blockers"]
    assert body["relaxations_applied"]
    assert body["quote"] is not None
    assert body["quote"]["fleet_mix"]["configurations"], "relaxed solve should have a feasible config"


def test_quote_stream_emits_stages_then_a_result():
    start, end = _laycan()
    with client.stream("POST", "/quote/stream", json={
        "cargo_volume_dwt": 70_000,
        "origin_port": "NEWCASTLE_AU",
        "dest_port": "PARADIP",
        "laycan_start": start,
        "laycan_end": end,
    }) as resp:
        assert resp.status_code == 200
        events = [line[len("event: "):] for line in resp.iter_lines() if line.startswith("event: ")]
    assert events[-1] == "result"
    assert events.count("stage") >= 6  # forecast_load + the pipeline steps, start and done


def test_quote_with_vessel_and_revenue_gets_scheduled():
    start, end = _laycan()
    today = latest_available_date()
    data = _feasible_quote({
        "cargo_volume_dwt": 70_000,
        "origin_port": "NEWCASTLE_AU",
        "dest_port": "PARADIP",
        "laycan_start": start,
        "laycan_end": end,
        "revenue_usd": 1_450_000.0,
        "vessels": [{
            "vessel_id": "TEST_Panamax",
            "vessel_class": "Panamax",
            "current_port": "NEWCASTLE_AU",
            "status": "idle",
            "available_from": today.isoformat(),
            "dwt": 70_000, "draft_m": 13.5, "loa_m": 220.0, "beam_m": 32.0, "speed_kn": 13.0,
            "laden_fuel_consumption_tpd": 32.0, "ballast_fuel_consumption_tpd": 27.0,
        }],
    })
    assignments = data["full_recommendation"]["voyage_assignments"]
    assert assignments, "a feasible vessel with real revenue should get a real voyage assignment"
    assert assignments[0]["vessel_id"] == "TEST_Panamax"


def test_quote_with_vessel_but_no_revenue_stays_unassigned():
    start, end = _laycan()
    today = latest_available_date()
    data = _feasible_quote({
        "cargo_volume_dwt": 70_000,
        "origin_port": "NEWCASTLE_AU",
        "dest_port": "PARADIP",
        "laycan_start": start,
        "laycan_end": end,
        "vessels": [{
            "vessel_id": "TEST_Panamax",
            "vessel_class": "Panamax",
            "current_port": "NEWCASTLE_AU",
            "status": "idle",
            "available_from": today.isoformat(),
            "dwt": 70_000, "draft_m": 13.5, "loa_m": 220.0, "beam_m": 32.0, "speed_kn": 13.0,
            "laden_fuel_consumption_tpd": 32.0, "ballast_fuel_consumption_tpd": 27.0,
        }],
    })
    # No revenue_usd -> zero revenue is never profitable -> correctly no assignment.
    assert data["full_recommendation"]["voyage_assignments"] == []


def test_quote_unknown_vessel_class_returns_422_with_valid_options_listed():
    start, end = _laycan()
    today = latest_available_date()
    r = client.post("/quote", json={
        "cargo_volume_dwt": 70_000,
        "origin_port": "NEWCASTLE_AU",
        "dest_port": "PARADIP",
        "laycan_start": start,
        "laycan_end": end,
        "vessels": [{
            "vessel_id": "TEST_Bad",
            "vessel_class": "Ultramax",
            "current_port": "NEWCASTLE_AU",
            "status": "idle",
            "available_from": today.isoformat(),
            "dwt": 70_000, "draft_m": 13.5, "loa_m": 220.0, "beam_m": 32.0, "speed_kn": 13.0,
            "laden_fuel_consumption_tpd": 32.0, "ballast_fuel_consumption_tpd": 27.0,
        }],
    })
    assert r.status_code == 422
    assert "Panamax" in r.json()["detail"]


def test_quote_vessel_class_is_case_insensitive():
    start, end = _laycan()
    today = latest_available_date()
    _feasible_quote({
        "cargo_volume_dwt": 70_000,
        "origin_port": "NEWCASTLE_AU",
        "dest_port": "PARADIP",
        "laycan_start": start,
        "laycan_end": end,
        "revenue_usd": 1_450_000.0,
        "vessels": [{
            "vessel_id": "TEST_Panamax",
            "vessel_class": "panamax",
            "current_port": "newcastle_au",
            "status": "idle",
            "available_from": today.isoformat(),
            "dwt": 70_000, "draft_m": 13.5, "loa_m": 220.0, "beam_m": 32.0, "speed_kn": 13.0,
            "laden_fuel_consumption_tpd": 32.0, "ballast_fuel_consumption_tpd": 27.0,
        }],
    })


def test_quote_without_vessels_field_is_unchanged():
    """Omitting `vessels` entirely (the pre-existing request shape) must keep
    working -- vessels/revenue_usd are additive, not a breaking change."""
    start, end = _laycan()
    data = _feasible_quote({
        "cargo_volume_dwt": 70_000,
        "origin_port": "NEWCASTLE_AU",
        "dest_port": "PARADIP",
        "laycan_start": start,
        "laycan_end": end,
    })
    assert data["full_recommendation"]["voyage_assignments"] == []


def test_quote_far_future_as_of_returns_503():
    far_future = (latest_available_date() + timedelta(days=3650))
    r = client.post("/quote", json={
        "cargo_volume_dwt": 70_000,
        "origin_port": "NEWCASTLE_AU",
        "dest_port": "PARADIP",
        "laycan_start": (far_future + timedelta(days=5)).isoformat(),
        "laycan_end": (far_future + timedelta(days=18)).isoformat(),
        "as_of": far_future.isoformat(),
    })
    assert r.status_code == 503
