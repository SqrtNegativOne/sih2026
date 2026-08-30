"""Tests for opt.congestion -- dynamic port congestion -> expected wait-days.

Test strategy
-------------
The scaling math (_congestion_multiplier) is tested in isolation first with
synthetic, hand-verified inputs, matching opt.risk's own convention for its
_zscore_of_last helper. dynamic_wait_days itself is then tested against the
real IMF PortWatch data already on disk (real ports, real CSVs) -- loosely
for the full real port set (must never crash or warn), and with one pinned,
real, currently-true finding as a regression check.
"""
from __future__ import annotations

import warnings

import pytest

from opt.congestion import (
    _MAX_MULTIPLIER,
    _MIN_MULTIPLIER,
    _congestion_multiplier,
    clear_wait_days_cache,
    dynamic_wait_days,
)
from opt.network import PORT_TO_TONNAGE_LABEL, PortEnum

# ---------------------------------------------------------------------------
# Pure math: deterministic, synthetic
# ---------------------------------------------------------------------------

class TestCongestionMultiplier:
    def test_equal_recent_and_baseline_gives_one(self):
        m = _congestion_multiplier([5.0] * 14, [5.0] * 60)
        assert m == pytest.approx(1.0)

    def test_busier_recent_raises_multiplier(self):
        m = _congestion_multiplier([10.0] * 14, [5.0] * 60)
        assert m == pytest.approx(2.0)

    def test_quieter_recent_lowers_multiplier(self):
        m = _congestion_multiplier([2.5] * 14, [5.0] * 60)
        assert m == pytest.approx(0.5)

    def test_extreme_busy_clamped_to_max(self):
        m = _congestion_multiplier([100.0] * 14, [5.0] * 60)
        assert m == pytest.approx(_MAX_MULTIPLIER)

    def test_extreme_quiet_clamped_to_min(self):
        m = _congestion_multiplier([0.01] * 14, [5.0] * 60)
        assert m == pytest.approx(_MIN_MULTIPLIER)

    def test_zero_baseline_mean_returns_none(self):
        # No real activity to compare against -- not meaningful to say
        # "twice as busy as zero."
        assert _congestion_multiplier([5.0] * 14, [0.0] * 60) is None

    def test_at_min_multiplier_boundary_not_clamped_further(self):
        m = _congestion_multiplier([2.5] * 14, [5.0] * 60)  # exactly 0.5x
        assert m == pytest.approx(_MIN_MULTIPLIER)

    def test_at_max_multiplier_boundary_not_clamped_further(self):
        m = _congestion_multiplier([15.0] * 14, [5.0] * 60)  # exactly 3.0x
        assert m == pytest.approx(_MAX_MULTIPLIER)


# ---------------------------------------------------------------------------
# Real data: real IMF PortWatch CSVs already on disk
# ---------------------------------------------------------------------------

class TestDynamicWaitDaysRealData:
    def test_runs_clean_for_every_real_port_no_crash_no_warning(self):
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            for port in PortEnum:
                value, is_real = dynamic_wait_days(port)
                assert isinstance(value, float)
                assert isinstance(is_real, bool)
                assert value >= 0.0

    def test_ports_with_no_tonnage_label_use_the_unmodified_static_baseline(self):
        for port, label in PORT_TO_TONNAGE_LABEL.items():
            if label is not None:
                continue
            value, is_real = dynamic_wait_days(port)
            assert is_real is False
            assert value == pytest.approx(port.value.expected_wait_days)

    def test_real_data_result_is_bounded_by_the_clamp_range(self):
        for port in PortEnum:
            value, is_real = dynamic_wait_days(port)
            static = port.value.expected_wait_days
            if is_real:
                assert static * _MIN_MULTIPLIER - 1e-9 <= value <= static * _MAX_MULTIPLIER + 1e-9
            else:
                assert value == pytest.approx(static)

    def test_newcastle_au_currently_has_real_coverage(self):
        """Real, pinned finding: Newcastle_AU (a major, well-covered real
        port) has enough real PortWatch history on disk for a dynamic
        estimate as of when this test was written. A durable fact about
        this port's data coverage, not a specific multiplier value that
        would drift as new days of real data land."""
        value, is_real = dynamic_wait_days(PortEnum.NEWCASTLE_AU)
        assert is_real is True
        assert value > 0.0

    def test_cache_clear_does_not_error_and_is_still_consistent(self):
        before, _ = dynamic_wait_days(PortEnum.NEWCASTLE_AU)
        clear_wait_days_cache()
        after, _ = dynamic_wait_days(PortEnum.NEWCASTLE_AU)
        # Same real data on disk -> same real answer, cache or not.
        assert after == pytest.approx(before)


# ---------------------------------------------------------------------------
# F-21: as_of -- a historical quote must price congestion as of the date it
# claims to price, not always today's trailing rows.
# ---------------------------------------------------------------------------

class TestDynamicWaitDaysAsOf:
    def test_as_of_none_matches_legacy_unbounded_behaviour(self):
        clear_wait_days_cache()
        legacy = dynamic_wait_days(PortEnum.NEWCASTLE_AU)
        clear_wait_days_cache()
        explicit_none = dynamic_wait_days(PortEnum.NEWCASTLE_AU, None)
        assert explicit_none == legacy

    def test_a_real_historical_as_of_gives_a_genuinely_different_answer(self):
        """Pinned, real regression: Newcastle_AU's real PortWatch CSV runs
        2019-01-01 to well past 2022-10-23. Pricing as of that mid-history
        date must use only rows on/before it, not the file's full trailing
        window -- a different (real, recomputed) multiplier than "today"."""
        import datetime

        clear_wait_days_cache()
        today_value, today_real = dynamic_wait_days(PortEnum.NEWCASTLE_AU)
        clear_wait_days_cache()
        historical_value, historical_real = dynamic_wait_days(
            PortEnum.NEWCASTLE_AU, datetime.date(2022, 10, 23)
        )
        assert today_real is True
        assert historical_real is True
        assert historical_value != pytest.approx(today_value)

    def test_as_of_before_enough_real_history_exists_falls_back_to_static(self):
        import datetime

        clear_wait_days_cache()
        value, is_real = dynamic_wait_days(PortEnum.NEWCASTLE_AU, datetime.date(2019, 2, 1))
        assert is_real is False
        assert value == pytest.approx(PortEnum.NEWCASTLE_AU.value.expected_wait_days)

    def test_as_of_and_none_are_cached_separately_not_conflated(self):
        clear_wait_days_cache()
        import datetime

        dynamic_wait_days(PortEnum.NEWCASTLE_AU, datetime.date(2022, 10, 23))
        info_after_historical = dynamic_wait_days.cache_info()
        dynamic_wait_days(PortEnum.NEWCASTLE_AU)
        info_after_legacy = dynamic_wait_days.cache_info()
        # The legacy (None) call must be a fresh cache entry, not a hit on
        # the as_of-bearing call for the same port.
        assert info_after_legacy.misses == info_after_historical.misses + 1
