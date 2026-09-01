"""Parser for Adani's live vessel-schedule pages (Dhamra, Gangavaram).

Both ports are served from the same page template. Confirmed by fetching
both real pages: every ``<table>`` on both carries the identical
``id="TrackingDataTableDahej..."`` attribute (a leftover from a *different*
Adani port's template), so table ``id`` cannot be used to tell a table apart
-- only the ``<h2>`` heading text immediately before it can. This parser
locates tables by heading, never by id.

One parser, port-configured by nothing more than the URL passed in --
the page markup, headings and column layout are identical for both ports.
The only real per-port difference observed is berth-identifier vocabulary
(Dhamra: BB1, BB2, ..., DHS1; Gangavaram: B1, ..., B9), which this module
does not need to know about: it stores whatever string was in the cell.

Two real quirks this parser must handle, both confirmed in the live markup
rather than assumed:

* A vacant berth is rendered as a row whose vessel-name *and* imp/exp cells
  both literally contain the text ``"VACANT"`` -- not an empty cell, not a
  boolean flag. ``berth_no`` is still populated and is exactly how the six
  Dhamra berths with no published constraint (BB2E, BB3B, BB3N, BB4N, BRGB,
  LNGT) were first confirmed to be operating at all.
* Timestamps mix two formats *within the same page*: Dhamra's "at berth" and
  "sailed" tables use ``DD.MM.YYYY HH:MM`` (dots), while its "at anchorage"
  and "expected" tables use ``DD-MM-YYYY HH:MM`` (dashes). Both are parsed;
  neither is assumed from the other; anything matching neither is quarantined
  with the raw string, never guessed.
"""
from __future__ import annotations

import re
from datetime import datetime
from html import unescape

from berth_truth.models import (
    PARSER_VERSION,
    PortId,
    QuarantinedField,
    ScheduleRow,
    ScheduleSnapshot,
    TableKind,
)

__all__ = ["parse_snapshot", "parse_timestamp"]

# Real on-page heading text (case as displayed), normalised for matching by
# _normalize(): lowercased, trailing punctuation and whitespace collapsed.
_HEADING_TO_KIND: dict[str, TableKind] = {
    "vessels at berth": TableKind.AT_BERTH,
    "vessels at anchorage": TableKind.AT_ANCHORAGE,
    "vessels expected": TableKind.EXPECTED,
    "vessels sailed in last 24 hours": TableKind.SAILED_24H,
}

# Column order per table, confirmed against the real <thead> of both fetched
# pages. Field names match berth_truth.models.ScheduleRow exactly.
_COLUMNS: dict[TableKind, tuple[str, ...]] = {
    TableKind.AT_BERTH: ("berth_no", "vessel_name", "imp_exp", "cargo_raw", "etc_ts"),
    TableKind.AT_ANCHORAGE: ("sbu_name", "vessel_name", "imp_exp", "ata_ts"),
    TableKind.EXPECTED: ("sbu_name", "vessel_name", "imp_exp", "eta_ts"),
    TableKind.SAILED_24H: ("sbu_name", "vessel_name", "pob_ts", "pd_ts", "atub_ts"),
}

_TIMESTAMP_FIELDS = frozenset({"etc_ts", "ata_ts", "eta_ts", "pob_ts", "pd_ts", "atub_ts"})

# Both formats observed live, on the same page, on different tables. Order
# doesn't matter: the two are unambiguous (different separators), so there is
# no "guessing" between them -- either one matches exactly, or neither does.
_TIMESTAMP_FORMATS: tuple[str, ...] = ("%d.%m.%Y %H:%M", "%d-%m-%Y %H:%M")

_HEADING_RE = re.compile(r"<h2\b[^>]*>(.*?)</h2>", re.IGNORECASE | re.DOTALL)
_TABLE_RE = re.compile(r"<table\b[^>]*>.*?</table>", re.IGNORECASE | re.DOTALL)
_THEAD_RE = re.compile(r"<thead\b[^>]*>(.*?)</thead>", re.IGNORECASE | re.DOTALL)
_TBODY_RE = re.compile(r"<tbody\b[^>]*>(.*?)</tbody>", re.IGNORECASE | re.DOTALL)
_TR_RE = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.IGNORECASE | re.DOTALL)
_TD_OR_TH_RE = re.compile(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")


def _normalize(text: str) -> str:
    """Lowercase, strip, collapse internal whitespace, drop a trailing '.'.

    Handles the real inconsistency between tables on the same page: the
    "SBU Name." header carries a trailing dot on two tables and not on a
    third. Used only for matching headings/headers, never for stored values.
    """
    collapsed = re.sub(r"\s+", " ", text).strip()
    return collapsed.rstrip(".").lower()


def _cell_text(raw_inner_html: str) -> str:
    """Strip any nested tags (none expected in real data, cheap insurance),
    decode HTML entities (real data contains e.g. ``&#39;`` for an
    apostrophe), and collapse/trim whitespace. Never invents content."""
    stripped_tags = _TAG_RE.sub("", raw_inner_html)
    return re.sub(r"\s+", " ", unescape(stripped_tags)).strip()


def parse_timestamp(raw: str) -> tuple[datetime | None, str | None]:
    """Parse a timestamp cell against both known formats.

    Returns ``(value, None)`` on success, ``(None, None)`` for a genuinely
    blank cell (expected for vacant-berth rows), or ``(None, reason)`` when
    the text is non-empty but matches neither known format -- the caller
    quarantines that case with the raw string. Never falls back to a partial
    or guessed interpretation.

    Deliberately returns a naive ``datetime``: the page itself never states a
    timezone for these times. Attaching one -- UTC or otherwise -- would
    assert a fact the source doesn't provide; these are almost certainly IST
    (India Standard Time) port-local clock times, and stamping them UTC would
    silently shift every value by 5:30. Naive-and-documented is more honest
    than tz-aware-and-wrong. If a caller later needs a tz-aware value,
    converting is one explicit step at that call site with an explicit, cited
    reason for the offset -- not this function's job.

    The DTZ007 below is suppressed per-line rather than left to fire. It used
    to be left visible on purpose, so a reader would meet the tension; in
    practice it just kept `ruff check` permanently red, which trains everyone
    to skim past a failing lint run and is how a real finding eventually gets
    missed. The reasoning above is the durable record; the noqa carries a
    pointer to it.
    """
    text = raw.strip()
    if not text:
        return None, None
    for fmt in _TIMESTAMP_FORMATS:
        try:
            # Rationale for the suppression below: see this function's
            # docstring -- the source states no timezone and these are
            # port-local (almost certainly IST) clock times.
            return datetime.strptime(text, fmt), None  # noqa: DTZ007
        except ValueError:
            continue
    return None, f"matches neither known format {_TIMESTAMP_FORMATS!r}"


def _split_table_rows(table_html: str) -> tuple[list[str], list[list[str]]]:
    """Return ``(header_cell_texts, [row_cell_texts, ...])`` for one <table>.

    An absent <tbody> (shouldn't happen on this template, but a table with a
    <thead> and no <tbody> at all is a different fact from one with an empty
    <tbody>) yields an empty row list either way -- both read as "present,
    zero rows" to the caller, which only distinguishes "table found" from
    "table not found on the page at all".
    """
    header_cells: list[str] = []
    if thead_match := _THEAD_RE.search(table_html):
        header_row = _TR_RE.search(thead_match.group(1))
        if header_row:
            header_cells = [_cell_text(c) for c in _TD_OR_TH_RE.findall(header_row.group(1))]

    rows: list[list[str]] = []
    if tbody_match := _TBODY_RE.search(table_html):
        for tr_match in _TR_RE.finditer(tbody_match.group(1)):
            cells = [_cell_text(c) for c in _TD_OR_TH_RE.findall(tr_match.group(1))]
            if cells:  # a stray whitespace-only <tr> between real rows -> skip, not a row
                rows.append(cells)
    return header_cells, rows


def parse_snapshot(
    html_text: str,
    *,
    port_id: PortId,
    source_url: str,
    fetched_at: datetime,
    content_sha256: str,
) -> ScheduleSnapshot:
    """Parse one fetched page into a ``ScheduleSnapshot``.

    ``content_sha256`` is passed in rather than computed here: it must be the
    hash of the exact bytes fetched, before this function's own decoding and
    whitespace normalisation, so that byte-identical re-fetches are detected
    correctly by ``store`` regardless of how this parser evolves.
    """
    heading_matches = list(_HEADING_RE.finditer(html_text))

    tables_present: set[TableKind] = set()
    tables_empty: set[TableKind] = set()
    rows: list[ScheduleRow] = []
    quarantined: list[QuarantinedField] = []

    for i, heading_match in enumerate(heading_matches):
        kind = _HEADING_TO_KIND.get(_normalize(_cell_text(heading_match.group(1))))
        if kind is None:
            continue  # a heading on the page we don't recognise -- not this parser's table

        segment_start = heading_match.end()
        segment_end = heading_matches[i + 1].start() if i + 1 < len(heading_matches) else len(html_text)
        segment = html_text[segment_start:segment_end]

        table_match = _TABLE_RE.search(segment)
        if table_match is None:
            continue  # heading present, but no table followed it -- treat as not found

        tables_present.add(kind)
        _header_cells, body_rows = _split_table_rows(table_match.group(0))
        if not body_rows:
            tables_empty.add(kind)
            continue

        columns = _COLUMNS[kind]
        for row_index, cells in enumerate(body_rows):
            if len(cells) != len(columns):
                # Schema drift from what was verified live: don't guess a
                # column mapping for a row shaped differently than expected.
                quarantined.append(
                    QuarantinedField(
                        table_kind=kind,
                        row_index=row_index,
                        field_name="__row__",
                        raw_value=" | ".join(cells),
                        reason=f"expected {len(columns)} cells, got {len(cells)}",
                    )
                )
                continue

            values: dict[str, object] = {}
            for field_name, cell in zip(columns, cells, strict=True):
                if field_name in _TIMESTAMP_FIELDS:
                    ts_value, reason = parse_timestamp(cell)
                    if reason is not None:
                        quarantined.append(
                            QuarantinedField(
                                table_kind=kind,
                                row_index=row_index,
                                field_name=field_name,
                                raw_value=cell,
                                reason=reason,
                            )
                        )
                    values[field_name] = ts_value
                else:
                    values[field_name] = cell if cell else None

            vessel_name = values.get("vessel_name")
            is_vacant = isinstance(vessel_name, str) and vessel_name.upper() == "VACANT"
            if is_vacant:
                values["vessel_name"] = None
                imp_exp = values.get("imp_exp")
                if isinstance(imp_exp, str) and imp_exp.upper() == "VACANT":
                    values["imp_exp"] = None

            rows.append(ScheduleRow(table_kind=kind, is_vacant=is_vacant, **values))

    return ScheduleSnapshot(
        port_id=port_id,
        fetched_at=fetched_at,
        source_url=source_url,
        content_sha256=content_sha256,
        tables_present=frozenset(tables_present),
        tables_empty=frozenset(tables_empty),
        rows=tuple(rows),
        quarantined_fields=tuple(quarantined),
        parser_version=PARSER_VERSION,
    )
