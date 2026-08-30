"""P4 requirement 7 -- point-in-time snapshot archiving: real round-trip,
the disclosed "no retroactive history" limitation, and no second backtest
system.
"""
from __future__ import annotations

from datetime import UTC, date, datetime

import polars as pl
import pytest

from data_builders import pit_archive as pit


@pytest.fixture()
def isolated_archive(tmp_path, monkeypatch):
    """Real archiving logic, against a real but throwaway directory -- never
    writes into the project's own raw_data/pit_archive during tests."""
    monkeypatch.setattr(pit, "PIT_ARCHIVE_DIR", tmp_path / "pit_archive")
    monkeypatch.setattr(pit, "SNAPSHOTS_DIR", tmp_path / "pit_archive" / "snapshots")
    monkeypatch.setattr(pit, "MANIFEST_LOG", tmp_path / "pit_archive" / "manifest.jsonl")
    return tmp_path


class TestArchiveRoundTrip:
    def test_archiving_a_frame_and_reading_it_back_is_byte_identical(self, isolated_archive) -> None:
        df = pl.DataFrame({"series_id": ["A", "B"], "date": [date(2026, 1, 1), date(2026, 1, 2)], "value": [1.0, 2.0]})
        retrieved = datetime(2026, 1, 5, tzinfo=UTC)
        entry = pit.archive_snapshot("master_long", df=df, retrieved_at=retrieved)
        assert entry.is_new_snapshot is True
        assert entry.n_rows == 2

        loaded = pit.load_snapshot_as_of("master_long", date(2026, 1, 5))
        assert loaded is not None
        assert loaded.equals(df)

    def test_identical_content_archived_twice_is_a_re_observation_not_a_duplicate(self, isolated_archive) -> None:
        df = pl.DataFrame({"series_id": ["A"], "date": [date(2026, 1, 1)], "value": [1.0]})
        first = pit.archive_snapshot("master_long", df=df, retrieved_at=datetime(2026, 1, 1, tzinfo=UTC))
        second = pit.archive_snapshot("master_long", df=df, retrieved_at=datetime(2026, 1, 2, tzinfo=UTC))
        assert first.is_new_snapshot is True
        assert second.is_new_snapshot is False
        assert first.content_sha256 == second.content_sha256
        # Only one real parquet file on disk for this content.
        snap_dir = pit.SNAPSHOTS_DIR / "master_long"
        assert len(list(snap_dir.glob("*.parquet"))) == 1

    def test_revised_content_produces_a_new_real_snapshot(self, isolated_archive) -> None:
        df1 = pl.DataFrame({"series_id": ["A"], "date": [date(2026, 1, 1)], "value": [1.0]})
        df2 = pl.DataFrame({"series_id": ["A"], "date": [date(2026, 1, 1)], "value": [1.5]})  # a real revision
        pit.archive_snapshot("master_long", df=df1, retrieved_at=datetime(2026, 1, 1, tzinfo=UTC))
        entry2 = pit.archive_snapshot("master_long", df=df2, retrieved_at=datetime(2026, 1, 10, tzinfo=UTC))
        assert entry2.is_new_snapshot is True

        as_of_early = pit.load_snapshot_as_of("master_long", date(2026, 1, 5))
        as_of_late = pit.load_snapshot_as_of("master_long", date(2026, 1, 15))
        assert as_of_early is not None and as_of_early["value"][0] == 1.0
        assert as_of_late is not None and as_of_late["value"][0] == 1.5

    def test_manifest_is_append_only_and_real(self, isolated_archive) -> None:
        df = pl.DataFrame({"series_id": ["A"], "date": [date(2026, 1, 1)], "value": [1.0]})
        pit.archive_snapshot("master_long", df=df, retrieved_at=datetime(2026, 1, 1, tzinfo=UTC))
        pit.archive_snapshot("macro_long", df=df, retrieved_at=datetime(2026, 1, 2, tzinfo=UTC))
        all_entries = pit.list_manifest()
        assert len(all_entries) == 2
        master_only = pit.list_manifest("master_long")
        assert len(master_only) == 1
        assert master_only[0].source == "master_long"


class TestDisclosedLimitation:
    """P4 edge case: for historical periods without vintage data, disclose
    the limitation rather than silently substitute today's file."""

    def test_a_date_before_any_archiving_returns_none_not_current_data(self, isolated_archive) -> None:
        df = pl.DataFrame({"series_id": ["A"], "date": [date(2026, 6, 1)], "value": [1.0]})
        pit.archive_snapshot("master_long", df=df, retrieved_at=datetime(2026, 6, 1, tzinfo=UTC))
        result = pit.load_snapshot_as_of("master_long", date(2020, 1, 1))
        assert result is None

    def test_no_archiving_at_all_returns_empty_manifest_not_an_error(self, isolated_archive) -> None:
        assert pit.list_manifest() == []
        assert pit.load_snapshot_as_of("master_long", date(2026, 1, 1)) is None


class TestRealSourceIntegration:
    def test_archive_all_sources_against_the_real_repo_files(self, isolated_archive) -> None:
        """Not mocked -- reads the real master_long.parquet/macro_long.parquet
        already built on disk (into the isolated archive dir, not the real
        one)."""
        entries = pit.archive_all_sources()
        sources = {e.source for e in entries}
        assert "master_long" in sources  # always present in this repo
        for e in entries:
            assert e.n_rows > 0


class TestNoSecondBacktestSystem:
    def test_module_does_not_read_the_frozen_test_split(self) -> None:
        import re
        from pathlib import Path

        src = Path(__file__).resolve().parents[2] / "src" / "data_builders" / "pit_archive.py"
        text = src.read_text(encoding="utf-8")
        assert re.search(r"""load_split\(\s*['"]test['"]\s*\)""", text) is None
        # archives snapshots; does not itself CALL the guard (a docstring
        # reference explaining how a caller would integrate is fine and
        # expected -- an actual invocation would mean this module quietly
        # started reading the frozen split itself).
        assert "load_frozen_test()" not in text
