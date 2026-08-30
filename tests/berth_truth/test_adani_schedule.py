"""Tests for the Adani vessel-schedule parser, against real fetched pages.

The two HTML fixtures in fixtures/ are byte-for-byte copies of what
https://www.adaniports.com/ports-and-terminals/{dhamra,gangavaram}-port/vesselschedule
served on 2026-08-27, fetched for this task. Row counts and berth identifiers
asserted below were counted directly from that markup (grep -c on the raw
file, not eyeballed), so a change here should mean the parser regressed, not
that the fixture disagrees with a guess.

The Gangavaram fixture's "Vessels at Anchorage" table is real evidence that a
table can be present with zero rows -- it was observed that way live, not
constructed to make a point. The one genuinely synthetic HTML fragment in
this file (``_MINIMAL_HTML_MISSING_A_TABLE``) is used only to test the
"table absent from the page" branch, which the real fixtures don't exercise
because both real pages had all four tables. It is clearly not real Adani
data and is never used to assert anything about the real ports.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from berth_truth.models import PortId, QuarantinedField, TableKind
from berth_truth.parsers.adani_schedule import parse_snapshot, parse_timestamp

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> str:
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


DHAMRA_HTML = _load_fixture("dhamra_vesselschedule_2026-08-27.html")
GANGAVARAM_HTML = _load_fixture("gangavaram_vesselschedule_2026-08-27.html")

_FETCHED_AT = datetime(2026, 8, 27, 12, 0, 0)


def _parse(html: str, port_id: PortId):
    return parse_snapshot(
        html,
        port_id=port_id,
        source_url="https://example.invalid/fixture",
        fetched_at=_FETCHED_AT,
        content_sha256="0" * 64,
    )


class TestRowCounts:
    """Exact counts, verified against the real markup with grep -c before
    being written here -- not estimated."""

    def test_dhamra_row_counts_per_table(self) -> None:
        snapshot = _parse(DHAMRA_HTML, PortId.DHAMRA)
        counts = {
            kind: sum(1 for row in snapshot.rows if row.table_kind is kind) for kind in TableKind
        }
        assert counts == {
            TableKind.AT_BERTH: 18,  # 12 named (incl. DHS1 x5) + 6 vacant
            TableKind.AT_ANCHORAGE: 6,
            TableKind.EXPECTED: 5,
            TableKind.SAILED_24H: 5,
        }

    def test_gangavaram_row_counts_per_table(self) -> None:
        snapshot = _parse(GANGAVARAM_HTML, PortId.GANGAVARAM)
        counts = {
            kind: sum(1 for row in snapshot.rows if row.table_kind is kind) for kind in TableKind
        }
        assert counts == {
            TableKind.AT_BERTH: 9,  # 5 named + 4 vacant
            TableKind.AT_ANCHORAGE: 0,
            TableKind.EXPECTED: 14,
            TableKind.SAILED_24H: 1,
        }

    def test_neither_real_fixture_needed_row_level_quarantine(self) -> None:
        """A __row__ quarantine entry would mean the header/column mapping
        didn't match what was verified live -- it shouldn't happen on either
        real fixture."""
        for html, port in [(DHAMRA_HTML, PortId.DHAMRA), (GANGAVARAM_HTML, PortId.GANGAVARAM)]:
            snapshot = _parse(html, port)
            assert not any(q.field_name == "__row__" for q in snapshot.quarantined_fields)


class TestAbsentVsPresentButEmpty:
    """These are different facts and must be distinguishable in the result."""

    def test_gangavaram_anchorage_is_present_and_empty(self) -> None:
        """Real, observed live on 2026-08-27: the table exists on the page
        with a real <thead> and an empty <tbody>."""
        snapshot = _parse(GANGAVARAM_HTML, PortId.GANGAVARAM)
        assert TableKind.AT_ANCHORAGE in snapshot.tables_present
        assert TableKind.AT_ANCHORAGE in snapshot.tables_empty

    def test_a_table_missing_from_the_page_is_absent_not_empty(self) -> None:
        """Synthetic fragment (not real Adani data): the same page template
        with the 'Vessels Expected' heading and table removed entirely, to
        exercise a branch neither real fixture reaches."""
        synthetic_missing_expected = """
        <html><body>
        <div class="container mb-5">
            <h2 class="heading pl-4 mb-4">Vessels at Berth</h2>
            <table class="table bg-gray" id="TrackingDataTableDahejBerth">
                <thead><tr>
                    <th>Berth no.</th><th>Vessels Name</th><th>Imp or Exp</th>
                    <th>Cargo</th><th>Expected Time of Completion (ETC)</th>
                </tr></thead>
                <tbody>
                    <tr><td>B1</td><td>VACANT</td><td>VACANT</td><td></td><td></td></tr>
                </tbody>
            </table>
        </div>
        </body></html>
        """
        snapshot = _parse(synthetic_missing_expected, PortId.GANGAVARAM)
        assert TableKind.AT_BERTH in snapshot.tables_present
        assert TableKind.EXPECTED not in snapshot.tables_present
        assert TableKind.EXPECTED not in snapshot.tables_empty  # never claimed empty either

    def test_present_and_empty_is_not_the_same_result_as_absent(self) -> None:
        """Direct contrast, same assertion style a caller would actually use."""
        present_and_empty = _parse(GANGAVARAM_HTML, PortId.GANGAVARAM)
        absent = _parse(
            '<h2 class="heading">Vessels at Berth</h2><table><thead><tr><th>x</th></tr>'
            "</thead><tbody></tbody></table>",
            PortId.DHAMRA,
        )
        assert TableKind.AT_ANCHORAGE in present_and_empty.tables_present
        assert TableKind.AT_ANCHORAGE not in absent.tables_present
        # Both are "zero AT_ANCHORAGE rows" if you only look at row counts --
        # this is exactly why tables_present/tables_empty must be separate.
        anchorage_rows_present_case = [
            r for r in present_and_empty.rows if r.table_kind is TableKind.AT_ANCHORAGE
        ]
        anchorage_rows_absent_case = [r for r in absent.rows if r.table_kind is TableKind.AT_ANCHORAGE]
        assert anchorage_rows_present_case == anchorage_rows_absent_case == []


class TestVacantRows:
    def test_vacant_berth_row_has_null_name_not_the_literal_string(self) -> None:
        """Real row from the Dhamra fixture: BB2E, one of the six berths
        with no published constraint, confirmed operating only because it
        showed up here as VACANT rather than being absent from the table."""
        snapshot = _parse(DHAMRA_HTML, PortId.DHAMRA)
        bb2e_rows = [
            r for r in snapshot.rows if r.table_kind is TableKind.AT_BERTH and r.berth_no == "BB2E"
        ]
        assert len(bb2e_rows) == 1
        row = bb2e_rows[0]
        assert row.is_vacant is True
        assert row.vessel_name is None  # not the string "VACANT"
        assert row.imp_exp is None
        assert row.etc_ts is None

    def test_occupied_berth_row_is_not_marked_vacant(self) -> None:
        snapshot = _parse(DHAMRA_HTML, PortId.DHAMRA)
        bb1_rows = [
            r for r in snapshot.rows if r.table_kind is TableKind.AT_BERTH and r.berth_no == "BB1"
        ]
        assert len(bb1_rows) == 1
        row = bb1_rows[0]
        assert row.is_vacant is False
        assert row.vessel_name == "MV CAPE CRANE"
        assert row.cargo_raw == "COKING COAL"
        assert row.etc_ts == datetime(2026, 8, 30, 10, 0)

    def test_all_six_dhamra_vacant_berths_present(self) -> None:
        """The six real berths confirmed operating with no published
        constraint (BT-1's PUBLISHED_CONSTRAINT_BERTH /
        OBSERVED_OPERATIONAL_BERTH split depends on this list being right)."""
        snapshot = _parse(DHAMRA_HTML, PortId.DHAMRA)
        vacant_berths = {
            r.berth_no
            for r in snapshot.rows
            if r.table_kind is TableKind.AT_BERTH and r.is_vacant
        }
        assert vacant_berths == {"BB2E", "BB3B", "BB3N", "BB4N", "BRGB", "LNGT"}


class TestBerthIdentity:
    def test_dhamra_yields_all_14_observed_berth_identifiers_unaliased(self) -> None:
        snapshot = _parse(DHAMRA_HTML, PortId.DHAMRA)
        assert snapshot.observed_berth_ids == {
            "B3AS", "BB1", "BB2", "BB3", "BB3A", "BB4", "BB5",
            "DHS1", "BB2E", "BB3B", "BB3N", "BB4N", "BRGB", "LNGT",
        }
        # The whole point: these are stored as distinct strings, never merged
        # by name similarity onto a "parent" berth.
        assert "BB3" in snapshot.observed_berth_ids
        assert "BB3A" in snapshot.observed_berth_ids
        assert "BB3N" in snapshot.observed_berth_ids
        assert "BB3B" in snapshot.observed_berth_ids
        assert len({"BB3", "BB3A", "BB3N", "BB3B"}) == 4  # confirms none collapsed together

    def test_gangavaram_berth_ids_match_its_own_numbering(self) -> None:
        snapshot = _parse(GANGAVARAM_HTML, PortId.GANGAVARAM)
        assert snapshot.observed_berth_ids == {f"B{i}" for i in range(1, 10)}

    def test_dhs1_appearing_five_times_counts_once_as_an_identifier(self) -> None:
        """DHS1 hosts five different LPG vessels in this snapshot -- a real
        multi-vessel STS berth, not five different berths."""
        snapshot = _parse(DHAMRA_HTML, PortId.DHAMRA)
        dhs1_rows = [r for r in snapshot.rows if r.berth_no == "DHS1"]
        assert len(dhs1_rows) == 5
        assert "DHS1" in snapshot.observed_berth_ids


class TestTimestampParsing:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("27.08.2026 21:00", datetime(2026, 8, 27, 21, 0)),
            ("27-08-2026 21:00", datetime(2026, 8, 27, 21, 0)),
            ("11-08-2026 18:42", datetime(2026, 8, 11, 18, 42)),
            ("26.08.2026 16:00", datetime(2026, 8, 26, 16, 0)),
        ],
    )
    def test_both_real_formats_parse(self, raw: str, expected: datetime) -> None:
        value, reason = parse_timestamp(raw)
        assert value == expected
        assert reason is None

    def test_blank_is_not_an_error(self) -> None:
        """A vacant berth's ETC cell is genuinely blank -- that's not a
        parse failure, it's an expected absence."""
        value, reason = parse_timestamp("")
        assert value is None
        assert reason is None

    def test_a_third_format_quarantines_rather_than_guessing(self) -> None:
        value, reason = parse_timestamp("2026/08/27 21:00")
        assert value is None
        assert reason is not None
        assert "2026/08/27" not in (reason or "")  # reason explains, doesn't just echo

    def test_garbage_quarantines_rather_than_guessing(self) -> None:
        value, reason = parse_timestamp("not a date")
        assert value is None
        assert reason is not None

    def test_end_to_end_quarantine_keeps_the_rest_of_the_row(self) -> None:
        """A bad timestamp must not discard the vessel/berth identity sitting
        in the same row. Synthetic single-row fragment, not real data."""
        synthetic_bad_timestamp = """
        <h2 class="heading">Vessels at Berth</h2>
        <table><thead><tr>
            <th>Berth no.</th><th>Vessels Name</th><th>Imp or Exp</th>
            <th>Cargo</th><th>Expected Time of Completion (ETC)</th>
        </tr></thead><tbody>
            <tr><td>BB1</td><td>MV TEST</td><td>I</td><td>COAL</td><td>27/08/2026 not-a-time</td></tr>
        </tbody></table>
        """
        snapshot = _parse(synthetic_bad_timestamp, PortId.DHAMRA)
        assert len(snapshot.rows) == 1
        row = snapshot.rows[0]
        assert row.berth_no == "BB1"
        assert row.vessel_name == "MV TEST"
        assert row.cargo_raw == "COAL"
        assert row.etc_ts is None  # not guessed

        assert len(snapshot.quarantined_fields) == 1
        q: QuarantinedField = snapshot.quarantined_fields[0]
        assert q.table_kind is TableKind.AT_BERTH
        assert q.field_name == "etc_ts"
        assert q.raw_value == "27/08/2026 not-a-time"


class TestEntitiesAndWhitespace:
    def test_html_entity_in_cargo_is_decoded(self) -> None:
        """Real cell from the Gangavaram fixture: CONTAINER 20' GP LOADED,
        encoded on the page as &#39; for the apostrophe."""
        snapshot = _parse(GANGAVARAM_HTML, PortId.GANGAVARAM)
        b9_rows = [
            r for r in snapshot.rows if r.table_kind is TableKind.AT_BERTH and r.berth_no == "B9"
        ]
        assert len(b9_rows) == 1
        assert b9_rows[0].cargo_raw == "CONTAINER 20' GP LOADED"

    def test_trailing_whitespace_in_a_real_cell_is_trimmed(self) -> None:
        """Real cell from the Gangavaram fixture: '<td>MV ARKTOS </td>' has
        a genuine trailing space in the source markup."""
        snapshot = _parse(GANGAVARAM_HTML, PortId.GANGAVARAM)
        arktos_rows = [r for r in snapshot.rows if r.vessel_name == "MV ARKTOS"]
        assert len(arktos_rows) == 1  # matches exactly, no trailing space survived


class TestSnapshotIdentity:
    def test_content_sha256_and_source_are_carried_through(self) -> None:
        snapshot = _parse(DHAMRA_HTML, PortId.DHAMRA)
        assert snapshot.port_id is PortId.DHAMRA
        assert snapshot.content_sha256 == "0" * 64
        assert snapshot.fetched_at == _FETCHED_AT

    def test_parse_confidence_is_full_when_all_tables_found_no_quarantine(self) -> None:
        snapshot = _parse(DHAMRA_HTML, PortId.DHAMRA)
        assert snapshot.tables_present == frozenset(TableKind)
        assert snapshot.quarantined_fields == ()
        assert snapshot.parse_confidence == 1.0

    def test_parse_confidence_drops_when_a_table_is_absent(self) -> None:
        synthetic_missing_expected = """
        <h2 class="heading">Vessels at Berth</h2>
        <table><thead><tr><th>Berth no.</th><th>Vessels Name</th><th>Imp or Exp</th>
        <th>Cargo</th><th>Expected Time of Completion (ETC)</th></tr></thead>
        <tbody><tr><td>B1</td><td>VACANT</td><td>VACANT</td><td></td><td></td></tr></tbody></table>
        """
        snapshot = _parse(synthetic_missing_expected, PortId.DHAMRA)
        assert snapshot.tables_present == frozenset({TableKind.AT_BERTH})
        assert snapshot.parse_confidence == pytest.approx(1 / 4)
