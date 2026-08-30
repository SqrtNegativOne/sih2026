"""Tests for the Monthly Draft Declaration parser and the strict validity
rule, against the real fetched document's text (see the fixture file's own
header comment for provenance)."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from berth_truth.declarations import parse_declaration_text, resolve_draft
from berth_truth.models import DraftStatus, PortId

FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "dhamra_monthly_draft_declaration_2026-08-27.txt"
)
REAL_TEXT = FIXTURE_PATH.read_text(encoding="utf-8")
RETRIEVED_AT = date(2026, 8, 27)


def _parse(text: str = REAL_TEXT):
    return parse_declaration_text(
        text, port_id=PortId.DHAMRA, source_url="https://example.invalid/declaration", retrieved_at=RETRIEVED_AT
    )


class TestParsingTheRealDocument:
    def test_parses_all_31_daily_rows(self) -> None:
        rows = _parse()
        assert len(rows) == 31

    def test_summary_rows_are_skipped_not_parsed_as_data(self) -> None:
        rows = _parse()
        assert all(row.max_sw_arrival_draft_m != 0 for row in rows)  # sanity: no garbage rows
        # "MAX IN MONTH" / "MIN IN MONTH" would parse as day=None if they
        # leaked through; every row has a real December 2017 date instead.
        assert all(row.date.year == 2017 and row.date.month == 12 for row in rows)

    def test_berth_scope_is_extracted_from_the_real_title_line_not_hardcoded(self) -> None:
        rows = _parse()
        assert all(row.berth_scope == ("BB1", "BB2") for row in rows)

    def test_every_row_has_the_same_uniform_value(self) -> None:
        """Real, if unremarkable: every day in this document reads 17.20m."""
        rows = _parse()
        assert all(row.max_sw_arrival_draft_m == 17.20 for row in rows)

    def test_doc_internal_date_is_the_latest_date_the_document_covers(self) -> None:
        rows = _parse()
        assert all(row.doc_internal_date == date(2017, 12, 31) for row in rows)

    def test_date_range_is_the_full_month(self) -> None:
        rows = _parse()
        dates = sorted(row.date for row in rows)
        assert dates[0] == date(2017, 12, 1)
        assert dates[-1] == date(2017, 12, 31)
        assert len(dates) == len(set(dates))  # no duplicate days

    def test_unicode_hyphens_in_real_dates_are_handled(self) -> None:
        """The real document's dates use U+2010 (HYPHEN), not ASCII '-' --
        confirmed by inspecting the fixture file's own bytes. If this parser
        only handled ASCII hyphens, it would silently parse zero rows rather
        than fail loudly, which is exactly the kind of silent gap this test
        exists to catch."""
        assert "‐" in REAL_TEXT  # the fixture genuinely contains it
        rows = _parse()
        assert len(rows) > 0


class TestParsingAFutureMonth:
    """A different month's file, same real shape, to prove the parser
    doesn't hardcode December 2017 or a specific berth pair."""

    def test_a_different_month_and_berth_scope_parses_correctly(self) -> None:
        text = (
            "Mar-26\n"
            "DATE\n"
            "1-Mar-26 16.90\n"
            "2-Mar-26 17.00\n"
            "MAX IN MONTH 17.00\n"
            "MIN IN MONTH 16.90\n"
            "MAX.  SW ARRIVAL DRAFT AT DHAMRA PORT BB1 BERTH\n"
        )
        rows = parse_declaration_text(
            text, port_id=PortId.DHAMRA, source_url="https://example.invalid", retrieved_at=RETRIEVED_AT
        )
        assert len(rows) == 2
        assert all(row.berth_scope == ("BB1",) for row in rows)
        assert rows[0].date == date(2026, 3, 1)
        assert rows[0].doc_internal_date == date(2026, 3, 2)


class TestMissingTitleLineFailsLoudly:
    def test_no_title_line_raises_rather_than_guessing_a_scope(self) -> None:
        text = "1-Dec-17 17.20\n2-Dec-17 17.20\n"
        with pytest.raises(ValueError, match="title line"):
            parse_declaration_text(
                text, port_id=PortId.DHAMRA, source_url="https://example.invalid", retrieved_at=RETRIEVED_AT
            )

    def test_empty_text_returns_empty_not_an_error(self) -> None:
        assert _parse("") == ()


class TestResolveDraftStrictRule:
    def test_exact_date_and_month_match_declares(self) -> None:
        rows = _parse()
        result = resolve_draft(rows, port_id=PortId.DHAMRA, berth_id="BB1", as_of=date(2017, 12, 15))
        assert result.draft_status is DraftStatus.DECLARED
        assert result.permissible_draft_m == 17.20
        assert result.draft_as_of == date(2017, 12, 15)

    def test_2026_query_against_2017_document_is_stale(self) -> None:
        """The exact real-world case this rule exists for."""
        rows = _parse()
        result = resolve_draft(rows, port_id=PortId.DHAMRA, berth_id="BB1", as_of=date(2026, 8, 15))
        assert result.draft_status is DraftStatus.STALE_OR_UNAVAILABLE
        assert result.permissible_draft_m is None
        assert result.draft_as_of is None
        assert result.warning is not None
        assert "2017" in result.warning or "2017-12-31" in result.warning

    def test_no_nearest_match_carry_forward(self) -> None:
        """A date one day outside the declared month must not silently
        borrow the nearest day's value."""
        rows = _parse()
        result = resolve_draft(rows, port_id=PortId.DHAMRA, berth_id="BB2", as_of=date(2018, 1, 1))
        assert result.draft_status is DraftStatus.STALE_OR_UNAVAILABLE
        assert result.permissible_draft_m is None

    def test_berth_outside_scope_is_also_stale_not_not_applicable(self) -> None:
        """resolve_draft itself never returns NOT_APPLICABLE -- that status
        is assigned only by resolver.py, based on a berth's draft_source,
        before this function is even called."""
        rows = _parse()
        result = resolve_draft(rows, port_id=PortId.DHAMRA, berth_id="BB3", as_of=date(2017, 12, 15))
        assert result.draft_status is DraftStatus.STALE_OR_UNAVAILABLE

    def test_wrong_port_is_also_stale(self) -> None:
        rows = _parse()
        result = resolve_draft(rows, port_id=PortId.GANGAVARAM, berth_id="BB1", as_of=date(2017, 12, 15))
        assert result.draft_status is DraftStatus.STALE_OR_UNAVAILABLE

    def test_no_declarations_at_all_is_stale_with_an_explanatory_warning(self) -> None:
        result = resolve_draft((), port_id=PortId.DHAMRA, berth_id="BB1", as_of=date(2026, 8, 15))
        assert result.draft_status is DraftStatus.STALE_OR_UNAVAILABLE
        assert result.permissible_draft_m is None
        assert result.warning is not None

    def test_a_document_dated_the_same_month_but_different_day_still_declares(self) -> None:
        """The rule is 'same calendar month', not 'same document instance
        already used for this exact day' -- any day within a document whose
        internal date falls in the query's month is valid for that day."""
        text = (
            "MAX. SW ARRIVAL DRAFT AT DHAMRA PORT BB1 & BB2 IMPORT BERTH\n"
            "1-Jun-26 15.00\n"
            "15-Jun-26 15.50\n"
            "30-Jun-26 15.90\n"
        )
        rows = parse_declaration_text(
            text, port_id=PortId.DHAMRA, source_url="https://example.invalid", retrieved_at=RETRIEVED_AT
        )
        result = resolve_draft(rows, port_id=PortId.DHAMRA, berth_id="BB2", as_of=date(2026, 6, 15))
        assert result.draft_status is DraftStatus.DECLARED
        assert result.permissible_draft_m == 15.50
