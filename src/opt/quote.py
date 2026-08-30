"""The single-cargo "quote" front door -- the final-lap centerpiece.

Every prior entry point (``opt.api.run_optimizer``) takes a fully-assembled
``OptimizerInputs``: real forecast fans, real today's TC quotes per class, a
built ``CargoParcel``, a fleet of ``Vessel`` objects -- assembly a caller has
to do themselves (see ``run_live_scenario.py``'s ~150 lines of scenario
setup). A charterer asking "what should I do about this cargo?" has none of
that; they have a cargo lot, an origin, a destination, a laycan window, and a
contract type. ``quote()`` is the function that takes exactly those real
inputs, loads the real trained-model forecast for "today" itself (via
``ml.live_forecast``, the same logic every demo script already runs on), and
returns one clean, polished result -- no ``OptimizerInputs`` assembly
required of the caller.

Internally this is a thin wrapper, not a new decision engine: it builds the
smallest honest ``OptimizerInputs`` (one parcel, no vessel unless the caller
supplies one -- ``opt.voyage.schedule_voyages`` already degrades gracefully
to "no assignments" on an empty fleet, so the lock/wait + fleet-mix + risk
pipeline runs exactly as it does today), calls the already-tested
``opt.api.run_optimizer`` unchanged, and layers ``opt.present``'s display
polish ($/MT, per-horizon confidence%, LOW/MODERATE/HIGH congestion for BOTH
ends) on top of its real output. Nothing here re-derives a number
``run_optimizer`` already computed correctly.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path
from time import perf_counter as _perf

from berth_truth.empirical import WaitInterval, compute_wait_distribution
from emissions.projection import project_voyage_emissions
from ml.live_forecast import forecast_all_classes, latest_available_date, resolve_as_of
from opt.api import ProgressCallback, run_optimizer
from opt.basis import basis_table_to_entries, build_route_basis_table
from opt.ceiling import route_adjusted_fans, select_vessel_class_for_cargo
from opt.congestion import dynamic_wait_days
from opt.feasibility import blocking_reasons, check_structural
from opt.network import PortEnum, route_family_for_origin
from opt.present import (
    congestion_label,
    estimate_transit_days,
    forecast_confidence,
    usd_per_day_to_usd_per_mt,
)
from opt.route_trace import build_route_exploration
from opt.types import (
    CargoParcel,
    OptimizerInputs,
    PortCheck,
    ProgressStage,
    QuoteEnvelope,
    QuoteResult,
    RateHorizon,
    Relaxation,
    RouteEvidenceLevel,
    StructuralProblem,
    Vessel,
)

# Aliased: the local variable holding the result is also named
# transit_buffer (matching QuoteResult/OptimizerRecommendation's own field
# name) -- importing the function under its own name would shadow it.
from opt.weather_window import transit_buffer as compute_transit_buffer

#: The real route-basis table (opt.basis.build_route_basis_table), built once
#: from master_long.parquet and cached for the process lifetime -- P4
#: requirement 2 ("built once and cached"). Same reasoning as
#: ml.live_forecast's own module-level "today" caching: master_long.parquet
#: does not change within a running process, so there is nothing to gain by
#: re-reading and re-joining it on every quote. Call
#: ``clear_route_basis_cache()`` after rebuilding master_long.parquet within
#: a live process (mirrors tonnage.basins.clear_cache's naming).
LOGGER = logging.getLogger(__name__)

_ROUTE_BASIS_TABLE: dict | None = None


def _cached_route_basis_table() -> dict:
    global _ROUTE_BASIS_TABLE
    if _ROUTE_BASIS_TABLE is None:
        _ROUTE_BASIS_TABLE = build_route_basis_table()
    return _ROUTE_BASIS_TABLE


def clear_route_basis_cache() -> None:
    global _ROUTE_BASIS_TABLE
    _ROUTE_BASIS_TABLE = None

__all__ = ["InsufficientMarketDataError", "quote", "quote_envelope"]


class InsufficientMarketDataError(RuntimeError):
    """No real forecast/TC-quote data exists for the derived vessel class as
    of the requested date -- there is nothing honest to price. Distinct from
    a plain ValueError (a caller mistake, e.g. a negative tonnage) so a
    FastAPI wrapper can tell "your input is invalid" apart from "the real
    data doesn't cover this date/class" and return the right status code for
    each."""


def _port_check(port: PortEnum, as_of: date | None = None) -> PortCheck:
    # F-21 fix: as_of is now threaded through to the congestion estimate
    # (previously always "today" regardless of what date the quote itself
    # claimed to price -- a real gap for a reproducible historical quote).
    label, is_real = congestion_label(port, as_of)
    wait_days, _ = dynamic_wait_days(port, as_of)
    p = port.value

    # P2: real arrival-to-berth percentiles where berth_truth.fact_port_call
    # has a sufficient sample -- additive, alongside (never replacing)
    # expected_wait_days above, which CP-SAT's own objective still consumes
    # unchanged. See PortCheck's own docstring for why this stays separate.
    empirical = compute_wait_distribution(port, WaitInterval.ARRIVAL_TO_BERTH)

    return PortCheck(
        port=port,
        max_dwt=p.max_dwt,
        max_draft_m=p.max_draft_m,
        max_loa_m=p.max_loa_m,
        max_beam_m=p.max_beam_m,
        expected_wait_days=wait_days,
        wait_days_is_real_data=is_real,
        congestion_label=label,
        empirical_wait_p50_hours=empirical.p50_hours,
        empirical_wait_p90_hours=empirical.p90_hours,
        empirical_wait_sample_n=empirical.n,
    )


def quote(
    cargo_volume_dwt: float,
    origin_port: PortEnum,
    dest_port: PortEnum,
    laycan_start: date,
    laycan_end: date,
    contract_term_days: int = 30,
    commodity: str = "Dry Bulk",
    as_of: date | None = None,
    risk_tolerance: float = 0.0,
    vessels: list[Vessel] | None = None,
    revenue_usd: float | None = None,
    master_path: Path | None = None,
    on_progress: ProgressCallback | None = None,
    relaxed_fleet_mix: bool = False,
) -> QuoteResult:
    """Price one real cargo lot end to end: rate forecast, LOCK/WAIT (option-
    value-aware), vessel-type recommendation, port constraints at both ends,
    risk flags, all with a real "why" -- one call, one clean result.

    Parameters
    ----------
    cargo_volume_dwt, origin_port, dest_port, laycan_start, laycan_end:
        The cargo lot itself -- exactly what a charterer actually has.
    contract_term_days:
        Length of the TC contract being considered (default 30).
    commodity:
        Free-text label, carried through to the result; does not affect
        pricing (this system prices by class/route/tonnage, not commodity --
        same as every other entry point).
    as_of:
        Treat this date as "today." Defaults to the most recent date the
        real Baltic index history on disk actually has -- the same "today"
        every demo script already uses. Pass an explicit date for a
        reproducible historical quote.
    risk_tolerance:
        0=risk-neutral (P50), 1=fully risk-averse (P90) -- same meaning as
        everywhere else in ``opt``.
    vessels:
        Optional real vessels already in hand. When omitted, the quote is
        priced with no specific vessel assigned (the common case: "what
        should I do about this cargo," not "here's my fleet, schedule it") --
        voyage scheduling has nothing to assign, while lock/wait, vessel-type
        recommendation, port checks, and risk flags are unaffected. A
        supplied idle vessel still gets a real repositioning recommendation
        even with no assignment (see ``revenue_usd`` below), since that only
        compares candidate ports' own hazard-weighted value, not this
        parcel's.
    revenue_usd:
        What this specific cargo is worth to the caller, for
        ``opt.voyage``'s profit-maximizing CP-SAT assignment (revenue is a
        business fact -- what SAIL actually captures from moving this lot --
        that no market forecast can supply, same reasoning as
        ``opt.portfolio``'s ``stockout_cost_usd``). Defaults to 0.0, which is
        an honest, safe default (never fabricates a profit that would force
        an assignment), not a real assumption -- pass a real figure to get a
        real "will a supplied vessel actually get assigned to this cargo"
        answer; without it, ``full_recommendation.voyage_assignments`` will
        correctly stay empty even for an otherwise-feasible vessel.
    master_path:
        Override for ``src/data/master_long.parquet`` (tests only).

    Raises
    ------
    ValueError
        Invalid input (non-positive tonnage/contract term, laycan_end before
        laycan_start).
    InsufficientMarketDataError
        No real forecast/quote data exists for the derived vessel class as
        of the resolved date.
    """
    if cargo_volume_dwt <= 0:
        raise ValueError(f"cargo_volume_dwt must be positive, got {cargo_volume_dwt}.")
    if contract_term_days <= 0:
        raise ValueError(f"contract_term_days must be positive, got {contract_term_days}.")
    if laycan_end < laycan_start:
        raise ValueError(f"laycan_end ({laycan_end}) is before laycan_start ({laycan_start}).")

    # F-15 fix: an explicitly-supplied as_of used to need to be an EXACT
    # match to a real trading day or every class silently had no data --
    # resolve_as_of finds the latest real trading day on or before it
    # instead, the same backward as-of convention every other date lookup
    # in this codebase already uses. A caller who omits as_of entirely
    # still gets exactly latest_available_date(), unchanged.
    resolved_as_of = (
        resolve_as_of(as_of, master_path) if as_of is not None else latest_available_date(master_path)
    )

    target_class = select_vessel_class_for_cargo(cargo_volume_dwt)
    if on_progress is not None:
        on_progress(ProgressStage(key="forecast_load", label="Loading real forecast", status="start", elapsed_ms=0.0))
    _t0 = _perf()
    fans_by_class, quotes = forecast_all_classes(resolved_as_of, master_path)
    if on_progress is not None:
        on_progress(ProgressStage(key="forecast_load", label="Loading real forecast", status="done", elapsed_ms=(_perf() - _t0) * 1000.0))
    if target_class not in quotes:
        raise InsufficientMarketDataError(
            f"No real TC quote/forecast for {target_class.value} as of {resolved_as_of} -- "
            f"cannot price a {cargo_volume_dwt:,.0f} dwt cargo lot. Try a different as_of date."
        )
    all_fans = [f for class_fans in fans_by_class.values() for f in class_fans]

    route_family = route_family_for_origin(origin_port)
    route_basis_table = _cached_route_basis_table()
    route_basis_result = route_basis_table.get(route_family)

    parcel = CargoParcel(
        parcel_id="quote_parcel",
        origin_port=origin_port,
        dest_port=dest_port,
        commodity=commodity,
        volume_dwt=cargo_volume_dwt,
        laycan_start=laycan_start,
        laycan_end=laycan_end,
        route_family=route_family,
        revenue_usd=revenue_usd if revenue_usd is not None else 0.0,
    )
    inputs = OptimizerInputs(
        parcels=[parcel],
        vessels=list(vessels) if vessels else [],
        tc_quotes=quotes,
        planning_horizon_days=max(90, contract_term_days),
        contract_term_days=contract_term_days,
        forecasts=all_fans,
        # P4: the real, evidence-gated route-basis table (opt.basis) --
        # entries are present only where real route-level rate evidence
        # cleared VALIDATED or MODELLED (opt.basis.RouteEvidence); every
        # other route family is correctly absent, which
        # opt.ceiling._apply_basis already treats as "use the class
        # benchmark unadjusted" (LockWaitResult.route_adjusted reports
        # False for those, honestly).
        basis=basis_table_to_entries(route_basis_table),
        risk_tolerance=risk_tolerance,
    )

    # 2.4: weather/cyclone transit buffer, computed BEFORE the lock/wait
    # solve so its expected_delay_days can tax the WAIT branch (see
    # opt.stopping.solve_lock_or_wait's weather_delay_days parameter) --
    # never allowed to fail a quote (opt.weather_window itself already
    # degrades to None on any network/cache failure; this catches anything
    # else -- a malformed climatology file, an unexpected error -- the same
    # defensive pattern as the CII emissions projection just below).
    transit_buffer = None
    try:
        transit_buffer = compute_transit_buffer(
            origin_port, dest_port, laycan_start, laycan_end, as_of=resolved_as_of
        )
    except Exception:
        LOGGER.warning("Weather/cyclone transit buffer unavailable; pricing without a weather tax.", exc_info=True)

    rec = run_optimizer(
        inputs,
        as_of=resolved_as_of,
        on_progress=on_progress,
        relaxed_fleet_mix=relaxed_fleet_mix,
        transit_buffer=transit_buffer,
    )

    # IMO CII projection for every real vessel supplied -- additive, never
    # allowed to fail a quote (same defensive pattern opt.api uses around
    # assess_risk): None when there's no vessel to rate, the rating year
    # isn't published, or the projection itself raises for any other reason.
    emissions = None
    try:
        emissions = project_voyage_emissions(vessels, origin_port, dest_port, as_of=resolved_as_of)
    except Exception:
        LOGGER.warning("CII emissions projection unavailable; leaving quote.emissions unset.", exc_info=True)

    base_class_fans = sorted(
        (f for f in fans_by_class.get(rec.target_vessel_class, [])), key=lambda f: f.horizon_days
    )
    transit_days = estimate_transit_days(origin_port, dest_port)

    route_evidence_level = (
        RouteEvidenceLevel(route_basis_result.evidence.value)
        if route_basis_result is not None
        else RouteEvidenceLevel.ROUTE_RATE_BASIS_UNAVAILABLE
    )
    route_basis_entry = route_basis_result.basis_entry if route_basis_result is not None else None
    route_adjustment_value = route_basis_entry.basis_mean if route_basis_entry is not None else None

    # The SAME route adjustment opt.ceiling applies internally when pricing
    # the LOCK/WAIT ceiling (opt.api.run_optimizer -> opt.ceiling.lock_or_wait)
    # -- applied here too so the displayed $/day fan matches what the
    # ceiling decision was actually priced against, not the unadjusted class
    # fan. A no-op (returns the input unchanged) when route_basis_entry is
    # None, i.e. every route today (ROUTE_RATE_BASIS_UNAVAILABLE).
    target_class_fans = route_adjusted_fans(base_class_fans, rec.target_vessel_class, route_basis_entry)

    rate_forecast = tuple(
        RateHorizon(
            horizon_days=fan.horizon_days,
            p10_usd_per_day=fan.p10,
            p50_usd_per_day=fan.p50,
            p90_usd_per_day=fan.p90,
            p50_usd_per_mt=usd_per_day_to_usd_per_mt(fan.p50, transit_days, cargo_volume_dwt),
            direction=(dc := forecast_confidence(rec.tc_quote_usd_per_day, fan))[0],
            confidence_pct=dc[1],
            route_evidence=route_evidence_level,
            route_adjustment=route_adjustment_value,
        )
        for fan in target_class_fans
    )

    return QuoteResult(
        cargo_volume_dwt=cargo_volume_dwt,
        commodity=commodity,
        origin_port=origin_port,
        dest_port=dest_port,
        laycan_start=laycan_start,
        laycan_end=laycan_end,
        contract_term_days=contract_term_days,
        as_of=resolved_as_of,
        today_quote_usd_per_day=rec.tc_quote_usd_per_day,
        today_quote_usd_per_mt=usd_per_day_to_usd_per_mt(rec.tc_quote_usd_per_day, transit_days, cargo_volume_dwt),
        assumed_transit_days=transit_days,
        rate_forecast=rate_forecast,
        route_evidence=route_evidence_level,
        route_adjustment=route_adjustment_value,
        target_vessel_class=rec.target_vessel_class,
        lock_action=rec.lock_action,
        ceiling_usd_per_day=rec.ceiling_usd_per_day,
        ceiling_usd_per_mt=usd_per_day_to_usd_per_mt(rec.ceiling_usd_per_day, transit_days, cargo_volume_dwt),
        optimal_entry_window_start_day=rec.optimal_entry_window_start_day,
        optimal_entry_window_end_day=rec.optimal_entry_window_end_day,
        expected_savings_usd_per_day=rec.expected_savings_usd_per_day,
        expected_savings_usd_total=rec.expected_savings_usd_per_day * contract_term_days,
        prob_savings_positive=rec.prob_savings_positive,
        fleet_mix=rec.fleet_mix,
        origin_port_check=_port_check(origin_port, resolved_as_of),
        dest_port_check=_port_check(dest_port, resolved_as_of),
        risk_assessment=rec.risk_assessment,
        explanations=rec.explanations,
        full_recommendation=rec,
        route_exploration=build_route_exploration(rec),
        emissions=emissions,
        transit_buffer=transit_buffer,
    )


def quote_envelope(
    cargo_volume_dwt: float,
    origin_port: PortEnum,
    dest_port: PortEnum,
    laycan_start: date,
    laycan_end: date,
    contract_term_days: int = 30,
    commodity: str = "Dry Bulk",
    as_of: date | None = None,
    risk_tolerance: float = 0.0,
    vessels: list[Vessel] | None = None,
    revenue_usd: float | None = None,
    master_path: Path | None = None,
    on_progress: ProgressCallback | None = None,
) -> QuoteEnvelope:
    """``quote()`` wrapped in the feasibility taxonomy.

    - Runs the structural checks first (impossible input -> ``structural_infeasible``,
      no solve attempted).
    - Otherwise solves. If the solve is numerically complete and actionable ->
      ``feasible``.
    - If the solve completed but no vessel configuration is feasible, loosens the
      binding constraints (wider laycan, then a bounded part-load tolerance with
      nearest-hub transshipment) and re-solves. A loosened solve that works ->
      ``contingent_infeasible`` with the relaxations listed. Nothing rescues it ->
      ``structural_infeasible`` naming the route.

    Malformed requests (non-positive tonnage/term, inverted laycan) still raise
    ``ValueError``; missing real data still raises ``InsufficientMarketDataError``.
    The HTTP layer maps those to 4xx/503 as before; this envelope is only about
    "the request is well-formed, but is it solvable."
    """
    # F-15 fix: an explicitly-supplied as_of used to need to be an EXACT
    # match to a real trading day or every class silently had no data --
    # resolve_as_of finds the latest real trading day on or before it
    # instead, the same backward as-of convention every other date lookup
    # in this codebase already uses. A caller who omits as_of entirely
    # still gets exactly latest_available_date(), unchanged.
    resolved_as_of = (
        resolve_as_of(as_of, master_path) if as_of is not None else latest_available_date(master_path)
    )

    if cargo_volume_dwt <= 0:
        raise ValueError(f"cargo_volume_dwt must be positive, got {cargo_volume_dwt}.")
    if contract_term_days <= 0:
        raise ValueError(f"contract_term_days must be positive, got {contract_term_days}.")
    if laycan_end < laycan_start:
        raise ValueError(f"laycan_end ({laycan_end}) is before laycan_start ({laycan_start}).")

    problems = check_structural(
        cargo_volume_dwt=cargo_volume_dwt,
        origin_port=origin_port,
        dest_port=dest_port,
        laycan_end=laycan_end,
        as_of=resolved_as_of,
        contract_term_days=contract_term_days,
    )
    if problems:
        return QuoteEnvelope(status="structural_infeasible", structural_problems=problems)

    base_kwargs = {
        "cargo_volume_dwt": cargo_volume_dwt,
        "origin_port": origin_port,
        "dest_port": dest_port,
        "laycan_start": laycan_start,
        "contract_term_days": contract_term_days,
        "commodity": commodity,
        "as_of": resolved_as_of,
        "risk_tolerance": risk_tolerance,
        "vessels": vessels,
        "revenue_usd": revenue_usd,
        "master_path": master_path,
    }

    base = quote(laycan_end=laycan_end, on_progress=on_progress, **base_kwargs)
    reasons = blocking_reasons(base)
    if not reasons:
        return QuoteEnvelope(status="feasible", quote=base)

    widened_end = laycan_end + timedelta(days=21)
    attempts: list[tuple[tuple[Relaxation, ...], dict]] = [
        (
            (
                Relaxation(
                    kind="laycan_widened",
                    message="Widened the laycan window so a vessel has time to position and load.",
                    before=f"{laycan_start.isoformat()} to {laycan_end.isoformat()}",
                    after=f"{laycan_start.isoformat()} to {widened_end.isoformat()}",
                ),
            ),
            {"laycan_end": widened_end},
        ),
        (
            (
                Relaxation(
                    kind="laycan_widened",
                    message="Widened the laycan window so a vessel has time to position and load.",
                    before=f"{laycan_start.isoformat()} to {laycan_end.isoformat()}",
                    after=f"{laycan_start.isoformat()} to {widened_end.isoformat()}",
                ),
                Relaxation(
                    kind="transshipment_allowed",
                    message=(
                        "Allowed a part cargo and transshipment through the nearest deep-water "
                        "port, since no class could enter the ports as given."
                    ),
                    before="direct call, full berth limits",
                    after="part-load tolerance plus transshipment at any deep-water hub",
                ),
            ),
            {"laycan_end": widened_end, "relaxed_fleet_mix": True},
        ),
    ]

    for relaxations, overrides in attempts:
        try:
            relaxed = quote(**{**base_kwargs, "laycan_end": laycan_end, **overrides})
        except (ValueError, InsufficientMarketDataError):
            continue
        if not blocking_reasons(relaxed):
            return QuoteEnvelope(
                status="contingent_infeasible",
                quote=relaxed,
                original_blockers=reasons,
                relaxations_applied=relaxations,
            )

    return QuoteEnvelope(
        status="structural_infeasible",
        structural_problems=(
            StructuralProblem(
                field="route",
                message=(
                    "No vessel configuration can move this cargo on this route, even after "
                    "widening the laycan and allowing transshipment. Change the ports or the tonnage."
                ),
                observed="; ".join(reasons)[:400],
                limit="a class and route the network can actually serve",
            ),
        ),
        original_blockers=reasons,
    )
