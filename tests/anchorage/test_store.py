"""Tests for anchorage.store -- real JSONL persistence, no network/imagery."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from anchorage.detect import AnchorageCensus
from anchorage.store import latest_census, read_censuses, record_census


def _census(port: str, scene_id: str, acquired_at: datetime, vessel_count: int = 3) -> AnchorageCensus:
    return AnchorageCensus(
        port=port,
        scene_id=scene_id,
        acquired_at=acquired_at,
        vessel_count=vessel_count,
        detections=(),
        mean_sea_state_proxy=0.1,
        confidence="high",
        provenance="MODEL_DERIVED",
    )


class TestReadCensuses:
    def test_a_nonexistent_log_returns_empty_not_an_error(self, tmp_path: Path):
        assert read_censuses(path=tmp_path / "no_such_file.jsonl") == ()

    def test_a_recorded_census_round_trips_exactly(self, tmp_path: Path):
        path = tmp_path / "census.jsonl"
        c = _census("PARADIP", "scene-1", datetime(2026, 8, 20, 12, 0, tzinfo=UTC))
        record_census(c, path=path)
        result = read_censuses(path=path)
        assert result == (c,)

    def test_multiple_records_append_rather_than_overwrite(self, tmp_path: Path):
        path = tmp_path / "census.jsonl"
        c1 = _census("PARADIP", "scene-1", datetime(2026, 8, 1, tzinfo=UTC))
        c2 = _census("PARADIP", "scene-2", datetime(2026, 8, 15, tzinfo=UTC))
        record_census(c1, path=path)
        record_census(c2, path=path)
        assert read_censuses(path=path) == (c1, c2)

    def test_blank_lines_are_skipped_not_crashed_on(self, tmp_path: Path):
        path = tmp_path / "census.jsonl"
        c = _census("PARADIP", "scene-1", datetime(2026, 8, 1, tzinfo=UTC))
        record_census(c, path=path)
        with path.open("a") as f:
            f.write("\n\n")
        assert read_censuses(path=path) == (c,)


class TestLatestCensus:
    def test_no_records_for_the_port_returns_none(self, tmp_path: Path):
        path = tmp_path / "census.jsonl"
        record_census(_census("VISAKHAPATNAM", "s1", datetime(2026, 8, 1, tzinfo=UTC)), path=path)
        assert latest_census("PARADIP", path=path) is None

    def test_returns_the_most_recently_acquired_not_most_recently_written(self, tmp_path: Path):
        """Written out of chronological order on purpose -- 'latest' must
        mean latest real acquired_at, not last line in the file."""
        path = tmp_path / "census.jsonl"
        older = _census("PARADIP", "old", datetime(2026, 8, 1, tzinfo=UTC))
        newer = _census("PARADIP", "new", datetime(2026, 8, 20, tzinfo=UTC))
        record_census(newer, path=path)  # written first
        record_census(older, path=path)  # written second, but acquired earlier
        assert latest_census("PARADIP", path=path) == newer

    def test_only_matches_the_requested_port(self, tmp_path: Path):
        path = tmp_path / "census.jsonl"
        paradip = _census("PARADIP", "p1", datetime(2026, 8, 20, tzinfo=UTC))
        vizag = _census("VISAKHAPATNAM", "v1", datetime(2026, 8, 25, tzinfo=UTC))
        record_census(paradip, path=path)
        record_census(vizag, path=path)
        assert latest_census("PARADIP", path=path) == paradip
        assert latest_census("VISAKHAPATNAM", path=path) == vizag
