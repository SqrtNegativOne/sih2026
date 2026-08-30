"""The M4 Berth Reality Engine -- P2 §4.

Composes what already exists (BT-1/BT-2's register-backed feasibility check)
with what P2 adds (tide authority, empirical waits/handling) into one
three-state report per port. Reuses ``berth_truth.service.
check_vessel_against_register`` and ``opt.voyage._REGISTER_PORT_ID`` --
the exact same functions BT-2 wired into the optimizer -- rather than
re-implementing constraint resolution; this module is a composition layer,
not a second feasibility engine.

Deliberately keyed by ``opt.network.PortEnum``, not ``berth_truth.models.
PortId``: PortEnum is the superset every P1 module (port_master, sources,
fact_port_call) already uses, and the empirical half needs to answer for
ports (Paradip) that have real fact_port_call history but no PortId/register
entry at all -- a PortId-only key would make that impossible to express.
"""
from __future__ import annotations

from datetime import date
from enum import Enum

from pydantic import BaseModel, ConfigDict

from berth_truth.empirical import (
    HandlingDistribution,
    WaitDistribution,
    WaitInterval,
    compute_handling_distribution,
    compute_wait_distribution,
)
from berth_truth.fact_port_call import (
    FactPortCallStore,
    classify_cargo,
    distinct_calls,
)
from berth_truth.models import BerthConstraint
from berth_truth.service import check_vessel_against_register
from berth_truth.sources import PORT_SOURCES, SourceQuality
from berth_truth.tide import TideAssessment, TideImpact, assess_tide
from opt.network import PortEnum
from opt.voyage import _REGISTER_PORT_ID, _portenum_fallback_verdict

__all__ = [
    "PortRealityReport",
    "RealityVerdict",
    "get_port_reality",
]


class RealityVerdict(str, Enum):
    FEASIBLE = "FEASIBLE"
    INFEASIBLE = "INFEASIBLE"
    CANNOT_VERIFY = "CANNOT_VERIFY"
    """A real check exists that this system cannot resolve either way --
    e.g. a genuine PORT_RULE tide restriction with no timetable data, or a
    draft check whose only declaration is STALE_OR_UNAVAILABLE. Never
    collapsed into FEASIBLE (a silent pass) or INFEASIBLE (a false negative
    on a real vessel that likely can call)."""


class ObservedEnvelope(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_loa_m: float | None = None
    max_beam_m: float | None = None
    max_draft_m: float | None = None
    n_calls: int = 0


class DeclaredVsObservedConflict(BaseModel):
    model_config = ConfigDict(frozen=True)

    dimension: str
    declared_value: float
    observed_value: float
    note: str


class PortRealityReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    port: PortEnum
    as_of: date
    verdict: RealityVerdict

    berth_id: str | None = None
    binding_constraint: BerthConstraint | None = None
    limit_source: str = "NONE"
    margin_draft_m: float | None = None
    margin_loa_m: float | None = None
    margin_beam_m: float | None = None

    tide: TideAssessment

    wait_arrival_to_berth: WaitDistribution | None = None
    handling: HandlingDistribution | None = None

    untested_checks: tuple[str, ...] = ()
    stale_inputs: tuple[str, ...] = ()
    observed_envelope: ObservedEnvelope = ObservedEnvelope()
    declared_vs_observed_conflicts: tuple[DeclaredVsObservedConflict, ...] = ()

    source_quality: SourceQuality | None = None
    confidence: float
    """A single [0, 1] rollup: 1.0 only when constraint, tide and wait are
    all real, current and sufficient; reduced by each dimension that is
    untested, stale, or falls back to an assumption. Documented, not a
    black box -- see _compute_confidence."""

    reason: str | None = None


def _observed_envelope(port: PortEnum, store: FactPortCallStore) -> ObservedEnvelope:
    """F-12 fix (the second half -- see _declared_vs_observed above for the
    first): this used to include every real call at the port regardless of
    trade, so a dry-bulk chartering tool's own "max observed draft" figure
    for Paradip came from a 339.76m crude-oil tanker at an offshore mooring
    buoy (21.5m draft) -- real, but irrelevant to whether a coal carrier
    fits at a coal berth, and large enough to make the port look far more
    permissive than it is for the cargo this system actually prices.
    Excludes calls this module's own free-text classifier (classify_cargo)
    reads as liquid/gas cargo; a call it can't classify either way is kept
    (never dropped on an uncertain guess). Also deduplicated to one row per
    real call (distinct_calls) for the same reason empirical.py's wait/
    handling stats are -- n_calls should count real port calls, not report
    re-listings of the same one."""
    rows = tuple(
        r for r in distinct_calls(store.query(port=port))
        if classify_cargo(r.cargo_raw) != "liquid_or_gas"
    )
    loas = [r.loa_m for r in rows if r.loa_m is not None]
    beams = [r.beam_m for r in rows if r.beam_m is not None]
    drafts = [r.arrival_draft_m for r in rows if r.arrival_draft_m is not None]
    return ObservedEnvelope(
        max_loa_m=max(loas) if loas else None,
        max_beam_m=max(beams) if beams else None,
        max_draft_m=max(drafts) if drafts else None,
        n_calls=len(rows),
    )


def _declared_vs_observed(
    *, permissible_draft_m: float | None, max_loa_m: float | None, max_beam_m: float | None,
    envelope: ObservedEnvelope,
) -> tuple[DeclaredVsObservedConflict, ...]:
    """A vessel observed larger than the published limit is a reportable
    finding -- never used to raise the declared limit itself (BT-2's
    invariant, preserved: observed_only_berths / observations never clear a
    vessel on their own).

    F-12 fix: this used to take a ``BerthConstraint | None`` and return
    ``()`` immediately whenever it was None -- which is exactly the
    PORTENUM_FALLBACK case (a port with real fact_port_call history but no
    berth register, Paradip being the one real example of both at once).
    The result was a real, load-bearing contradiction on screen: Port
    Twin's own Feasibility Verdict panel would reject a 14.5m-draft vessel
    against a declared 14.3m limit, while the Observed Envelope panel two
    columns over said "no conflicts" against a real 21.5m observed draft --
    because the conflict check had silently compared against nothing.
    Taking the three raw limit values directly (whichever source actually
    produced them -- register or PortEnum fallback, resolved by the caller)
    instead of a register-only object fixes that: the same limit the
    verdict panel used is now the same limit this panel checks against."""
    conflicts: list[DeclaredVsObservedConflict] = []
    pairs = (
        ("draft_m", permissible_draft_m, envelope.max_draft_m),
        ("loa_m", max_loa_m, envelope.max_loa_m),
        ("beam_m", max_beam_m, envelope.max_beam_m),
    )
    for dimension, declared, observed in pairs:
        if declared is not None and observed is not None and observed > declared:
            conflicts.append(
                DeclaredVsObservedConflict(
                    dimension=dimension, declared_value=declared, observed_value=observed,
                    note=(
                        f"a real vessel call observed {dimension}={observed} at this port, "
                        f"exceeding the declared limit of {declared} -- disclosed, and the "
                        f"declared limit is NOT raised on the strength of this observation."
                    ),
                )
            )
    return conflicts


def _compute_confidence(
    *, is_feasible: bool | None, tide_impact: TideImpact, wait_sufficient: bool,
    source_quality: SourceQuality | None, untested_count: int, limit_source: str,
) -> float:
    """Documented rollup, not a black box. Starts at 1.0; each real gap in
    evidence subtracts a fixed, stated amount. Floored at 0.0.

    F-13 fix: ``source_quality`` describes the *fact_port_call arrival
    data*'s provenance (the DTR/PDF reports a port's calls were harvested
    from) -- it says nothing about where the berth *constraint* (the draft/
    LOA/beam limit the verdict was actually decided against) came from.
    Those are two independent axes that used to be conflated into one
    penalty: Paradip has real, OFFICIAL_PORT_AUTHORITY-sourced call data
    (so the old ``source_quality is None`` branch never fired) but its
    berth limit is a hardcoded PortEnum literal with no citation at all
    (``limit_source == "PORTENUM_FALLBACK"``) -- verified live, the old
    scoring reported 100% confidence on exactly that fallback constant.
    ``limit_source`` is now penalised directly and separately from
    ``source_quality``, so an unsourced constraint always costs confidence
    regardless of how good the unrelated arrival-data provenance is.
    """
    score = 1.0
    if is_feasible is None:
        score -= 0.4  # constraint check itself could not run at all
    elif limit_source == "PORTENUM_FALLBACK":
        score -= 0.3  # a real verdict, but against an unsourced literal, not a published register
    if tide_impact is not TideImpact.NONE:
        score -= 0.15
    if not wait_sufficient:
        score -= 0.1
    if source_quality is SourceQuality.PUBLIC_AGGREGATOR:
        score -= 0.15
    elif source_quality is None:
        score -= 0.2  # no real arrival-data source at all for this port
    score -= min(0.2, 0.05 * untested_count)
    return max(0.0, round(score, 3))


def get_port_reality(
    port: PortEnum,
    *,
    vessel_draft_m: float,
    vessel_loa_m: float,
    vessel_beam_m: float,
    vessel_dwt: float,
    vessel_class: str | None = None,
    vessel_is_laden: bool | None = None,
    commodity: str | None = None,
    as_of: date | None = None,
    store: FactPortCallStore | None = None,
) -> PortRealityReport:
    """The M4 entry point. Composes:

    1. Constraint resolution -- reuses check_vessel_against_register via the
       same PortId bridge opt.voyage._vessel_can_call uses, falling back to
       the PortEnum literal (via _portenum_fallback_verdict) exactly as
       BT-2 already does, so the constraint half of this report is never a
       second, divergent answer from what the optimizer itself sees.
    2. Tide -- assess_tide() against the binding constraint, if any.
    3. Empirical wait (arrival->berth) and handling -- from fact_port_call,
       gated by the real sufficiency floor.

    Verdict logic:
      - No constraint could be resolved at all (no register, no PortEnum
        limits set) -> CANNOT_VERIFY.
      - Constraint says infeasible -> INFEASIBLE (tide/wait do not override
        a hard geometry failure).
      - Constraint says feasible AND tide is CONDITIONAL -> CANNOT_VERIFY
        (a real, unresolved restriction exists -- never silently upgraded
        to FEASIBLE).
      - Constraint says feasible AND tide is NONE -> FEASIBLE.
    """
    resolved_as_of = as_of if as_of is not None else date.today()  # noqa: DTZ011 -- no as_of context available at this call boundary, matching opt.voyage._vessel_can_call's own documented same choice
    store = store or FactPortCallStore()

    register_port_id = _REGISTER_PORT_ID.get(port)
    binding_constraint: BerthConstraint | None = None
    berth_id: str | None = None
    is_feasible: bool | None
    limit_source = "NONE"
    margin_draft = margin_loa = margin_beam = None
    untested_checks: tuple[str, ...] = ()
    reason: str | None = None

    if register_port_id is not None:
        check = check_vessel_against_register(
            register_port_id, commodity=commodity, as_of=resolved_as_of,
            vessel_draft_m=vessel_draft_m, vessel_loa_m=vessel_loa_m,
            vessel_beam_m=vessel_beam_m, vessel_dwt=vessel_dwt,
        )
        if check is not None:
            is_feasible = check.is_feasible
            berth_id = check.berth_id
            binding_constraint = check.binding_constraint
            limit_source = "REGISTER"
            margin_draft, margin_loa, margin_beam = check.draft_margin_m, check.loa_margin_m, check.beam_margin_m
            untested_checks = check.untested_checks
            reason = check.reason
        else:
            is_feasible = None
            reason = f"{port.name}: register_port_id is mapped but berth_truth has no rows for it at all."
    else:
        fallback = _portenum_fallback_verdict(
            _FallbackVessel(vessel_dwt, vessel_draft_m, vessel_loa_m, vessel_beam_m), port
        )
        is_feasible = fallback.is_feasible
        limit_source = "PORTENUM_FALLBACK"
        margin_draft = fallback.margins.draft_margin_m
        margin_loa = fallback.margins.loa_margin_m
        margin_beam = fallback.margins.beam_margin_m
        reason = fallback.reason

    # F-12: the limit actually used to decide the verdict above, in one
    # place, regardless of which branch produced it -- so the observed-vs-
    # declared conflict check below compares against the SAME number the
    # verdict panel shows, not against nothing (register path with no
    # binding constraint resolved, or the PORTENUM_FALLBACK path, both
    # previously fell through _declared_vs_observed's old None-constraint
    # short-circuit).
    if limit_source == "REGISTER" and binding_constraint is not None:
        effective_draft_limit = binding_constraint.permissible_draft_m
        effective_loa_limit = binding_constraint.max_loa_m
        effective_beam_limit = binding_constraint.max_beam_m
    elif limit_source == "PORTENUM_FALLBACK":
        effective_draft_limit = port.value.max_draft_m
        effective_loa_limit = port.value.max_loa_m
        effective_beam_limit = port.value.max_beam_m
    else:
        effective_draft_limit = effective_loa_limit = effective_beam_limit = None

    tide = (
        assess_tide(binding_constraint, vessel_class=vessel_class, vessel_is_laden=vessel_is_laden)
        if binding_constraint is not None
        else TideAssessment(impact=TideImpact.NONE, authority=None)
    )

    # A draft check specifically going untested (most commonly
    # draft_status=STALE_OR_UNAVAILABLE -- BT-2's documented "the check did
    # not run" state) is the literal edge case this report must not pass
    # through as a silent FEASIBLE: the underlying FeasibilityVerdict/
    # BerthCheckResult correctly treats "nothing tested" as vacuously
    # is_feasible=True (BT-2's own documented, unchanged design for THAT
    # type) and separately discloses the gap via untested_checks -- this
    # report's own, stricter verdict must not launder that gap into an
    # unqualified pass.
    draft_untested = any(check_str.startswith("draft:") for check_str in untested_checks)

    if is_feasible is None:
        verdict = RealityVerdict.CANNOT_VERIFY
    elif not is_feasible:
        verdict = RealityVerdict.INFEASIBLE
    elif draft_untested:
        verdict = RealityVerdict.CANNOT_VERIFY
        reason = reason or "draft check did not run for the binding berth -- see untested_checks."
    elif tide.impact is not TideImpact.NONE:
        verdict = RealityVerdict.CANNOT_VERIFY
        reason = tide.reason or reason
    else:
        verdict = RealityVerdict.FEASIBLE

    wait_dist = compute_wait_distribution(
        port, WaitInterval.ARRIVAL_TO_BERTH, vessel_class=vessel_class, commodity_class=commodity, store=store,
    )
    handling_dist = compute_handling_distribution(port, commodity_class=commodity, store=store)
    envelope = _observed_envelope(port, store)
    conflicts = _declared_vs_observed(
        permissible_draft_m=effective_draft_limit, max_loa_m=effective_loa_limit,
        max_beam_m=effective_beam_limit, envelope=envelope,
    )

    source_quality: SourceQuality | None = None
    port_sources = PORT_SOURCES.get(port, ())
    if port_sources:
        source_quality = port_sources[0].source_quality

    confidence = _compute_confidence(
        is_feasible=is_feasible, tide_impact=tide.impact, wait_sufficient=wait_dist.is_sufficient,
        source_quality=source_quality, untested_count=len(untested_checks), limit_source=limit_source,
    )

    return PortRealityReport(
        port=port, as_of=resolved_as_of, verdict=verdict,
        berth_id=berth_id, binding_constraint=binding_constraint, limit_source=limit_source,
        margin_draft_m=margin_draft, margin_loa_m=margin_loa, margin_beam_m=margin_beam,
        tide=tide,
        wait_arrival_to_berth=wait_dist, handling=handling_dist,
        untested_checks=untested_checks, stale_inputs=(),
        observed_envelope=envelope, declared_vs_observed_conflicts=conflicts,
        source_quality=source_quality, confidence=confidence, reason=reason,
    )


class _FallbackVessel:
    """Minimal stand-in matching the 4 attributes _portenum_fallback_verdict
    reads off opt.types.Vessel -- avoids importing the full Vessel model's
    broader required-field set (fuel consumption, vessel_id, ...) into this
    module purely to check port geometry, which is all this call needs."""

    __slots__ = ("beam_m", "draft_m", "dwt", "loa_m")

    def __init__(self, dwt: float, draft_m: float, loa_m: float, beam_m: float) -> None:
        self.dwt, self.draft_m, self.loa_m, self.beam_m = dwt, draft_m, loa_m, beam_m
