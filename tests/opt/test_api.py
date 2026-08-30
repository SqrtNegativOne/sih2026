from datetime import date, timedelta

import pytest

from opt.api import format_recommendation_text, run_optimizer
from opt.network import PortEnum, RouteFamily
from opt.types import CargoParcel, ForecastFan, OptimizerInputs, Vessel, VesselClass


def _entry_window_vessel() -> Vessel:
    return Vessel(
        vessel_id="V1",
        vessel_class=VesselClass.SUPRAMAX,
        current_port=PortEnum.PARADIP,
        status="idle",
        available_from=date(2025, 1, 1),
        dwt=55_000,
        draft_m=12.0,
        loa_m=190.0,
        beam_m=32.0,
        speed_kn=12.0,
        laden_fuel_consumption_tpd=30.0,
        ballast_fuel_consumption_tpd=25.0,
    )


def test_optimal_entry_window_output():
    v = _entry_window_vessel()

    # 55,000 dwt derives to Supramax (opt.ceiling.select_vessel_class_for_cargo),
    # matching the vessel above and the old hardcoded target_class=SUPRAMAX --
    # origin/dest are real EC-India ports so no long ballast leg confounds the
    # scheduling side-effect of run_optimizer; this test only cares about the
    # entry-window math below, which is unaffected by that assignment either way.
    # F-07: laycan_start is set 60 days out from as_of (comfortably past the
    # 30-day planning horizon) so the laycan-respecting clamp added below
    # never binds here -- this test is specifically the "laycan is not the
    # constraint" case; test_optimal_entry_window_clamped_by_laycan below
    # covers the case where it is.
    as_of = date(2025, 1, 1)
    parcel = CargoParcel(
        parcel_id="P1",
        origin_port=PortEnum.PARADIP,
        dest_port=PortEnum.VIZAG,
        commodity="Coal",
        volume_dwt=55_000,
        laycan_start=as_of + timedelta(days=60),
        laycan_end=as_of + timedelta(days=70),
        route_family=RouteFamily.INTRA_EC_INDIA,
        revenue_usd=500_000.0,
    )

    fan7 = ForecastFan(vessel_class=VesselClass.SUPRAMAX, horizon_days=7, p10=7000, p50=8000, p90=9000)
    fan30 = ForecastFan(vessel_class=VesselClass.SUPRAMAX, horizon_days=30, p10=3000, p50=4000, p90=5000)

    inputs = OptimizerInputs(
        vessels=[v],
        parcels=[parcel],
        tc_quotes={VesselClass.SUPRAMAX: 12000.0},
        planning_horizon_days=30,
        contract_term_days=30,
        forecasts=[fan7, fan30],
        basis={}
    )

    res = run_optimizer(inputs, as_of=as_of)

    # P50 falls monotonically from 8000 at day 7 to 4000 at day 30, so the trough is
    # at the far end of the planning horizon and the window is centred there.
    # The quote of 12000 sits far above the horizon-weighted ceiling
    # ((19*8000 + 11*4000) / 30 = 6533), so the action must be WAIT.
    assert res.lock_action == "WAIT"
    assert res.optimal_entry_window_start_day == 27
    assert res.optimal_entry_window_end_day == 30
    assert res.optimal_entry_window_p50_usd == pytest.approx(4000.0)

    # The rendered text is presentation over those fields, not a separate
    # source of truth -- assert it stays in sync rather than asserting only
    # on the structured fields.
    text = format_recommendation_text(res)
    assert "Optimal entry window: Day 27 to 30 (P50 trough at ~$4,000/day)" in text


def test_optimal_entry_window_clamped_by_laycan():
    """F-07: the true (unconstrained) trough here is day 30 -- see the test
    above -- but a cargo whose laycan opens well before that has no use for
    a "wait until day 30" recommendation: the vessel would already be late.
    The entry window must never extend past laycan_start."""
    v = _entry_window_vessel()
    as_of = date(2025, 1, 1)
    parcel = CargoParcel(
        parcel_id="P1",
        origin_port=PortEnum.PARADIP,
        dest_port=PortEnum.VIZAG,
        commodity="Coal",
        volume_dwt=55_000,
        laycan_start=as_of + timedelta(days=10),
        laycan_end=as_of + timedelta(days=20),
        route_family=RouteFamily.INTRA_EC_INDIA,
        revenue_usd=500_000.0,
    )
    fan7 = ForecastFan(vessel_class=VesselClass.SUPRAMAX, horizon_days=7, p10=7000, p50=8000, p90=9000)
    fan30 = ForecastFan(vessel_class=VesselClass.SUPRAMAX, horizon_days=30, p10=3000, p50=4000, p90=5000)
    inputs = OptimizerInputs(
        vessels=[v], parcels=[parcel], tc_quotes={VesselClass.SUPRAMAX: 12000.0},
        planning_horizon_days=30, contract_term_days=30, forecasts=[fan7, fan30], basis={},
    )

    res = run_optimizer(inputs, as_of=as_of)

    assert res.lock_action == "WAIT"
    assert res.optimal_entry_window_end_day is not None
    assert res.optimal_entry_window_end_day <= 10  # never past laycan_start
    assert res.optimal_entry_window_start_day is not None
    assert res.optimal_entry_window_start_day >= 1


def test_optimal_entry_window_none_when_laycan_already_open():
    """F-07: if the laycan has already opened (or opens today), there is no
    honest "wait until day X" window left to search -- report it as no
    clear trough (None), not a stale or out-of-range date."""
    v = _entry_window_vessel()
    as_of = date(2025, 1, 1)
    parcel = CargoParcel(
        parcel_id="P1",
        origin_port=PortEnum.PARADIP,
        dest_port=PortEnum.VIZAG,
        commodity="Coal",
        volume_dwt=55_000,
        laycan_start=as_of,
        laycan_end=as_of + timedelta(days=10),
        route_family=RouteFamily.INTRA_EC_INDIA,
        revenue_usd=500_000.0,
    )
    fan7 = ForecastFan(vessel_class=VesselClass.SUPRAMAX, horizon_days=7, p10=7000, p50=8000, p90=9000)
    fan30 = ForecastFan(vessel_class=VesselClass.SUPRAMAX, horizon_days=30, p10=3000, p50=4000, p90=5000)
    inputs = OptimizerInputs(
        vessels=[v], parcels=[parcel], tc_quotes={VesselClass.SUPRAMAX: 12000.0},
        planning_horizon_days=30, contract_term_days=30, forecasts=[fan7, fan30], basis={},
    )

    res = run_optimizer(inputs, as_of=as_of)

    assert res.optimal_entry_window_start_day is None
    assert res.optimal_entry_window_end_day is None
    assert res.optimal_entry_window_p50_usd is None

    # Text rendering must degrade honestly too, not crash on the None
    # fields or claim a specific day that doesn't exist.
    text = format_recommendation_text(res)
    assert "laycan is already open" in text
