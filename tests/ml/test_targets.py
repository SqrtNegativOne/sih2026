"""Tests for calendar-horizon target construction.

The property under test is that ``y_step_h{h}`` really is the value h *calendar* days
ahead. The implementation it replaces stepped h rows, which made the h=90 target
resolve 132 days out while every consumer priced it as 90.
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest

from ml.targets import (
    build_forward_targets,
    required_embargo_days,
    slip_tolerance_days,
)


def _series(dates: list[date], values: list[float]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "date": dates,
            "target_value": values,
            "log_value": np.log(np.array(values, dtype=float)),
        }
    )


def _weekdays(start: date, n: int) -> list[date]:
    """n consecutive weekdays, so the series looks like a real trading calendar."""
    out: list[date] = []
    d = start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


# ---------------------------------------------------------------------------
# The core guarantee
# ---------------------------------------------------------------------------


def test_target_lands_h_calendar_days_ahead_not_h_rows() -> None:
    """The whole point of the module."""
    dates = _weekdays(date(2020, 1, 1), 400)
    values = [1000.0 + i for i in range(len(dates))]
    out = build_forward_targets(_series(dates, values), (30,))

    lookup = {d: v for d, v in zip(dates, values)}
    rows = out.drop_nulls("y_step_h30")
    assert rows.height > 300

    for row in rows.iter_rows(named=True):
        intended = row["date"] + timedelta(days=30)
        # The resolved value must be the first trading day on or after intended.
        candidates = [d for d in dates if d >= intended]
        assert candidates, "test series too short"
        expected = lookup[candidates[0]]
        assert row["y_step_h30"] == pytest.approx(float(np.log(expected)))


def test_row_stepping_would_give_a_different_answer() -> None:
    """Guards against silently reverting to shift(-h).

    On a weekday-only calendar, 30 rows is about 42 calendar days, so a row-stepped
    target is materially different from a calendar-stepped one.
    """
    dates = _weekdays(date(2020, 1, 1), 300)
    values = [1000.0 + i for i in range(len(dates))]
    df = _series(dates, values)
    out = build_forward_targets(df, (30,))

    calendar = out["y_step_h30"].to_numpy()
    row_stepped = np.concatenate([out["log_value"].to_numpy()[30:], [np.nan] * 30])
    both = ~np.isnan(calendar) & ~np.isnan(row_stepped)
    assert both.sum() > 100
    assert not np.allclose(calendar[both], row_stepped[both])


def test_resolution_slip_is_bounded_and_recorded() -> None:
    dates = _weekdays(date(2020, 1, 1), 300)
    values = [1000.0] * len(dates)
    out = build_forward_targets(_series(dates, values), (7, 30, 90))

    for h in (7, 30, 90):
        slip = out[f"y_slip_h{h}"].drop_nulls().to_numpy()
        assert slip.min() >= 0.0, "a target may never resolve before its intended date"
        assert slip.max() <= slip_tolerance_days(h)


def test_rows_beyond_the_slip_tolerance_are_dropped() -> None:
    """A long data hole must not silently become a longer-horizon observation."""
    early = _weekdays(date(2020, 1, 1), 40)
    late = _weekdays(date(2020, 6, 1), 40)  # ~3-month hole
    dates = early + late
    values = [1000.0] * len(dates)
    out = build_forward_targets(_series(dates, values), (7,))

    # Rows just before the hole cannot resolve a 7-day target within tolerance.
    tail = out.filter(pl.col("date") >= early[-5])
    assert tail.filter(pl.col("date") <= early[-1])["y_step_h7"].null_count() == len(
        [d for d in early if d >= early[-5]]
    )


# ---------------------------------------------------------------------------
# y_mean
# ---------------------------------------------------------------------------


def test_mean_target_averages_the_forward_window_exclusive_of_today() -> None:
    dates = _weekdays(date(2020, 1, 1), 200)
    values = [float(100 + i) for i in range(len(dates))]
    out = build_forward_targets(_series(dates, values), (30,))

    lookup = dict(zip(dates, values))
    row = out.drop_nulls("y_mean_h30").row(0, named=True)
    t = row["date"]
    window = [lookup[d] for d in dates if t < d <= t + timedelta(days=30)]
    assert window
    assert row["y_mean_h30"] == pytest.approx(float(np.log(np.mean(window))))


def test_mean_target_excludes_the_current_day() -> None:
    """You cannot charter at a rate you have not observed yet."""
    dates = _weekdays(date(2020, 1, 1), 120)
    # Today is an extreme outlier; if it leaked into the window the mean would move.
    values = [1000.0] * len(dates)
    values[0] = 100_000.0
    out = build_forward_targets(_series(dates, values), (30,))
    first = out.row(0, named=True)
    assert first["y_mean_h30"] == pytest.approx(float(np.log(1000.0)))


def test_targets_are_null_at_the_tail_rather_than_truncated() -> None:
    dates = _weekdays(date(2020, 1, 1), 100)
    out = build_forward_targets(_series(dates, [1000.0] * 100), (90,))
    assert out.height == 100, "rows must be preserved; the caller decides what to drop"
    assert out["y_step_h90"].null_count() > 0


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------


def test_unsorted_input_is_rejected() -> None:
    dates = [date(2020, 1, 3), date(2020, 1, 1), date(2020, 1, 2)]
    with pytest.raises(ValueError, match="sorted"):
        build_forward_targets(_series(dates, [1.0, 2.0, 3.0]), (7,))


def test_embargo_converts_lag_rows_to_calendar_days() -> None:
    """The original assert added rows to days and let a leaking split through."""
    needed = required_embargo_days(max_lag_rows=63, horizons=(7, 30, 90))
    assert needed > 63 + 90, "must exceed the old rows-plus-days arithmetic"
    assert 170 <= needed <= 200, f"expected roughly 181 days, got {needed}"
