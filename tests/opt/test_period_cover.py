"""Break-even hire for a season plan, and the spot benchmark it is judged against.

The thing most worth pinning here is what this module refuses to do. There is
no period charter rate anywhere in this repository -- the only real $/day
series on disk are the Baltic class TC *averages*, which are spot indices. A
test suite that only checked the arithmetic would happily keep passing if
someone later "improved" the module by deriving a period rate from the spot
average with an assumed premium, which is exactly the failure the module exists
to avoid.
"""

from __future__ import annotations

from datetime import date

import pytest

from opt.period_cover import (
    NoRealTcAverageError,
    assess_period_cover,
    real_spot_tc_average,
)

#: A date the market was really open, with a published Supramax TC average.
_OPEN_DAY = date(2026, 8, 20)


class TestRealSpotBenchmark:
    def test_reads_the_real_published_average(self) -> None:
        value, series_id = real_spot_tc_average("Supramax", _OPEN_DAY)
        assert series_id == "SUPRAMAX_TCAVG"
        # Not pinned to an exact figure -- the data file is rebuilt from real
        # pulls and the value legitimately changes. What must hold is that it
        # is a real dry-bulk daily rate and not a placeholder.
        assert 3_000 < value < 200_000

    def test_every_mapped_class_resolves(self) -> None:
        for cls in ("Capesize", "Panamax", "Supramax", "Handysize"):
            value, series_id = real_spot_tc_average(cls, _OPEN_DAY)
            assert series_id.endswith("_TCAVG")
            assert value > 0

    def test_unknown_class_raises_rather_than_defaulting(self) -> None:
        with pytest.raises(NoRealTcAverageError, match="Ultramax"):
            real_spot_tc_average("Ultramax", _OPEN_DAY)

    def test_a_date_with_no_observation_raises(self) -> None:
        """The index is not published every calendar day. Carrying the last
        value forward would be a different number wearing today's date."""
        with pytest.raises(NoRealTcAverageError):
            real_spot_tc_average("Supramax", date(1990, 1, 1))


class TestBreakEven:
    def test_break_even_is_profit_over_ship_days(self) -> None:
        a = assess_period_cover(
            total_profit_usd=1_020_000.0,
            n_vessels=2,
            span_hours=51 * 24,
            vessel_class="Supramax",
            as_of=_OPEN_DAY,
        )
        assert a.ship_days == pytest.approx(102.0)
        assert a.break_even_hire_usd_per_day == pytest.approx(10_000.0)

    def test_idle_vessels_still_consume_ship_days(self) -> None:
        """Chartering three ships and using two still costs three ships'
        hire, so the break-even must fall as the fleet grows on fixed
        profit."""
        kw = {
            "total_profit_usd": 1_000_000.0,
            "span_hours": 30 * 24,
            "vessel_class": "Supramax",
            "as_of": _OPEN_DAY,
        }
        two = assess_period_cover(n_vessels=2, **kw)  # type: ignore[arg-type]
        three = assess_period_cover(n_vessels=3, **kw)  # type: ignore[arg-type]
        assert three.break_even_hire_usd_per_day < two.break_even_hire_usd_per_day

    def test_a_loss_making_book_gives_a_negative_break_even(self) -> None:
        """A real answer, not an error: no hire rate makes a loss-making
        programme worth covering."""
        a = assess_period_cover(
            total_profit_usd=-500_000.0,
            n_vessels=1,
            span_hours=10 * 24,
            vessel_class="Supramax",
            as_of=_OPEN_DAY,
        )
        assert a.break_even_hire_usd_per_day < 0
        assert a.verdict == "spot_beats_cover"

    def test_no_vessels_or_no_span_raises(self) -> None:
        base = {"total_profit_usd": 1.0, "vessel_class": "Supramax", "as_of": _OPEN_DAY}
        with pytest.raises(ValueError):
            assess_period_cover(n_vessels=0, span_hours=24, **base)  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            assess_period_cover(n_vessels=1, span_hours=0, **base)  # type: ignore[arg-type]


class TestVerdict:
    def test_beating_the_spot_average_is_reported_with_its_margin(self) -> None:
        spot, _ = real_spot_tc_average("Supramax", _OPEN_DAY)
        # One ship for one day earning twice the spot average.
        a = assess_period_cover(
            total_profit_usd=spot * 2,
            n_vessels=1,
            span_hours=24,
            vessel_class="Supramax",
            as_of=_OPEN_DAY,
        )
        assert a.verdict == "cover_beats_spot"
        assert a.margin_over_spot_usd_per_day == pytest.approx(spot)

    def test_missing_benchmark_is_its_own_verdict_not_a_neutral_one(self) -> None:
        """No real spot rate is a gap in the data. Reporting it as "spot
        wins" or "cover wins" would be inventing the comparison."""
        a = assess_period_cover(
            total_profit_usd=1_000_000.0,
            n_vessels=1,
            span_hours=30 * 24,
            vessel_class="Supramax",
            as_of=date(1990, 1, 1),
        )
        assert a.verdict == "no_benchmark"
        assert a.spot_tc_average_usd_per_day is None
        assert a.margin_over_spot_usd_per_day is None
        assert a.spot_tc_as_of is None
        # The break-even itself is unaffected -- it needs no market data.
        assert a.break_even_hire_usd_per_day == pytest.approx(1_000_000.0 / 30.0)


class TestPeriodOffer:
    def test_an_offer_below_the_break_even_clears(self) -> None:
        a = assess_period_cover(
            total_profit_usd=1_020_000.0,
            n_vessels=2,
            span_hours=51 * 24,
            vessel_class="Supramax",
            as_of=_OPEN_DAY,
        )
        assert a.clears_period_offer(9_999) is True
        assert a.clears_period_offer(10_001) is False


class TestNoInventedPeriodRate:
    def test_the_benchmark_is_the_spot_index_itself_not_a_premium_over_it(self) -> None:
        """Guards the module's whole reason for existing.

        If anyone ever adds a period-premium multiplier to turn the spot
        average into a "period rate", this fails. The benchmark must be the
        published number, unmodified, because no real period quote exists on
        disk to calibrate a premium against.
        """
        published, series_id = real_spot_tc_average("Supramax", _OPEN_DAY)
        a = assess_period_cover(
            total_profit_usd=1.0,
            n_vessels=1,
            span_hours=24,
            vessel_class="Supramax",
            as_of=_OPEN_DAY,
        )
        assert a.spot_tc_average_usd_per_day == published
        assert a.spot_tc_series_id == series_id
