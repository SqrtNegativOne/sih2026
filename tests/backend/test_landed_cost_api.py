"""P6 -- POST /landed-cost, and POST /quote's additive `landed_cost` field."""
from __future__ import annotations

from typing import ClassVar

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from opt import ledger

client = TestClient(app)


@pytest.fixture()
def isolated_ledger(tmp_path, monkeypatch):
    """Same isolation pattern as tests/backend/test_ledger_api.py -- POST
    /quote records a real live-ledger entry as a side effect, and this
    suite must never write into the project's own raw_data/ledger."""
    monkeypatch.setattr(ledger, "LEDGER_DIR", tmp_path / "ledger")
    monkeypatch.setattr(ledger, "ENTRIES_LOG", tmp_path / "ledger" / "ledger_entries.jsonl")
    monkeypatch.setattr(ledger, "OUTCOMES_LOG", tmp_path / "ledger" / "ledger_outcomes.jsonl")
    return tmp_path


class TestLandedCostEndpoint:
    def test_component_wise_breakdown_with_no_commercial_terms(self) -> None:
        r = client.post("/landed-cost", json={
            "dest_port": "PARADIP", "cargo_volume_mt": 75_000,
            "freight_usd_per_day": 15_000, "voyage_days": 25, "commodity": "coal",
        })
        assert r.status_code == 200
        body = r.json()
        assert body["freight_usd_per_mt"] == pytest.approx((15_000 * 25) / 75_000)
        assert body["handling_cost_usd_per_mt"] is None
        assert body["demurrage_cost_usd_per_mt"] is None
        assert "handling_cost" in body["components_missing"]
        assert body["commodity_price_usd_per_mt"] is not None
        assert body["commodity_price_provenance"] == "OBSERVED"

    def test_user_supplied_commercial_terms_are_declared(self) -> None:
        r = client.post("/landed-cost", json={
            "dest_port": "PARADIP", "cargo_volume_mt": 75_000,
            "freight_usd_per_day": 15_000, "voyage_days": 25,
            "handling_rate_usd_per_mt": 3.5,
        })
        assert r.status_code == 200
        body = r.json()
        assert body["handling_cost_usd_per_mt"] == 3.5
        assert body["handling_cost_provenance"] == "DECLARED"

    def test_unknown_port_is_422(self) -> None:
        r = client.post("/landed-cost", json={
            "dest_port": "NOT_A_REAL_PORT", "cargo_volume_mt": 75_000,
            "freight_usd_per_day": 15_000, "voyage_days": 25,
        })
        assert r.status_code == 422

    def test_unknown_commodity_is_422(self) -> None:
        r = client.post("/landed-cost", json={
            "dest_port": "PARADIP", "cargo_volume_mt": 75_000,
            "freight_usd_per_day": 15_000, "voyage_days": 25, "commodity": "limestone",
        })
        assert r.status_code == 422

    def test_fx_conversion_round_trips(self) -> None:
        r = client.post("/landed-cost", json={
            "dest_port": "PARADIP", "cargo_volume_mt": 75_000,
            "freight_usd_per_day": 15_000, "voyage_days": 25, "convert_to_inr": True,
        })
        body = r.json()
        assert body["fx_inr_per_usd"] is not None
        assert body["partial_total_inr_per_mt"] == pytest.approx(body["partial_total_usd_per_mt"] * body["fx_inr_per_usd"])


class TestQuoteCarriesAPartialLandedCost:
    def test_a_real_feasible_quote_includes_freight_only_landed_cost(self, isolated_ledger) -> None:
        r = client.post("/quote", json={
            "cargo_volume_dwt": 75_000, "origin_port": "NEWCASTLE_AU", "dest_port": "PARADIP",
            "laycan_start": "2026-09-15", "laycan_end": "2026-09-25",
        })
        assert r.status_code == 200
        body = r.json()
        assert "landed_cost" in body
        if body["status"] == "feasible" and body["quote"]["assumed_transit_days"] is not None:
            lc = body["landed_cost"]
            assert lc is not None
            # wait cost is NOT auto-computed at /quote (no real opex figure
            # available there) -- must be explicitly absent, not a fake 0.0.
            assert lc["wait_cost_usd_per_mt"] is None
            assert "wait_cost" in lc["components_missing"]
            assert "wait_cost" not in lc["components_included"]
            assert lc["freight_usd_per_mt"] == pytest.approx(
                body["quote"]["today_quote_usd_per_day"] * body["quote"]["assumed_transit_days"] / 75_000
            )


class TestWarRiskPremiumOverHttp:
    """The war-risk line must be reachable over HTTP, and must stay absent
    -- not zero -- whenever the caller has not supplied what it needs."""

    _BASE: ClassVar[dict[str, object]] = {
        "dest_port": "VIZAG", "cargo_volume_mt": 75_000,
        "freight_usd_per_day": 20_000, "voyage_days": 31,
    }

    def test_omitting_hull_value_leaves_the_line_absent_not_zero(self) -> None:
        r = client.post("/landed-cost", json={**self._BASE, "origin_port": "HAMPTON_ROADS"})
        assert r.status_code == 200
        body = r.json()
        assert body["war_risk_usd_per_mt"] is None
        assert body["war_risk_premium_usd"] is None
        assert "war_risk" in body["components_missing"]
        # The route's real Listed Areas are still reported -- the gap is the
        # hull value, not the geography.
        assert "gulf_of_aden" in body["war_risk_areas"]

    def test_a_real_exposure_returns_its_own_labelled_line(self) -> None:
        r = client.post("/landed-cost", json={
            **self._BASE, "origin_port": "HAMPTON_ROADS", "hull_value_usd": 45_000_000,
        })
        assert r.status_code == 200
        body = r.json()
        assert body["war_risk_usd_per_mt"] == pytest.approx(45_000_000 * 0.004 * 5 / 75_000)
        assert body["war_risk_provenance"] == "ESTIMATED"
        assert body["war_risk_rate_is_caller_supplied"] is False
        assert "NOT A MARKET QUOTE" in body["war_risk_reason"]
        assert "war_risk" in body["components_included"]
        # Freight is untouched -- the premium is never folded into it.
        assert body["freight_usd_per_mt"] == pytest.approx((20_000 * 31) / 75_000)

    def test_a_route_through_no_listed_area_owes_nothing(self) -> None:
        r = client.post("/landed-cost", json={
            **self._BASE, "dest_port": "PARADIP", "origin_port": "NEWCASTLE_AU",
            "hull_value_usd": 45_000_000,
        })
        assert r.status_code == 200
        body = r.json()
        assert body["war_risk_usd_per_mt"] is None
        assert body["war_risk_areas"] == []
        assert "no Joint War Committee Listed Area" in body["war_risk_reason"]

    def test_a_caller_supplied_rate_is_used_and_flagged_as_theirs(self) -> None:
        r = client.post("/landed-cost", json={
            **self._BASE, "origin_port": "HAMPTON_ROADS", "hull_value_usd": 45_000_000,
            "war_risk_rate_pct_per_7_days": 1.5,
        })
        assert r.status_code == 200
        body = r.json()
        assert body["war_risk_rate_pct_per_7_days"] == 1.5
        assert body["war_risk_rate_is_caller_supplied"] is True
        assert "NOT A MARKET QUOTE" not in body["war_risk_reason"]

    def test_an_unknown_origin_port_is_a_422_via_the_shared_resolver(self) -> None:
        r = client.post("/landed-cost", json={
            **self._BASE, "origin_port": "NOT_A_PORT", "hull_value_usd": 45_000_000,
        })
        assert r.status_code == 422
        assert "origin_port" in r.json()["detail"]
