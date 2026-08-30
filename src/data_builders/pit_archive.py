"""Point-in-time snapshot archiving -- P4 requirement 7.

PortWatch (and, now, the P4 macro sources) revise their own published
history; this repo has always held exactly one snapshot of each, with no
record of what a past pull actually looked like at the time. A backtest run
today implicitly assumes today's ``master_long.parquet``/``macro_long.parquet``
is what was "knowable" on every past date it scores -- which is false
whenever a source has revised a historical value since. This module archives
real snapshots forward, content-addressed and keyed by retrieval date,
mirroring ``berth_truth.store``'s existing pattern (P1) exactly rather than
inventing a second archiving idiom: same content-hash identity, same
append-only ledger, same "never overwrite, a repeat of identical content is
a re-observation" semantics.

**Honest limitation, stated plainly (P4's own edge-case requirement):** this
module can only archive snapshots FORWARD from whenever it is first run --
there is no historical vintage data to retroactively reconstruct for dates
before that. The first real snapshot this module ever writes is dated to
today; genuine point-in-time backtesting (scoring a past date against only
what was knowable then) becomes possible only once multiple real snapshots
have accumulated across real elapsed time. ``load_snapshot_as_of`` returns
``None`` for any date before the first archived snapshot -- callers must
treat that as "no vintage data available for this date," per the P4
requirement to disclose the limitation rather than silently substitute
today's file.

**Integration with ``ml.frozen_test`` (P4's explicit instruction: do not
build a second backtest system):** this module does not run backtests and
does not decide what counts as "the test split" -- it only answers "what did
source X look like as of date Y." A caller building a genuinely
point-in-time-correct backtest still reads the frozen test split exactly as
today (``ml.frozen_test.load_frozen_test`` inside
``allow_test_set_access``); the only change such a caller would make is
swapping ``pl.read_parquet(MASTER_LONG_PATH)`` for
``load_snapshot_as_of(MASTER, as_of)`` when scoring a specific historical
``as_of``. No new guard, no new split, no new evaluation metric.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Final, Literal

import polars as pl

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
PIT_ARCHIVE_DIR: Final[Path] = REPO_ROOT / "raw_data" / "pit_archive"
SNAPSHOTS_DIR: Final[Path] = PIT_ARCHIVE_DIR / "snapshots"
MANIFEST_LOG: Final[Path] = PIT_ARCHIVE_DIR / "manifest.jsonl"

SourceName = Literal["master_long", "macro_long"]

_SOURCE_PATHS: Final[dict[SourceName, Path]] = {
    "master_long": REPO_ROOT / "src" / "data" / "master_long.parquet",
    "macro_long": REPO_ROOT / "src" / "data" / "macro_long.parquet",
}

__all__ = [
    "ManifestEntry",
    "SourceName",
    "archive_all_sources",
    "archive_snapshot",
    "content_sha256_of_frame",
    "list_manifest",
    "load_snapshot_as_of",
]


@dataclass(frozen=True)
class ManifestEntry:
    """One line of the append-only ledger -- one archive attempt, whether or
    not it produced new content (mirrors ``berth_truth.store.Observation``)."""

    retrieved_at: datetime
    source: SourceName
    content_sha256: str
    is_new_snapshot: bool
    n_rows: int


def content_sha256_of_frame(df: pl.DataFrame) -> str:
    """Hash of the frame's own parquet bytes -- the identity this module is
    keyed on, same construction as ``berth_truth.store.content_sha256``
    (hash of exact bytes), applied to the serialized frame rather than an
    HTTP response body."""
    import io

    buf = io.BytesIO()
    df.write_parquet(buf)
    return hashlib.sha256(buf.getvalue()).hexdigest()


def _snapshot_path(source: SourceName, sha256: str) -> Path:
    return SNAPSHOTS_DIR / source / f"{sha256}.parquet"


def archive_snapshot(source: SourceName, df: pl.DataFrame | None = None, retrieved_at: datetime | None = None) -> ManifestEntry:
    """Archive the current real content of ``source`` (or an explicitly
    supplied frame, for testing). Never overwrites: identical content at a
    later retrieval is recorded as a re-observation, not duplicated on disk.
    """
    df = df if df is not None else pl.read_parquet(_SOURCE_PATHS[source])
    retrieved_at = retrieved_at or datetime.now(UTC)
    sha256 = content_sha256_of_frame(df)
    path = _snapshot_path(source, sha256)
    is_new = not path.exists()
    if is_new:
        path.parent.mkdir(parents=True, exist_ok=True)
        df.write_parquet(path)

    entry = ManifestEntry(retrieved_at=retrieved_at, source=source, content_sha256=sha256, is_new_snapshot=is_new, n_rows=df.height)
    MANIFEST_LOG.parent.mkdir(parents=True, exist_ok=True)
    with MANIFEST_LOG.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "retrieved_at": entry.retrieved_at.isoformat(), "source": entry.source,
                    "content_sha256": entry.content_sha256, "is_new_snapshot": entry.is_new_snapshot,
                    "n_rows": entry.n_rows,
                },
                sort_keys=True,
            )
            + "\n"
        )
    return entry


def archive_all_sources(retrieved_at: datetime | None = None) -> list[ManifestEntry]:
    """Archive every real source that currently exists on disk. Sources not
    yet built (e.g. macro_long.parquet before ``build_macro`` has run) are
    skipped, not errored on."""
    retrieved_at = retrieved_at or datetime.now(UTC)
    out = []
    for source, path in _SOURCE_PATHS.items():
        if path.exists():
            out.append(archive_snapshot(source, retrieved_at=retrieved_at))
    return out


def list_manifest(source: SourceName | None = None) -> list[ManifestEntry]:
    """Every real archive event ever recorded, oldest first. Never empty
    after the first ``archive_snapshot``/``archive_all_sources`` call; empty
    (not an error) if archiving has never run."""
    if not MANIFEST_LOG.exists():
        return []
    out: list[ManifestEntry] = []
    with MANIFEST_LOG.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            if source is not None and record["source"] != source:
                continue
            out.append(
                ManifestEntry(
                    retrieved_at=datetime.fromisoformat(record["retrieved_at"]), source=record["source"],
                    content_sha256=record["content_sha256"], is_new_snapshot=record["is_new_snapshot"],
                    n_rows=record["n_rows"],
                )
            )
    return out


def load_snapshot_as_of(source: SourceName, as_of: date) -> pl.DataFrame | None:
    """The frame as it was archived at the LATEST retrieval on or before
    ``as_of``. Returns ``None`` -- never today's current file -- when no
    snapshot was archived on or before that date (see the module docstring's
    disclosed limitation: this cannot retroactively reconstruct history from
    before archiving began)."""
    entries = [e for e in list_manifest(source) if e.retrieved_at.date() <= as_of]
    if not entries:
        return None
    latest = max(entries, key=lambda e: e.retrieved_at)
    path = _snapshot_path(source, latest.content_sha256)
    if not path.exists():
        return None
    return pl.read_parquet(path)


def _setup_logging() -> None:
    import logging

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def main() -> None:
    """Archive today's real snapshot of every source that exists on disk.
    Run with `python -m data_builders.pit_archive` -- intended to run once
    per real data refresh (matching every other `data_builders` script's
    `main()` convention), so the manifest accumulates one real vintage per
    refresh over time."""
    import logging

    _setup_logging()
    logger = logging.getLogger(__name__)
    entries = archive_all_sources()
    if not entries:
        logger.warning("No real source files found to archive (master_long.parquet / macro_long.parquet).")
        return
    for e in entries:
        status = "new snapshot" if e.is_new_snapshot else "unchanged (re-observation)"
        logger.info(f"{e.source}: {status}, {e.n_rows} rows, sha256={e.content_sha256[:12]}...")


if __name__ == "__main__":
    main()
