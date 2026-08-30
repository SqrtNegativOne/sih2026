"""Turn the pure CII arithmetic in ``emissions.cii`` into a projection for a
real vessel on a real quoted route.

No HTTP, no frontend -- this is consumed by ``opt.quote.quote`` and attached
to ``QuoteResult.emissions``.
"""
from __future__ import annotations

import logging
import math
from datetime import date
from typing import Final

from data_builders.provenance import Provenance
from emissions import cii
from opt.geography import UnknownPortPairError
from opt.geography import distance_nm as geo_distance_nm
from opt.network import PortEnum
from opt.types import Vessel, VesselCIIProjection, VoyageEmissions

__all__ = ["project_vessel_cii", "project_voyage_emissions"]

LOGGER: Final[logging.Logger] = logging.getLogger(__name__)

_EARTH_RADIUS_NM: Final[float] = 3440.065


def _lonlat(port: PortEnum) -> tuple[float, float]:
    """(lon, lat) for a port, from the same coordinate table opt.route_trace
    uses for its own great-circle fallback."""
    from data_builders.build_geography import PORT_COORDS

    loc = PORT_COORDS[port.value.id]
    return (loc.lon, loc.lat)


def _great_circle_nm(a: PortEnum, b: PortEnum) -> float:
    """Haversine great-circle distance (nm) between two ports -- the same
    fallback opt.route_trace._rough_nm uses when the real sea-route matrix
    has no entry for a pair."""
    lon1, lat1 = _lonlat(a)
    lon2, lat2 = _lonlat(b)
    rlon1, rlat1, rlon2, rlat2 = map(math.radians, (lon1, lat1, lon2, lat2))
    hav = (
        math.sin((rlat2 - rlat1) / 2) ** 2
        + math.cos(rlat1) * math.cos(rlat2) * math.sin((rlon2 - rlon1) / 2) ** 2
    )
    return 2 * _EARTH_RADIUS_NM * math.asin(math.sqrt(hav))


def _leg_distance_nm(from_port: PortEnum, to_port: PortEnum) -> tuple[float, bool]:
    """Real sea distance for one leg, falling back to a great-circle
    approximation (flagged) exactly the way opt.route_trace does for the
    same UnknownPortPairError -- see that module's own docstring for why a
    missing pair degrades rather than raising into a quote."""
    if from_port is to_port:
        return 0.0, False
    try:
        return geo_distance_nm(from_port.value.id, to_port.value.id), False
    except UnknownPortPairError:
        return _great_circle_nm(from_port, to_port), True


def project_vessel_cii(
    vessel: Vessel,
    origin_port: PortEnum,
    dest_port: PortEnum,
    *,
    rating_year: int,
    fuel_type: str = "HFO",
) -> VesselCIIProjection:
    """Project this vessel's IMO CII rating for the voyage
    ``vessel.current_port -> origin_port -> dest_port``.

    Steps
    -----
    1. Ballast leg: ``vessel.current_port -> origin_port`` -- the position
       leg to reach the load port. Zero distance/days when the vessel is
       already there (no ``distance_nm`` lookup needed or attempted for a
       same-port pair).
    2. Laden leg: ``origin_port -> dest_port`` -- the cargo-carrying leg.
    3. Days at sea per leg: ``distance_nm / (speed_kn * 24)``.
    4. Fuel burnt: ``laden_days * laden_fuel_consumption_tpd +
       ballast_days * ballast_fuel_consumption_tpd`` -- both legs' burn goes
       into one total, since a real bunker bill does not separate them.
    5. CO2: ``cii.co2_tonnes(fuel_tonnes, fuel_type)``.
    6. Attained CII: ``cii.attained_cii(co2_tonnes, dwt, laden_distance_nm)``
       -- LADEN distance only in the denominator.

       Step 6 is the subtle one. The IMO's own AER definition (MEPC.352(78)
       para 4.2) takes transport work over the ship's FULL annual distance
       sailed, which for a real vessel's real logbook includes every laden
       and ballast leg it ran that year -- not achievable here, since this
       function only ever sees one voyage, not a full year. Given that, two
       readings are both defensible for a single-voyage projection:
       charge only the laden leg's own fuel against the laden distance
       (an EEOI-style "cost of carrying this cargo" view), or charge BOTH
       legs' fuel against the laden distance only. This implementation
       takes the second, more conservative reading -- ballast fuel is real
       fuel burnt to be ABLE to carry this cargo, and omitting it from the
       numerator while keeping the full transport-work denominator would
       understate the voyage's true carbon intensity. It correctly reads
       worse (never better) than a real annual DCS-reported AER would for a
       vessel with a genuinely productive ballast leg (e.g. one that also
       carried backhaul cargo, which this single-voyage view cannot see) --
       flagged here explicitly rather than silently picked.

    Raises
    ------
    cii.CIIYearNotPublishedError
        ``rating_year`` is outside 2023-2026 -- propagated, not swallowed;
        ``project_voyage_emissions`` is the layer that catches it.
    """
    ballast_distance_nm, ballast_fallback = _leg_distance_nm(vessel.current_port, origin_port)
    laden_distance_nm, laden_fallback = _leg_distance_nm(origin_port, dest_port)
    is_distance_fallback = ballast_fallback or laden_fallback

    sea_speed_nm_per_day = vessel.speed_kn * 24.0
    ballast_days = ballast_distance_nm / sea_speed_nm_per_day
    laden_days = laden_distance_nm / sea_speed_nm_per_day
    sea_days = ballast_days + laden_days

    fuel_tonnes = (
        laden_days * vessel.laden_fuel_consumption_tpd
        + ballast_days * vessel.ballast_fuel_consumption_tpd
    )
    co2_t = cii.co2_tonnes(fuel_tonnes, fuel_type)
    attained = cii.attained_cii(co2_t, vessel.dwt, laden_distance_nm)
    required = cii.required_cii_bulk_carrier(vessel.dwt, rating_year)
    rating = cii.rating_bulk_carrier(attained, required)
    margin_pct = (required - attained) / required * 100.0

    return VesselCIIProjection(
        vessel_id=vessel.vessel_id,
        vessel_class=vessel.vessel_class,
        dwt=vessel.dwt,
        laden_distance_nm=laden_distance_nm,
        ballast_distance_nm=ballast_distance_nm,
        sea_days=sea_days,
        fuel_tonnes=fuel_tonnes,
        co2_tonnes=co2_t,
        attained_cii=attained,
        required_cii=required,
        rating=rating,
        rating_year=rating_year,
        margin_pct=margin_pct,
        is_distance_fallback=is_distance_fallback,
        provenance=Provenance.MODEL_DERIVED.value,
    )


_RATING_ORDER: Final[dict[str, int]] = {"A": 0, "B": 1, "C": 2, "D": 3, "E": 4}


def project_voyage_emissions(
    vessels: list[Vessel] | None,
    origin_port: PortEnum,
    dest_port: PortEnum,
    *,
    as_of: date,
    fuel_type: str = "HFO",
) -> VoyageEmissions | None:
    """CII projections for every real vessel on this route, or None.

    Returns None when ``vessels`` is None/empty -- a cargo-only quote has no
    vessel to rate, and inventing one would violate house rule 1. Also
    returns None (with a logged warning) when ``as_of.year`` is outside the
    IMO's published 2023-2026 range -- see
    ``cii.CIIYearNotPublishedError``. Never raises into a quote.
    """
    if not vessels:
        return None

    rating_year = as_of.year
    projections: list[VesselCIIProjection] = []
    for vessel in vessels:
        try:
            projections.append(
                project_vessel_cii(
                    vessel, origin_port, dest_port, rating_year=rating_year, fuel_type=fuel_type
                )
            )
        except cii.CIIYearNotPublishedError:
            LOGGER.warning(
                "No published IMO CII reduction factor for %s; skipping CII projection.",
                rating_year,
            )
            return None

    fleet_worst_rating = max((p.rating for p in projections), key=lambda r: _RATING_ORDER[r])
    return VoyageEmissions(
        as_of=as_of,
        rating_year=rating_year,
        projections=tuple(projections),
        fleet_worst_rating=fleet_worst_rating,
    )
