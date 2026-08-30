"""Provider framework -- P1 §4.

A ``Provider`` fetches one ``PortSource`` and parses it into a
``ParseResult``. This is the seam that lets the BT-0 Adani logic
(``html_table.py``, moved verbatim from ``archiver.py``/
``parsers/adani_schedule.py``) sit alongside new source shapes
(``pdf_report.py`` for Paradip/Visakhapatnam, and stubs for the formats no
port in this network needs yet) without archiver.py needing to know which
kind of source it's talking to.

Registry dispatch is on ``PortSource.adapter`` (an ``AdapterKind``) -- an
unrecognised kind raises, it is never silently skipped, per P1's explicit
requirement.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from berth_truth.fact_port_call import FactPortCall
from berth_truth.models import ScheduleSnapshot
from berth_truth.sources import AdapterKind, PortSource

__all__ = [
    "ParseResult",
    "Provider",
    "RawCapture",
    "UnknownAdapterError",
    "get_provider",
]


class RawCapture(BaseModel):
    """What one ``fetch()`` call produced, before parsing. Mirrors BT-0's
    existing provenance fields (``store.py``'s ``content_sha256``/
    ``fetched_at``) so the html_table adapter's move here changes nothing
    about what gets recorded."""

    model_config = ConfigDict(frozen=True)

    source_url: str
    retrieved_at: datetime
    doc_published_date: date | None
    content_sha256: str
    parser_version: str
    raw_bytes_path: str | None = None
    """Path the raw bytes were (or will be) written to by ``store.py``-style
    content-addressed storage; ``None`` if the caller writes them itself."""
    is_new_content: bool = True
    """Whether this fetch's bytes were genuinely new (vs. a byte-identical
    re-fetch of content already on disk, per the sha256 content-addressed
    store) -- the same distinction ``store.write_raw_html``'s own return
    value has always drawn. Set from that return value by any provider that
    persists through ``store.py``; left at the default only for an adapter
    that doesn't persist raw bytes at all."""


class ParseResult(BaseModel):
    """What one ``parse()`` call produced. ``schedule_snapshot`` is
    populated only by the html_table adapter (the pre-existing BT-0 shape,
    preserved exactly); ``fact_rows`` is the normalised P1 output every
    adapter (including html_table, via a projection) can also produce for
    ``fact_port_call``."""

    model_config = ConfigDict(frozen=True)

    capture: RawCapture
    schedule_snapshot: ScheduleSnapshot | None = None
    fact_rows: tuple[FactPortCall, ...] = ()
    quarantine_count: int = 0
    parse_confidence: float | None = None


@runtime_checkable
class Provider(Protocol):
    def supports(self, source: PortSource) -> bool: ...
    def fetch(self, source: PortSource) -> RawCapture: ...
    def parse(self, source: PortSource, capture: RawCapture) -> ParseResult: ...


class UnknownAdapterError(ValueError):
    """Raised when a ``PortSource`` names an ``AdapterKind`` no registered
    provider handles. Never silently skipped."""


def get_provider(kind: AdapterKind) -> Provider:
    """Registry dispatch. Imports adapters lazily so importing this module
    never pulls in every adapter's own dependencies (e.g. pypdf) unless
    that adapter is actually used."""
    if kind is AdapterKind.HTML_TABLE:
        from berth_truth.providers.html_table import HtmlTableProvider

        return HtmlTableProvider()
    if kind is AdapterKind.PDF_REPORT:
        from berth_truth.providers.pdf_report import PdfReportProvider

        return PdfReportProvider()
    if kind is AdapterKind.JSON_API:
        from berth_truth.providers.json_api import JsonApiProvider

        return JsonApiProvider()
    if kind is AdapterKind.CSV_XLSX:
        from berth_truth.providers.csv_xlsx import CsvXlsxProvider

        return CsvXlsxProvider()
    if kind is AdapterKind.STATIC_DOC:
        from berth_truth.providers.static_doc import StaticDocProvider

        return StaticDocProvider()
    if kind is AdapterKind.MANUAL_DECLARATION:
        from berth_truth.providers.manual_declaration import ManualDeclarationProvider

        return ManualDeclarationProvider()
    raise UnknownAdapterError(f"No provider registered for adapter kind {kind!r}.")
