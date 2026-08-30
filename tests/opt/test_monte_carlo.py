"""Tests for opt.monte_carlo — covers both the original single-shock path and
the new OU mean-reverting path introduced with MonteCarloConfig.
"""
from __future__ import annotations

import pytest

from opt.monte_carlo import (
    MonteCarloConfig,
    SavingsDistribution,
    estimate_savings_distribution,
)
from opt.types import ForecastFan, VesselClass

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def fans_supramax() -> list[ForecastFan]:
    """Standard 3-horizon fan used across multiple tests."""
    return [
        ForecastFan(vessel_class=VesselClass.SUPRAMAX, horizon_days=7,  p10=8000,  p50=10000, p90=12000),
        ForecastFan(vessel_class=VesselClass.SUPRAMAX, horizon_days=30, p10=9000,  p50=12000, p90=15000),
        ForecastFan(vessel_class=VesselClass.SUPRAMAX, horizon_days=90, p10=10000, p50=14000, p90=18000),
    ]


# ---------------------------------------------------------------------------
# Original test (preserved — must still pass)
# ---------------------------------------------------------------------------

def test_monte_carlo_savings(fans_supramax: list[ForecastFan]) -> None:
    # Model predicts market going UP from 10k to 14k over 90 days.
    # Fan is fairly wide (uncertainty).

    # We are offered a TC quote of 11,500 for a 30-day term.
    # The P50 spot average over those 30 days is roughly 11,000 (starts at 10k, ends at 12k).
    # Since the quote (11,500) is slightly HIGHER than the P50 spot average (11,000),
    # the EXPECTED savings should be slightly negative (we lose money by locking).
    # But because the market *could* explode upward (P90 is 15k), the best-case savings will be positive.

    dist = estimate_savings_distribution(
        forecasts=fans_supramax,
        vessel_class=VesselClass.SUPRAMAX,
        contract_term_days=30,
        today_quote_usd_per_day=11_500,
        num_simulations=10_000,  # High count for stable percentiles
    )

    assert dist.vessel_class == VesselClass.SUPRAMAX
    assert dist.contract_term_days == 30

    # P50 savings should be roughly -500 (since spot avg ~ 11,000 and quote is 11,500)
    assert -1000 < dist.expected_p50_savings < 0

    # Worst case P10 (market drops to the 8k-9k range)
    # Spot avg ~ 8.5k. Quote = 11.5k. Savings = -3000.
    assert -4000 < dist.worst_case_p10_savings < -2000

    # Best case P90 (market moons to the 12k-15k range)
    # Spot avg ~ 13.5k. Quote = 11.5k. Savings = +2000.
    assert 1000 < dist.best_case_p90_savings < 3000

    # There should be *some* probability of positive savings, but < 50%
    assert 0.1 < dist.prob_positive_savings < 0.5


# ---------------------------------------------------------------------------
# New tests: MonteCarloConfig / OU process
# ---------------------------------------------------------------------------


class TestMonteCarloConfigDefaults:
    """theta=0 should give the same *shape* of result as the original code."""

    def test_theta_zero_returns_savings_distribution(self, fans_supramax: list[ForecastFan]) -> None:
        cfg = MonteCarloConfig(theta=0.0, num_simulations=2000)
        dist = estimate_savings_distribution(
            forecasts=fans_supramax,
            vessel_class=VesselClass.SUPRAMAX,
            contract_term_days=30,
            today_quote_usd_per_day=11_500,
            config=cfg,
        )
        assert isinstance(dist, SavingsDistribution)
        assert dist.vessel_class == VesselClass.SUPRAMAX
        assert dist.contract_term_days == 30

    def test_theta_zero_p10_lt_p50_lt_p90(self, fans_supramax: list[ForecastFan]) -> None:
        cfg = MonteCarloConfig(theta=0.0, num_simulations=3000)
        dist = estimate_savings_distribution(
            forecasts=fans_supramax,
            vessel_class=VesselClass.SUPRAMAX,
            contract_term_days=30,
            today_quote_usd_per_day=11_500,
            config=cfg,
        )
        assert dist.worst_case_p10_savings < dist.expected_p50_savings < dist.best_case_p90_savings

    def test_theta_zero_matches_original_qualitative_bounds(
        self, fans_supramax: list[ForecastFan]
    ) -> None:
        """theta=0 result should satisfy the same qualitative bounds as the original test."""
        cfg = MonteCarloConfig(theta=0.0, num_simulations=10_000)
        dist = estimate_savings_distribution(
            forecasts=fans_supramax,
            vessel_class=VesselClass.SUPRAMAX,
            contract_term_days=30,
            today_quote_usd_per_day=11_500,
            config=cfg,
        )
        assert -1000 < dist.expected_p50_savings < 0
        assert -4000 < dist.worst_case_p10_savings < -2000
        assert 1000 < dist.best_case_p90_savings < 3000
        assert 0.1 < dist.prob_positive_savings < 0.5


class TestMeanReversionNarrowsSpread:
    """Fast mean reversion (theta=0.3) should produce a narrower P10-P90
    spread than the flat-shock baseline (theta=0) for a long 90-day contract.
    """

    def _spread(self, fans: list[ForecastFan], theta: float, n_sims: int = 8000) -> float:
        cfg = MonteCarloConfig(theta=theta, sigma_long=0.30, num_simulations=n_sims)
        dist = estimate_savings_distribution(
            forecasts=fans,
            vessel_class=VesselClass.SUPRAMAX,
            contract_term_days=90,
            today_quote_usd_per_day=12_000,
            config=cfg,
        )
        return dist.best_case_p90_savings - dist.worst_case_p10_savings

    def test_ou_spread_narrower_than_flat_shock(self, fans_supramax: list[ForecastFan]) -> None:
        spread_flat = self._spread(fans_supramax, theta=0.0)
        spread_ou = self._spread(fans_supramax, theta=0.3)
        # OU mean reversion compresses tail uncertainty → narrower spread
        assert spread_ou < spread_flat, (
            f"Expected OU spread ({spread_ou:.0f}) < flat spread ({spread_flat:.0f}) "
            "for a 90-day contract"
        )

    def test_ou_returns_valid_distribution(self, fans_supramax: list[ForecastFan]) -> None:
        cfg = MonteCarloConfig(theta=0.3, sigma_long=0.30, num_simulations=2000)
        dist = estimate_savings_distribution(
            forecasts=fans_supramax,
            vessel_class=VesselClass.SUPRAMAX,
            contract_term_days=90,
            today_quote_usd_per_day=12_000,
            config=cfg,
        )
        assert isinstance(dist, SavingsDistribution)
        assert dist.worst_case_p10_savings < dist.expected_p50_savings < dist.best_case_p90_savings
        assert 0.0 <= dist.prob_positive_savings <= 1.0


class TestPriceFloor:
    """No simulated path should produce a day-spot below floor_usd."""

    def _run_with_very_low_fans(self, theta: float, floor: float = 4000.0) -> SavingsDistribution:
        """Create fans well below the floor to stress-test the floor logic."""
        fans = [
            ForecastFan(vessel_class=VesselClass.SUPRAMAX, horizon_days=7,  p10=100,  p50=500,  p90=1000),
            ForecastFan(vessel_class=VesselClass.SUPRAMAX, horizon_days=30, p10=200,  p50=600,  p90=1200),
            ForecastFan(vessel_class=VesselClass.SUPRAMAX, horizon_days=90, p10=300,  p50=700,  p90=1400),
        ]
        cfg = MonteCarloConfig(theta=theta, sigma_long=0.30, floor_usd=floor, num_simulations=500)
        return estimate_savings_distribution(
            forecasts=fans,
            vessel_class=VesselClass.SUPRAMAX,
            contract_term_days=30,
            today_quote_usd_per_day=5_000,
            config=cfg,
        )

    def test_floor_respected_theta_zero(self) -> None:
        """With theta=0 and fans way below floor, worst savings = floor - quote."""
        floor = 4000.0
        quote = 5_000.0
        dist = self._run_with_very_low_fans(theta=0.0, floor=floor)
        # Worst-case savings: spot avg = floor, savings = floor - quote (negative)
        assert dist.worst_case_p10_savings >= floor - quote - 1  # allow 1 USD float error

    def test_floor_respected_theta_ou(self) -> None:
        """With theta>0 and fans way below floor, the OU path is still floored."""
        floor = 4000.0
        quote = 5_000.0
        dist = self._run_with_very_low_fans(theta=0.3, floor=floor)
        # Every day_spot >= floor, so path_avg >= floor
        # savings = path_avg - quote >= floor - quote
        assert dist.worst_case_p10_savings >= floor - quote - 1

    def test_floor_custom_value(self) -> None:
        """Custom floor_usd is respected for both theta modes."""
        fans = [
            ForecastFan(vessel_class=VesselClass.SUPRAMAX, horizon_days=7,  p10=1000, p50=3000, p90=5000),
            ForecastFan(vessel_class=VesselClass.SUPRAMAX, horizon_days=30, p10=1000, p50=3000, p90=5000),
        ]
        custom_floor = 6000.0
        cfg = MonteCarloConfig(theta=0.0, floor_usd=custom_floor, num_simulations=500)
        dist = estimate_savings_distribution(
            forecasts=fans,
            vessel_class=VesselClass.SUPRAMAX,
            contract_term_days=30,
            today_quote_usd_per_day=10_000,
            config=cfg,
        )
        # worst savings = floor - quote = 6000 - 10000 = -4000
        assert dist.worst_case_p10_savings >= custom_floor - 10_000 - 1
