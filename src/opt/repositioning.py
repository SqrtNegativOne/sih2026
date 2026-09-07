"""Repositioning Engine (Sub-problem 3), rewritten: PS deliverable (c), done
properly.

The original engine hardcoded ``basis_mean = 0.0`` for every candidate port, so
``port_tce`` came out identical everywhere -- score reduced to
``const - ballast_cost - wait_cost``, maximised by not moving. It was
structurally incapable of saying "there is more cargo at Singapore than at
Paradip," which is exactly what this PS deliverable asks for. P0 fixed the
*distance* half of that (real geography, no more silent fallback) but the
missing half -- a real, port-specific signal for how likely cargo actually is
-- needed data that didn't exist until the P2 tonnage field did.

**P(cargo within N days), as a real hazard rate, not a guess.** Real PortWatch
export tonnage at a specific port, class-attributed via the P2 tonnage field's
own classmix weights and divided by a class's representative shipload size
(``tonnage.classmix.CLASS_MIDPOINT_DWT``), gives an implied daily rate of
loading events at *that port* -- treat that as a Poisson process rate lambda,
and ``P(cargo within N days) = 1 - exp(-lambda * N)`` is the standard
hazard-model result the plan asks for. Deliberately computed per *port*, not
per *basin*: an earlier version of this module aggregated to basin level (reusing
``tonnage.stockflow``'s already-basin-aggregated flows directly), and on real
data every basin's rate was so high that P(cargo within 30 days) saturated to
~1.0 everywhere -- true but useless, since a basin contains dozens of ports and
"will *some* cargo appear *somewhere* in the whole Pacific basin" doesn't help
choose between two candidate ports in it. Per-port rates actually differ.

**Coverage is real but partial.** Only 6 of the 15 PortEnum members have a
directly-matching label in the P2 tonnage harvest; another 5 match under a
different label (country-suffix / documented-proxy mismatches, mapped
explicitly in ``opt.network.PORT_TO_TONNAGE_LABEL`` -- shared with
``opt.congestion``'s dynamic wait-day estimator, hence living there rather
than here); 4 (Gangavaram, Sagar Sandheads, Gladstone, Hampton Roads,
Singapore) have no real PortWatch coverage at all and get an honest neutral
default rather than a fabricated number -- ``RepositionOption.probability_is_real_data``
says which is which so a caller isn't misled about which recommendations are
data-backed. The wait-day figure used below (``opt.congestion.dynamic_wait_days``)
follows the exact same real-data-or-honest-fallback contract.

**Performance.** Building the hazard rates needs the P2 tonnage
reconstruction, which does real per-port file I/O (order of seconds, not
milliseconds) -- cached at process scope (``functools.lru_cache``) since
``opt.api.run_optimizer`` calls this once per idle vessel and must not redo
that work on every call.
"""
from __future__ import annotations

import functools
import math
from dataclasses import dataclass
from datetime import timedelta

from data_builders.provenance import Provenance
from opt.ceiling import compute_ceiling
from opt.congestion import dynamic_wait_days
from opt.geography import distance_nm as geo_distance_nm
from opt.network import BLENDED_BUNKER_USD_PER_TONNE, PORT_TO_TONNAGE_LABEL, PortEnum
from opt.types import OptimizerInputs, Vessel, VesselClass

#: Trailing window of the tonnage field used to estimate today's hazard rate --
#: recent enough to reflect current conditions, long enough to average over
#: routine day-to-day call-count noise.
HAZARD_TRAILING_WINDOW_DAYS: int = 180

#: Used when no real hazard rate is available for a port (no tonnage-field
#: coverage) -- a neutral "no informed opinion" prior, not an assumption that
#: cargo is likely or unlikely there.
_NEUTRAL_PROBABILITY: float = 0.5


@functools.lru_cache(maxsize=1)
def _port_class_hazard_rates() -> dict[tuple[str, str], float]:
    """Real mean daily implied-loading-event rate per (tonnage-field port
    label, vessel_class), from the trailing window of that specific port's real
    PortWatch export tonnage. Cached for the process lifetime -- see module
    docstring on why.
    """
    import polars as pl

    from tonnage.basins import PortIndexMissingError, load_port_index, port_csv_path
    from tonnage.classmix import (
        CLASS_MIDPOINT_DWT,
        NoActivityError,
        class_weights,
        mean_parcel_size,
    )

    try:
        ports = load_port_index()
    except PortIndexMissingError:
        return {}

    rates: dict[tuple[str, str], float] = {}
    for port in ports:
        path = port_csv_path(port.label)
        if not path.exists():
            continue
        try:
            estimate = mean_parcel_size(port.label, path)
        except NoActivityError:
            continue
        weights = class_weights(estimate.mean_parcel_t)

        daily = pl.read_csv(path, columns=["date", "export_dry_bulk"], schema_overrides={"date": pl.Date})
        if daily.is_empty():
            continue
        cutoff = daily["date"].max() - timedelta(days=HAZARD_TRAILING_WINDOW_DAYS)
        recent = daily.filter(pl.col("date") > cutoff)
        if recent.is_empty():
            continue
        mean_export_t = float(recent["export_dry_bulk"].mean())

        for cls, w in weights.items():
            if w <= 0:
                continue
            rates[(port.label, cls.value)] = (mean_export_t * w) / CLASS_MIDPOINT_DWT[cls]
    return rates


def clear_hazard_cache() -> None:
    """Drop the cached hazard rates. Call after rebuilding the P1/P2 harvest
    within a live process."""
    _port_class_hazard_rates.cache_clear()


def warm_hazard_cache() -> int:
    """Pay the "order of seconds, not milliseconds" cost from the module
    docstring now, not on whichever request first calls
    ``recommend_repositioning`` or ``opt.backhaul``'s scorer. Returns the
    number of (port, class) hazard rates loaded, purely so a caller can log
    something more informative than "done". Safe to call repeatedly --
    ``_port_class_hazard_rates`` is itself cached, so a second call is free.
    """
    return len(_port_class_hazard_rates())


def cargo_probability_within_window(
    port: PortEnum, vessel_class: VesselClass, window_days: float
) -> tuple[float, bool]:
    """(P(a class-appropriate cargo departs this specific port within
    window_days), is_real_data). The second element is False exactly when the
    port has no real tonnage-field coverage and the neutral default was used --
    always check it before treating the probability as a calibrated estimate.
    """
    tonnage_label = PORT_TO_TONNAGE_LABEL.get(port)
    if tonnage_label is None:
        return _NEUTRAL_PROBABILITY, False
    lam = _port_class_hazard_rates().get((tonnage_label, vessel_class.value))
    if lam is None or lam <= 0:
        return _NEUTRAL_PROBABILITY, False
    return 1.0 - math.exp(-lam * window_days), True


@dataclass
class RepositionOption:
    port: PortEnum
    ballast_distance_nm: float
    ballast_days: float
    wait_days: float
    ballast_cost_usd: float
    wait_cost_usd: float
    expected_tce_usd_per_day: float  # market rate IF cargo is found -- unweighted by probability
    cargo_probability_within_window: float  # P(cargo appears here in the assumed voyage window)
    probability_is_real_data: bool  # False -> neutral 0.5 default, no tonnage-field coverage here
    # P4: the real PortWatch field this probability is ultimately built from
    # (export_dry_bulk, via tonnage.classmix's class-attributed hazard rate)
    # is PortWatch's own model ESTIMATE, not a measured tonnage figure --
    # never presented as a measurement. None exactly when
    # probability_is_real_data is False (the neutral prior touches no real
    # data at all, so there is nothing to attribute provenance to).
    data_provenance: Provenance | None
    score_usd: float  # hazard-weighted net expected profit from moving here
    is_current_location: bool


@dataclass
class RepositionRecommendation:
    vessel_id: str
    current_port: PortEnum
    recommended_port: PortEnum
    options: list[RepositionOption]


def _get_distance_nm(port_a: PortEnum, port_b: PortEnum) -> float:
    """Sea distance between two ports, from the precomputed matrix. Shares one
    source of truth with opt.voyage."""
    return geo_distance_nm(port_a.value.id, port_b.value.id)


def recommend_repositioning(
    vessel: Vessel,
    candidate_ports: list[PortEnum],
    inputs: OptimizerInputs,
    assumed_voyage_days: int = 30,
) -> RepositionRecommendation:
    """Rank candidate ports for repositioning a vessel.

    Score = P(cargo within assumed_voyage_days at this port's basin, for this
    vessel's class) * (market TCE * assumed_voyage_days) - BallastCost -
    WaitCost. The probability is a real, class- and basin-specific hazard rate
    from the P2 tonnage field where coverage exists; where it doesn't, a
    neutral 0.5 prior is used and flagged (``probability_is_real_data=False``)
    rather than silently treated as calibrated.
    """
    try:
        baseline_res = compute_ceiling(
            inputs.forecasts, vessel.vessel_class,
            contract_term_days=assumed_voyage_days,
            risk_tolerance=inputs.risk_tolerance,
        )
        base_tce = baseline_res["expected_spot_p50"]
    except ValueError:
        base_tce = inputs.tc_quotes.get(vessel.vessel_class, 10_000.0)

    options: list[RepositionOption] = []

    for port in candidate_ports:
        dist_nm = _get_distance_nm(vessel.current_port, port)
        speed = vessel.speed_kn if vessel.speed_kn > 0 else 12.0
        ballast_days = dist_nm / (speed * 24.0)

        # Real, current congestion where the data supports it, falling back
        # to the port's static baseline otherwise -- see opt.congestion.
        wait_days, _ = dynamic_wait_days(port)

        fuel_cost = ballast_days * vessel.ballast_fuel_consumption_tpd * BLENDED_BUNKER_USD_PER_TONNE
        ballast_cost_usd = fuel_cost
        wait_cost_usd = wait_days * inputs.opex_usd_per_day

        probability, is_real = cargo_probability_within_window(port, vessel.vessel_class, assumed_voyage_days)

        expected_voyage_value = probability * base_tce * assumed_voyage_days
        score = expected_voyage_value - ballast_cost_usd - wait_cost_usd

        options.append(RepositionOption(
            port=port,
            ballast_distance_nm=dist_nm,
            ballast_days=ballast_days,
            wait_days=wait_days,
            ballast_cost_usd=ballast_cost_usd,
            wait_cost_usd=wait_cost_usd,
            expected_tce_usd_per_day=base_tce,
            cargo_probability_within_window=probability,
            probability_is_real_data=is_real,
            data_provenance=Provenance.ESTIMATED if is_real else None,
            score_usd=score,
            is_current_location=(port == vessel.current_port),
        ))

    options.sort(key=lambda o: o.score_usd, reverse=True)
    best_port = options[0].port if options else vessel.current_port

    return RepositionRecommendation(
        vessel_id=vessel.vessel_id,
        current_port=vessel.current_port,
        recommended_port=best_port,
        options=options,
    )
