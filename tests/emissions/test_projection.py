"""Real-data tests for emissions.projection -- turning emissions.cii's pure
arithmetic into a projection for a real vessel on a real quoted route."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from emissions import cii
from emissions.projection import project_vessel_cii, project_voyage_emissions
from ml.live_forecast import latest_available_date
from opt.geography import distance_nm
from opt.network import PortEnum
from opt.quote import quote
from opt.types import Vessel, VesselClass

_RATING_ORDER = {"A": 0, "B": 1, "C": 2, "D": 3, "E": 4}


def _vessel(
    *,
    vessel_id: str = "V1",
    current_port: PortEnum = PortEnum.VIZAG,
    dwt: float = 82_000.0,
    speed_kn: float = 14.0,
    laden_fuel_consumption_tpd: float = 35.0,
    ballast_fuel_consumption_tpd: float = 28.0,
) -> Vessel:
    return Vessel(
        vessel_id=vessel_id,
        vessel_class=VesselClass.PANAMAX,
        current_port=current_port,
        status="idle",
        available_from=date(2025, 1, 1),
        available_until=None,
        dwt=dwt,
        draft_m=12.0,
        loa_m=225.0,
        beam_m=32.0,
        speed_kn=speed_kn,
        laden_fuel_consumption_tpd=laden_fuel_consumption_tpd,
        ballast_fuel_consumption_tpd=ballast_fuel_consumption_tpd,
    )


def test_project_vessel_cii_matches_a_hand_checked_figure():
    vessel = _vessel(current_port=PortEnum.VIZAG)
    origin, dest = PortEnum.PARADIP, PortEnum.NEWCASTLE_AU
    rating_year = 2024

    projection = project_vessel_cii(vessel, origin, dest, rating_year=rating_year)

    # Expected values derived independently, from the same real distance
    # matrix and the same cii primitives -- not a hardcoded magic number.
    ballast_distance_nm = distance_nm(vessel.current_port.value.id, origin.value.id)
    laden_distance_nm = distance_nm(origin.value.id, dest.value.id)
    sea_speed_nm_per_day = vessel.speed_kn * 24.0
    ballast_days = ballast_distance_nm / sea_speed_nm_per_day
    laden_days = laden_distance_nm / sea_speed_nm_per_day
    fuel_tonnes = (
        laden_days * vessel.laden_fuel_consumption_tpd
        + ballast_days * vessel.ballast_fuel_consumption_tpd
    )
    co2_t = cii.co2_tonnes(fuel_tonnes, "HFO")
    attained = cii.attained_cii(co2_t, vessel.dwt, laden_distance_nm)
    required = cii.required_cii_bulk_carrier(vessel.dwt, rating_year)
    rating = cii.rating_bulk_carrier(attained, required)
    margin_pct = (required - attained) / required * 100.0

    assert projection.vessel_id == vessel.vessel_id
    assert projection.dwt == vessel.dwt
    assert projection.ballast_distance_nm == pytest.approx(ballast_distance_nm)
    assert projection.laden_distance_nm == pytest.approx(laden_distance_nm)
    assert projection.sea_days == pytest.approx(ballast_days + laden_days)
    assert projection.fuel_tonnes == pytest.approx(fuel_tonnes)
    assert projection.co2_tonnes == pytest.approx(co2_t)
    assert projection.attained_cii == pytest.approx(attained)
    assert projection.required_cii == pytest.approx(required)
    assert projection.rating == rating
    assert projection.rating_year == rating_year
    assert projection.margin_pct == pytest.approx(margin_pct)
    assert projection.is_distance_fallback is False
    assert projection.provenance == "MODEL_DERIVED"


def test_project_voyage_emissions_returns_none_for_no_vessels():
    assert (
        project_voyage_emissions(None, PortEnum.PARADIP, PortEnum.NEWCASTLE_AU, as_of=date(2024, 6, 1))
        is None
    )
    assert (
        project_voyage_emissions([], PortEnum.PARADIP, PortEnum.NEWCASTLE_AU, as_of=date(2024, 6, 1))
        is None
    )


def test_vessel_already_at_origin_has_zero_ballast_distance():
    vessel = _vessel(current_port=PortEnum.PARADIP)
    projection = project_vessel_cii(
        vessel, PortEnum.PARADIP, PortEnum.NEWCASTLE_AU, rating_year=2024
    )
    assert projection.ballast_distance_nm == 0.0
    assert projection.is_distance_fallback is False


def test_a_thirsty_vessel_rates_worse_than_an_efficient_one_on_the_identical_route():
    origin, dest = PortEnum.PARADIP, PortEnum.NEWCASTLE_AU
    rating_year = 2024
    # Both vessels start at the load port (ballast leg = 0), isolating the
    # comparison to laden-leg fuel consumption alone.
    efficient = _vessel(vessel_id="EFFICIENT", current_port=origin, laden_fuel_consumption_tpd=31.0)
    thirsty = _vessel(vessel_id="THIRSTY", current_port=origin, laden_fuel_consumption_tpd=45.0)

    efficient_projection = project_vessel_cii(efficient, origin, dest, rating_year=rating_year)
    thirsty_projection = project_vessel_cii(thirsty, origin, dest, rating_year=rating_year)

    assert thirsty_projection.attained_cii > efficient_projection.attained_cii
    assert _RATING_ORDER[thirsty_projection.rating] > _RATING_ORDER[efficient_projection.rating]


def test_project_voyage_emissions_returns_none_for_an_unpublished_rating_year():
    vessel = _vessel()
    result = project_voyage_emissions(
        [vessel], PortEnum.PARADIP, PortEnum.NEWCASTLE_AU, as_of=date(2027, 1, 1)
    )
    assert result is None


def test_quote_with_no_vessels_still_returns_emissions_none():
    today = latest_available_date()
    start, end = today + timedelta(days=5), today + timedelta(days=18)
    result = quote(
        cargo_volume_dwt=70_000,
        origin_port=PortEnum.NEWCASTLE_AU,
        dest_port=PortEnum.PARADIP,
        laycan_start=start,
        laycan_end=end,
    )
    assert result.emissions is None


def test_quote_with_a_real_vessel_populates_emissions():
    today = latest_available_date()
    start, end = today + timedelta(days=5), today + timedelta(days=18)
    vessel = _vessel(current_port=PortEnum.NEWCASTLE_AU)
    result = quote(
        cargo_volume_dwt=70_000,
        origin_port=PortEnum.NEWCASTLE_AU,
        dest_port=PortEnum.PARADIP,
        laycan_start=start,
        laycan_end=end,
        vessels=[vessel],
    )
    if today.year < 2023 or today.year > 2026:
        assert result.emissions is None
    else:
        assert result.emissions is not None
        assert result.emissions.rating_year == today.year
        assert len(result.emissions.projections) == 1
        assert result.emissions.fleet_worst_rating == result.emissions.projections[0].rating
