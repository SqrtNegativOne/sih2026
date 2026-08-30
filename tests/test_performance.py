"""P7 -- warm-cache performance budgets, measured against the real backend,
not asserted from memory. Budgets are set from real, live-measured timings
(see the P7 completion report for the exact numbers each budget is based
on), with real margin -- not tuned tight enough to be flaky, not so loose
they'd miss a real regression.

Three endpoints are deliberately NOT covered by a tight budget here, each
for a real, disclosed reason:
  - GET /tonnage-field (and /tonnage-field/validation): the FIRST call in a
    fresh process pays a real, one-time reconstruction cost (~7-11s and
    ~30-35s respectively, measured live) -- this file warms both before
    asserting anything, exactly matching how a real backend process would
    behave (the cost is paid once at whichever request happens to be first,
    not on every request).
  - POST /fragility: a real, disclosed multi-second sweep by design (Tier
    1/2/3 flip search across several variables) -- not a caching gap. Its
    real, live-measured cost (~17-21s) is asserted as a documented budget,
    not silently ignored.
  - GET /ledger/replay: a real ~20-25 minute cold PSO+Monte Carlo backtest,
    already covered elsewhere (see tests/test_integration_e2e.py's module
    docstring) -- not re-run here.
"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from opt import ledger

client = TestClient(app)


@pytest.fixture()
def isolated_ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger, "LEDGER_DIR", tmp_path / "ledger")
    monkeypatch.setattr(ledger, "ENTRIES_LOG", tmp_path / "ledger" / "ledger_entries.jsonl")
    monkeypatch.setattr(ledger, "OUTCOMES_LOG", tmp_path / "ledger" / "ledger_outcomes.jsonl")


def _timed(method: str, path: str, **kwargs) -> tuple[float, object]:
    t0 = time.perf_counter()
    resp = client.request(method, path, **kwargs)
    elapsed = time.perf_counter() - t0
    assert resp.status_code == 200, resp.text
    return elapsed, resp


class TestWarmEndpointBudgets:
    """Warms the real, disclosed cold-start endpoints first (matching real
    backend startup behaviour: whichever request happens first pays the
    real reconstruction/cache-population cost once, every request after is
    warm), then
    asserts every routine, no-UI-action-should-be-slow endpoint stays
    fast."""

    @classmethod
    @pytest.fixture(autouse=True, scope="class")
    def _warm_caches(cls):
        client.get("/tonnage-field")
        client.get("/tonnage-field/validation")
        # opt.backhaul's per-store file-read cache, opt.repositioning's
        # hazard-rate cache, and the BT register lookups it depends on are
        # each warmed independently of the two calls above -- a real,
        # measured first-ever /backhaul call in a cold process took 5.56s
        # for just 2 candidates (multiple caches warming at once), well
        # past a padded-but-still-tight budget. Warmed here the same way
        # tonnage-field is, so the timed call below measures a genuinely
        # warm request, not a second cold one wearing a bigger number.
        client.post("/backhaul", json={
            "vessel": {
                "vessel_id": "WARM-V1", "vessel_class": "Supramax", "current_port": "PARADIP", "status": "idle",
                "available_from": "2026-09-01", "dwt": 55_000, "draft_m": 12.0, "loa_m": 190.0, "beam_m": 32.0,
                "speed_kn": 12.0, "laden_fuel_consumption_tpd": 30.0, "ballast_fuel_consumption_tpd": 25.0,
            },
            "discharge_port": "PARADIP", "candidate_load_ports": ["DHAMRA"],
        })

    def test_health(self) -> None:
        elapsed, _ = _timed("GET", "/health")
        assert elapsed < 0.5

    def test_meta(self) -> None:
        elapsed, _ = _timed("GET", "/meta")
        assert elapsed < 0.5

    def test_ports(self) -> None:
        elapsed, _ = _timed("GET", "/ports")
        assert elapsed < 0.5

    def test_port_reality_warm(self) -> None:
        elapsed, _ = _timed(
            "GET", "/ports/PARADIP/reality",
            params={"vessel_dwt": 55000, "draft_m": 12.0, "loa_m": 190.0, "beam_m": 32.0},
        )
        assert elapsed < 1.0

    def test_port_berths(self) -> None:
        elapsed, _ = _timed("GET", "/ports/PARADIP/berths")
        assert elapsed < 0.5

    def test_port_calls(self) -> None:
        elapsed, _ = _timed("GET", "/ports/PARADIP/calls")
        assert elapsed < 1.0

    def test_port_waits(self) -> None:
        elapsed, _ = _timed("GET", "/ports/PARADIP/waits")
        assert elapsed < 1.0

    def test_tonnage_field_warm(self) -> None:
        # Already warmed by the class fixture -- this call must be fast.
        elapsed, _ = _timed("GET", "/tonnage-field")
        assert elapsed < 1.0

    def test_tonnage_field_forward_warm(self) -> None:
        elapsed, _ = _timed("GET", "/tonnage-field/forward")
        assert elapsed < 1.0

    def test_tonnage_field_validation_warm(self) -> None:
        # Already warmed by the class fixture -- must be fast on repeat.
        elapsed, _ = _timed("GET", "/tonnage-field/validation")
        assert elapsed < 1.0

    def test_quote(self, isolated_ledger) -> None:
        elapsed, _ = _timed(
            "POST", "/quote",
            json={
                "cargo_volume_dwt": 75_000, "origin_port": "NEWCASTLE_AU", "dest_port": "PARADIP",
                "laycan_start": "2026-09-15", "laycan_end": "2026-09-25",
            },
        )
        assert elapsed < 3.0  # real measured: ~0.8-1.6s

    def test_landed_cost(self) -> None:
        elapsed, _ = _timed(
            "POST", "/landed-cost",
            json={
                "dest_port": "PARADIP", "cargo_volume_mt": 75_000,
                "freight_usd_per_day": 18_790.0, "voyage_days": 18.17, "commodity": "coal",
            },
        )
        assert elapsed < 1.0  # real measured: ~0.03s

    def test_backhaul_small_candidate_set(self) -> None:
        elapsed, _ = _timed(
            "POST", "/backhaul",
            json={
                "vessel": {
                    "vessel_id": "PERF-V1", "vessel_class": "Supramax", "current_port": "PARADIP", "status": "idle",
                    "available_from": "2026-09-01", "dwt": 55_000, "draft_m": 12.0, "loa_m": 190.0, "beam_m": 32.0,
                    "speed_kn": 12.0, "laden_fuel_consumption_tpd": 30.0, "ballast_fuel_consumption_tpd": 25.0,
                },
                "discharge_port": "PARADIP", "candidate_load_ports": ["DHAMRA", "VIZAG"],
            },
        )
        # Warm (see the class fixture): a cold first call measured 5.56s
        # for 2 candidates: real, but that is the cost of the FIRST backhaul
        # request in a process, not a routine per-request cost -- this
        # budget is for the request after that one.
        assert elapsed < 3.0

    def test_ledger_live(self) -> None:
        elapsed, _ = _timed("GET", "/ledger/live")
        assert elapsed < 0.5

    def test_ledger_live_performance(self) -> None:
        elapsed, _ = _timed("GET", "/ledger/live/performance")
        assert elapsed < 0.5


class TestDisclosedNonInstantEndpoints:
    """Real, by-design costs -- documented budgets, not silently ignored."""

    def test_fragility_sweep_completes_within_its_real_documented_budget(self) -> None:
        elapsed, _ = _timed(
            "POST", "/fragility",
            json={
                "cargo_volume_dwt": 75_000, "origin_port": "NEWCASTLE_AU", "dest_port": "PARADIP",
                "laycan_start": "2026-09-15", "laycan_end": "2026-09-25",
            },
        )
        # Real measured: ~17-21s. This is a documented, disclosed sweep
        # cost (Tier 1/2/3 flip search), not a caching gap -- the frontend
        # shows a "Sweeping…" state for exactly this reason.
        assert elapsed < 40.0
