"""Append-only storage for vessel-schedule snapshots.

Two things are stored per fetch, both content-addressed by the sha256 of the
exact bytes fetched (before any decoding), and neither is ever overwritten:

* the raw HTML, verbatim -- the parser will be wrong about something at some
  point, and raw capture is the only way to reprocess a page after fixing it;
* the parsed ``ScheduleSnapshot``, keyed by ``(port_id, content_sha256)``.

Keying the snapshot by content hash rather than by fetch time is what makes
"a repeat fetch of identical content is a re-observation, not a new
snapshot" true by construction: fetching the same content twice produces the
same hash, so the second write finds the snapshot already on disk and skips
it, recording only a lightweight observation instead.

Layout, under ``raw_data/berth_truth/`` (tracked, not gitignored -- same
convention as ``raw_data/portwatch/``):
    raw_html/<sha256>.html
    raw_captures/<sha256>.<ext>   -- P1: non-HTML sources (PDF, etc.)
    snapshots/<port_id>/<sha256>.json
    observations.jsonl   -- append-only ledger, one line per fetch attempt
    fact_port_call.jsonl -- P1: see berth_truth.fact_port_call
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final

from berth_truth.models import PortId, ScheduleSnapshot

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
BERTH_TRUTH_DIR: Final[Path] = REPO_ROOT / "raw_data" / "berth_truth"
RAW_HTML_DIR: Final[Path] = BERTH_TRUTH_DIR / "raw_html"
RAW_CAPTURES_DIR: Final[Path] = BERTH_TRUTH_DIR / "raw_captures"
SNAPSHOTS_DIR: Final[Path] = BERTH_TRUTH_DIR / "snapshots"
OBSERVATIONS_LOG: Final[Path] = BERTH_TRUTH_DIR / "observations.jsonl"


@dataclass(frozen=True)
class Observation:
    """One line of the append-only ledger: one fetch attempt, regardless of
    whether it produced new content."""

    fetched_at: datetime
    port_id: PortId
    source_url: str
    content_sha256: str
    is_new_snapshot: bool


def content_sha256(data: bytes) -> str:
    """Hash of the exact bytes fetched -- the identity this whole module is
    keyed on."""
    return hashlib.sha256(data).hexdigest()


def raw_html_path(sha256: str) -> Path:
    return RAW_HTML_DIR / f"{sha256}.html"


def snapshot_path(port_id: PortId, sha256: str) -> Path:
    return SNAPSHOTS_DIR / port_id.value / f"{sha256}.json"


def write_raw_html(data: bytes) -> tuple[str, bool]:
    """Store raw bytes content-addressed. Returns ``(sha256, was_new)``.

    Never overwrites: if a file already exists at this hash, its content is
    by definition byte-identical (that's what the hash means), so the
    existing file is left untouched and ``was_new=False`` is returned.
    """
    sha256 = content_sha256(data)
    path = raw_html_path(sha256)
    if path.exists():
        return sha256, False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return sha256, True


def raw_capture_path(sha256: str, ext: str) -> Path:
    return RAW_CAPTURES_DIR / f"{sha256}.{ext}"


def write_raw_capture(data: bytes, ext: str) -> tuple[str, bool]:
    """Content-addressed storage for non-HTML source bytes (P1 §4) --
    ``write_raw_html``'s exact logic, generalised over a file extension
    rather than duplicated, since HTML's own path stays ``raw_html/`` for
    the byte-identical Adani regression guarantee (write_raw_html is left
    untouched)."""
    sha256 = content_sha256(data)
    path = raw_capture_path(sha256, ext)
    if path.exists():
        return sha256, False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return sha256, True


def write_snapshot(snapshot: ScheduleSnapshot) -> bool:
    """Store a parsed snapshot, keyed by ``(port_id, content_sha256)``.

    Returns ``True`` if this is a genuinely new snapshot, ``False`` if one
    already existed for this exact content -- the caller should record the
    latter as a re-observation via :func:`append_observation`, not treat it
    as a failure.
    """
    path = snapshot_path(snapshot.port_id, snapshot.content_sha256)
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(snapshot.model_dump_json(indent=2), encoding="utf-8")
    return True


def load_snapshot(port_id: PortId, sha256: str) -> ScheduleSnapshot:
    path = snapshot_path(port_id, sha256)
    return ScheduleSnapshot.model_validate_json(path.read_text(encoding="utf-8"))


def list_snapshot_hashes(port_id: PortId) -> list[str]:
    """Every content hash this port has a stored snapshot for, oldest first
    by filesystem mtime. Empty list if the port has never been archived."""
    port_dir = SNAPSHOTS_DIR / port_id.value
    if not port_dir.is_dir():
        return []
    return [p.stem for p in sorted(port_dir.glob("*.json"), key=lambda p: p.stat().st_mtime)]


def append_observation(observation: Observation) -> None:
    """Append one line to the ledger. Never truncates or rewrites earlier
    lines -- this is the append-only history of every fetch attempt, whether
    or not it turned out to be new content."""
    OBSERVATIONS_LOG.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "fetched_at": observation.fetched_at.isoformat(),
        "port_id": observation.port_id.value,
        "source_url": observation.source_url,
        "content_sha256": observation.content_sha256,
        "is_new_snapshot": observation.is_new_snapshot,
    }
    with OBSERVATIONS_LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")
