"""HTML-table adapter -- the BT-0 Adani logic, moved behind the Provider
protocol, not rewritten.

The actual fetch (requests.Session + USER_AGENT) and parse
(parsers.adani_schedule.parse_snapshot) are the exact same functions/logic
that shipped in BT-0 -- this module is a thin Provider-shaped wrapper around
them, not a reimplementation. Verified by the regression test in
tests/berth_truth/test_providers.py: the 3 pre-existing captures in
raw_data/berth_truth/raw_html/ must still produce identical sha256 and parse
output through this path.

``opt.voyage._REGISTER_PORT_ID`` is reused (not duplicated) as the
PortEnum->PortId bridge -- it already exists as the one place that
translation happens, per its own docstring.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Final

import requests

from berth_truth.models import PortId
from berth_truth.parsers.adani_schedule import parse_snapshot
from berth_truth.providers import ParseResult, RawCapture
from berth_truth.sources import AdapterKind, PortSource
from berth_truth.store import raw_html_path, write_raw_html
from opt.network import PortEnum
from opt.voyage import _REGISTER_PORT_ID

__all__ = ["HtmlTableProvider"]

#: Same user agent BT-0 shipped with -- unchanged, see archiver.py's own
#: docstring for the robots.txt/terms review this string documents.
USER_AGENT: Final[str] = (
    "SIH26006-BerthTruth-Research/0.1 "
    "(+https://github.com/SqrtNegativOne/sih2026; hackathon research project; "
    "internal archival only, not redistributed)"
)
_REQUEST_TIMEOUT_SECONDS: Final[int] = 20


def _port_id_for(port: PortEnum) -> PortId:
    mapped = _REGISTER_PORT_ID.get(port)
    if mapped is None:
        raise ValueError(
            f"{port!r} has no berth_truth.models.PortId mapping "
            f"(opt.voyage._REGISTER_PORT_ID) -- the html_table adapter only knows "
            f"how to label a ScheduleSnapshot for ports berth_truth itself tracks."
        )
    return mapped


class HtmlTableProvider:
    def supports(self, source: PortSource) -> bool:
        return source.adapter is AdapterKind.HTML_TABLE

    def fetch(self, source: PortSource) -> RawCapture:
        """Single attempt, no retries -- same reasoning as BT-0's original
        _fetch: a retry loop against a site this project doesn't control
        turns into a retry storm by accident; a failed fetch is reported and
        the next scheduled run tries again.

        Persists the raw bytes immediately via the exact same
        ``store.write_raw_html`` BT-0 always used, so ``parse()`` reads the
        bytes back from disk rather than hitting the network a second time
        -- two fetches of a live page are not guaranteed to return identical
        content, which would otherwise risk a capture/parse sha256 mismatch.
        """
        fetched_at = datetime.now(UTC)
        with requests.Session() as session:
            response = session.get(
                source.source_url, headers={"User-Agent": USER_AGENT}, timeout=_REQUEST_TIMEOUT_SECONDS
            )
            response.raise_for_status()
            raw_bytes = response.content

        sha256, is_new = write_raw_html(raw_bytes)
        return RawCapture(
            source_url=source.source_url,
            retrieved_at=fetched_at,
            doc_published_date=None,  # a live page has no stated publication date
            content_sha256=sha256,
            parser_version="adani_schedule/1",
            raw_bytes_path=str(raw_html_path(sha256)),
            is_new_content=is_new,
        )

    def parse(self, source: PortSource, capture: RawCapture) -> ParseResult:
        """Reads the bytes ``fetch()`` already persisted -- never re-fetches."""
        html_text = raw_html_path(capture.content_sha256).read_bytes().decode("utf-8", errors="replace")

        snapshot = parse_snapshot(
            html_text,
            port_id=_port_id_for(source.port),
            source_url=source.source_url,
            fetched_at=capture.retrieved_at,
            content_sha256=capture.content_sha256,
        )
        return ParseResult(
            capture=capture,
            schedule_snapshot=snapshot,
            quarantine_count=len(snapshot.quarantined_fields),
            parse_confidence=snapshot.parse_confidence,
        )
