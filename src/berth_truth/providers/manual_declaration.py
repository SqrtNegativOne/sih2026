"""Manual-declaration adapter -- P1 §4.

Not every source is fetchable at all: a research lead with no confirmed URL
(Muara Pantai's official live feed, per sources.py) has nothing an HTTP
client can retrieve. This adapter never performs network I/O -- it exists so
the registry can still name such a source (and its
``LIVE_OPERATIONAL_FEED_NOT_FOUND`` status) without a caller having to special
-case "no adapter at all" versus "an adapter that legitimately found
nothing". Calling ``fetch()`` raises with the same honest message
``sources.py`` already records in the source's own notes -- it does not
silently return an empty success.
"""
from __future__ import annotations

from berth_truth.providers import ParseResult, RawCapture
from berth_truth.sources import AdapterKind, PortSource

__all__ = ["ManualDeclarationProvider"]


class ManualDeclarationSourceUnavailableError(RuntimeError):
    """Raised by fetch() for a source with no confirmed URL -- distinct from
    a network failure, which would be a real ``requests`` exception."""


class ManualDeclarationProvider:
    def supports(self, source: PortSource) -> bool:
        return source.adapter is AdapterKind.MANUAL_DECLARATION

    def fetch(self, source: PortSource) -> RawCapture:
        raise ManualDeclarationSourceUnavailableError(
            f"{source.source_name!r} has no automatable source -- "
            f"coverage_level={source.coverage_level.value}. {source.notes}"
        )

    def parse(self, source: PortSource, capture: RawCapture) -> ParseResult:
        return ParseResult(capture=capture, fact_rows=(), quarantine_count=0, parse_confidence=None)
