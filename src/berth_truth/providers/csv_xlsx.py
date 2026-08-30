"""CSV/XLSX adapter -- P1 §4.

No port in the current network has a confirmed downloadable CSV/XLSX
operational report (distinct from raw_data/portwatch/, which is a separate,
already-integrated pipeline -- see opt.congestion/opt.risk/tonnage.basins).
Real and functional, not a placeholder: fetches and stores the file
content-addressed; per-source column mapping is added once a real source is
confirmed, same discipline as json_api.py.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Final

import requests

from berth_truth.providers import ParseResult, RawCapture
from berth_truth.sources import AdapterKind, PortSource
from berth_truth.store import write_raw_capture

__all__ = ["CsvXlsxProvider"]

_REQUEST_TIMEOUT_SECONDS: Final[int] = 20
_USER_AGENT: Final[str] = (
    "SIH26006-BerthTruth-Research/0.1 (hackathon research project; internal archival only)"
)


class CsvXlsxProvider:
    def supports(self, source: PortSource) -> bool:
        return source.adapter is AdapterKind.CSV_XLSX

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
        ext = "xlsx" if source.source_url.lower().endswith(("xlsx", "xls")) else "csv"
        sha256, _is_new = write_raw_capture(response.content, ext=ext)
        return RawCapture(
            source_url=source.source_url,
            retrieved_at=fetched_at,
            doc_published_date=None,
            content_sha256=sha256,
            parser_version="csv_xlsx/1",
            raw_bytes_path=None,
        )

    def parse(self, source: PortSource, capture: RawCapture) -> ParseResult:
        """No per-source column mapping is registered yet for any port in
        this network -- returns zero fact_rows rather than guessing a
        schema."""
        return ParseResult(capture=capture, fact_rows=(), quarantine_count=0, parse_confidence=None)
