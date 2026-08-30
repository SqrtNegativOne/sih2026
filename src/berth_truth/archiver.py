"""Fetch orchestration for the vessel-schedule snapshot archiver.

Crawl-permission finding (checked 2026-08-27, not re-checked automatically at
runtime -- see the note at the bottom of this docstring for why):

* ``https://www.adaniports.com/robots.txt`` sets ``User-agent: *`` with **no
  Disallow directive anywhere in the file**, and separately grants
  ``Allow: /`` to Google and Bing by name. Fetching
  ``/ports-and-terminals/dhamra-port/vesselschedule`` and
  ``/ports-and-terminals/gangavaram-port/vesselschedule`` is permitted.
* The site's Terms & Conditions and Legal Disclaimer pages were fetched and
  read; neither mentions automated access, scraping, crawling, or robots.
  The one relevant clause found: "The user shall not distribute text or
  graphics to others without the express written consent of ADANI GROUP,"
  and a general "reproduction is prohibited other than in accordance with
  the copyright notice." Read together with the absence of any
  anti-automation clause, this permits fetching and internal archival for
  research use; it does not license republishing or redistributing the
  captured pages. This module stores captures under ``raw_data/`` for this
  project's own internal use and never serves, republishes or redistributes
  them -- consistent with that reading, not a workaround of it.

Why this isn't re-checked automatically on every run: robots.txt is fetched
once by a human (or an agent acting under explicit instruction) as a
go/no-go gate before the fetcher is built at all, exactly as the task that
produced this module required. Re-fetching it before every scheduled run
would be reasonable *additional* hardening but is a different, larger task
(it needs its own failure policy: what should a scheduled run do if
robots.txt itself is unreachable, or changes to newly disallow the path
mid-history?) -- out of scope here, and flagged rather than silently added.
"""
from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

import requests

from berth_truth.models import PortId, ScheduleSnapshot, TableKind
from berth_truth.providers import get_provider
from berth_truth.sources import PORT_SOURCES, AdapterKind
from berth_truth.store import (
    Observation,
    append_observation,
    write_snapshot,
)
from opt.network import PortEnum
from opt.voyage import _REGISTER_PORT_ID

#: Reverse of opt.voyage._REGISTER_PORT_ID -- PortId is berth_truth's own
#: identity, PORT_SOURCES (P1) is keyed by opt.network.PortEnum. Built here,
#: not duplicated as a second hand-written dict, so the two enums can never
#: silently drift apart.
_PORT_ENUM_FOR: Final[dict[PortId, PortEnum]] = {v: k for k, v in _REGISTER_PORT_ID.items()}

#: The real fetch identity is now providers.html_table.USER_AGENT (moved,
#: not duplicated) -- this module no longer fetches directly.
SOURCE_URLS: Final[dict[PortId, str]] = {
    PortId.DHAMRA: "https://www.adaniports.com/ports-and-terminals/dhamra-port/vesselschedule",
    PortId.GANGAVARAM: "https://www.adaniports.com/ports-and-terminals/gangavaram-port/vesselschedule",
}

#: Minimum seconds between requests to this host. Documented rather than
#: merely applied so a future scheduler is configured against a known,
#: intentional number, not a magic constant discovered by reading the code.
#: Enforced within one run (between the two fetches below); a scheduler
#: calling this CLI must not itself run more often than this interval allows
#: for the number of ports being fetched.
MIN_FETCH_INTERVAL_SECONDS: Final[int] = 30


@dataclass(frozen=True)
class ArchiveResult:
    """What one archive_port() call did, for CLI reporting and for tests.
    Not persisted -- persisted state lives entirely in berth_truth.store."""

    port_id: PortId
    source_url: str
    fetched_at: datetime
    content_sha256: str
    is_new_content: bool
    is_new_snapshot: bool
    tables_present: frozenset[TableKind]
    tables_empty: frozenset[TableKind]
    observed_berth_ids: frozenset[str]
    quarantined_count: int
    parse_confidence: float
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def _html_source_for(port_id: PortId):
    """The registered HTML_TABLE PortSource for this PortId's live schedule
    page -- P1: dispatch through the provider registry rather than this
    module's own inline fetch. Raises the same way a missing SOURCE_URLS
    entry used to (a KeyError-shaped failure), since a PortId this module
    doesn't know how to fetch is a programming error, not a runtime one."""
    port_enum = _PORT_ENUM_FOR[port_id]
    for source in PORT_SOURCES.get(port_enum, ()):
        if source.adapter is AdapterKind.HTML_TABLE and source.source_url:
            return source
    raise KeyError(f"No registered HTML_TABLE source for {port_id!r} ({port_enum!r}).")


def archive_port(port_id: PortId, *, session: requests.Session | None = None) -> ArchiveResult:
    """Fetch, parse, and store one port's schedule page. Never raises for a
    network or parse failure -- returns an ``ArchiveResult`` with ``error``
    set, so ``archive_all`` can fetch the second port even if the first
    failed.

    Dispatches through the P1 provider registry (``providers.html_table``)
    rather than fetching/parsing inline -- the underlying HTTP and parsing
    logic is unchanged (moved, not rewritten; see html_table.py's own
    docstring), verified byte-identical against the 3 pre-existing captures
    in tests/berth_truth/test_providers.py. ``session`` is accepted for
    backward API compatibility but is no longer threaded through to the
    provider, which manages its own session per call -- this changes
    connection-pooling internals only, never the fetched content or the
    parsed result.
    """
    source = _html_source_for(port_id)
    url = source.source_url
    fetched_at = datetime.now(UTC)
    try:
        provider = get_provider(source.adapter)
        capture = provider.fetch(source)
        result = provider.parse(source, capture)
    except requests.RequestException as exc:
        return ArchiveResult(
            port_id=port_id,
            source_url=url,
            fetched_at=fetched_at,
            content_sha256="",
            is_new_content=False,
            is_new_snapshot=False,
            tables_present=frozenset(),
            tables_empty=frozenset(),
            observed_berth_ids=frozenset(),
            quarantined_count=0,
            parse_confidence=0.0,
            error=f"fetch failed: {exc!r}",
        )

    snapshot: ScheduleSnapshot | None = result.schedule_snapshot
    assert snapshot is not None, "HTML_TABLE provider must always produce a ScheduleSnapshot"

    is_new_content = capture.is_new_content
    is_new_snapshot = write_snapshot(snapshot)
    append_observation(
        Observation(
            fetched_at=fetched_at,
            port_id=port_id,
            source_url=url,
            content_sha256=capture.content_sha256,
            is_new_snapshot=is_new_snapshot,
        )
    )

    return ArchiveResult(
        port_id=port_id,
        source_url=url,
        fetched_at=fetched_at,
        content_sha256=capture.content_sha256,
        is_new_content=is_new_content,
        is_new_snapshot=is_new_snapshot,
        tables_present=snapshot.tables_present,
        tables_empty=snapshot.tables_empty,
        observed_berth_ids=snapshot.observed_berth_ids,
        quarantined_count=len(snapshot.quarantined_fields),
        parse_confidence=snapshot.parse_confidence,
    )


def archive_all() -> list[ArchiveResult]:
    """Archive every known port, honouring MIN_FETCH_INTERVAL_SECONDS
    between requests within this run."""
    results: list[ArchiveResult] = []
    with requests.Session() as session:
        for i, port_id in enumerate(SOURCE_URLS):
            if i > 0:
                time.sleep(MIN_FETCH_INTERVAL_SECONDS)
            results.append(archive_port(port_id, session=session))
    return results


def _format_result(result: ArchiveResult) -> str:
    if not result.ok:
        return f"{result.port_id.value}: FAILED -- {result.error}"
    novelty = "new content" if result.is_new_content else "re-observation of known content"
    lines = [
        f"{result.port_id.value}: {novelty} (sha256={result.content_sha256[:12]}...)",
        f"  tables present: {sorted(t.value for t in result.tables_present)}",
        f"  tables empty:   {sorted(t.value for t in result.tables_empty)}",
        (
            f"  berths observed ({len(result.observed_berth_ids)}): "
            f"{', '.join(sorted(result.observed_berth_ids)) or '(none)'}"
        ),
        f"  quarantined fields: {result.quarantined_count}",
        f"  parse confidence: {result.parse_confidence}",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Suitable for a scheduled run
    (``python -m berth_truth``); this module deliberately does not install
    or manage a scheduler itself."""
    parser = argparse.ArgumentParser(
        prog="berth_truth.archiver",
        description="Archive Adani Dhamra/Gangavaram live vessel-schedule snapshots.",
    )
    parser.add_argument(
        "--port",
        # From SOURCE_URLS, not PortId: PortId now also names register-only
        # ports (BT-1) with no schedule page to fetch. Iterating PortId here
        # would offer a choice this CLI cannot actually serve.
        choices=[p.value.lower() for p in SOURCE_URLS] + ["all"],
        default="all",
        help="Which port to archive (default: all).",
    )
    args = parser.parse_args(argv)

    if args.port == "all":
        results = archive_all()
    else:
        results = [archive_port(PortId(args.port.upper()))]

    for result in results:
        print(_format_result(result))

    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
