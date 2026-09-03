"""Unified Black-Box API for the Optimizer.

Orchestrates the sub-engines into a single function call that returns the
outputs defined in the product specification, plus the P3 additions (fleet-mix,
real risk alerts) layered in additively -- see run_optimizer's docstring.
"""
from __future__ import annotations

import contextlib
import logging
import time
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime

from opt.ceiling import select_vessel_class_for_cargo
from opt.chokepoints import chokepoints_for_route
from opt.explain import build_explanations
from opt.fleetmix import enumerate_fleet_mix
from opt.monte_carlo import estimate_savings_distribution
from opt.network import PORT_TO_TONNAGE_LABEL, PortEnum, route_family_for_origin
from opt.portfolio import optimize_portfolio_mix
from opt.repositioning import recommend_repositioning
from opt.risk import assess_risk
from opt.stopping import solve_lock_or_wait
from opt.types import (
    NoFeasibleConfigurationError,
    OptimizerInputs,
    OptimizerRecommendation,
    PortfolioMix,
    ProgressStage,
    RejectedOption,
    RepositioningAction,
    RepositioningOption,
    ReviewTrigger,
    VesselClass,
)
from opt.voyage import schedule_voyages
from opt.weather_window import TransitBuffer

LOGGER = logging.getLogger(__name__)

ProgressCallback = Callable[[ProgressStage], None]

#: How many repositioning alternates (beyond the recommended port) to carry on
#: the recommendation for the route-exploration trace.
_REPOSITION_OPTIONS_KEPT = 7


@contextlib.contextmanager
def _stage(on_progress: ProgressCallback | None, key: str, label: str) -> Iterator[None]:
    """Emit a ``start`` ProgressStage, run the block, emit a ``done`` with the
    wall time. A no-op when ``on_progress`` is None."""
    if on_progress is None:
        yield
        return
    on_progress(ProgressStage(key=key, label=label, status="start", elapsed_ms=0.0))
    t0 = time.perf_counter()
    try:
        yield
    finally:
        on_progress(
            ProgressStage(
                key=key,
                label=label,
                status="done",
                elapsed_ms=(time.perf_counter() - t0) * 1000.0,
            )
        )

#: 2.5: no longer the chokepoints checked on every solve -- opt.chokepoints
#: .chokepoints_for_route now determines that per-route (see its own module
#: docstring for the bug a fixed set caused: a Newcastle -> Paradip voyage
#: flagged for a Suez Canal risk it would never transit). Kept as the
#: fallback for the case where the route can't be resolved at all -- the
#: plan's own named flagship examples (Suez, Bab el-Mandeb, Malacca, Cape of
#: Good Hope, Hormuz), covering SAIL's real Australia/Africa/Indonesia ->
#: India lanes -- a reasonable "check something real" default, not a made-up
#: one, for that fallback path only.
_DEFAULT_CHOKEPOINTS: tuple[str, ...] = ("chokepoint1", "chokepoint4", "chokepoint5", "chokepoint6", "chokepoint7")


def run_optimizer(
    inputs: OptimizerInputs,
    as_of: date | None = None,
    on_progress: ProgressCallback | None = None,
    relaxed_fleet_mix: bool = False,
    *,
    transit_buffer: TransitBuffer | None = None,
) -> OptimizerRecommendation:
    """The unified Black-Box optimizer function.

    ``as_of`` controls the real-data risk assessment's reference date (defaults
    to today for live use; pass an explicit date for a reproducible historical
    run or a test). Fleet-mix and risk assessment both read real data files
    (the P1/P2 harvest, ``src/data/master_long.parquet``) directly from disk;
    if that data isn't present in a given environment, both degrade to ``None``
    / the pre-P3 default review trigger rather than raising -- the core lock/
    wait + voyage-scheduling recommendation must keep working either way.

    There is no ``target_class`` parameter: the vessel class evaluated
    throughout (lock/wait, its savings/risk/stopping extensions) is derived
    from the primary parcel's cargo tonnage, not supplied by the caller --
    see ``opt.ceiling.lock_or_wait_for_cargo``.

    The lock/wait decision itself is option-value-aware: it fuses the LSMC
    exercise boundary into the horizon-blended ceiling rule rather than
    using either alone, falling back to the plain rule when there isn't
    enough forecast data to calibrate a price path -- see
    ``opt.stopping.solve_lock_or_wait``.

    ``transit_buffer`` (2.4, optional, default ``None`` -- byte-identical
    output to before this parameter existed) is a real
    ``opt.weather_window.TransitBuffer`` the caller already computed (this
    function makes no network call of its own and has no opinion on how one
    was obtained -- see ``opt.quote.quote()``, the one real caller). When
    present, its ``expected_delay_days`` taxes the WAIT branch of the fused
    lock/wait decision (see ``opt.stopping.solve_lock_or_wait``'s own
    ``weather_delay_days`` parameter) and the buffer itself is carried
    through on the returned recommendation for display.
    """
    if not inputs.parcels:
        raise ValueError(
            "run_optimizer requires at least one cargo parcel -- the lock/wait "
            "decision is priced from the parcel's tonnage and origin/destination, "
            "not a directly-specified vessel class."
        )

    primary = inputs.parcels[0]
    route_basis = inputs.basis.get(route_family_for_origin(primary.origin_port))
    resolved_as_of = as_of if as_of is not None else datetime.now(UTC).date()

    # --- Fleet-mix recommendation (PS deliverable b), computed FIRST ---
    # F-03 fix: this used to run last, by which point `target_class` had
    # already been picked by tonnage size alone and used for everything
    # upstream of it -- the ceiling, the lock/wait call, the Monte Carlo
    # savings, the risk-assessment class -- so the headline verdict could,
    # and verifiably did, recommend a class this very frontier had already
    # ruled out as physically unable to enter one of the two ports (a real
    # case found live: 75,000t Newcastle->Paradip priced its verdict on
    # Panamax while this frontier rejected Panamax for exceeding Paradip's
    # DWT limit and recommended Supramax x2 instead -- both shown on the
    # same screen at once). Fleet-mix pricing doesn't depend on
    # target_class or tc_quote at all -- it prices every class
    # independently via its own compute_ceiling call -- so it can safely
    # move first; target_class below now reads its answer instead of
    # guessing blind.
    fleet_mix = None
    with _stage(on_progress, "fleet_mix", "Fleet-mix frontier"):
        try:
            fleet_mix = enumerate_fleet_mix(
                requirement_dwt=primary.volume_dwt,
                origin=primary.origin_port,
                dest=primary.dest_port,
                forecasts=inputs.forecasts,
                basis=route_basis,
                contract_term_days=inputs.contract_term_days,
                relaxed=relaxed_fleet_mix,
            )
        except (ValueError, NoFeasibleConfigurationError):
            LOGGER.info("Fleet-mix frontier unavailable for this parcel (no feasible/priceable configuration).")

    # --- 1. Lock/Wait & Ceiling (fused with the LSMC exercise boundary --
    # PS deliverables a and (implicitly) the timing half of the core ask) ---
    # Vessel class: the cheapest class the fleet-mix frontier above just
    # found physically and commercially feasible for this cargo/route, when
    # one exists. Falls back to the pure tonnage-size lookup only when the
    # frontier found nothing feasible at all -- there is nothing better to
    # price against in that case, and quote_envelope's own relaxation
    # ladder (wider laycan, then transshipment) is what actually handles
    # that situation, not this function.
    if fleet_mix is not None and fleet_mix.configurations:
        target_class = fleet_mix.configurations[0].vessel_class
    else:
        target_class = select_vessel_class_for_cargo(primary.volume_dwt)
    tc_quote = inputs.tc_quotes.get(target_class, 0.0)

    weather_delay_days = transit_buffer.expected_delay_days if transit_buffer is not None else 0.0
    with _stage(on_progress, "lock_wait", "Lock / wait decision"):
        lw_result, stopping_result = solve_lock_or_wait(
            forecasts=inputs.forecasts,
            cargo_volume_dwt=primary.volume_dwt,
            origin_port=primary.origin_port,
            dest_port=primary.dest_port,
            contract_term_days=inputs.contract_term_days,
            planning_horizon_days=inputs.planning_horizon_days,
            today_quote_usd_per_day=tc_quote,
            basis_table=inputs.basis,
            risk_tolerance=inputs.risk_tolerance,
            weather_delay_days=weather_delay_days,
            # Price the decision as the class we are actually recommending.
            # Without this the ceiling was built from the forecast fans of a
            # class derived from cargo tonnage alone, while `tc_quote` above
            # is the fleet-mix class's rate -- two different ships in one
            # decision. It also produced a flat contradiction in the output:
            # `target_vessel_class` said Supramax while the explanation text
            # said Panamax for the same quote.
            vessel_class_override=target_class,
        )

    start_day = None
    end_day = None
    min_p50 = None
    # F-07 fix: the trough search used to scan the whole planning horizon
    # with no regard for the laycan at all -- verified live, it recommended
    # a "wait for trough" window of Sep 16-22 for a cargo whose laycan
    # closed Sep 19, i.e. advice to fix a TC contract after the vessel was
    # already supposed to have loaded. A TC needs to be signed with enough
    # lead time to position a vessel before the cargo's own laycan opens,
    # so the useful search window ends at laycan_start, not at the full
    # planning horizon. When laycan_start has already passed (or is today),
    # there is no useful window left to search -- reported honestly as "no
    # clear trough" (start_day/end_day stay None) rather than an
    # out-of-range date.
    search_horizon_days = min(
        inputs.planning_horizon_days,
        (primary.laycan_start - resolved_as_of).days,
    )
    if lw_result.action == "WAIT" and search_horizon_days >= 1:
        from opt.monte_carlo import precompute_daily_fan
        class_fans = [f for f in inputs.forecasts if f.vessel_class == target_class]

        daily_fan = precompute_daily_fan(class_fans, search_horizon_days)
        best_day = 1
        min_p50 = float('inf')
        for d, (_, p50, _) in enumerate(daily_fan, start=1):
            if p50 < min_p50:
                min_p50 = p50
                best_day = d

        start_day = max(1, best_day - 3)
        end_day = min(search_horizon_days, best_day + 3)

    # --- 2. Voyage Scheduling ---
    with _stage(on_progress, "voyage_schedule", "Voyage scheduling"):
        vs_result = schedule_voyages(inputs, max_solve_seconds=5.0)

    rejected_opts = [
        RejectedOption(vessel_id=v_id, parcel_id=c_id, reason=reason)
        for v_id, c_id, reason in vs_result.infeasible_pairs
    ]

    # --- 3. Repositioning ---
    assigned_vessels = {a.vessel_id for a in vs_result.assignments}
    idle_vessels = [v for v in inputs.vessels if v.vessel_id not in assigned_vessels]

    candidates = list(PortEnum)
    repo_actions = []
    repo_options: list[RepositioningOption] = []
    with _stage(on_progress, "repositioning", "Repositioning idle vessels"):
        for v in idle_vessels:
            repo_rec = recommend_repositioning(v, candidates, inputs)
            # recommend_repositioning sorts options by score_usd, descending, so
            # options[0] is always the recommended one; the current-location
            # entry is looked up separately so the explanation layer can show a
            # direct "why move" comparison rather than just the winner's own number.
            winner = repo_rec.options[0]
            current = next((o for o in repo_rec.options if o.is_current_location), winner)
            repo_actions.append(RepositioningAction(
                vessel_id=v.vessel_id,
                current_port=v.current_port,
                recommended_port=repo_rec.recommended_port,
                is_staying=repo_rec.recommended_port == v.current_port,
                cargo_probability_within_window=winner.cargo_probability_within_window,
                probability_is_real_data=winner.probability_is_real_data,
                data_provenance=winner.data_provenance,
                recommended_score_usd=winner.score_usd,
                current_port_score_usd=current.score_usd,
            ))
            for o in repo_rec.options[:_REPOSITION_OPTIONS_KEPT]:
                repo_options.append(RepositioningOption(
                    vessel_id=v.vessel_id,
                    port=o.port,
                    is_recommended=o.port == repo_rec.recommended_port,
                    is_current_location=o.is_current_location,
                    ballast_distance_nm=o.ballast_distance_nm,
                    cargo_probability_within_window=o.cargo_probability_within_window,
                    probability_is_real_data=o.probability_is_real_data,
                    data_provenance=o.data_provenance,
                    score_usd=o.score_usd,
                ))

    # --- 4. Monte Carlo Savings ---
    with _stage(on_progress, "monte_carlo", "Savings simulation"):
        mc_dist = estimate_savings_distribution(
            forecasts=inputs.forecasts,
            vessel_class=target_class,
            contract_term_days=inputs.contract_term_days,
            today_quote_usd_per_day=tc_quote,
            num_simulations=2000
        )

    # --- 5. Risk assessment / review trigger (PS deliverable d) ---
    # Real signals from real data when it's available; degrades to the pre-P3
    # default rather than raising when it isn't (see run_optimizer's docstring).
    risk_assessment = None
    review_trigger = ReviewTrigger(schedule="WEEKLY", conditions=["BDI jumps >5%"])
    with _stage(on_progress, "risk", "Risk assessment"):
        try:
            # resolved_as_of computed once, above, alongside route_basis.
            # F-08 fix: assess_risk/port_congestion_alert key off the real
            # PortWatch harvest's own file-name label (e.g. "Visakhapatnam",
            # "Richards_Bay_ZA", "Beira_MZ"), not a PortEnum's own display id
            # (e.g. "Vizag", "Richards_Bay", "Beira") -- those differ for 10
            # of the 12 real, resolvable ports (opt.congestion's
            # dynamic_wait_days already uses this exact same mapping for the
            # same reason). Passing the display id silently missed the file
            # for every port whose id doesn't happen to match its harvest
            # label -- the congestion-alert channel returned nothing for
            # those ports, with no error, verified live. Ports with no real
            # PortWatch coverage at all (mapped to None) are correctly
            # skipped, not fed a made-up label.
            port_ports = {p.origin_port for p in inputs.parcels} | {p.dest_port for p in inputs.parcels}
            port_labels = sorted(
                {label for port in port_ports if (label := PORT_TO_TONNAGE_LABEL.get(port)) is not None}
            )
            # 2.5: which chokepoints does THIS route actually cross, not the
            # same fixed five on every quote -- see opt.chokepoints's own
            # module docstring for the bug this fixes. _DEFAULT_CHOKEPOINTS
            # is kept as the fallback for the (today, theoretical) case
            # where the route can't be resolved at all -- opt.route_trace's
            # own great-circle fallback already covers a missing real-sea-
            # distance entry, so this only trips on something more
            # fundamental (e.g. a port with no PORT_COORDS entry).
            try:
                route_chokepoints = list(chokepoints_for_route(primary.origin_port, primary.dest_port))
                LOGGER.debug(
                    f"Real per-route chokepoints for {primary.origin_port.value.id} -> "
                    f"{primary.dest_port.value.id}: {route_chokepoints or 'none'}."
                )
            except Exception:
                route_chokepoints = list(_DEFAULT_CHOKEPOINTS)
                LOGGER.debug(
                    "Route chokepoint resolution failed; falling back to _DEFAULT_CHOKEPOINTS.", exc_info=True
                )
            # Cyclone climatology needs real PortEnum members (to resolve a
            # basin) and the real laycan window (to resolve ISO weeks) --
            # port_ports is already the real set this quote's parcels touch;
            # the laycan spans every parcel's own window rather than just one.
            laycan_start = min((p.laycan_start for p in inputs.parcels), default=None)
            laycan_end = max((p.laycan_end for p in inputs.parcels), default=None)
            risk_assessment = assess_risk(
                target_class,
                port_labels,
                route_chokepoints,
                resolved_as_of,
                ports=list(port_ports),
                laycan_start=laycan_start,
                laycan_end=laycan_end,
            )
            review_trigger = risk_assessment.to_review_trigger()
        except Exception:
            LOGGER.warning("Risk assessment unavailable (missing P1/P2 data on disk); using the default trigger.", exc_info=True)

    # Fleet-mix was already computed above, before target_class was derived
    # from it (F-03) -- nothing left to do here.

    # --- 8. Explanations -- "why was this shown," not a black box ---
    with _stage(on_progress, "explanations", "Building explanations"):
        explanations = build_explanations(
            lw_result=lw_result,
            stopping_result=stopping_result,
            voyage_assignments=vs_result.assignments,
            repositioning_actions=repo_actions,
            mc_dist=mc_dist,
            fleet_mix=fleet_mix,
            inputs=inputs,
            transit_buffer=transit_buffer,
        )

    return OptimizerRecommendation(
        target_vessel_class=target_class,
        origin_port=primary.origin_port,
        dest_port=primary.dest_port,
        cargo_volume_dwt=primary.volume_dwt,
        lock_action=lw_result.action,
        ceiling_usd_per_day=lw_result.ceiling_usd_per_day,
        tc_quote_usd_per_day=tc_quote,
        contract_term_days=inputs.contract_term_days,
        optimal_entry_window_start_day=start_day,
        optimal_entry_window_end_day=end_day,
        optimal_entry_window_p50_usd=min_p50,
        voyage_assignments=vs_result.assignments,
        rejected_options=rejected_opts,
        total_voyage_profit_usd=vs_result.total_profit_usd,
        repositioning_actions=repo_actions,
        repositioning_options=repo_options,
        expected_savings_usd_per_day=mc_dist.expected_p50_savings,
        p10_savings_usd_per_day=mc_dist.worst_case_p10_savings,
        p90_savings_usd_per_day=mc_dist.best_case_p90_savings,
        prob_savings_positive=mc_dist.prob_positive_savings,
        review_trigger=review_trigger,
        fleet_mix=fleet_mix,
        explanations=explanations,
        risk_assessment=risk_assessment,
        stopping_result=stopping_result,
        transit_buffer=transit_buffer,
    )


def run_portfolio_analysis(
    inputs: OptimizerInputs,
    target_class: VesselClass,
    plant_burden_cover_days: float,
    stockout_cost_usd: float,
    spot_sourcing_hazard_rate_per_day: float,
    risk_aversion: float = 0.0,
) -> PortfolioMix:
    """Spot/period-TC/COA mix (PS's actual stated objective) -- deliberately
    NOT part of ``run_optimizer``'s automatic pipeline, unlike fleet-mix, risk,
    and the exercise boundary. Those three can all be computed from real data
    already on disk with no further input; this one genuinely can't --
    ``plant_burden_cover_days`` (how many days of buffer stock the plant is
    carrying), ``stockout_cost_usd`` (the real cost of a production stockout),
    and how fast SAIL can actually source spot tonnage are business facts only
    SAIL has, not something inferable from public market data. Giving them a
    silent default (e.g. 0 burden-cover days) would produce a recommendation
    that looks like real advice while actually just reflecting an arbitrary
    made-up number -- called explicitly, with real figures, instead.
    """
    tc_quote = inputs.tc_quotes.get(target_class, 0.0)
    route_basis = None
    if inputs.parcels and inputs.parcels[0].route_family in inputs.basis:
        route_basis = inputs.basis[inputs.parcels[0].route_family]
    return optimize_portfolio_mix(
        today_quote_usd_per_day=tc_quote,
        forecasts=inputs.forecasts,
        vessel_class=target_class,
        contract_term_days=inputs.contract_term_days,
        plant_burden_cover_days=plant_burden_cover_days,
        stockout_cost_usd=stockout_cost_usd,
        spot_sourcing_hazard_rate_per_day=spot_sourcing_hazard_rate_per_day,
        risk_aversion=risk_aversion,
        basis=route_basis,
    )


def _why_block(factors: tuple[str, ...], method: str) -> str:
    lines = [f"    Why: {f}" for f in factors]
    lines.append(f"    Method: {method}")
    return "\n" + "\n".join(lines)


def format_recommendation_text(rec: OptimizerRecommendation) -> str:
    """Formats the structured recommendation back into human-readable text,
    including the real "why" behind each output (rec.explanations) -- not
    just the numbers, so this never reads as a black box."""
    e = rec.explanations

    # 1. Lock/Wait
    route_desc = f"{rec.origin_port.value.id} -> {rec.dest_port.value.id} ({rec.cargo_volume_dwt:,.0f} dwt)"
    if rec.lock_action == "LOCK":
        lw_text = f"Sign a {rec.contract_term_days}-day {rec.target_vessel_class.value} TC now for {route_desc} at ${rec.tc_quote_usd_per_day:,.0f}/day (Ceiling is ${rec.ceiling_usd_per_day:,.0f}/day)."
    else:
        # F-07: the entry window is None when there's no laycan runway left
        # to usefully wait (laycan already open, or opening today) -- real,
        # not a bug to paper over, so state it plainly instead of crashing
        # the %-format below on a None.
        if rec.optimal_entry_window_start_day is not None and rec.optimal_entry_window_end_day is not None:
            window_text = (
                f"Optimal entry window: Day {rec.optimal_entry_window_start_day} to {rec.optimal_entry_window_end_day} "
                f"(P50 trough at ~${rec.optimal_entry_window_p50_usd:,.0f}/day)."
            )
        else:
            window_text = "No entry window to recommend: the laycan is already open (or opens today)."
        lw_text = (
            f"WAIT on {route_desc} ({rec.target_vessel_class.value}). "
            f"Do not sign at ${rec.tc_quote_usd_per_day:,.0f}/day. Walk away above ${rec.ceiling_usd_per_day:,.0f}/day.\n"
            f"{window_text}"
        )
    lw_text += _why_block(e.lock_wait.factors, e.lock_wait.method)

    # 2. Voyage Schedule
    voyage_explain_by_key = dict(zip(((a.vessel_id, a.parcel_id) for a in rec.voyage_assignments), e.voyage_assignments, strict=True))
    if not rec.voyage_assignments:
        vs_text = "No profitable voyages found in the current pool."
        if rec.rejected_options:
            vs_text += "\n\nRejected options:\n" + "\n".join(
                [f"  - Vessel {opt.vessel_id} -> Parcel {opt.parcel_id}: {opt.reason}" for opt in rec.rejected_options]
            )
    else:
        v_schedules = {}
        for a in rec.voyage_assignments:
            if a.vessel_id not in v_schedules:
                v_schedules[a.vessel_id] = []
            v_schedules[a.vessel_id].append(a)

        vs_lines = []
        for v_id, assigns in v_schedules.items():
            assigns.sort(key=lambda x: x.start_operation_hours)
            seq = " -> ".join([f"parcel {a.parcel_id} to {a.dest_port.name} (Hr {a.start_operation_hours})" for a in assigns])
            vs_lines.append(f"Vessel {v_id} routes: {seq}")
            for a in assigns:
                ex = voyage_explain_by_key[(a.vessel_id, a.parcel_id)]
                vs_lines.append(_why_block(ex.factors, ex.method).strip("\n"))

        if rec.rejected_options:
            vs_lines.append("\nRejected options:")
            for opt in rec.rejected_options:
                vs_lines.append(f"  - Vessel {opt.vessel_id} -> Parcel {opt.parcel_id}: {opt.reason}")

        vs_text = "\n".join(vs_lines)

    # 3. Repositioning
    repo_lines = []
    for action, ex in zip(rec.repositioning_actions, e.repositioning, strict=True):
        if action.is_staying:
            repo_lines.append(f"Vessel {action.vessel_id}: Stay at {action.current_port.name} (best available local market).")
        else:
            repo_lines.append(f"Vessel {action.vessel_id}: Don't idle at {action.current_port.name}. Sail empty toward {action.recommended_port.name}.")
        repo_lines.append(_why_block(ex.factors, ex.method).strip("\n"))
    repo_text = "\n".join(repo_lines) if repo_lines else "All vessels are scheduled; no immediate repositioning needed."

    # 4. Savings
    if rec.lock_action == "LOCK":
        sav_text = f"+${rec.expected_savings_usd_per_day:,.0f}/day expected vs always-spot; even in bad runs +${rec.p10_savings_usd_per_day:,.0f}/day (P10)."
    else:
        sav_text = f"Waiting avoids an expected loss of ${-rec.expected_savings_usd_per_day:,.0f}/day."
    sav_text += _why_block(e.savings.factors, e.savings.method)

    # 5. Review Trigger
    review_text = f"Re-solve {rec.review_trigger.schedule.lower()}, or immediately if " + " or ".join(rec.review_trigger.conditions) + "."

    # 6. Fleet-mix, when present
    fm_text = ""
    if rec.fleet_mix is not None and e.fleet_mix is not None:
        fm_text = "\n\n" + e.fleet_mix.summary + _why_block(e.fleet_mix.factors, e.fleet_mix.method)

    return f"--- OPTIMIZER RECOMMENDATION ---\n\n{lw_text}\n\n{vs_text}\n\n{repo_text}\n\n{sav_text}\n\n{review_text}{fm_text}"
