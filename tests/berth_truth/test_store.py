"""Tests for berth_truth.store's content-addressed archive.

Every test here redirects the module's storage paths into pytest's tmp_path
via monkeypatch -- these tests must never write into the repo's real
raw_data/berth_truth/, which is the one place real archived history lives.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from berth_truth import store
from berth_truth.models import PortId, ScheduleSnapshot, TableKind


@pytest.fixture(autouse=True)
def _isolated_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point every module-level storage path at a throwaway directory."""
    root = tmp_path / "berth_truth"
    monkeypatch.setattr(store, "BERTH_TRUTH_DIR", root)
    monkeypatch.setattr(store, "RAW_HTML_DIR", root / "raw_html")
    monkeypatch.setattr(store, "SNAPSHOTS_DIR", root / "snapshots")
    monkeypatch.setattr(store, "OBSERVATIONS_LOG", root / "observations.jsonl")
    return root


def _sample_snapshot(*, sha256: str = "a" * 64) -> ScheduleSnapshot:
    return ScheduleSnapshot(
        port_id=PortId.DHAMRA,
        fetched_at=datetime(2026, 8, 27, 12, 0, 0),
        source_url="https://example.invalid/fixture",
        content_sha256=sha256,
        tables_present=frozenset({TableKind.AT_BERTH}),
        tables_empty=frozenset(),
        rows=(),
    )


class TestRawHtmlStorage:
    def test_first_write_creates_the_file(self) -> None:
        sha256, was_new = store.write_raw_html(b"<html>one</html>")
        assert was_new is True
        assert store.raw_html_path(sha256).is_file()
        assert store.raw_html_path(sha256).read_bytes() == b"<html>one</html>"

    def test_identical_bytes_written_twice_is_not_a_second_file(self) -> None:
        first_sha, first_new = store.write_raw_html(b"<html>same</html>")
        mtime_after_first = store.raw_html_path(first_sha).stat().st_mtime_ns

        second_sha, second_new = store.write_raw_html(b"<html>same</html>")

        assert first_sha == second_sha
        assert first_new is True
        assert second_new is False
        # Content untouched -- "never overwrite" is checkable, not just claimed.
        assert store.raw_html_path(second_sha).stat().st_mtime_ns == mtime_after_first

    def test_different_bytes_produce_different_hashes(self) -> None:
        sha_a, _ = store.write_raw_html(b"<html>a</html>")
        sha_b, _ = store.write_raw_html(b"<html>b</html>")
        assert sha_a != sha_b
        assert store.raw_html_path(sha_a).is_file()
        assert store.raw_html_path(sha_b).is_file()


class TestSnapshotStorage:
    def test_first_write_is_new(self) -> None:
        snapshot = _sample_snapshot()
        assert store.write_snapshot(snapshot) is True
        assert store.snapshot_path(snapshot.port_id, snapshot.content_sha256).is_file()

    def test_reobserving_identical_content_creates_no_second_snapshot(self) -> None:
        """The literal BT-0 requirement: a repeat fetch of identical content
        is a re-observation, not a new snapshot."""
        snapshot = _sample_snapshot(sha256="b" * 64)

        first_write = store.write_snapshot(snapshot)
        path = store.snapshot_path(snapshot.port_id, snapshot.content_sha256)
        stored_after_first = path.read_text(encoding="utf-8")

        second_write = store.write_snapshot(snapshot)

        assert first_write is True
        assert second_write is False
        # File on disk is byte-identical to what the first write produced --
        # the second call did not touch it.
        assert path.read_text(encoding="utf-8") == stored_after_first
        # And there is exactly one snapshot file for this port, not two.
        assert store.list_snapshot_hashes(snapshot.port_id) == [snapshot.content_sha256]

    def test_two_different_contents_are_two_snapshots(self) -> None:
        first = _sample_snapshot(sha256="c" * 64)
        second = _sample_snapshot(sha256="d" * 64)
        store.write_snapshot(first)
        store.write_snapshot(second)
        assert set(store.list_snapshot_hashes(first.port_id)) == {first.content_sha256, second.content_sha256}

    def test_round_trips_through_the_store_unchanged(self) -> None:
        original = _sample_snapshot(sha256="e" * 64)
        store.write_snapshot(original)
        loaded = store.load_snapshot(original.port_id, original.content_sha256)

        assert loaded.port_id == original.port_id
        assert loaded.content_sha256 == original.content_sha256
        assert loaded.tables_present == original.tables_present
        assert loaded.fetched_at == original.fetched_at

    def test_different_ports_do_not_collide(self) -> None:
        dhamra = _sample_snapshot(sha256="f" * 64)
        gangavaram = ScheduleSnapshot(
            port_id=PortId.GANGAVARAM,
            fetched_at=datetime(2026, 8, 27, 12, 0, 0),
            source_url="https://example.invalid/fixture",
            content_sha256="f" * 64,  # same hash, different port -- must not collide
            tables_present=frozenset(),
            tables_empty=frozenset(),
            rows=(),
        )
        store.write_snapshot(dhamra)
        store.write_snapshot(gangavaram)
        assert store.list_snapshot_hashes(PortId.DHAMRA) == ["f" * 64]
        assert store.list_snapshot_hashes(PortId.GANGAVARAM) == ["f" * 64]


class TestObservationLedger:
    def test_append_is_additive_across_calls(self) -> None:
        store.append_observation(
            store.Observation(
                fetched_at=datetime(2026, 8, 27, 6, 0, 0),
                port_id=PortId.DHAMRA,
                source_url="https://example.invalid/fixture",
                content_sha256="a" * 64,
                is_new_snapshot=True,
            )
        )
        store.append_observation(
            store.Observation(
                fetched_at=datetime(2026, 8, 27, 6, 30, 0),
                port_id=PortId.DHAMRA,
                source_url="https://example.invalid/fixture",
                content_sha256="a" * 64,
                is_new_snapshot=False,
            )
        )
        lines = store.OBSERVATIONS_LOG.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2

    def test_a_missing_ledger_file_is_created_on_first_append(self) -> None:
        assert not store.OBSERVATIONS_LOG.exists()
        store.append_observation(
            store.Observation(
                fetched_at=datetime(2026, 8, 27, 6, 0, 0),
                port_id=PortId.GANGAVARAM,
                source_url="https://example.invalid/fixture",
                content_sha256="a" * 64,
                is_new_snapshot=True,
            )
        )
        assert store.OBSERVATIONS_LOG.exists()


class TestListSnapshotHashes:
    def test_a_port_never_archived_returns_empty(self) -> None:
        assert store.list_snapshot_hashes(PortId.DHAMRA) == []
