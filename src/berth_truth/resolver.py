"""resolve_constraint -- the one function BT-2's feasibility rewiring will
call. Everything else in this package (registry, declarations) exists to
feed this.

Point-in-time by construction: filtering the register to rows whose
[effective_from, effective_to] window contains ``as_of`` means a query for a
past date naturally returns whatever was actually in force then (Dhamra
DPC/06's numbers for a 2025-11-01 query, DPC/07's for a 2026-05-01 one), with
no separate "historical mode" to remember to use.
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from berth_truth.declarations import resolve_draft
from berth_truth.models import (
    BerthConstraint,
    CommodityClass,
    ConstraintResolution,
    DraftDeclaration,
    DraftResolution,
    DraftSource,
    DraftStatus,
    PortId,
    ResolvedBerth,
)
from berth_truth.registry import REGISTRY

__all__ = ["resolve_constraint"]


def _in_force(row: BerthConstraint, as_of: date) -> bool:
    if row.effective_from > as_of:
        return False
    return not (row.effective_to is not None and row.effective_to < as_of)


def _commodity_priority(row: BerthConstraint, requested: CommodityClass | None) -> int:
    """0 = this berth is prioritised for exactly the requested commodity (or
    the caller expressed no preference); 1 = the berth states no commodity
    preference of its own (generally usable); 2 = the berth is prioritised
    for a *different* specific commodity. Never excludes the last group --
    the source documents describe priority, not exclusivity, so a lower-
    ranked berth is still a real candidate, just listed after better-matched
    ones."""
    if requested is None:
        return 0
    if row.commodity_class == requested:
        return 0
    if row.commodity_class is None:
        return 1
    return 2


def _capacity_key(row: BerthConstraint) -> tuple[int, float]:
    """Sorts by permissible_draft_m descending, missing values last -- 'then
    permissive capacity' from the spec. Falls back to max_displacement_t only
    to break ties among rows with no draft at all (e.g. Dhamra's published
    berths, which have none), never to override a real draft comparison."""
    if row.permissible_draft_m is not None:
        return (0, -row.permissible_draft_m)
    if row.max_displacement_t is not None:
        return (1, -row.max_displacement_t)
    return (2, 0.0)


def _resolve_one_draft(
    constraint: BerthConstraint,
    declarations: Sequence[DraftDeclaration],
    as_of: date,
) -> DraftResolution:
    if constraint.draft_source is DraftSource.DAILY_DECLARATION:
        return resolve_draft(
            declarations, port_id=constraint.port_id, berth_id=constraint.berth_id, as_of=as_of
        )
    return DraftResolution(
        draft_status=DraftStatus.NOT_APPLICABLE,
        permissible_draft_m=constraint.permissible_draft_m,
        draft_as_of=None,
        source_doc_id=constraint.source_doc_id if constraint.permissible_draft_m is not None else None,
        warning=(
            None
            if constraint.draft_source is DraftSource.NONE and constraint.permissible_draft_m is None
            else "draft_source is not DAILY_DECLARATION for this berth; the static "
            "register value (if any) applies directly, with no per-date lookup"
        ),
    )


def resolve_constraint(
    port_id: PortId,
    commodity_class: CommodityClass | None,
    as_of: date,
    *,
    declarations: Sequence[DraftDeclaration] = (),
    registry: Sequence[BerthConstraint] | None = None,
) -> ConstraintResolution:
    """Ordered candidate berths in force at ``as_of``, plus the berths this
    port is known to operate with no citable limits at all.

    ``registry`` defaults to the real seeded REGISTRY; overridable so tests
    can resolve against a small synthetic set without touching module-level
    data. ``declarations`` defaults to empty, which is a legitimate input --
    every berth whose draft_source isn't DAILY_DECLARATION doesn't need one,
    and a Dhamra query made without one simply reports BB1/BB2's draft as
    STALE_OR_UNAVAILABLE (correctly: no declaration was supplied to check).
    """
    rows = registry if registry is not None else REGISTRY
    port_rows = [row for row in rows if row.port_id is port_id and _in_force(row, as_of)]

    published = [row for row in port_rows if row.is_published_constraint_berth]
    observed_only = tuple(row for row in port_rows if not row.is_published_constraint_berth)

    published.sort(key=lambda row: (_commodity_priority(row, commodity_class), _capacity_key(row)))

    candidates = tuple(
        ResolvedBerth(constraint=row, draft=_resolve_one_draft(row, declarations, as_of))
        for row in published
    )

    return ConstraintResolution(
        port_id=port_id,
        commodity_class=commodity_class,
        as_of=as_of,
        candidates=candidates,
        observed_only=observed_only,
    )
