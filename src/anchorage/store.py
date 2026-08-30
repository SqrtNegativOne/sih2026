"""Real, persisted ``AnchorageCensus`` records -- append-only JSONL, the
same storage shape ``opt.ledger``/``berth_truth.fact_port_call`` already use
for exactly this reason: a census is a real, dated observation of a fact
that happened (a scene was processed on this date and this many vessels
were counted), never rewritten after the fact.

``anchorage.detect.count_vessels`` itself stays a pure function returning an
in-memory ``AnchorageCensus`` -- writing to disk is a separate, explicit
step (``record_census``) a caller takes when it wants this census to become
part of the real historical record, mirroring ``opt.ledger.
record_recommendation``'s own "not every call, only the ones that count"
convention.

Genuinely empty is a real, expected state, not an error: as of this
module's own build, no real Sentinel-1 scene has ever been processed in
this environment (4.1's harvester never obtained real Copernicus
credentials -- see its own PULL_NOTES.md), so ``CENSUS_LOG`` does not exist
on disk and every reader here returns an empty result rather than raising.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Final

from anchorage.detect import AnchorageCensus

__all__ = ["CENSUS_LOG", "latest_census", "read_censuses", "record_census"]

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
ANCHORAGE_DIR: Final[Path] = REPO_ROOT / "raw_data" / "anchorage"
CENSUS_LOG: Final[Path] = ANCHORAGE_DIR / "census.jsonl"


def record_census(census: AnchorageCensus, *, path: Path = CENSUS_LOG) -> None:
    """Append one real census. Never rewrites or deduplicates -- if the same
    scene is processed twice, both records are kept; a reader that wants
    "the latest" (``latest_census``) resolves that at read time instead."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(census.model_dump_json() + "\n")


def read_censuses(*, path: Path = CENSUS_LOG) -> tuple[AnchorageCensus, ...]:
    """Every real census ever recorded, oldest first by file order. Genuinely
    empty (not an error) when ``path`` does not exist yet -- see module
    docstring."""
    if not path.exists():
        return ()
    records: list[AnchorageCensus] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            records.append(AnchorageCensus.model_validate(json.loads(line)))
    return tuple(records)


def latest_census(port: str, *, path: Path = CENSUS_LOG) -> AnchorageCensus | None:
    """The most recently ACQUIRED (not most recently written) real census
    for ``port``, or ``None`` when none has ever been recorded -- the real
    404 case ``GET /anchorage/{port_code}/census`` reports."""
    matches = [c for c in read_censuses(path=path) if c.port == port]
    if not matches:
        return None
    return max(matches, key=lambda c: c.acquired_at)
