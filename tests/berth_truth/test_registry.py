"""Tests for the constraint register and resolver, against the real seeded
data in registry.py -- every value asserted here traces to a cited document
(see registry.py's own citations), not to a value invented for the test.
"""
from __future__ import annotations

from datetime import date

import pytest

from berth_truth.models import (
    CommodityClass,
    DraftStatus,
    LimitStatus,
    PortId,
    PromulgationCycle,
)
from berth_truth.registry import (
    DHAMRA_ROWS,
    GANGAVARAM_ROWS,
    REGISTRY,
    VISAKHAPATNAM_ROWS,
    rows_for_port,
)
from berth_truth.resolver import resolve_constraint

TODAY = date(2026, 8, 27)


class TestGangavaramCommodityRouting:
    def test_coal_coke_resolves_to_berths_5_and_6(self) -> None:
        result = resolve_constraint(PortId.GANGAVARAM, CommodityClass.COAL_COKE, TODAY)
        top_two = {c.constraint.berth_id for c in result.candidates[:2]}
        assert top_two == {"B5", "B6"}
        for c in result.candidates[:2]:
            assert c.constraint.permissible_draft_m == 18.0

    def test_berth_4_designed_depth_differs_from_permissible_draft(self) -> None:
        berth_4 = next(r for r in GANGAVARAM_ROWS if r.berth_id == "B4")
        assert berth_4.designed_depth_m == 19.5
        assert berth_4.permissible_draft_m == 17.7
        assert berth_4.designed_depth_m != berth_4.permissible_draft_m

    def test_iron_ore_prioritises_berth_4_first(self) -> None:
        result = resolve_constraint(PortId.GANGAVARAM, CommodityClass.IRON_ORE_FINES_PELLETS, TODAY)
        assert result.candidates[0].constraint.berth_id == "B4"

    def test_container_prioritises_berth_9_first(self) -> None:
        result = resolve_constraint(PortId.GANGAVARAM, CommodityClass.CONTAINER, TODAY)
        assert result.candidates[0].constraint.berth_id == "B9"

    def test_no_commodity_preference_orders_by_draft_alone(self) -> None:
        result = resolve_constraint(PortId.GANGAVARAM, None, TODAY)
        drafts = [c.constraint.permissible_draft_m for c in result.candidates]
        assert drafts == sorted(drafts, reverse=True)

    def test_a_lower_priority_berth_is_still_a_candidate_not_excluded(self) -> None:
        """The BPTS describes priority, not exclusivity -- a coal request
        must still see berth 4 (iron-ore priority) as a real option, just
        ranked behind 5 and 6."""
        result = resolve_constraint(PortId.GANGAVARAM, CommodityClass.COAL_COKE, TODAY)
        assert "B4" in {c.constraint.berth_id for c in result.candidates}


class TestGangavaramTidalRestriction:
    def test_berths_5_and_6_carry_a_tide_rule(self) -> None:
        for berth_id in ("B5", "B6"):
            row = next(r for r in GANGAVARAM_ROWS if r.berth_id == berth_id)
            assert row.tide_rule is not None
            assert "Cape size" in row.tide_rule

    def test_berth_9_has_no_tidal_restriction_text(self) -> None:
        row = next(r for r in GANGAVARAM_ROWS if r.berth_id == "B9")
        assert row.tide_rule is not None
        assert "any time" in row.tide_rule.lower()
        assert "restriction" not in row.tide_rule.lower()


class TestDraftValidityRule:
    def test_stale_declaration_returns_null_draft_and_stale_status(self) -> None:
        """The literal BT-1 requirement: as_of 2026-08-15 against the real
        Dec-2017 declaration must return permissible_draft_m null and
        draft_status STALE_OR_UNAVAILABLE -- not a nearest-match, not the
        stale value carried forward."""
        result = resolve_constraint(PortId.DHAMRA, None, date(2026, 8, 15))
        bb1 = next(c for c in result.candidates if c.constraint.berth_id == "BB1")
        assert bb1.draft.permissible_draft_m is None
        assert bb1.draft.draft_status is DraftStatus.STALE_OR_UNAVAILABLE

    def test_a_declaration_matching_date_and_month_returns_declared(self) -> None:
        from berth_truth.declarations import parse_declaration_text

        text = (
            "MAX. SW ARRIVAL DRAFT AT DHAMRA PORT BB1 & BB2 IMPORT BERTH\n"
            "1-Jan-26 16.80\n"
        )
        declarations = parse_declaration_text(
            text, port_id=PortId.DHAMRA, source_url="https://example.invalid", retrieved_at=TODAY
        )
        result = resolve_constraint(
            PortId.DHAMRA, None, date(2026, 1, 1), declarations=declarations
        )
        bb1 = next(c for c in result.candidates if c.constraint.berth_id == "BB1")
        assert bb1.draft.draft_status is DraftStatus.DECLARED
        assert bb1.draft.permissible_draft_m == 16.80


class TestObservedOnlyBerths:
    def test_dhamra_bb5_exists_as_observed_only(self) -> None:
        bb5 = next(r for r in DHAMRA_ROWS if r.berth_id == "BB5")
        assert bb5.is_observed_operational_berth is True
        assert bb5.is_published_constraint_berth is False
        assert bb5.limit_status is LimitStatus.NOT_PUBLISHED
        assert bb5.max_loa_m is None
        assert bb5.max_beam_m is None
        assert bb5.permissible_draft_m is None
        assert bb5.max_displacement_t is None
        assert bb5.max_dwt is None

    @pytest.mark.parametrize("berth_id", ["BB5", "BB2E", "BB3B", "BB3N", "BB4N", "B3AS", "DHS1"])
    def test_every_observed_only_berth_has_all_dimensions_null(self, berth_id: str) -> None:
        row = next(r for r in DHAMRA_ROWS if r.berth_id == berth_id and not r.is_published_constraint_berth)
        assert row.channel_depth_m is None
        assert row.designed_depth_m is None
        assert row.permissible_draft_m is None
        assert row.max_loa_m is None
        assert row.max_beam_m is None
        assert row.max_displacement_t is None
        assert row.max_dwt is None

    def test_observed_only_berths_appear_in_observed_only_not_candidates(self) -> None:
        result = resolve_constraint(PortId.DHAMRA, None, TODAY)
        candidate_ids = {c.constraint.berth_id for c in result.candidates}
        observed_ids = {r.berth_id for r in result.observed_only}
        assert observed_ids == {"BB5", "BB2E", "BB3B", "BB3N", "BB4N", "B3AS", "DHS1"}
        assert candidate_ids.isdisjoint(observed_ids)


class TestNoInheritanceAcrossBerths:
    def test_bb3n_does_not_inherit_from_bb3(self) -> None:
        bb3 = next(r for r in DHAMRA_ROWS if r.berth_id == "BB3" and r.limit_status is LimitStatus.PUBLISHED)
        bb3n = next(r for r in DHAMRA_ROWS if r.berth_id == "BB3N")
        assert bb3.max_loa_m == 350.0
        assert bb3n.max_loa_m is None  # not 350.0 -- never inherited
        assert bb3n.is_published_constraint_berth is False

    def test_bb5_does_not_inherit_from_bb4(self) -> None:
        bb4 = next(r for r in DHAMRA_ROWS if r.berth_id == "BB4")
        bb5 = next(r for r in DHAMRA_ROWS if r.berth_id == "BB5")
        assert bb4.max_loa_m == 347.0
        assert bb5.max_loa_m is None  # not 347.0

    def test_b3as_does_not_inherit_from_bb3a(self) -> None:
        bb3a = next(r for r in DHAMRA_ROWS if r.berth_id == "BB3A")
        b3as = next(r for r in DHAMRA_ROWS if r.berth_id == "B3AS")
        assert bb3a.max_loa_m == 350.0
        assert b3as.max_loa_m is None
        assert b3as.is_published_constraint_berth is False

    def test_all_fourteen_dhamra_identifiers_are_distinct_rows(self) -> None:
        """Confirms the register's berth_id set matches BT-0's real observed
        set exactly -- 7 published (current edition) + 7 observed-only."""
        current_edition_ids = {
            r.berth_id for r in DHAMRA_ROWS if r.limit_status is LimitStatus.PUBLISHED
        }
        observed_only_ids = {
            r.berth_id for r in DHAMRA_ROWS if not r.is_published_constraint_berth
        }
        assert current_edition_ids == {"BB1", "BB2", "BB3", "BB3A", "BB4", "BRGB", "LNGT"}
        assert observed_only_ids == {"BB5", "BB2E", "BB3B", "BB3N", "BB4N", "B3AS", "DHS1"}
        assert len(current_edition_ids | observed_only_ids) == 14


class TestPointInTimeEditions:
    def test_bb4_exists_as_of_dpc07_effective_date_not_before(self) -> None:
        after = resolve_constraint(PortId.DHAMRA, None, date(2026, 4, 1))
        before = resolve_constraint(PortId.DHAMRA, None, date(2025, 11, 1))
        assert "BB4" in {c.constraint.berth_id for c in after.candidates}
        assert "BB4" not in {c.constraint.berth_id for c in before.candidates}

    def test_dpc06_row_is_retained_with_superseded_status_and_effective_to(self) -> None:
        dpc06_bb1 = next(
            r for r in DHAMRA_ROWS
            if r.berth_id == "BB1" and r.source_doc_id == "Dhamra-BPTS-DPC-06"
        )
        assert dpc06_bb1.limit_status is LimitStatus.SUPERSEDED
        assert dpc06_bb1.effective_to is not None
        assert dpc06_bb1.effective_to < date(2026, 4, 1)

    def test_a_past_query_resolves_to_the_superseded_edition_not_the_current_one(self) -> None:
        result = resolve_constraint(PortId.DHAMRA, None, date(2025, 11, 1))
        bb1 = next(c for c in result.candidates if c.constraint.berth_id == "BB1")
        assert bb1.constraint.source_doc_id == "Dhamra-BPTS-DPC-06"
        assert bb1.constraint.limit_status is LimitStatus.SUPERSEDED

    def test_dpc07_cites_dpc06_as_the_document_it_supersedes(self) -> None:
        dpc07_bb1 = next(
            r for r in DHAMRA_ROWS
            if r.berth_id == "BB1" and r.source_doc_id == "Dhamra-BPTS-DPC-07"
        )
        assert dpc07_bb1.supersedes_doc_id == "Dhamra-BPTS-DPC-06"

    def test_dpc06_has_no_bb4_row_at_all(self) -> None:
        dpc06_berths = {
            r.berth_id for r in DHAMRA_ROWS if r.source_doc_id == "Dhamra-BPTS-DPC-06"
        }
        assert "BB4" not in dpc06_berths


class TestInternalConflict:
    def test_bb4_records_the_section19_vs_section20_conflict(self) -> None:
        bb4 = next(
            r for r in DHAMRA_ROWS
            if r.berth_id == "BB4" and r.limit_status is LimitStatus.PUBLISHED
        )
        assert bb4.internal_conflict is not None
        assert "BB3A" in bb4.internal_conflict

    def test_other_dhamra_berths_carry_no_conflict_note(self) -> None:
        bb1 = next(
            r for r in DHAMRA_ROWS
            if r.berth_id == "BB1" and r.limit_status is LimitStatus.PUBLISHED
        )
        assert bb1.internal_conflict is None


class TestVisakhapatnamSuperseded:
    def test_every_vizag_row_is_marked_superseded(self) -> None:
        assert VISAKHAPATNAM_ROWS  # non-empty: seeded, not skipped entirely
        assert all(r.limit_status is LimitStatus.SUPERSEDED for r in VISAKHAPATNAM_ROWS)

    def test_vizag_still_resolves_candidates(self) -> None:
        """DONE WHEN requires the register to resolve for Vizag -- it does,
        the results are just all SUPERSEDED, which is the honest state."""
        result = resolve_constraint(PortId.VISAKHAPATNAM, None, TODAY)
        assert len(result.candidates) > 0
        assert all(c.constraint.limit_status is LimitStatus.SUPERSEDED for c in result.candidates)

    def test_or1_has_no_dimensions_and_documents_the_conflicting_evidence(self) -> None:
        or1 = next(r for r in VISAKHAPATNAM_ROWS if r.berth_id == "OR-1")
        assert or1.max_loa_m is None
        assert or1.permissible_draft_m is None
        assert or1.internal_conflict is not None
        assert "Decommissioned" in or1.internal_conflict
        assert "2026-05-12" in or1.internal_conflict

    def test_vgcb_is_flagged_coal_coke(self) -> None:
        vgcb = next(r for r in VISAKHAPATNAM_ROWS if r.berth_id == "VGCB")
        assert vgcb.commodity_class is CommodityClass.COAL_COKE
        assert vgcb.permissible_draft_m == 18.1


class TestEverySeededRowIsCited:
    def test_every_row_has_a_nonempty_source_doc_id(self) -> None:
        for row in REGISTRY:
            assert row.source_doc_id
            assert row.source_doc_id.strip() != ""

    def test_every_row_has_an_effective_from(self) -> None:
        for row in REGISTRY:
            assert row.effective_from is not None

    def test_no_row_has_permissible_draft_populated_from_designed_depth(self) -> None:
        """A regression guard for the data rule itself: nowhere in the seed
        data should permissible_draft_m equal designed_depth_m when both are
        populated and the source actually distinguishes them (Gangavaram
        berths 4/5/6 are the only rows with both set)."""
        for row in REGISTRY:
            if row.designed_depth_m is not None and row.permissible_draft_m is not None:
                assert row.designed_depth_m != row.permissible_draft_m or row.port_id != PortId.GANGAVARAM


class TestUnknownPort:
    def test_rows_for_port_on_an_unseeded_port_returns_empty(self) -> None:
        """No fourth PortId member exists to test this against directly, so
        this proves the mechanism (filtering, not a lookup that could KeyError)
        rather than a literal unknown-enum-value case."""
        assert rows_for_port(PortId.VISAKHAPATNAM) == VISAKHAPATNAM_ROWS  # sanity: not accidentally empty
        empty_registry_result = resolve_constraint(PortId.GANGAVARAM, None, TODAY, registry=())
        assert empty_registry_result.candidates == ()
        assert empty_registry_result.observed_only == ()


class TestPromulgationCycleConsistency:
    def test_not_published_rows_have_no_promulgation_cycle(self) -> None:
        for row in REGISTRY:
            if row.limit_status is LimitStatus.NOT_PUBLISHED:
                assert row.promulgation_cycle is None

    def test_bb1_bb2_are_daily_declaration_others_are_not(self) -> None:
        for row in DHAMRA_ROWS:
            if row.limit_status is not LimitStatus.PUBLISHED:
                continue
            from berth_truth.models import DraftSource
            if row.berth_id in ("BB1", "BB2"):
                assert row.draft_source is DraftSource.DAILY_DECLARATION
                assert row.promulgation_cycle is PromulgationCycle.DAILY
            else:
                assert row.draft_source is DraftSource.NONE
