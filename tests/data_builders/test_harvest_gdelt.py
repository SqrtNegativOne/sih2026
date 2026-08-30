"""Tests for data_builders.harvest_gdelt -- no network. The parsing test
uses a real, trimmed sample of the actual GDELT 2.0 daily Event Database
export (fetched by hand from data.gdeltproject.org/events/20240115.export.CSV.zip
and trimmed to six real rows -- three real conflict-coded events that
geolocate inside a real opt.chokepoints circle, plus three real non-matches
-- see tests/data_builders/fixtures/gdelt_sample.export.CSV), not a
hand-written fictional response shape."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from data_builders.harvest_gdelt import (
    _accumulate_day,
    _append_week_rows,
    _existing_weeks,
    _WeekAccumulator,
    harvest,
)
from opt.chokepoints import CHOKEPOINT_GEOMETRY

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "gdelt_sample.export.CSV"


def _fixture_lines() -> list[str]:
    return _FIXTURE_PATH.read_text(encoding="utf-8").splitlines()


# ---------------------------------------------------------------------------
# Parsing -- against the real, trimmed sample
# ---------------------------------------------------------------------------


def test_real_sample_parses_into_the_expected_rows():
    accumulators = {cp_id: _WeekAccumulator() for cp_id in CHOKEPOINT_GEOMETRY}
    _accumulate_day(_fixture_lines(), accumulators)

    # Three real conflict-coded events in the fixture, each geolocated
    # inside a different real chokepoint's circle (Bosporus, Dover,
    # Oresund) -- exact values reproduced independently from the same
    # fixture file, not hardcoded blind.
    assert accumulators["chokepoint3"].event_count == 1
    assert accumulators["chokepoint3"].tone_sum == pytest.approx(-4.50957193200347)
    assert accumulators["chokepoint3"].n_source_articles == 14

    assert accumulators["chokepoint9"].event_count == 1
    assert accumulators["chokepoint9"].tone_sum == pytest.approx(-9.25266903914592)
    assert accumulators["chokepoint9"].n_source_articles == 10

    assert accumulators["chokepoint10"].event_count == 1
    assert accumulators["chokepoint10"].tone_sum == pytest.approx(1.84409052808047)
    assert accumulators["chokepoint10"].n_source_articles == 6


def test_real_sample_does_not_match_the_wrong_event_codes_or_far_away_events():
    # The fixture also has: two real rows with a non-conflict root code
    # (07, Provide Aid) that must not match ANY chokepoint, and one real
    # conflict-coded (18) row far from every chokepoint circle.
    accumulators = {cp_id: _WeekAccumulator() for cp_id in CHOKEPOINT_GEOMETRY}
    _accumulate_day(_fixture_lines(), accumulators)

    total_matched = sum(acc.event_count for acc in accumulators.values())
    assert total_matched == 3  # exactly the three real matches, not six


def test_malformed_and_short_lines_are_skipped_without_raising():
    lines = [
        "",  # empty
        "too\tfew\tcolumns",  # far under 58 columns
        *_fixture_lines(),
    ]
    accumulators = {cp_id: _WeekAccumulator() for cp_id in CHOKEPOINT_GEOMETRY}
    _accumulate_day(lines, accumulators)  # must not raise
    assert sum(acc.event_count for acc in accumulators.values()) == 3


# ---------------------------------------------------------------------------
# Incremental / checkpointed
# ---------------------------------------------------------------------------


def test_existing_weeks_reads_back_what_was_written(tmp_path: Path):
    out_path = tmp_path / "chokepoint_conflict_weekly.csv"
    rows = [
        {
            "chokepoint_id": "chokepoint1", "iso_year": 2024, "iso_week": w,
            "event_count": 0, "avg_tone": "", "n_source_articles": 0, "retrieved_at": "2024-01-01T00:00:00+00:00",
        }
        for w in range(1, 11)
    ]
    _append_week_rows(out_path, rows)
    assert _existing_weeks(out_path) == {(2024, w) for w in range(1, 11)}


def test_harvest_only_requests_weeks_not_already_present(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    out_path = tmp_path / "chokepoint_conflict_weekly.csv"
    # Pre-seed weeks 1-10 of 2024 as already done.
    existing_rows = [
        {
            "chokepoint_id": "chokepoint1", "iso_year": 2024, "iso_week": w,
            "event_count": 0, "avg_tone": "", "n_source_articles": 0, "retrieved_at": "2024-01-01T00:00:00+00:00",
        }
        for w in range(1, 11)
    ]
    _append_week_rows(out_path, existing_rows)

    requested_days: list[date] = []

    def fake_fetch(day: date) -> list[str] | None:
        requested_days.append(day)
        return []  # a real (empty) day, not a failure

    import data_builders.harvest_gdelt as gdelt_module

    monkeypatch.setattr(gdelt_module, "_fetch_daily_events", fake_fetch)
    monkeypatch.setattr(gdelt_module, "REQUEST_DELAY_SECONDS", 0.0)

    # 2024 week 12 spans 2024-03-18 to 2024-03-24 -- request through week 12
    # only; every requested day must fall on/after week 11's Monday.
    harvest(date(2024, 1, 1), date(2024, 3, 24), out_path=out_path, request_delay_seconds=0.0)

    week_11_monday = date.fromisocalendar(2024, 11, 1)
    assert requested_days, "harvest() should have requested at least the new weeks' days"
    assert all(d >= week_11_monday for d in requested_days), (
        f"a day before week 11 was requested even though weeks 1-10 already existed: "
        f"{[d for d in requested_days if d < week_11_monday]}"
    )


# ---------------------------------------------------------------------------
# Resilience -- one bad day/week must not abort the whole run
# ---------------------------------------------------------------------------


def test_a_failed_day_within_a_week_still_lets_the_weeks_other_days_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    out_path = tmp_path / "chokepoint_conflict_weekly.csv"
    real_lines = _fixture_lines()

    def flaky_fetch(day: date) -> list[str] | None:
        # Monday of the week fails; every other day of that week succeeds
        # and contributes the real fixture's matches.
        if day.isocalendar().weekday == 1:
            return None
        return real_lines

    import data_builders.harvest_gdelt as gdelt_module

    monkeypatch.setattr(gdelt_module, "_fetch_daily_events", flaky_fetch)

    # A single-week window.
    monday = date.fromisocalendar(2024, 5, 1)
    sunday = date.fromisocalendar(2024, 5, 7)
    n_written = harvest(monday, sunday, out_path=out_path, request_delay_seconds=0.0)

    assert n_written == len(CHOKEPOINT_GEOMETRY)  # the week still produced a real row set
    weeks = _existing_weeks(out_path)
    assert (2024, 5) in weeks


def test_a_week_where_every_day_fails_is_skipped_not_written_as_a_false_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    out_path = tmp_path / "chokepoint_conflict_weekly.csv"

    import data_builders.harvest_gdelt as gdelt_module

    monkeypatch.setattr(gdelt_module, "_fetch_daily_events", lambda day: None)

    monday = date.fromisocalendar(2024, 6, 1)
    sunday = date.fromisocalendar(2024, 6, 7)
    n_written = harvest(monday, sunday, out_path=out_path, request_delay_seconds=0.0)

    assert n_written == 0
    assert (2024, 6) not in _existing_weeks(out_path)


def test_one_failed_week_does_not_abort_a_later_weeks_processing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    out_path = tmp_path / "chokepoint_conflict_weekly.csv"
    real_lines = _fixture_lines()

    week10_monday = date.fromisocalendar(2024, 10, 1)
    week10_sunday = date.fromisocalendar(2024, 10, 7)

    def fetch(day: date) -> list[str] | None:
        if week10_monday <= day <= week10_sunday:
            return None  # week 10 entirely fails
        return real_lines  # every other day succeeds

    import data_builders.harvest_gdelt as gdelt_module

    monkeypatch.setattr(gdelt_module, "_fetch_daily_events", fetch)

    start = date.fromisocalendar(2024, 9, 1)
    end = date.fromisocalendar(2024, 11, 7)
    harvest(start, end, out_path=out_path, request_delay_seconds=0.0)

    weeks = _existing_weeks(out_path)
    assert (2024, 9) in weeks
    assert (2024, 10) not in weeks  # the entirely-failed week, correctly absent
    assert (2024, 11) in weeks  # processing continued past the failed week
