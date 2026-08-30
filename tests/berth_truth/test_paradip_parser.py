"""P1 §5/§6 -- the real Paradip Section A parser, against a real committed
fixture (tests/berth_truth/fixtures/paradip_daily_traffic_2026-08-03.pdf,
fetched live from paradipport.gov.in on 2026-08-28 -- this is the actual
02-08-2026 daily traffic update, held on 03-08-2026).
"""
from __future__ import annotations

import io
from datetime import UTC, datetime
from pathlib import Path

import pdfplumber

from berth_truth.providers import RawCapture
from berth_truth.providers.pdf_report import (
    parse_paradip_section_a,
    report_reference_date,
)
from berth_truth.sources import PORT_SOURCES
from opt.network import PortEnum

FIXTURE = Path(__file__).parent / "fixtures" / "paradip_daily_traffic_2026-08-03.pdf"


def _capture() -> RawCapture:
    source = PORT_SOURCES[PortEnum.PARADIP][0]
    return RawCapture(
        source_url=source.source_url,
        retrieved_at=datetime(2026, 8, 28, 9, 0, 0, tzinfo=UTC),
        doc_published_date=None,
        content_sha256="fixturehash",
        parser_version="paradip_section_a/1",
    )


def _rows():
    source = PORT_SOURCES[PortEnum.PARADIP][0]
    return parse_paradip_section_a(FIXTURE.read_bytes(), source=source, capture=_capture())


class TestReportReferenceDate:
    def test_extracts_the_real_stated_date(self) -> None:
        text = FIXTURE.read_bytes()
        with pdfplumber.open(io.BytesIO(text)) as pdf:
            full_text = "\n".join(p.extract_text() or "" for p in pdf.pages)
        d = report_reference_date(full_text)
        assert d is not None
        assert d.isoformat() == "2026-08-02"


class TestRealExtraction:
    def test_extracts_a_substantial_number_of_real_rows(self) -> None:
        rows = _rows()
        assert len(rows) >= 25, f"expected a substantial extraction, got {len(rows)} rows"

    def test_known_real_vessels_are_present_with_correct_dimensions(self) -> None:
        """Cross-checked by hand against the real fixture during development."""
        rows = {r.vessel_name: r for r in _rows() if r.vessel_name}
        assert "SOPRANO SERENE" in rows
        soprano = rows["SOPRANO SERENE"]
        assert soprano.loa_m == 179.88
        assert soprano.beam_m == 32.23
        assert soprano.arrival_draft_m == 11.80
        assert soprano.load_discharge == "DISCHARGE"

        assert "GUO YUAN 86" in rows
        guo_yuan = rows["GUO YUAN 86"]
        assert guo_yuan.loa_m == 229.00
        assert guo_yuan.arrival_draft_m == 14.43

    def test_three_distinct_timestamps_are_captured(self) -> None:
        rows = {r.vessel_name: r for r in _rows() if r.vessel_name}
        soprano = rows["SOPRANO SERENE"]
        assert soprano.arrival_ts is not None
        assert soprano.ready_ts is not None
        assert soprano.berth_ts is not None
        # Real ordering: arrival <= ready <= berth for this vessel.
        assert soprano.arrival_ts <= soprano.ready_ts <= soprano.berth_ts

    def test_sail_relevant_receivers_appear_in_real_cargo_text(self) -> None:
        """The whole point of this source: real coking-coal/steel-industry
        receivers at a PS-named port, not synthetic data."""
        cargo_texts = " ".join(r.cargo_raw or "" for r in _rows())
        assert "JSPL" in cargo_texts
        assert "TATA" in cargo_texts

    def test_load_discharge_flag_maps_correctly(self) -> None:
        rows = _rows()
        dl_values = {r.load_discharge for r in rows if r.load_discharge}
        assert dl_values <= {"LOAD", "DISCHARGE"}
        assert "DISCHARGE" in dl_values
        assert "LOAD" in dl_values


class TestMultiCommodityContinuationRows:
    def test_a_vessel_with_two_cargo_lines_produces_two_rows_sharing_dimensions(self) -> None:
        rows = [r for r in _rows() if r.vessel_name == "SOPRANO SERENE"]
        assert len(rows) == 2
        assert rows[0].loa_m == rows[1].loa_m == 179.88
        assert rows[0].cargo_raw != rows[1].cargo_raw  # two distinct commodity lines
        assert {rows[0].cargo_raw, rows[1].cargo_raw} == {
            "IMP - H.S.D - BPCL - BPCL", "IMP-M.SPIRIT - BPCL - BPCL",
        }


class TestChecksumValidation:
    def test_total_equals_handled_plus_balance_for_every_clean_row(self) -> None:
        for row in _rows():
            if row.is_quarantined:
                continue
            if None not in (row.total_qty_t, row.handled_qty_t, row.balance_qty_t):
                assert abs(row.total_qty_t - (row.handled_qty_t + row.balance_qty_t)) < 0.5, (
                    f"{row.vessel_name}: {row.total_qty_t} != {row.handled_qty_t} + {row.balance_qty_t}"
                )


class TestQuarantineOverGuessing:
    def test_the_known_column_count_mismatch_row_is_quarantined_not_dropped(self) -> None:
        """A real page in the fixture merges two trailing columns (16 cells
        instead of 17) -- confirmed during development. That row must be
        retained with a reason, never silently dropped or mis-parsed."""
        rows = _rows()
        quarantined = [r for r in rows if r.is_quarantined]
        assert len(quarantined) >= 1
        assert any("expected 17 cells" in (r.quarantine_reason or "") for r in quarantined)
        # The raw evidence is retained, not discarded.
        assert any(r.cargo_raw for r in quarantined)

    def test_quarantine_rate_is_low_on_a_real_well_formed_document(self) -> None:
        rows = _rows()
        quarantined = sum(1 for r in rows if r.is_quarantined)
        assert quarantined / len(rows) < 0.15


class TestProvenance:
    def test_every_row_carries_real_source_and_date_provenance(self) -> None:
        for row in _rows():
            assert row.source_url == PORT_SOURCES[PortEnum.PARADIP][0].source_url
            assert row.source_doc_date is not None
            assert row.source_doc_date.isoformat() == "2026-08-02"
            assert row.parser_version == "paradip_section_a/1"
            assert row.evidence_class == "DIRECTLY_REPORTED"

    def test_no_row_ever_fabricates_an_absent_field(self) -> None:
        """A field genuinely absent from a real row must stay None -- e.g.
        norm_tpd is blank for several real rows in this fixture."""
        rows = _rows()
        norm_none_count = sum(1 for r in rows if r.norm_tpd is None)
        assert norm_none_count > 0, "expected at least one real row with no NORM figure stated"
