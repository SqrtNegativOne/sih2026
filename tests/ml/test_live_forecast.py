"""Real-data tests for ml.live_forecast -- the promoted run_live_scenario.py
logic. No synthetic fixtures: this module's whole job is turning real market
history + real trained models into a real forecast, so it's tested against
the real master_long.parquet + real exported models already on disk, same
as every other real-data module in this codebase."""
from __future__ import annotations

from datetime import timedelta

import polars as pl
import pytest

from ml.live_forecast import (
    default_master_path,
    forecast_all_classes,
    latest_available_date,
    resolve_as_of,
)
from opt.types import VesselClass


def test_latest_available_date_matches_real_bc_index_max():
    master = pl.read_parquet(default_master_path())
    expected = master.filter(pl.col("series_id") == "BC_INDEX")["date"].max()
    assert latest_available_date() == expected


def test_forecast_all_classes_returns_real_data_for_latest_date():
    today = latest_available_date()
    fans, quotes = forecast_all_classes(today)

    assert quotes, "expected at least one real TC quote at the latest real date"
    assert fans, "expected at least one class with real forecast fans"
    for cls in quotes:
        assert quotes[cls] > 0

    populated_classes = [cls for cls, class_fans in fans.items() if class_fans]
    assert populated_classes, "expected at least one class with real forecast horizons"
    for cls in populated_classes:
        for fan in fans[cls]:
            assert fan.vessel_class == cls
            assert fan.horizon_days in (7, 30, 90)
            assert fan.p10 <= fan.p50 <= fan.p90
            assert fan.p10 > 0


def test_forecast_all_classes_every_class_key_present_even_if_empty():
    today = latest_available_date()
    fans, _ = forecast_all_classes(today)
    assert set(fans.keys()) == set(VesselClass)


def test_forecast_all_classes_no_data_far_in_the_future_returns_empty():
    far_future = latest_available_date() + timedelta(days=3650)
    fans, quotes = forecast_all_classes(far_future)
    assert quotes == {}
    assert all(class_fans == [] for class_fans in fans.values())


@pytest.mark.parametrize("horizon", [7, 30, 90])
def test_fans_at_each_real_horizon_are_internally_consistent(horizon):
    today = latest_available_date()
    fans, _ = forecast_all_classes(today)
    for class_fans in fans.values():
        for fan in class_fans:
            if fan.horizon_days == horizon:
                assert fan.p10 <= fan.p50 <= fan.p90


class TestResolveAsOf:
    """F-15: an explicit as_of used to need to be an EXACT match to a real
    trading day -- a weekend or any other real gap day 503'd with no
    explanation. resolve_as_of finds the latest real trading day on or
    before the request instead, bounded so a genuinely out-of-range date
    (not just a nearby gap) is never silently reinterpreted as "today"."""

    def test_exact_match_passes_through_unchanged(self):
        today = latest_available_date()
        assert resolve_as_of(today) == today

    def test_a_weekend_resolves_back_to_the_last_real_trading_day(self):
        today = latest_available_date()
        master = pl.read_parquet(default_master_path())
        real_dates = sorted(
            master.filter(pl.col("series_id") == "BC_INDEX")["date"].unique().to_list()
        )
        # Find a real gap of at least 2 calendar days near the end of the
        # series (a real weekend/holiday) to resolve across, rather than
        # assuming a specific weekday lands on a specific real gap.
        gap_day = None
        for prev, nxt in zip(real_dates[-30:-1], real_dates[-29:]):
            if (nxt - prev).days >= 2:
                gap_day = prev + timedelta(days=1)
                expected = prev
                break
        if gap_day is None:
            pytest.skip("no real multi-day gap found in the last 30 trading days to test against")
        assert resolve_as_of(gap_day) == expected
        assert resolve_as_of(gap_day) <= today

    def test_far_future_is_not_silently_reinterpreted_as_today(self):
        """The bounded lookback's whole point: a date genuinely outside the
        real data's range must stay reported as missing data (see
        test_forecast_all_classes_no_data_far_in_the_future_returns_empty),
        not be quietly resolved to the latest real date as if that were
        what was actually requested."""
        far_future = latest_available_date() + timedelta(days=3650)
        assert resolve_as_of(far_future) == far_future

    def test_far_past_is_not_silently_reinterpreted(self):
        far_past = latest_available_date() - timedelta(days=3650 * 3)
        assert resolve_as_of(far_past) == far_past
