"""Static-document adapter -- P1 §4.

For sources that publish authoritative berth/navigation *constraints* but no
live vessel schedule (Richards Bay, Beira, Hampton Roads/Lamberts Point in
this network's current source registry -- see sources.py). These feed BT-1's
constraint register (P2), not fact_port_call: a static document has no
per-vessel rows to normalise, only geometry/draft figures. ``fetch()`` is
real and functional; ``parse()`` intentionally returns no fact_rows, since a
static constraint document is not a source of vessel-call facts.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Final

import requests

from berth_truth.providers import ParseResult, RawCapture
from berth_truth.sources import AdapterKind, PortSource
from berth_truth.store import write_raw_capture

__all__ = ["StaticDocProvider"]

_REQUEST_TIMEOUT_SECONDS: Final[int] = 20
_USER_AGENT: Final[str] = (
    "SIH26006-BerthTruth-Research/0.1 (hackathon research project; internal archival only)"
)


class StaticDocProvider:
    def supports(self, source: PortSource) -> bool:
        return source.adapter is AdapterKind.STATIC_DOC

    def fetch(self, source: PortSource) -> RawCapture:
        if not source.source_url:
            raise ValueError(
                f"{source.source_name!r} has no source_url yet -- it is a research lead "
                f"(coverage_level={source.coverage_level.value}), not a fetchable source. "
                f"Its constraint figures (if any are seeded in berth_truth.registry) came "
                f"from manual reading of the document, not an automated fetch."
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
            parser_version="static_doc/1",
            raw_bytes_path=None,
        )

    def parse(self, source: PortSource, capture: RawCapture) -> ParseResult:
        """A static constraint document has no per-vessel rows -- its
        numbers are read (by a human, or a future dedicated extractor) into
        berth_truth.registry.BerthConstraint directly, the same way BT-1's
        existing Dhamra/Gangavaram/Visakhapatnam entries were seeded."""
        return ParseResult(capture=capture, fact_rows=(), quarantine_count=0, parse_confidence=None)
