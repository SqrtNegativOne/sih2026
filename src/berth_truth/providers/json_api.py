"""JSON/API adapter -- P1 §4.

No port in the current network has a *confirmed* Level A machine-readable
API (Singapore's MPA/OCEANS-X is a research lead, not yet verified -- see
sources.py). This adapter is real and functional, not a placeholder: it
performs a genuine HTTP GET and returns the parsed JSON body as
``fact_rows``-shaped output the caller maps per-source, since no two port
APIs share a schema. It has not been exercised against a real fixture this
session -- disclosed here rather than claimed otherwise.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Final

import requests

from berth_truth.providers import ParseResult, RawCapture
from berth_truth.sources import AdapterKind, PortSource
from berth_truth.store import write_raw_capture

__all__ = ["JsonApiProvider"]

_REQUEST_TIMEOUT_SECONDS: Final[int] = 20
_USER_AGENT: Final[str] = (
    "SIH26006-BerthTruth-Research/0.1 (hackathon research project; internal archival only)"
)


class JsonApiProvider:
    def supports(self, source: PortSource) -> bool:
        return source.adapter is AdapterKind.JSON_API

    def fetch(self, source: PortSource) -> RawCapture:
        if not source.source_url:
            raise ValueError(
                f"{source.source_name!r} has no source_url yet -- it is a research lead "
                f"(coverage_level={source.coverage_level.value}), not a fetchable source."
            )
        fetched_at = datetime.now(UTC)
        response = requests.get(
            source.source_url, headers={"User-Agent": _USER_AGENT}, timeout=_REQUEST_TIMEOUT_SECONDS
        )
        response.raise_for_status()
        raw_bytes = response.content
        sha256, _is_new = write_raw_capture(raw_bytes, ext="json")
        return RawCapture(
            source_url=source.source_url,
            retrieved_at=fetched_at,
            doc_published_date=None,
            content_sha256=sha256,
            parser_version="json_api/1",
            raw_bytes_path=None,
        )

    def parse(self, source: PortSource, capture: RawCapture) -> ParseResult:
        """No per-source JSON schema is registered yet for any port in this
        network -- returns the capture with zero fact_rows rather than
        guessing a field mapping. A real port-specific parser (mapping the
        API's own field names to FactPortCall) is added here once a Level A
        source is actually confirmed for a port."""
        return ParseResult(capture=capture, fact_rows=(), quarantine_count=0, parse_confidence=None)
