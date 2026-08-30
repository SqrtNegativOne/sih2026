"""Explainability layer: "why was this shown," for every ML/decision output
in a solve, not a black box.

Deliberately deterministic and template-based, not a separately-generated
(e.g. LLM) narrative: every sentence here is built directly from real numbers
already computed elsewhere in the same solve (the forecast fan, the LSMC
exercise boundary, the CP-SAT assignment's own cost breakdown, the real
per-port hazard rate behind a repositioning call, the Monte Carlo savings
distribution, the fleet-mix cost/reliability frontier). Nothing here can say
something the numbers don't support, because nothing here computes a new
number of its own -- it only narrates values the rest of ``opt`` already
produced and, in several cases (repositioning, savings), had already computed
but were being discarded before reaching the caller (see the real fields
added to ``RepositioningAction`` and ``OptimizerRecommendation`` alongside
this module).

An extension of the pattern ``opt.risk`` already established for its alerts
(a real message plus the metric/threshold behind it) -- generalised here to
every other output. ``opt.risk``'s own alerts and ``FleetConfiguration``'s
own cost/reliability/infeasible_reason fields are intentionally NOT re-wrapped
here for RiskAlert (already a full, real per-alert explanation on its own);
fleet-mix IS wrapped, since its raw numbers have no plain-language synthesis
of their own yet.
"""
from __future__ import annotations

from opt.monte_carlo import SavingsDistribution
from opt.network import route_family_for_origin
from opt.types import (
    Explanation,
    FleetMixFrontier,
    LockWaitResult,
    OptimizerInputs,
    RecommendationExplanations,
    RepositioningAction,
    StoppingResult,
    VoyageAssignment,
)
from opt.weather_window import TransitBuffer

__all__ = ["build_explanations"]


def _explain_lock_wait(
    lw_result: LockWaitResult,
    stopping_result: StoppingResult | None,
    inputs: OptimizerInputs,
    transit_buffer: TransitBuffer | None = None,
) -> Explanation:
    route = f"{lw_result.origin_port.value.id} -> {lw_result.dest_port.value.id}"
    factors = [
        (
            f"{lw_result.cargo_volume_dwt:,.0f} dwt derives a {lw_result.vessel_class.value} for pricing; "
            f"today's ${lw_result.today_quote_usd_per_day:,.0f}/day compared against a "
            f"{lw_result.contract_term_days}-day threshold of ${lw_result.ceiling_usd_per_day:,.0f}/day."
        )
    ]
    if lw_result.route_adjusted:
        route_family = route_family_for_origin(lw_result.origin_port)
        basis = inputs.basis.get(route_family)
        if basis is not None:
            factors.append(
                f"Route basis for {route_family.value} applied: {basis.basis_mean:+.1%} vs the class-wide "
                f"forecast, +/-{basis.basis_std:.0%} extra spread."
            )
    else:
        factors.append("No route-specific basis calibration exists for this origin -- priced at the class-wide rate.")

    if stopping_result is not None:
        factors.append(
            f"Option value of waiting: ${stopping_result.option_value_usd_per_day:,.0f}/day (the expected "
            f"benefit of being able to keep watching and lock later instead of deciding only today), from "
            f"{stopping_result.n_paths:,} simulated market paths."
        )
        boundary_today = stopping_result.exercise_boundary_usd_per_day[0]
        if boundary_today < stopping_result.strike_usd_per_day:
            factors.append(
                f"That option value pulls today's threshold down to ${boundary_today:,.0f}/day, below the "
                f"${stopping_result.strike_usd_per_day:,.0f}/day it would be without accounting for waiting."
            )
        # weather_delay (2.4): documented reason string for the one-directional
        # tax opt.stopping.solve_lock_or_wait applies to the WAIT branch when a
        # real opt.weather_window.TransitBuffer is in play -- see that
        # function's own arithmetic comment for exactly how ceiling_usd_per_day
        # here relates to boundary_today above (which is never itself taxed).
        if transit_buffer is not None and transit_buffer.expected_delay_days > 0:
            # Mirrors opt.stopping.solve_lock_or_wait's own arithmetic exactly,
            # INCLUDING the amortisation over the contract term -- the one-off
            # delay cost spread across the contract it is priced against, so
            # this reads in USD/day like the threshold it is added to. (This
            # string previously reported the un-amortised total, which both
            # overstated the effect and failed to add up to the ceiling shown
            # beside it; fixed alongside the same real units bug in stopping.)
            weather_cost_usd = transit_buffer.expected_delay_days * lw_result.today_quote_usd_per_day
            weather_cost_usd_per_day = weather_cost_usd / lw_result.contract_term_days
            factors.append(
                f"weather_delay: {transit_buffer.explanation} Spread over the {lw_result.contract_term_days}-day "
                f"contract that is ${weather_cost_usd_per_day:,.0f}/day, raising today's "
                f"${boundary_today:,.0f}/day threshold to ${lw_result.ceiling_usd_per_day:,.0f}/day -- a "
                f"one-directional tax on waiting that can only favor LOCK, never push it back to WAIT."
            )
        method = "LSMC optimal-stopping simulation (Longstaff-Schwartz), fused with the horizon-blended forecast."
    else:
        method = (
            "Horizon-blended P10/P50/P90 quantile forecast only (no option-value refinement: insufficient "
            "forecast horizons to simulate a price path for this planning window)."
        )

    comparison = "at or below" if lw_result.action == "LOCK" else "above"
    summary = (
        f"{lw_result.action}: {route} ({lw_result.cargo_volume_dwt:,.0f} dwt {lw_result.vessel_class.value}) -- "
        f"today's ${lw_result.today_quote_usd_per_day:,.0f}/day is {comparison} the "
        f"${lw_result.ceiling_usd_per_day:,.0f}/day threshold."
    )
    return Explanation(summary=summary, factors=tuple(factors), method=method)


def _explain_voyage_assignment(a: VoyageAssignment) -> Explanation:
    factors = [
        f"Ballast (empty transit to load): {a.ballast_hours}h.",
        f"Wait at port before operations could start: {a.wait_hours}h.",
        f"Gap held before this vessel's next fixture: {a.inter_cargo_gap_hours}h.",
        f"Net profit after fuel, port, and demurrage costs: ${a.profit_usd:,.0f}.",
    ]
    summary = (
        f"Vessel {a.vessel_id} assigned to cargo {a.parcel_id} (-> {a.dest_port.value.id}): "
        f"${a.profit_usd:,.0f} net profit."
    )
    method = (
        "CP-SAT constraint solver (OR-Tools): profit-maximizing assignment across every feasible vessel-cargo "
        "pairing, subject to port draft/beam/LOA/DWT and laycan-window constraints."
    )
    return Explanation(summary=summary, factors=tuple(factors), method=method)


def _explain_repositioning(a: RepositioningAction) -> Explanation:
    data_note = (
        "real IMF PortWatch export-tonnage data"
        if a.probability_is_real_data
        else "no PortWatch coverage on record for this port (neutral 50% default used)"
    )
    if a.is_staying:
        summary = (
            f"Vessel {a.vessel_id}: stay at {a.current_port.value.id} -- the best expected outcome among the "
            f"ports checked."
        )
    else:
        improvement = a.recommended_score_usd - a.current_port_score_usd
        summary = (
            f"Vessel {a.vessel_id}: reposition from {a.current_port.value.id} to "
            f"{a.recommended_port.value.id} -- an estimated ${improvement:,.0f} better expected outcome."
        )
    factors = [
        (
            f"P(class-appropriate cargo appears at {a.recommended_port.value.id} within the assumed window) = "
            f"{a.cargo_probability_within_window:.0%}, from {data_note}."
        ),
        f"Expected net score staying at {a.current_port.value.id}: ${a.current_port_score_usd:,.0f}.",
        f"Expected net score at {a.recommended_port.value.id}: ${a.recommended_score_usd:,.0f}.",
    ]
    method = (
        "Poisson hazard-rate model (P(cargo within N days) = 1 - exp(-lambda*N)) over real port export "
        "tonnage, net of ballast fuel and port-wait cost."
    )
    return Explanation(summary=summary, factors=tuple(factors), method=method)


def _explain_savings(mc_dist: SavingsDistribution) -> Explanation:
    summary = (
        f"${mc_dist.expected_p50_savings:,.0f}/day expected savings vs. staying in the spot market "
        f"(P10 worst case ${mc_dist.worst_case_p10_savings:,.0f}/day, "
        f"P90 best case ${mc_dist.best_case_p90_savings:,.0f}/day)."
    )
    factors = (
        (
            f"In {mc_dist.prob_positive_savings:.0%} of the simulated market futures, locking today beat "
            f"staying in spot."
        ),
        f"Compared against a {mc_dist.contract_term_days}-day contract at ${mc_dist.quote_usd_per_day:,.0f}/day.",
    )
    method = "Monte Carlo simulation over the ML forecast's own P10/P50/P90 uncertainty fan."
    return Explanation(summary=summary, factors=factors, method=method)


def _explain_fleet_mix(frontier: FleetMixFrontier) -> Explanation:
    if not frontier.configurations:
        factors = tuple(
            f"{c.vessel_class.value}: {c.infeasible_reason}" for c in frontier.rejected_configurations
        )
        return Explanation(
            summary=(
                f"No vessel class can move {frontier.requirement_dwt:,.0f} dwt from "
                f"{frontier.origin.value.id} to {frontier.dest.value.id}."
            ),
            factors=factors,
            method="Enumerated one configuration per vessel class, checked against real port physical limits.",
        )

    best = frontier.configurations[0]
    factors = [
        f"{best.vessel_class.value}: {best.n_vessels}x{best.dwt_per_vessel:,.0f} dwt, "
        f"${best.cost_p50_usd:,.0f} total cost (P50), reliability {best.reliability_score:.2f}"
        + (f", via transshipment through {best.transshipment_hub.value.id}" if best.transshipment_hub else "")
        + "."
    ]
    for alt in frontier.configurations[1:]:
        delta = alt.cost_p50_usd - best.cost_p50_usd
        factors.append(
            f"{alt.vessel_class.value}: ${alt.cost_p50_usd:,.0f} ({delta:+,.0f} vs the pick), "
            f"reliability {alt.reliability_score:.2f}."
        )
    for rej in frontier.rejected_configurations:
        factors.append(f"{rej.vessel_class.value}: ruled out -- {rej.infeasible_reason}")

    summary = (
        f"{best.vessel_class.value} recommended for {frontier.requirement_dwt:,.0f} dwt "
        f"{frontier.origin.value.id} -> {frontier.dest.value.id}: cheapest feasible option at "
        f"${best.cost_p50_usd:,.0f} (P50)."
    )
    method = "Per-class pricing via the horizon-blended forecast, plus real port LOA/beam/draft/DWT constraints."
    return Explanation(summary=summary, factors=tuple(factors), method=method)


def build_explanations(
    lw_result: LockWaitResult,
    stopping_result: StoppingResult | None,
    voyage_assignments: list[VoyageAssignment],
    repositioning_actions: list[RepositioningAction],
    mc_dist: SavingsDistribution,
    fleet_mix: FleetMixFrontier | None,
    inputs: OptimizerInputs,
    transit_buffer: TransitBuffer | None = None,
) -> RecommendationExplanations:
    """Build a real-numbers-only explanation for every explainable output in
    a solve. Takes the individual pieces already computed by
    ``opt.api.run_optimizer`` rather than the assembled ``OptimizerRecommendation``
    itself, since this result becomes one of that object's own fields.
    """
    return RecommendationExplanations(
        lock_wait=_explain_lock_wait(lw_result, stopping_result, inputs, transit_buffer),
        voyage_assignments=tuple(_explain_voyage_assignment(a) for a in voyage_assignments),
        repositioning=tuple(_explain_repositioning(a) for a in repositioning_actions),
        savings=_explain_savings(mc_dist),
        fleet_mix=_explain_fleet_mix(fleet_mix) if fleet_mix is not None else None,
    )
