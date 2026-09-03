"""analyze_fragility -- the public entry point.

For each of the eight v1 variables, picks the cheapest tier that can
possibly answer it (never a tier that structurally cannot -- see the
per-variable notes below, each verified against the real source rather than
assumed), searches there, and escalates to the next tier only if that
search finds no flip. Every real solve this module performs goes through
opt.quote.quote_envelope, opt.fleetmix.enumerate_fleet_mix,
opt.voyage._vessel_can_call or opt.ceiling.select_vessel_class_for_cargo --
nothing here recomputes what any of those already compute.

Per-variable tier assignment, and why (checked against the real code, not
assumed):

* cargo_volume_dwt -- Tier 1 only. select_vessel_class_for_cargo is an exact
  step function; a target_vessel_class change IS a full DecisionSignature
  component changing, so a Tier 1 answer is already definitive.
* vessel_draft_m, permissible_draft_m -- Tier 1 only. _vessel_can_call is
  the *same* function opt.fleetmix and opt.voyage's CP-SAT objective call
  internally (BT-2), so a Tier 1 feasibility flip is guaranteed to reappear
  at Tier 2/3, not merely likely to.
* origin_wait_days, dest_wait_days -- UNAVAILABLE, always, and not merely
  because of a null register limit (requirement 3's literal case). Checked
  directly: opt.fleetmix never reads dynamic_wait_days at all (grepped the
  real module -- zero references), and neither opt.quote.quote nor
  opt.quote.quote_envelope exposes any parameter to override it. Its only
  consumer in the whole decision pipeline is opt.voyage's CP-SAT objective,
  which (a) requires real vessels to be supplied and (b) does not feed any
  of the five DecisionSignature components even then (voyage assignments
  aren't part of the signature). There is no honest injection point for
  this variable anywhere reachable from a quote -- reported as such, not
  silently searched against nothing.
* laycan_width_days -- Tier 3 only. This IS what quote_envelope's own
  relaxation ladder probes; there is no cheaper closed-form or fleet-mix-only
  answer to "how wide before booking closes."
* risk_tolerance -- Tier 3 only. Verified: opt.fleetmix._price_configuration
  calls compute_ceiling with risk_tolerance hardcoded to 0.0 internally, so
  Tier 2 is structurally blind to this variable; only the top-level LSMC/
  ceiling fusion (Tier 3) sees it.
* contract_term_days -- Tier 2 then Tier 3. Verified: fleet-mix pricing
  takes contract_term_days directly (affects chosen_config_id), and the
  ceiling/LSMC horizon-blend also depends on it (affects lock_action) --
  genuinely a two-tier variable, escalated only if Tier 2 finds nothing.
"""
from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Final

from berth_truth.empirical import WaitInterval, compute_wait_distribution
from berth_truth.tide import assess_tide
from fragility.models import (
    BerthTruthContext,
    DecisionSignature,
    FlipPoint,
    FragilityReport,
    Provenance,
    Tier,
    VariableId,
)
from fragility.search import search_flip
from fragility.tiers import (
    tier1_cargo_class_signature,
    tier1_draft_signature,
    tier2_fleet_mix_signature,
    tier3_full_quote_signature,
)
from ml.live_forecast import forecast_all_classes, latest_available_date, resolve_as_of
from opt.api import ProgressCallback
from opt.ceiling import select_vessel_class_for_cargo
from opt.feasibility import _MAX_TERM_DAYS
from opt.fleetmix import _CLASS_SPECS, _representative_vessel
from opt.network import PortEnum
from opt.types import ProgressStage
from opt.voyage import _vessel_can_call

__all__ = ["analyze_fragility"]

# Evaluation caps, per tier -- not uniform, because the tiers are wildly
# different in cost: Tier 1 is sub-millisecond, Tier 2 is a few milliseconds
# once forecasts are loaded, Tier 3 is a real CP-SAT + LSMC solve (~1-3s
# measured against a real quote). A search that finds nothing nearby is
# honestly reported as flip_found=False with the range it actually covered
# (requirement 7) -- these caps bound how far that range reaches, they don't
# invalidate the answer.
_MAX_EVALUATIONS_TIER1: Final[int] = 120
_MAX_EVALUATIONS_TIER2: Final[int] = 60
_MAX_EVALUATIONS_TIER3: Final[int] = 10

# F-30 fix: the string below is user-facing (it renders verbatim on the
# Fragility screen), so it must not name modules, functions, or internal
# build-phase labels -- it did, at length, before this fix. The developer
# rationale it used to state inline still holds and is preserved here as a
# comment instead: no parameter on the quote or fleet-mix entry points
# overrides wait days independently of live PortWatch/empirical data --
# opt.fleetmix never reads the dynamic wait-day estimator at all, the LSMC
# exercise-boundary solve takes no wait-day argument, and the one real
# consumer (the CP-SAT voyage scheduler) requires real vessels and doesn't
# feed any part of the decision signature even then. The empirical wait
# percentiles berth_truth.empirical computes are DISPLAY-only context on
# PortCheck (surfaced below as context chips), not a decision input yet --
# so there is no honest injection point for this variable to search.
_WAIT_DAYS_UNAVAILABLE_REASON: Final[str] = (
    "Wait-time sensitivity can't be tested yet -- nothing in the pricing or "
    "scheduling logic currently takes a wait-day figure as an input, so "
    "there's no lever here to search for a flip point. The real wait data "
    "shown as context below informs the picture, but doesn't change the "
    "recommendation on its own."
)


@dataclass
class _Memo:
    """One cache per analyze_fragility call, shared across every variable's
    search -- several variables' base points (and Tier 3's own base call)
    are literally the same real query, and Tier 2 evaluations are cheap
    enough that memoisation matters more for Tier 3's ~1-3s calls."""

    _cache: dict[tuple, DecisionSignature] | None = None

    def __post_init__(self) -> None:
        self._cache = {}

    def get_or_compute(self, key: tuple, compute: Callable[[], DecisionSignature]) -> DecisionSignature:
        assert self._cache is not None
        if key not in self._cache:
            self._cache[key] = compute()
        return self._cache[key]


def _emit(
    on_progress: ProgressCallback | None,
    key: str,
    label: str,
    status: str,
    timers: dict[str, float] | None = None,
) -> None:
    """The fragility sweep's own progress, one start/done pair per variable
    -- NOT a forwarding of quote_envelope's own internal sub-stages into
    every one of a search's ~10 evaluations, which would spam a caller with
    per-evaluation noise unrelated to sweep-level progress. Same
    ProgressCallback/ProgressStage types as everywhere else in opt --
    requirement 9 asks for the existing mechanism, not a second one, and
    this is that mechanism, used at the granularity that's actually useful
    to report.

    ``elapsed_ms`` on a ``done`` stage is real wall time, measured from this
    key's own ``start``. It used to be hardcoded to 0.0, which was harmless
    only for as long as nothing consumed it -- the moment a caller rendered
    the field (the desk's sweep checklist now does), a variable search that
    genuinely takes several hundred milliseconds was reporting itself as
    "<1 ms". A number nobody reads is still a number this codebase should
    not be inventing.

    ``timers`` is created per ``analyze_fragility`` call rather than held at
    module scope: the FastAPI route runs sweeps in a threadpool, and a shared
    dict would let two concurrent sweeps attribute each other's timings.
    """
    if on_progress is None:
        return
    elapsed_ms = 0.0
    if timers is not None:
        if status == "start":
            timers[key] = time.perf_counter()
        else:
            t0 = timers.pop(key, None)
            if t0 is not None:
                elapsed_ms = (time.perf_counter() - t0) * 1000.0
    on_progress(ProgressStage(key=key, label=label, status=status, elapsed_ms=elapsed_ms))


def _binding_port_verdict(
    *, vessel, origin_port: PortEnum, dest_port: PortEnum, commodity: str | None, as_of: date
):
    """Whichever of origin/dest is currently more draft-binding for this
    vessel -- the port a real draft-margin question should be asked about.
    Both sides go through the exact same _vessel_can_call BT-2 wired into
    the real pipeline; this picks between two already-correct answers, it
    doesn't compute a new one."""
    origin_verdict = _vessel_can_call(vessel, origin_port, commodity=commodity, as_of=as_of)
    dest_verdict = _vessel_can_call(vessel, dest_port, commodity=commodity, as_of=as_of)

    def margin_or_inf(v):
        m = v.margins.draft_margin_m
        return m if m is not None else float("inf")

    if margin_or_inf(origin_verdict) <= margin_or_inf(dest_verdict):
        return origin_port, origin_verdict
    return dest_port, dest_verdict


def _draft_provenance(verdict) -> Provenance:
    from opt.types import LimitSource

    if verdict.limit_source is LimitSource.PORTENUM_FALLBACK:
        return Provenance.ASSUMPTION  # the old, unsourced PortEnum literal
    return Provenance.OBSERVED  # a cited register value, current as of the query


def _draft_and_tide_context(
    verdict, *, vessel_class: str | None = None, vessel_is_laden: bool | None = None
) -> BerthTruthContext | None:
    """P5: real draft-resolution detail plus real tide sensitivity for the
    binding berth, when the verdict actually rests on a register row
    (verdict.binding_constraint is None for a PortEnum-fallback port -- there
    is no berth-level document to report on, so this returns None rather
    than a context with every field empty)."""
    if verdict.binding_constraint is None:
        return None
    tide = assess_tide(verdict.binding_constraint, vessel_class=vessel_class, vessel_is_laden=vessel_is_laden)
    return BerthTruthContext(
        draft_status=verdict.draft_status.value if verdict.draft_status else None,
        draft_source=verdict.draft_source.value if verdict.draft_source else None,
        draft_as_of=verdict.draft_as_of.isoformat() if verdict.draft_as_of else None,
        tide_impact=tide.impact.value,
        tide_authority=tide.authority.value if tide.authority else None,
        tide_reason=tide.reason,
    )


def _empirical_wait_context(port: PortEnum) -> BerthTruthContext:
    """P5: real empirical arrival-to-berth wait percentiles for `port`, the
    same real function (and interval) opt.quote._port_check already calls
    for display -- reused, not recomputed differently.
    compute_wait_distribution never raises (checked directly: a port with no
    real coverage at all queries to zero rows and returns is_sufficient=False,
    not an exception), so this always returns a real context -- with
    empirical_wait_is_sufficient=False and every percentile None for a port
    with no evidence, which is itself real, disclosable information distinct
    from "we never looked."""
    dist = compute_wait_distribution(port, WaitInterval.ARRIVAL_TO_BERTH)
    return BerthTruthContext(
        empirical_wait_p50_hours=dist.p50_hours,
        empirical_wait_p90_hours=dist.p90_hours,
        empirical_wait_sample_n=dist.n,
        empirical_wait_is_sufficient=dist.is_sufficient,
    )


def _search_variable(
    *,
    variable: VariableId,
    tier: Tier,
    base_value: float,
    unit: str,
    provenance: Provenance,
    evaluate: Callable[[float], DecisionSignature],
    base_signature: DecisionSignature,
    tolerance: float,
    min_value: float | None,
    max_value: float | None,
    max_evaluations: int,
) -> FlipPoint:
    result = search_flip(
        base_value=base_value,
        base_signature=base_signature,
        evaluate=evaluate,
        tolerance=tolerance,
        max_evaluations=max_evaluations,
        min_value=min_value,
        max_value=max_value,
    )
    if not result.flip_found:
        return FlipPoint(
            variable=variable,
            tier=tier,
            provenance=provenance,
            base_value=base_value,
            unit=unit,
            flip_found=False,
            base_signature=base_signature,
            range_searched_low=result.range_low,
            range_searched_high=result.range_high,
            evaluations_used=result.evaluations_used,
        )

    delta = result.flip_value - base_value
    changed = base_signature.changed_fields(result.flipped_signature)
    return FlipPoint(
        variable=variable,
        tier=tier,
        provenance=provenance,
        base_value=base_value,
        unit=unit,
        flip_found=True,
        flip_value=result.flip_value,
        absolute_delta=delta,
        percent_delta=(delta / base_value * 100.0) if base_value != 0 else None,
        direction="increase" if delta > 0 else "decrease",
        base_signature=base_signature,
        flipped_signature=result.flipped_signature,
        changed_components=changed,
        evaluations_used=result.evaluations_used,
    )


def analyze_fragility(
    *,
    cargo_volume_dwt: float,
    origin_port: PortEnum,
    dest_port: PortEnum,
    laycan_start: date,
    laycan_end: date,
    contract_term_days: int = 30,
    commodity: str = "Dry Bulk",
    as_of: date | None = None,
    risk_tolerance: float = 0.0,
    master_path: Path | None = None,
    on_progress: ProgressCallback | None = None,
    variables: Sequence[VariableId] | None = None,
) -> FragilityReport:
    """How far the recommendation for this cargo is from changing, one
    variable at a time. ``variables`` defaults to all eight; pass a subset
    to search only what a caller actually needs (each Tier 3 escalation is a
    real solve, so this matters for latency).
    """
    # F-15 fix: same backward as-of resolution opt.quote now uses, so an
    # explicit as_of that isn't an exact trading-day match (a weekend, a
    # gap day) doesn't silently starve forecast_all_classes of data below
    # for every class instead of raising anything -- a caller who omits
    # as_of entirely still gets exactly latest_available_date().
    resolved_as_of = (
        resolve_as_of(as_of, master_path) if as_of is not None else latest_available_date(master_path)
    )
    memo = _Memo()
    total_evaluations = 0
    # Per-sweep, never module scope -- see _emit's docstring. Holds the
    # perf_counter reading for each stage that has started but not finished,
    # so a `done` event can report the real wall time it took.
    _timers: dict[str, float] = {}

    _emit(on_progress, "fragility_baseline", "Computing current recommendation", "start", _timers)
    current_decision = memo.get_or_compute(
        ("tier3", cargo_volume_dwt, origin_port, dest_port, laycan_start, laycan_end,
         contract_term_days, commodity, resolved_as_of, risk_tolerance),
        lambda: tier3_full_quote_signature(
            cargo_volume_dwt=cargo_volume_dwt, origin_port=origin_port, dest_port=dest_port,
            laycan_start=laycan_start, laycan_end=laycan_end, contract_term_days=contract_term_days,
            commodity=commodity, as_of=resolved_as_of, risk_tolerance=risk_tolerance,
        ),
    )
    total_evaluations += 1
    _emit(on_progress, "fragility_baseline", "Computing current recommendation", "done", _timers)

    wanted = set(variables) if variables is not None else set(VariableId)
    findings: list[FlipPoint] = []

    target_class = select_vessel_class_for_cargo(cargo_volume_dwt)
    class_spec = _CLASS_SPECS[target_class]
    representative_vessel = _representative_vessel(target_class)

    # Forecasts loaded once, reused by every Tier 2 evaluation this call
    # makes -- forecast_all_classes is a real ~0.2s file/model load with no
    # internal caching of its own; it does not depend on any variable being
    # perturbed here (only on as_of, which is fixed for this whole report).
    _fans_by_class, _quotes = forecast_all_classes(resolved_as_of, master_path)
    all_fans = [f for cf in _fans_by_class.values() for f in cf]

    if VariableId.CARGO_VOLUME_DWT in wanted:
        _emit(on_progress, "fragility_cargo_volume_dwt", "Searching cargo_volume_dwt", "start", _timers)
        base_sig = tier1_cargo_class_signature(cargo_volume_dwt)

        def eval_cargo(x: float) -> DecisionSignature:
            return memo.get_or_compute(("tier1_cargo", x), lambda: tier1_cargo_class_signature(x))

        fp = _search_variable(
            variable=VariableId.CARGO_VOLUME_DWT, tier=Tier.TIER1_CLOSED_FORM,
            base_value=cargo_volume_dwt, unit="dwt", provenance=Provenance.USER_INPUT,
            evaluate=eval_cargo, base_signature=base_sig, tolerance=500.0,
            min_value=1.0, max_value=50_000_000.0, max_evaluations=_MAX_EVALUATIONS_TIER1,
        )
        findings.append(fp)
        total_evaluations += fp.evaluations_used
        _emit(on_progress, "fragility_cargo_volume_dwt", "Searching cargo_volume_dwt", "done", _timers)

    if {VariableId.VESSEL_DRAFT_M, VariableId.PERMISSIBLE_DRAFT_M} & wanted:
        binding_port, binding_verdict = _binding_port_verdict(
            vessel=representative_vessel, origin_port=origin_port, dest_port=dest_port,
            commodity=commodity, as_of=resolved_as_of,
        )
        draft_untested = binding_verdict.margins.draft_margin_m is None
        # P5: real draft-resolution detail + tide sensitivity for the binding
        # berth, computed once and attached to both draft findings below --
        # vessel_is_laden is deliberately None (unknown), not True: the
        # underlying _vessel_can_call feasibility check itself does not model
        # laden state (confirmed -- its signature takes no such argument), so
        # asserting one here for the tide check would claim more than this
        # sweep actually resolves.
        binding_context = _draft_and_tide_context(
            binding_verdict, vessel_class=target_class.value, vessel_is_laden=None
        )

        if VariableId.VESSEL_DRAFT_M in wanted:
            _emit(on_progress, "fragility_vessel_draft_m", "Searching vessel_draft_m", "start", _timers)
            if draft_untested:
                findings.append(FlipPoint(
                    variable=VariableId.VESSEL_DRAFT_M, tier=Tier.TIER1_CLOSED_FORM,
                    provenance=Provenance.ASSUMPTION, base_value=class_spec["draft_m"], unit="m",
                    flip_found=False,
                    unavailable_reason=(
                        f"draft at the binding port ({binding_port.value.id}) is "
                        f"{binding_verdict.draft_status.value if binding_verdict.draft_status else 'unresolved'} -- "
                        "no defined limit to search a boundary against."
                    ),
                    berth_truth_context=binding_context,
                ))
            else:
                base_sig = tier1_draft_signature(
                    vessel=representative_vessel, port=binding_port, commodity=commodity, as_of=resolved_as_of
                )

                def eval_vessel_draft(x: float, _port=binding_port) -> DecisionSignature:
                    v = representative_vessel.model_copy(update={"draft_m": x})
                    return memo.get_or_compute(
                        ("tier1_draft", "vessel", _port, x, commodity, resolved_as_of),
                        lambda: tier1_draft_signature(vessel=v, port=_port, commodity=commodity, as_of=resolved_as_of),
                    )

                fp = _search_variable(
                    variable=VariableId.VESSEL_DRAFT_M, tier=Tier.TIER1_CLOSED_FORM,
                    base_value=class_spec["draft_m"], unit="m", provenance=Provenance.DERIVED,
                    evaluate=eval_vessel_draft, base_signature=base_sig, tolerance=0.05,
                    min_value=0.1, max_value=30.0, max_evaluations=_MAX_EVALUATIONS_TIER1,
                )
                findings.append(fp.model_copy(update={"berth_truth_context": binding_context}))
                total_evaluations += fp.evaluations_used
            _emit(on_progress, "fragility_vessel_draft_m", "Searching vessel_draft_m", "done", _timers)

        if VariableId.PERMISSIBLE_DRAFT_M in wanted:
            _emit(on_progress, "fragility_permissible_draft_m", "Searching permissible_draft_m", "start", _timers)
            if draft_untested:
                findings.append(FlipPoint(
                    variable=VariableId.PERMISSIBLE_DRAFT_M, tier=Tier.TIER1_CLOSED_FORM,
                    provenance=Provenance.ASSUMPTION, base_value=class_spec["draft_m"], unit="m",
                    flip_found=False,
                    unavailable_reason=(
                        f"draft at the binding port ({binding_port.value.id}) is "
                        f"{binding_verdict.draft_status.value if binding_verdict.draft_status else 'unresolved'} -- "
                        "no defined limit to perturb."
                    ),
                    berth_truth_context=binding_context,
                ))
            else:
                vessel_draft = class_spec["draft_m"]
                current_limit = vessel_draft + binding_verdict.margins.draft_margin_m
                base_sig = DecisionSignature(feasibility_set=frozenset({target_class.value}))

                def eval_permissible_draft(x: float) -> DecisionSignature:
                    # A direct threshold comparison, not a re-implementation
                    # of _vessel_can_call's multi-dimension logic -- there is
                    # no override parameter anywhere to ask "what if the
                    # published limit were X" through the real engine, and
                    # this specific comparison is the unavoidable, one-line
                    # primitive that question reduces to.
                    feasible = x >= vessel_draft
                    return frozenset({target_class.value}) if feasible else frozenset()

                def eval_permissible_draft_sig(x: float) -> DecisionSignature:
                    return memo.get_or_compute(
                        ("tier1_draft", "limit", binding_port, x),
                        lambda: DecisionSignature(feasibility_set=eval_permissible_draft(x)),
                    )

                fp = _search_variable(
                    variable=VariableId.PERMISSIBLE_DRAFT_M, tier=Tier.TIER1_CLOSED_FORM,
                    base_value=current_limit, unit="m", provenance=_draft_provenance(binding_verdict),
                    evaluate=eval_permissible_draft_sig, base_signature=base_sig, tolerance=0.05,
                    min_value=0.0, max_value=30.0, max_evaluations=_MAX_EVALUATIONS_TIER1,
                )
                findings.append(fp.model_copy(update={"berth_truth_context": binding_context}))
                total_evaluations += fp.evaluations_used
            _emit(on_progress, "fragility_permissible_draft_m", "Searching permissible_draft_m", "done", _timers)

    for wait_var, wait_port in ((VariableId.ORIGIN_WAIT_DAYS, origin_port), (VariableId.DEST_WAIT_DAYS, dest_port)):
        if wait_var in wanted:
            # Still UNAVAILABLE for search (see _WAIT_DAYS_UNAVAILABLE_REASON --
            # verified directly, no injection point exists), but P5 attaches
            # real empirical P50/P90 as context where evidence exists, rather
            # than the plain reason alone.
            findings.append(FlipPoint(
                variable=wait_var, tier=Tier.TIER3_FULL_QUOTE, provenance=Provenance.ASSUMPTION,
                base_value=0.0, unit="days", flip_found=False,
                unavailable_reason=_WAIT_DAYS_UNAVAILABLE_REASON,
                berth_truth_context=_empirical_wait_context(wait_port),
            ))

    if VariableId.LAYCAN_WIDTH_DAYS in wanted:
        _emit(on_progress, "fragility_laycan_width_days", "Searching laycan_width_days", "start", _timers)
        base_width = float((laycan_end - laycan_start).days)

        def eval_laycan(width: float) -> DecisionSignature:
            end = laycan_start + timedelta(days=round(width))
            return memo.get_or_compute(
                ("tier3", cargo_volume_dwt, origin_port, dest_port, laycan_start, end,
                 contract_term_days, commodity, resolved_as_of, risk_tolerance),
                lambda: tier3_full_quote_signature(
                    cargo_volume_dwt=cargo_volume_dwt, origin_port=origin_port, dest_port=dest_port,
                    laycan_start=laycan_start, laycan_end=end, contract_term_days=contract_term_days,
                    commodity=commodity, as_of=resolved_as_of, risk_tolerance=risk_tolerance,
                ),
            )

        fp = _search_variable(
            variable=VariableId.LAYCAN_WIDTH_DAYS, tier=Tier.TIER3_FULL_QUOTE,
            base_value=base_width, unit="days", provenance=Provenance.USER_INPUT,
            evaluate=eval_laycan, base_signature=current_decision, tolerance=0.5,
            min_value=1.0, max_value=365.0, max_evaluations=_MAX_EVALUATIONS_TIER3,
        )
        findings.append(fp)
        total_evaluations += fp.evaluations_used
        _emit(on_progress, "fragility_laycan_width_days", "Searching laycan_width_days", "done", _timers)

    if VariableId.RISK_TOLERANCE in wanted:
        _emit(on_progress, "fragility_risk_tolerance", "Searching risk_tolerance", "start", _timers)

        def eval_risk(x: float) -> DecisionSignature:
            return memo.get_or_compute(
                ("tier3", cargo_volume_dwt, origin_port, dest_port, laycan_start, laycan_end,
                 contract_term_days, commodity, resolved_as_of, x),
                lambda: tier3_full_quote_signature(
                    cargo_volume_dwt=cargo_volume_dwt, origin_port=origin_port, dest_port=dest_port,
                    laycan_start=laycan_start, laycan_end=laycan_end, contract_term_days=contract_term_days,
                    commodity=commodity, as_of=resolved_as_of, risk_tolerance=x,
                ),
            )

        fp = _search_variable(
            variable=VariableId.RISK_TOLERANCE, tier=Tier.TIER3_FULL_QUOTE,
            base_value=risk_tolerance, unit="fraction", provenance=Provenance.USER_INPUT,
            evaluate=eval_risk, base_signature=current_decision, tolerance=0.01,
            min_value=0.0, max_value=1.0, max_evaluations=_MAX_EVALUATIONS_TIER3,
        )
        findings.append(fp)
        total_evaluations += fp.evaluations_used
        _emit(on_progress, "fragility_risk_tolerance", "Searching risk_tolerance", "done", _timers)

    if VariableId.CONTRACT_TERM_DAYS in wanted:
        _emit(on_progress, "fragility_contract_term_days", "Searching contract_term_days", "start", _timers)

        def eval_term_tier2(x: float) -> DecisionSignature:
            days = max(1, round(x))
            return memo.get_or_compute(
                ("tier2", cargo_volume_dwt, origin_port, dest_port, days),
                lambda: tier2_fleet_mix_signature(
                    cargo_volume_dwt=cargo_volume_dwt, origin_port=origin_port, dest_port=dest_port,
                    forecasts=all_fans, contract_term_days=days,
                ),
            )

        base_sig_t2 = eval_term_tier2(float(contract_term_days))
        fp = _search_variable(
            variable=VariableId.CONTRACT_TERM_DAYS, tier=Tier.TIER2_FLEET_MIX,
            base_value=float(contract_term_days), unit="days", provenance=Provenance.USER_INPUT,
            evaluate=eval_term_tier2, base_signature=base_sig_t2, tolerance=1.0,
            min_value=1.0, max_value=float(_MAX_TERM_DAYS), max_evaluations=_MAX_EVALUATIONS_TIER2,
        )

        if not fp.flip_found:
            def eval_term_tier3(x: float) -> DecisionSignature:
                days = max(1, round(x))
                return memo.get_or_compute(
                    ("tier3", cargo_volume_dwt, origin_port, dest_port, laycan_start, laycan_end,
                     days, commodity, resolved_as_of, risk_tolerance),
                    lambda: tier3_full_quote_signature(
                        cargo_volume_dwt=cargo_volume_dwt, origin_port=origin_port, dest_port=dest_port,
                        laycan_start=laycan_start, laycan_end=laycan_end, contract_term_days=days,
                        commodity=commodity, as_of=resolved_as_of, risk_tolerance=risk_tolerance,
                    ),
                )

            fp3 = _search_variable(
                variable=VariableId.CONTRACT_TERM_DAYS, tier=Tier.TIER3_FULL_QUOTE,
                base_value=float(contract_term_days), unit="days", provenance=Provenance.USER_INPUT,
                evaluate=eval_term_tier3, base_signature=current_decision, tolerance=1.0,
                min_value=1.0, max_value=float(_MAX_TERM_DAYS), max_evaluations=_MAX_EVALUATIONS_TIER3,
            )
            fp = FlipPoint(**{**fp3.model_dump(), "evaluations_used": fp.evaluations_used + fp3.evaluations_used})

        findings.append(fp)
        total_evaluations += fp.evaluations_used
        _emit(on_progress, "fragility_contract_term_days", "Searching contract_term_days", "done", _timers)

    return FragilityReport(
        current_decision=current_decision,
        findings=tuple(findings),
        evaluations_used=total_evaluations,
    )
