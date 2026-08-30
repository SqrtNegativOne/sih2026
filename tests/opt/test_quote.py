"""Real-data, end-to-end tests for opt.quote.quote() -- the single-cargo
front door. No synthetic ForecastFan fixtures: quote() exists specifically
to turn real market history + real trained models into a real result, so it
is tested the same way ml.live_forecast and every other real-data module in
this codebase is -- against what's actually on disk."""
from __future__ import annotations

from datetime import timedelta

import pytest

from ml.live_forecast import latest_available_date
from opt.network import PortEnum
from opt.quote import InsufficientMarketDataError, quote
from opt.types import Vessel, VesselClass


def _today():
    return latest_available_date()


def _laycan(today):
    return today + timedelta(days=5), today + timedelta(days=18)


def test_quote_real_route_end_to_end():
    today = _today()
    start, end = _laycan(today)
    result = quote(
        cargo_volume_dwt=70_000,
        origin_port=PortEnum.NEWCASTLE_AU,
        dest_port=PortEnum.PARADIP,
        laycan_start=start,
        laycan_end=end,
        contract_term_days=30,
        commodity="Thermal Coal",
    )

    assert result.origin_port == PortEnum.NEWCASTLE_AU
    assert result.dest_port == PortEnum.PARADIP
    assert result.laycan_start == start
    assert result.laycan_end == end
    assert result.as_of == today
    assert result.cargo_volume_dwt == 70_000
    # F-03: target_vessel_class is feasibility-aware, not a pure tonnage-size
    # lookup -- 70,000 dwt sits in the Panamax reference band (58k-82k), but
    # a real Panamax (82,000 dwt reference) cannot call Paradip at all (its
    # published max is 75,000 dwt), so the honest answer here is whatever
    # the fleet-mix frontier itself found cheapest and feasible, not the
    # class this cargo would "naturally" ship on somewhere unconstrained.
    assert result.fleet_mix is not None and result.fleet_mix.configurations, (
        "expected at least one feasible configuration for this real route"
    )
    assert result.target_vessel_class == result.fleet_mix.configurations[0].vessel_class
    assert result.lock_action in ("LOCK", "WAIT")
    assert result.today_quote_usd_per_day > 0
    assert result.ceiling_usd_per_day > 0

    assert result.rate_forecast, "expected at least one real forecast horizon"
    for h in result.rate_forecast:
        assert h.p10_usd_per_day <= h.p50_usd_per_day <= h.p90_usd_per_day
        assert h.direction in ("up", "down", "flat")
        assert 0.0 <= h.confidence_pct <= 100.0

    assert result.origin_port_check.port == PortEnum.NEWCASTLE_AU
    assert result.dest_port_check.port == PortEnum.PARADIP
    assert result.origin_port_check.congestion_label in ("LOW", "MODERATE", "HIGH")
    assert result.dest_port_check.congestion_label in ("LOW", "MODERATE", "HIGH")

    assert result.explanations.lock_wait.summary


def test_quote_usd_per_mt_is_internally_consistent_with_usd_per_day():
    today = _today()
    start, end = _laycan(today)
    result = quote(
        cargo_volume_dwt=70_000,
        origin_port=PortEnum.NEWCASTLE_AU,
        dest_port=PortEnum.PARADIP,
        laycan_start=start,
        laycan_end=end,
    )
    assert result.assumed_transit_days is not None
    assert result.today_quote_usd_per_mt == pytest.approx(
        result.today_quote_usd_per_day * result.assumed_transit_days / result.cargo_volume_dwt
    )
    assert result.ceiling_usd_per_mt == pytest.approx(
        result.ceiling_usd_per_day * result.assumed_transit_days / result.cargo_volume_dwt
    )


def test_quote_savings_total_is_per_day_times_contract_term():
    today = _today()
    start, end = _laycan(today)
    result = quote(
        cargo_volume_dwt=70_000,
        origin_port=PortEnum.NEWCASTLE_AU,
        dest_port=PortEnum.PARADIP,
        laycan_start=start,
        laycan_end=end,
        contract_term_days=45,
    )
    assert result.expected_savings_usd_total == pytest.approx(
        result.expected_savings_usd_per_day * 45
    )


def test_quote_derives_smaller_class_for_smaller_cargo():
    today = _today()
    start, end = _laycan(today)
    result = quote(
        cargo_volume_dwt=30_000,  # within Handysize's reference band
        origin_port=PortEnum.BALIKPAPAN,
        dest_port=PortEnum.VIZAG,
        laycan_start=start,
        laycan_end=end,
    )
    assert result.target_vessel_class == VesselClass.HANDYSIZE


@pytest.mark.parametrize("bad_dwt", [0, -100])
def test_quote_rejects_non_positive_cargo(bad_dwt):
    today = _today()
    start, end = _laycan(today)
    with pytest.raises(ValueError):
        quote(
            cargo_volume_dwt=bad_dwt,
            origin_port=PortEnum.NEWCASTLE_AU,
            dest_port=PortEnum.PARADIP,
            laycan_start=start,
            laycan_end=end,
        )


def test_quote_rejects_non_positive_contract_term():
    today = _today()
    start, end = _laycan(today)
    with pytest.raises(ValueError):
        quote(
            cargo_volume_dwt=70_000,
            origin_port=PortEnum.NEWCASTLE_AU,
            dest_port=PortEnum.PARADIP,
            laycan_start=start,
            laycan_end=end,
            contract_term_days=0,
        )


def test_quote_rejects_inverted_laycan():
    today = _today()
    with pytest.raises(ValueError):
        quote(
            cargo_volume_dwt=70_000,
            origin_port=PortEnum.NEWCASTLE_AU,
            dest_port=PortEnum.PARADIP,
            laycan_start=today + timedelta(days=18),
            laycan_end=today + timedelta(days=5),
        )


def test_quote_raises_insufficient_market_data_far_in_the_future():
    far_future = _today() + timedelta(days=3650)
    with pytest.raises(InsufficientMarketDataError):
        quote(
            cargo_volume_dwt=70_000,
            origin_port=PortEnum.NEWCASTLE_AU,
            dest_port=PortEnum.PARADIP,
            laycan_start=far_future + timedelta(days=5),
            laycan_end=far_future + timedelta(days=18),
            as_of=far_future,
        )


def test_quote_with_a_real_vessel_produces_a_voyage_assignment():
    today = _today()
    start, end = _laycan(today)
    vessel = Vessel(
        vessel_id="TEST_Panamax",
        vessel_class=VesselClass.PANAMAX,
        current_port=PortEnum.NEWCASTLE_AU,
        status="idle",
        available_from=today,
        dwt=70_000, draft_m=13.5, loa_m=220.0, beam_m=32.0, speed_kn=13.0,
        laden_fuel_consumption_tpd=32.0, ballast_fuel_consumption_tpd=27.0,
    )
    result = quote(
        cargo_volume_dwt=70_000,
        origin_port=PortEnum.NEWCASTLE_AU,
        dest_port=PortEnum.PARADIP,
        laycan_start=start,
        laycan_end=end,
        vessels=[vessel],
        revenue_usd=1_450_000.0,  # real figure from run_live_scenario.py's scenario_a
    )
    assert result.full_recommendation.voyage_assignments, (
        "a real, feasible vessel + real revenue supplied to quote() should get scheduled"
    )


def test_quote_with_a_vessel_but_no_revenue_leaves_it_unassigned_not_fabricated():
    # Honest default: without a real revenue figure, quote() must not invent
    # one just to force an assignment -- the vessel should show up as idle
    # (and get a real repositioning recommendation) instead.
    today = _today()
    start, end = _laycan(today)
    vessel = Vessel(
        vessel_id="TEST_Panamax",
        vessel_class=VesselClass.PANAMAX,
        current_port=PortEnum.NEWCASTLE_AU,
        status="idle",
        available_from=today,
        dwt=70_000, draft_m=13.5, loa_m=220.0, beam_m=32.0, speed_kn=13.0,
        laden_fuel_consumption_tpd=32.0, ballast_fuel_consumption_tpd=27.0,
    )
    result = quote(
        cargo_volume_dwt=70_000,
        origin_port=PortEnum.NEWCASTLE_AU,
        dest_port=PortEnum.PARADIP,
        laycan_start=start,
        laycan_end=end,
        vessels=[vessel],
    )
    assert result.full_recommendation.voyage_assignments == []
    assert len(result.full_recommendation.repositioning_actions) == 1
    assert result.full_recommendation.repositioning_actions[0].vessel_id == "TEST_Panamax"


def test_quote_without_a_vessel_has_no_assignments_but_still_prices():
    today = _today()
    start, end = _laycan(today)
    result = quote(
        cargo_volume_dwt=70_000,
        origin_port=PortEnum.NEWCASTLE_AU,
        dest_port=PortEnum.PARADIP,
        laycan_start=start,
        laycan_end=end,
    )
    assert result.full_recommendation.voyage_assignments == []
    assert result.full_recommendation.repositioning_actions == []
    assert result.lock_action in ("LOCK", "WAIT")  # the actual quote still computed


def test_quote_still_succeeds_end_to_end_when_the_weather_window_is_unavailable(monkeypatch):
    """2.4: opt.weather_window.transit_buffer() makes a real, cache-first
    HTTP call -- a cold cache with no network (or any other failure) must
    degrade the quote to transit_buffer=None, never break it. Forced here
    rather than relying on ambient network conditions at test-run time."""
    import opt.quote as quote_module

    def _raise(*args, **kwargs):
        raise RuntimeError("mock: weather window unavailable (no network / cold cache)")

    monkeypatch.setattr(quote_module, "compute_transit_buffer", _raise)

    today = _today()
    start, end = _laycan(today)
    result = quote(
        cargo_volume_dwt=70_000,
        origin_port=PortEnum.NEWCASTLE_AU,
        dest_port=PortEnum.PARADIP,
        laycan_start=start,
        laycan_end=end,
    )
    assert result.transit_buffer is None
    assert result.full_recommendation.transit_buffer is None
    assert result.lock_action in ("LOCK", "WAIT")  # the actual quote still computed
