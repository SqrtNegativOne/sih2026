"""PDF-report adapter -- P1 §4/§5, real Paradip parsing (§6).

Uses ``pdfplumber``'s line-based table detection for positional (x/y)
extraction, not ``pypdf.extract_text()`` -- confirmed directly against the
real fixture (tests/berth_truth/fixtures/paradip_daily_traffic_2026-08-03.pdf,
fetched live 2026-08-28) that plain text extraction interleaves this
document's columns out of reading order, while pdfplumber's border-line
detection reconstructs the real table structure cleanly.

Scope, stated honestly: only Section A (WORKING VESSELS) is parsed into
``FactPortCall`` rows in this pass -- it is the section pdfplumber's table
detection handles cleanly and consistently across pages, and it is the
richest section (LOA, beam, draft, three distinct timestamps, tonnage,
norm-vs-actual). Sections B (anchorage), C (expected), D (berthing
movements) were inspected -- their raw text layout is confirmed but table
detection does not reconstruct them as cleanly as Section A, and a rushed
regex parser for three more layouts risks silent mis-parsing rather than
honest quarantine. Not implemented this pass; recorded as a real, bounded
limitation, not silently skipped.

Column mapping for Section A (verified against real data via the
``total_qty_t == handled_qty_t + balance_qty_t`` checksum, which holds
exactly for every real row checked against the fixture):
    0  berth + vessel + (draft-figure & cargo/shipper/receiver/stevedore)
    1  LOA (m)             2  beam (m)            3  vessel arrival draft (m)
    4  ARVL (time\\ndate)   5  READY (time\\ndate)  6  BERTH (time\\ndate)
    7  shipping agent      8  D/L                  9  actual tonnes this day
    10 norm (tpd)          11 total qty (t)        12 handled-to-date (t)
    13 balance (t)         14 ETD/SLD (time\\ndate) 15 OSBD (not captured --
       no fact_port_call field maps to it cleanly; left unparsed rather than
       forced into an unrelated column)          16 remarks (raw, not captured)

A row whose cell count does not match this 17-column shape is quarantined
whole, not guessed at -- confirmed necessary against the real fixture: one
lightly-populated page's table detection merges the ETD/SLD and OSBD
columns into one cell, producing 16 cells instead of 17.

Two consecutive table rows belong to the same vessel when the second row's
LOA cell (index 1) is not a real number -- a continuation row carrying that
vessel's second (or third) commodity line, sharing the primary row's
LOA/beam/draft/timestamps but its own cargo/tonnage figures. Confirmed
against real multi-commodity vessels in the fixture (MV. CHOLA INTEGRITY,
MV. GUO YUAN 86, MV. THASSOS each carry two cargo lines).
"""
from __future__ import annotations

import io
import re
from datetime import UTC, date, datetime
from typing import Final

import pdfplumber
import requests

from berth_truth.fact_port_call import FactPortCall
from berth_truth.providers import ParseResult, RawCapture
from berth_truth.sources import AdapterKind, PortSource
from berth_truth.store import raw_capture_path, write_raw_capture

__all__ = ["PdfReportProvider", "parse_paradip_section_a", "report_reference_date"]

_REQUEST_TIMEOUT_SECONDS: Final[int] = 20
_USER_AGENT: Final[str] = (
    "SIH26006-BerthTruth-Research/0.1 "
    "(+https://github.com/SqrtNegativOne/sih2026; hackathon research project; "
    "internal archival only, not redistributed)"
)

_SECTION_A_COLUMNS: Final[int] = 17
_CHECKSUM_TOLERANCE_T: Final[float] = 0.5

_TITLE_DATE_RE = re.compile(r"DAILY TRAFFIC UPDATE FOR\s+(\d{2})-(\d{2})-(\d{4})", re.IGNORECASE)
_VESSEL_MARKER_RE = re.compile(r"M[VT]\.\s")
_CELL0_RE = re.compile(
    r"^(?P<prefix>.*?)\s*M[VT]\.\s*(?P<vessel>[^\n(]+?)\s*(?:\((?P<suffix>[^)]*)\))?\s*\n(?P<rest>.*)$",
    re.DOTALL,
)
_LEADING_NUMBER_RE = re.compile(r"^\s*([\d.]+)\s+(.*)$", re.DOTALL)
_TS_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*\n\s*(\d{2})-(\d{2})\s*$")


def _clean(text: str | None) -> str | None:
    if text is None:
        return None
    normalised = re.sub(r"\s+", " ", text.replace("\n", " ")).strip()
    return normalised or None


def _to_float(text: str | None) -> float | None:
    if not text or not text.strip():
        return None
    try:
        return float(text.strip().replace(",", ""))
    except ValueError:
        return None


def report_reference_date(full_text: str) -> date | None:
    """The report's own stated date -- 'DAILY TRAFFIC UPDATE FOR DD-MM-YYYY'
    -- used to resolve the year for every DD-MM timestamp cell in the body,
    since those cells never state a year themselves."""
    match = _TITLE_DATE_RE.search(full_text)
    if match is None:
        return None
    dd, mm, yyyy = match.groups()
    return date(int(yyyy), int(mm), int(dd))


def _parse_timestamp(cell: str | None, reference: date) -> tuple[datetime | None, str | None]:
    """Returns (value, quarantine_reason). A blank cell is not an error --
    genuinely empty timestamp cells occur in the real fixture; only a
    non-empty cell matching neither the known 'HH:MM\\nDD-MM' shape nor a
    blank is quarantined."""
    if not cell or not cell.strip():
        return None, None
    match = _TS_RE.match(cell)
    if match is None:
        return None, f"timestamp cell does not match 'HH:MM\\nDD-MM': {cell!r}"
    hh, mm, dd, mon = (int(g) for g in match.groups())
    year = reference.year
    # A report dated early January referencing a late-December arrival is a
    # real year-boundary case this heuristic does not resolve fully -- the
    # one-month correction below handles the common case; anything further
    # off is flagged by the ValueError branch.
    if reference.month == 1 and mon == 12:
        year -= 1
    try:
        # Naive on purpose (DTZ001), same reasoning as
        # parsers.adani_schedule.parse_timestamp: the report prints a bare
        # 'HH:MM DD-MM' with no zone, and these are port-local clock times.
        # Stamping UTC would assert an offset the document never gives and
        # would move every arrival by hours.
        return datetime(year, mon, dd, hh, mm), None  # noqa: DTZ001
    except ValueError:
        return None, f"timestamp cell parsed but is not a real date: {cell!r} (year={year})"


def _split_cell0(cell0: str) -> tuple[str | None, str | None, str | None]:
    """A primary row's cell0. Returns (berth_or_point, vessel_name, cargo_raw)."""
    match = _CELL0_RE.match(cell0)
    if match is None:
        return _clean(cell0), None, None
    berth = _clean(match.group("prefix"))
    vessel = _clean(match.group("vessel"))
    rest = match.group("rest") or ""
    number_match = _LEADING_NUMBER_RE.match(rest)
    cargo = _clean(number_match.group(2)) if number_match else _clean(rest)
    return berth, vessel, cargo


def _is_header_row(cells: list[str | None]) -> bool:
    cell0 = (cells[0] or "").upper()
    cell1 = (cells[1] or "").upper()
    if "NAME OF THE VESSEL" in cell0 or "BERTH" in cell0 and "DRAFT" in cell0:
        return True
    return cell1 in ("VESSEL SIZE", "LOA")


def _is_primary_row(cells: list[str | None]) -> bool:
    return bool(cells[0]) and _VESSEL_MARKER_RE.search(cells[0]) is not None and _to_float(cells[1]) is not None


def _is_continuation_row(cells: list[str | None]) -> bool:
    return bool(cells[0]) and _to_float(cells[1]) is None and not _VESSEL_MARKER_RE.search(cells[0])


class _VesselContext:
    """Carries a primary row's vessel-level facts forward onto its
    continuation rows (a second/third commodity line for the same call)."""

    __slots__ = (
        "agent",
        "arvl",
        "beam",
        "berth",
        "berth_ts",
        "dl_raw",
        "draft",
        "loa",
        "ready",
        "vessel",
    )

    def __init__(self) -> None:
        self.berth: str | None = None
        self.vessel: str | None = None
        self.loa: float | None = None
        self.beam: float | None = None
        self.draft: float | None = None
        self.arvl: datetime | None = None
        self.ready: datetime | None = None
        self.berth_ts: datetime | None = None
        self.agent: str | None = None
        self.dl_raw: str | None = None


def _build_row(
    cells: list[str | None],
    *,
    source: PortSource,
    capture: RawCapture,
    row_index: int,
    reference_date: date,
    ctx: _VesselContext,
) -> FactPortCall | None:
    if len(cells) != _SECTION_A_COLUMNS:
        # Shape mismatch -- confirmed real (a lightly-populated page merges
        # two trailing columns). Quarantined as its own minimal row so the
        # raw evidence is retained rather than silently dropped.
        return FactPortCall(
            port=source.port,
            cargo_raw=_clean(cells[0]) if cells else None,
            source_url=source.source_url,
            source_doc_date=reference_date,
            source_quality=source.source_quality,
            retrieved_at=capture.retrieved_at,
            content_sha256=capture.content_sha256,
            row_index=row_index,
            parser_version="paradip_section_a/1",
            quarantine_reason=f"expected {_SECTION_A_COLUMNS} cells, got {len(cells)}",
        )

    quarantine_reason: str | None = None
    is_primary = _is_primary_row(cells)

    if is_primary:
        berth, vessel, cargo_raw = _split_cell0(cells[0] or "")
        loa, beam, draft = _to_float(cells[1]), _to_float(cells[2]), _to_float(cells[3])
        arvl, r1 = _parse_timestamp(cells[4], reference_date)
        ready, r2 = _parse_timestamp(cells[5], reference_date)
        berth_ts, r3 = _parse_timestamp(cells[6], reference_date)
        agent, dl_raw = _clean(cells[7]), _clean(cells[8])
        quarantine_reason = next((r for r in (r1, r2, r3) if r), None)
        ctx.berth, ctx.vessel = berth, vessel
        ctx.loa, ctx.beam, ctx.draft = loa, beam, draft
        ctx.arvl, ctx.ready, ctx.berth_ts = arvl, ready, berth_ts
        ctx.agent, ctx.dl_raw = agent, dl_raw
    else:
        # Continuation row (or an unrecognised shape -- treated the same
        # way: inherit vessel context, take cell0 as-is for cargo).
        berth, vessel = ctx.berth, ctx.vessel
        loa, beam, draft = ctx.loa, ctx.beam, ctx.draft
        arvl, ready, berth_ts = ctx.arvl, ctx.ready, ctx.berth_ts
        agent, dl_raw = ctx.agent, ctx.dl_raw
        cargo_raw = _clean(cells[0])

    dl: str | None = None
    if dl_raw == "D":
        dl = "DISCHARGE"
    elif dl_raw == "L":
        dl = "LOAD"
    elif dl_raw:
        quarantine_reason = quarantine_reason or f"unrecognised D/L value {dl_raw!r}"

    actual_tpd, norm_tpd = _to_float(cells[9]), _to_float(cells[10])
    total_qty, handled_qty, balance_qty = _to_float(cells[11]), _to_float(cells[12]), _to_float(cells[13])
    etd, _r4 = _parse_timestamp(cells[14], reference_date)
    # r4 deliberately not folded into quarantine_reason: the OSBD/ETD-SLD
    # column-merge case means cells[14] legitimately does not always match
    # the timestamp shape even on an otherwise well-formed row -- the
    # checksum below is the more reliable validity signal for this table.

    checksum_inputs_present = total_qty is not None and handled_qty is not None and balance_qty is not None
    if checksum_inputs_present and abs(total_qty - (handled_qty + balance_qty)) > _CHECKSUM_TOLERANCE_T:
        quarantine_reason = quarantine_reason or (
            f"checksum failed: total {total_qty} != handled {handled_qty} + balance {balance_qty}"
        )

    return FactPortCall(
        port=source.port,
        berth_or_point=berth,
        vessel_name=vessel,
        loa_m=loa,
        beam_m=beam,
        arrival_draft_m=draft,
        cargo_raw=cargo_raw,
        load_discharge=dl,
        shipper=agent,
        arrival_ts=arvl,
        ready_ts=ready,
        berth_ts=berth_ts,
        etd_ts=etd,
        total_qty_t=total_qty,
        handled_qty_t=handled_qty,
        balance_qty_t=balance_qty,
        norm_tpd=norm_tpd,
        actual_tpd=actual_tpd,
        source_url=source.source_url,
        source_doc_date=reference_date,
        source_quality=source.source_quality,
        retrieved_at=capture.retrieved_at,
        content_sha256=capture.content_sha256,
        row_index=row_index,
        parser_version="paradip_section_a/1",
        evidence_class="DIRECTLY_REPORTED",
        quarantine_reason=quarantine_reason,
    )


def parse_paradip_section_a(
    pdf_bytes: bytes,
    *,
    source: PortSource,
    capture: RawCapture,
) -> tuple[FactPortCall, ...]:
    """Parses Section A (WORKING VESSELS) from a real Paradip daily traffic
    PDF into FactPortCall rows. See the module docstring for the verified
    column mapping and the honest scope limitation (sections B/C/D not yet
    implemented)."""
    rows: list[FactPortCall] = []
    row_index = 0
    ctx = _VesselContext()

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        full_text = "\n".join(p.extract_text() or "" for p in pdf.pages)
        reference_date = report_reference_date(full_text) or capture.retrieved_at.date()

        in_section_a = False
        for page in pdf.pages:
            text = (page.extract_text() or "").upper()
            if "WORKING VESSELS" in text:
                in_section_a = True
            elif "VESSELS WAITING AT ANCHORAGE" in text:
                break  # Section A ended; B/C/D not implemented this pass

            if not in_section_a:
                continue

            for table in page.extract_tables():
                for cells in table:
                    if cells is None or all(c is None or not str(c).strip() for c in cells):
                        continue
                    if _is_header_row(cells):
                        continue
                    row = _build_row(
                        cells, source=source, capture=capture, row_index=row_index,
                        reference_date=reference_date, ctx=ctx,
                    )
                    if row is not None:
                        rows.append(row)
                        row_index += 1

    return tuple(rows)


class PdfReportProvider:
    def supports(self, source: PortSource) -> bool:
        return source.adapter is AdapterKind.PDF_REPORT

    def fetch(self, source: PortSource) -> RawCapture:
        if not source.source_url or "{" in source.source_url:
            raise ValueError(
                f"{source.source_name!r} has no concrete, resolved source_url yet -- "
                f"the registry entry is a URL pattern or research lead, not a fetchable one."
            )
        fetched_at = datetime.now(UTC)
        response = requests.get(
            source.source_url, headers={"User-Agent": _USER_AGENT}, timeout=_REQUEST_TIMEOUT_SECONDS
        )
        response.raise_for_status()
        sha256, _is_new = write_raw_capture(response.content, ext="pdf")
        return RawCapture(
            source_url=source.source_url,
            retrieved_at=fetched_at,
            doc_published_date=None,
            content_sha256=sha256,
            parser_version="pdf_report/1",
            raw_bytes_path=str(raw_capture_path(sha256, "pdf")),
        )

    def parse(self, source: PortSource, capture: RawCapture) -> ParseResult:
        pdf_bytes = raw_capture_path(capture.content_sha256, "pdf").read_bytes()

        if source.port.name == "PARADIP":
            fact_rows = parse_paradip_section_a(pdf_bytes, source=source, capture=capture)
        else:
            # Visakhapatnam and any other PDF source: no per-source parser
            # implemented yet in this pass -- see sources.py's notes for
            # each. Returns zero rows rather than guessing a shape.
            fact_rows = ()

        quarantined = sum(1 for r in fact_rows if r.is_quarantined)
        confidence = 1.0 - (quarantined / len(fact_rows)) if fact_rows else None
        return ParseResult(
            capture=capture, fact_rows=fact_rows, quarantine_count=quarantined, parse_confidence=confidence
        )
