"""Tests for resolve_constraint's own mechanics -- ordering, in-force
filtering, and the candidates/observed_only split -- against a small
synthetic registry, isolated from registry.py's real (and large) seed data.
Real-data correctness is covered separately in test_registry.py.
"""
from __future__ import annotations

from datetime import date

from berth_truth.models import (
    BerthConstraint,
    CommodityClass,
    DraftSource,
    DraftStatus,
    LimitStatus,
    PortId,
    PromulgationCycle,
)
from berth_truth.resolver import resolve_constraint

_TODAY = date(2026, 8, 27)


def _row(
    berth_id: str,
    *,
    published: bool = True,
    observed: bool = True,
    draft: float | None = None,
    displacement: float | None = None,
    commodity: CommodityClass | None = None,
    effective_from: date = date(2020, 1, 1),
    effective_to: date | None = None,
    limit_status: LimitStatus = LimitStatus.PUBLISHED,
) -> BerthConstraint:
    return BerthConstraint(
        port_id=PortId.DHAMRA,
        berth_id=berth_id,
        is_published_constraint_berth=published,
        is_observed_operational_berth=observed,
        limit_status=limit_status,
        permissible_draft_m=draft,
        max_displacement_t=displacement,
        commodity_class=commodity,
        promulgation_cycle=PromulgationCycle.STATIC if published else None,
        draft_source=DraftSource.BPTS_STATIC if draft is not None else DraftSource.NONE,
        source_doc_id="synthetic-test-doc",
        effective_from=effective_from,
        effective_to=effective_to,
        retrieved_at=_TODAY,
    )


class TestInForceFiltering:
    def test_a_row_effective_in_the_future_is_excluded(self) -> None:
        future = _row("A", effective_from=date(2030, 1, 1))
        result = resolve_constraint(PortId.DHAMRA, None, _TODAY, registry=[future])
        assert result.candidates == ()

    def test_a_row_whose_window_has_closed_is_excluded(self) -> None:
        expired = _row("A", effective_from=date(2020, 1, 1), effective_to=date(2021, 1, 1))
        result = resolve_constraint(PortId.DHAMRA, None, _TODAY, registry=[expired])
        assert result.candidates == ()

    def test_a_row_with_no_effective_to_is_open_ended(self) -> None:
        open_ended = _row("A", effective_from=date(2020, 1, 1), effective_to=None)
        result = resolve_constraint(PortId.DHAMRA, None, date(2099, 1, 1), registry=[open_ended])
        assert len(result.candidates) == 1

    def test_boundary_dates_are_inclusive(self) -> None:
        row = _row("A", effective_from=date(2026, 1, 1), effective_to=date(2026, 12, 31))
        start = resolve_constraint(PortId.DHAMRA, None, date(2026, 1, 1), registry=[row])
        end = resolve_constraint(PortId.DHAMRA, None, date(2026, 12, 31), registry=[row])
        assert len(start.candidates) == 1
        assert len(end.candidates) == 1


class TestCandidatesVsObservedOnly:
    def test_published_and_observed_only_are_split_correctly(self) -> None:
        published = _row("A", published=True, observed=True)
        observed_only = _row("B", published=False, observed=True)
        result = resolve_constraint(PortId.DHAMRA, None, _TODAY, registry=[published, observed_only])
        assert {c.constraint.berth_id for c in result.candidates} == {"A"}
        assert {r.berth_id for r in result.observed_only} == {"B"}

    def test_observed_only_berths_never_appear_as_candidates(self) -> None:
        observed_only = _row("B", published=False, observed=True, draft=20.0)
        result = resolve_constraint(PortId.DHAMRA, None, _TODAY, registry=[observed_only])
        assert result.candidates == ()
        assert len(result.observed_only) == 1


class TestOrdering:
    def test_higher_draft_sorts_first_with_no_commodity_preference(self) -> None:
        shallow = _row("SHALLOW", draft=10.0)
        deep = _row("DEEP", draft=18.0)
        result = resolve_constraint(PortId.DHAMRA, None, _TODAY, registry=[shallow, deep])
        assert [c.constraint.berth_id for c in result.candidates] == ["DEEP", "SHALLOW"]

    def test_matching_commodity_outranks_higher_draft_elsewhere(self) -> None:
        deep_no_commodity = _row("DEEP", draft=18.0, commodity=None)
        shallow_matching = _row("SHALLOW", draft=10.0, commodity=CommodityClass.COAL_COKE)
        result = resolve_constraint(
            PortId.DHAMRA, CommodityClass.COAL_COKE, _TODAY,
            registry=[deep_no_commodity, shallow_matching],
        )
        assert result.candidates[0].constraint.berth_id == "SHALLOW"

    def test_a_different_commoditys_priority_berth_ranks_last(self) -> None:
        iron_ore_berth = _row("IRON", draft=18.0, commodity=CommodityClass.IRON_ORE_FINES_PELLETS)
        general_berth = _row("GENERAL", draft=10.0, commodity=None)
        result = resolve_constraint(
            PortId.DHAMRA, CommodityClass.COAL_COKE, _TODAY,
            registry=[iron_ore_berth, general_berth],
        )
        assert [c.constraint.berth_id for c in result.candidates] == ["GENERAL", "IRON"]

    def test_no_draft_falls_back_to_displacement_for_tie_breaking(self) -> None:
        big = _row("BIG", draft=None, displacement=250_000.0)
        small = _row("SMALL", draft=None, displacement=8_000.0)
        result = resolve_constraint(PortId.DHAMRA, None, _TODAY, registry=[big, small])
        assert [c.constraint.berth_id for c in result.candidates] == ["BIG", "SMALL"]

    def test_a_row_with_draft_outranks_a_row_with_only_displacement(self) -> None:
        has_draft = _row("HASDRAFT", draft=1.0, displacement=None)  # deliberately shallow
        no_draft = _row("NODRAFT", draft=None, displacement=999_999.0)  # deliberately huge
        result = resolve_constraint(PortId.DHAMRA, None, _TODAY, registry=[has_draft, no_draft])
        assert result.candidates[0].constraint.berth_id == "HASDRAFT"


class TestDraftResolutionWiring:
    def test_bpts_static_draft_is_not_applicable_but_carries_the_value(self) -> None:
        row = _row("A", draft=14.5)
        result = resolve_constraint(PortId.DHAMRA, None, _TODAY, registry=[row])
        draft = result.candidates[0].draft
        assert draft.draft_status is DraftStatus.NOT_APPLICABLE
        assert draft.permissible_draft_m == 14.5

    def test_daily_declaration_berth_is_looked_up_not_read_from_the_static_field(self) -> None:
        row = BerthConstraint(
            port_id=PortId.DHAMRA,
            berth_id="BB1",
            is_published_constraint_berth=True,
            is_observed_operational_berth=True,
            limit_status=LimitStatus.PUBLISHED,
            permissible_draft_m=None,  # static field genuinely null, as in the real register
            promulgation_cycle=PromulgationCycle.DAILY,
            draft_source=DraftSource.DAILY_DECLARATION,
            source_doc_id="synthetic-test-doc",
            effective_from=date(2020, 1, 1),
            retrieved_at=_TODAY,
        )
        result = resolve_constraint(PortId.DHAMRA, None, _TODAY, registry=[row], declarations=())
        draft = result.candidates[0].draft
        # No declarations supplied -> stale, never silently NOT_APPLICABLE,
        # and never a fabricated draft value.
        assert draft.draft_status is DraftStatus.STALE_OR_UNAVAILABLE
        assert draft.permissible_draft_m is None

    def test_no_source_and_no_value_carries_no_spurious_warning(self) -> None:
        row = _row("A", draft=None, displacement=None)
        result = resolve_constraint(PortId.DHAMRA, None, _TODAY, registry=[row])
        draft = result.candidates[0].draft
        assert draft.draft_status is DraftStatus.NOT_APPLICABLE
        assert draft.permissible_draft_m is None
