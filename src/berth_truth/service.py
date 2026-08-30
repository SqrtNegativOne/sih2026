"""The read-only query surface ``opt.voyage`` and ``opt.fleetmix`` consume.

Everything upstream of this module (registry, declarations, resolver) is
port-data plumbing that knows nothing about vessels or the optimizer.
``check_vessel_against_register`` is where a specific vessel's dimensions
meet a specific port's resolved constraints -- the one new piece of logic
this task adds, deliberately kept out of ``resolver.py`` (which orders
berths, but has no concept of "vessel", by design: the same resolution is
reused by things that aren't vessel clearance, like Feature 4's fragility
sweep).

This module has no import of anything under ``opt`` -- keeping that
direction one-way (opt depends on berth_truth, never the reverse) is why
BT-0's PortId docstring already insists berth_truth doesn't need to know
about ``opt.network.PortEnum``. The PortEnum <-> PortId translation, and the
construction of ``opt.types.FeasibilityVerdict`` itself, are both
``opt.voyage``'s job -- see that module's ``_vessel_can_call``.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date

from berth_truth.models import (
    BerthConstraint,
    CommodityClass,
    ConstraintResolution,
    DraftSource,
    DraftStatus,
    PortId,
    ResolvedBerth,
)
from berth_truth.resolver import resolve_constraint

__all__ = ["BerthCheckResult", "check_vessel_against_register", "infer_commodity_class"]

# Free-text commodity strings this codebase actually uses (CargoParcel.commodity
# is a plain str -- "Coal", "Thermal Coal" appear in real fixtures/demo scripts)
# mapped onto the register's closed CommodityClass vocabulary. Gangavaram's own
# BPTS states berth priority as "Coal/Coke" together, not split by thermal vs
# coking grade, so both map to the same COAL_COKE class -- that IS the source's
# own granularity, not a simplification made here. Anything not recognised maps
# to None (no commodity preference), never a guessed class.
_COMMODITY_KEYWORDS: tuple[tuple[str, CommodityClass], ...] = (
    ("coal", CommodityClass.COAL_COKE),
    ("coke", CommodityClass.COAL_COKE),
    ("iron ore", CommodityClass.IRON_ORE_FINES_PELLETS),
    ("container", CommodityClass.CONTAINER),
)


def infer_commodity_class(commodity: str | None) -> CommodityClass | None:
    """Best-effort mapping from a free-text commodity string to the register's
    CommodityClass. Returns None on no match -- resolve_constraint already
    treats None as "no preference" and still returns every candidate berth,
    just unordered by commodity, so an unrecognised string degrades to a
    sensible answer rather than an error."""
    if not commodity:
        return None
    lowered = commodity.lower()
    for keyword, commodity_class in _COMMODITY_KEYWORDS:
        if keyword in lowered:
            return commodity_class
    return None


@dataclass(frozen=True)
class BerthCheckResult:
    """Everything opt.voyage needs to build a FeasibilityVerdict from one
    register-backed clearance check. Kept out of berth_truth.models
    deliberately -- this shape exists to feed one specific opt-side type,
    not as a general-purpose berth_truth data model.
    """

    is_feasible: bool
    berth_id: str | None
    binding_constraint: BerthConstraint | None
    draft_source: DraftSource | None
    draft_status: DraftStatus | None
    draft_as_of: date | None
    draft_margin_m: float | None
    loa_margin_m: float | None
    beam_margin_m: float | None
    is_soft_limit: bool
    staleness_days: int | None
    untested_checks: tuple[str, ...]
    observed_only_berths: tuple[str, ...]
    reason: str | None


def _staleness_days(constraint: BerthConstraint, as_of: date) -> int | None:
    """Age, in days, of the document backing ``constraint`` as of ``as_of`` --
    doc_internal_date when the source states one (every seeded PUBLISHED row
    does), falling back to effective_from otherwise. This is the number that
    would have flagged Vizag's 2021 table and Dhamra's 2017 declaration as
    exactly what they are before either was ever trusted."""
    reference = constraint.doc_internal_date or constraint.effective_from
    return (as_of - reference).days


def _check_draft(candidate: ResolvedBerth, vessel_draft_m: float) -> tuple[bool | None, float | None, str | None]:
    """(passed, margin_m, untested_reason). passed is None when untested --
    never coerced to True or False, so a caller can't accidentally treat
    'not tested' as 'passed'."""
    draft = candidate.draft
    if draft.permissible_draft_m is None:
        reason = draft.warning or (
            f"no permissible draft available for berth {candidate.constraint.berth_id} "
            f"(draft_status={draft.draft_status.value})"
        )
        return None, None, f"draft: {reason}"
    margin = draft.permissible_draft_m - vessel_draft_m
    return margin >= 0, margin, None


def _check_loa(candidate: ResolvedBerth, vessel_loa_m: float) -> tuple[bool | None, float | None, str | None]:
    limit = candidate.constraint.max_loa_m
    if limit is None:
        return None, None, f"LOA: no max_loa_m published for berth {candidate.constraint.berth_id}"
    margin = limit - vessel_loa_m
    return margin >= 0, margin, None


def _check_beam(candidate: ResolvedBerth, vessel_beam_m: float) -> tuple[bool | None, float | None, str | None]:
    limit = candidate.constraint.max_beam_m
    if limit is None:
        return None, None, f"beam: no max_beam_m published for berth {candidate.constraint.berth_id}"
    margin = limit - vessel_beam_m
    return margin >= 0, margin, None


def _check_size(candidate: ResolvedBerth, vessel_dwt: float) -> tuple[bool | None, str | None]:
    """DWT and displacement are not the same unit and not directly
    comparable. A berth publishing displacement only (Dhamra, Gangavaram's
    own BPTS tables) must never be compared against vessel DWT -- that
    comparison is recorded untested, with the reason saying exactly why,
    rather than silently treated as either a pass or a fail."""
    constraint = candidate.constraint
    if constraint.max_dwt is not None:
        return vessel_dwt <= constraint.max_dwt, None
    if constraint.max_displacement_t is not None:
        return None, (
            f"size: berth {constraint.berth_id} publishes displacement "
            f"({constraint.max_displacement_t:,.0f} t), not DWT -- vessel DWT not compared"
        )
    return None, f"size: no max_dwt or max_displacement_t published for berth {constraint.berth_id}"


def _evaluate_candidate(
    candidate: ResolvedBerth,
    *,
    vessel_draft_m: float,
    vessel_loa_m: float,
    vessel_beam_m: float,
    vessel_dwt: float,
    as_of: date,
) -> BerthCheckResult:
    draft_ok, draft_margin, draft_untested = _check_draft(candidate, vessel_draft_m)
    loa_ok, loa_margin, loa_untested = _check_loa(candidate, vessel_loa_m)
    beam_ok, beam_margin, beam_untested = _check_beam(candidate, vessel_beam_m)
    size_ok, size_untested = _check_size(candidate, vessel_dwt)

    untested = tuple(r for r in (draft_untested, loa_untested, beam_untested, size_untested) if r is not None)

    # A dimension that could not be tested never blocks and never passes it
    # silently -- only dimensions that WERE tested (not None) participate in
    # the pass/fail gate.
    tested_results = [v for v in (draft_ok, loa_ok, beam_ok, size_ok) if v is not None]
    is_feasible = all(tested_results)  # vacuously True if nothing was testable

    reason: str | None = None
    if not is_feasible:
        failing = next(
            label
            for label, ok in (("draft", draft_ok), ("LOA", loa_ok), ("beam", beam_ok), ("size", size_ok))
            if ok is False
        )
        margin_by_label = {"draft": draft_margin, "LOA": loa_margin, "beam": beam_margin}
        margin = margin_by_label.get(failing)
        reason = (
            f"vessel exceeds {failing} limit at berth {candidate.constraint.berth_id} "
            f"by {abs(margin):.2f}" if margin is not None else
            f"vessel exceeds {failing} limit at berth {candidate.constraint.berth_id}"
        )

    return BerthCheckResult(
        is_feasible=is_feasible,
        # Populated whether or not this candidate cleared: "binding" means
        # "the constraint this verdict rests on / was evaluated against,"
        # not "the constraint that was satisfied." A caller redoing its own
        # tolerance-widened comparison on a rejection (opt.fleetmix's relaxed
        # pass) needs exactly this -- the resolved register numbers a failed
        # check used, not the constraint erased the moment it says no.
        berth_id=candidate.constraint.berth_id,
        binding_constraint=candidate.constraint,
        draft_source=candidate.constraint.draft_source,
        draft_status=candidate.draft.draft_status,
        draft_as_of=candidate.draft.draft_as_of,
        draft_margin_m=draft_margin,
        loa_margin_m=loa_margin,
        beam_margin_m=beam_margin,
        is_soft_limit=candidate.constraint.is_soft_limit,
        staleness_days=_staleness_days(candidate.constraint, as_of),
        untested_checks=untested,
        observed_only_berths=(),  # filled in by the caller, once, not per-candidate
        reason=reason,
    )


def check_vessel_against_register(
    port_id: PortId,
    *,
    commodity: str | None,
    as_of: date,
    vessel_draft_m: float,
    vessel_loa_m: float,
    vessel_beam_m: float,
    vessel_dwt: float,
) -> BerthCheckResult | None:
    """Check one vessel against every in-force candidate berth for
    ``port_id``, in the resolver's own priority order, and return the first
    that clears -- or, if none does, the closest-matching failure.

    Returns ``None`` only when the register has nothing at all for this port
    (no published candidates and no observed-only berths either) -- the
    caller's signal to fall back to PortEnum entirely, per requirement 8.
    A port that has observed-only berths but no PUBLISHED ones still returns
    a result (infeasible, with those berths surfaced), because that is real,
    useful information, not "nothing known".
    """
    commodity_class = infer_commodity_class(commodity)
    resolution: ConstraintResolution = resolve_constraint(port_id, commodity_class, as_of)

    if not resolution.candidates and not resolution.observed_only:
        return None

    observed_only_ids = tuple(row.berth_id for row in resolution.observed_only)

    if not resolution.candidates:
        # Every berth this port is known to operate is observed-only -- no
        # citable limit exists anywhere to clear a vessel against.
        return BerthCheckResult(
            is_feasible=False,
            berth_id=None,
            binding_constraint=None,
            draft_source=None,
            draft_status=None,
            draft_as_of=None,
            draft_margin_m=None,
            loa_margin_m=None,
            beam_margin_m=None,
            is_soft_limit=False,
            staleness_days=None,
            untested_checks=(),
            observed_only_berths=observed_only_ids,
            reason=(
                f"{port_id.value} has no published constraint berth for this query -- only "
                f"observed-operational berths with no citable limits: {', '.join(observed_only_ids)}"
            ),
        )

    evaluated = [
        _evaluate_candidate(
            candidate,
            vessel_draft_m=vessel_draft_m,
            vessel_loa_m=vessel_loa_m,
            vessel_beam_m=vessel_beam_m,
            vessel_dwt=vessel_dwt,
            as_of=as_of,
        )
        for candidate in resolution.candidates
    ]

    cleared = next((r for r in evaluated if r.is_feasible), None)
    result = cleared if cleared is not None else evaluated[0]
    return replace(result, observed_only_berths=observed_only_ids)
