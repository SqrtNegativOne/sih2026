"""Tests for opt.backtest — the decision value backtester."""
from __future__ import annotations

import math
import random
from datetime import date

import polars as pl
import pytest

from opt.backtest import (
    _build_fan,
    simulate,
    summarise,
    to_dataframe,
)
from opt.monte_carlo import MonteCarloConfig
from opt.types import VesselClass


@pytest.fixture
def mock_test_split() -> pl.DataFrame:
    """Mock test split with two days for Supramax.
    
    Day 1 (2025-01-01): Spot = 10,000, Actual h30 = 12,000 (Market went up)
    Day 2 (2025-01-02): Spot = 10,000, Actual h30 =  8,000 (Market went down)
    """
    return pl.DataFrame({
        "date": [date(2025, 1, 1), date(2025, 1, 2)],
        "target_class": ["Supramax", "Supramax"],
        "log_value": [math.log(10_000), math.log(10_000)],
        "y_step_h30": [math.log(12_000), math.log(8_000)],
        "y_mean_h30": [math.log(12_000), math.log(8_000)],
        "y_step_h90": [math.log(15_000), math.log(7_000)],
        "y_mean_h90": [math.log(15_000), math.log(7_000)],
    })


@pytest.fixture
def mock_preds() -> pl.DataFrame:
    """Mock ML predictions for h=7 and h=30 in LOG-LEVEL space.
    
    Day 1: Model expects spot to go UP (P50 = 11,000)
    Day 2: Model expects spot to go DOWN (P50 = 9,500)
    """
    return pl.DataFrame({
        "date": [date(2025, 1, 1)] * 2 + [date(2025, 1, 2)] * 2,
        "target_class": ["Supramax"] * 4,
        "h": [7, 30, 7, 30],
        "p_0.1": [math.log(9_000), math.log(9_000), math.log(9_000), math.log(9_000)],
        "p_0.5": [math.log(11_000), math.log(11_000), math.log(9_500), math.log(9_500)],
        "p_0.9": [math.log(13_000), math.log(13_000), math.log(13_000), math.log(13_000)],
    })


class TestBuildFan:
    def test_build_fan_success(self, mock_preds):
        row = {"date": date(2025, 1, 1)}
        fan, was_repaired = _build_fan(row, mock_preds, 30, VesselClass.SUPRAMAX)
        assert fan is not None
        assert fan.p10 == pytest.approx(9_000)
        assert fan.p50 == pytest.approx(11_000)
        assert fan.p90 == pytest.approx(13_000)
        assert was_repaired is False

    def test_build_fan_missing_returns_none(self, mock_preds):
        row = {"date": date(2025, 1, 3)} # No predictions for day 3
        fan, was_repaired = _build_fan(row, mock_preds, 30, VesselClass.SUPRAMAX)
        assert fan is None
        assert was_repaired is False

    def test_build_fan_repairs_crossed_quantiles_instead_of_dropping(self):
        """Regression test for the survivorship-bias defect this module fixes.

        LightGBM's h=90 output crosses on 23% of the real test split -- a model
        confused enough to predict p10 > p50 is not "missing a forecast", it is
        the row we most need to keep in the backtest. The old behaviour returned
        None here, quietly removing exactly the rows where the model was least
        confident from every downstream metric.
        """
        import math

        crossed_preds = pl.DataFrame({
            "date": [date(2025, 1, 1)],
            "target_class": ["Supramax"],
            "h": [90],
            "p_0.1": [math.log(15_000)],  # p10 > p50 > p90: fully inverted
            "p_0.5": [math.log(12_000)],
            "p_0.9": [math.log(9_000)],
        })
        row = {"date": date(2025, 1, 1)}
        fan, was_repaired = _build_fan(row, crossed_preds, 90, VesselClass.SUPRAMAX)

        assert fan is not None, "a crossed forecast must be repaired, not discarded"
        assert was_repaired is True
        # Rank-sorted: the smallest value becomes p10, largest becomes p90.
        assert fan.p10 == pytest.approx(9_000)
        assert fan.p50 == pytest.approx(12_000)
        assert fan.p90 == pytest.approx(15_000)

    def test_build_fan_underflowed_p10_still_dropped(self):
        """A model that diverges enough to underflow exp() is genuinely broken.

        math.exp() of a sufficiently negative log-value underflows to exactly 0.0
        (e.g. exp(-1000) == 0.0), which no amount of quantile rearrangement can
        repair -- there is no real forecast here, so the row must still be dropped.
        """
        broken_preds = pl.DataFrame({
            "date": [date(2025, 1, 1)],
            "target_class": ["Supramax"],
            "h": [30],
            "p_0.1": [-1000.0],
            "p_0.5": [-900.0],
            "p_0.9": [-800.0],
        })
        row = {"date": date(2025, 1, 1)}
        fan, was_repaired = _build_fan(row, broken_preds, 30, VesselClass.SUPRAMAX)
        assert fan is None
        assert was_repaired is False  # already monotone; just degenerate


class TestSimulate:
    def test_simulation_rows_count(self, mock_test_split, mock_preds):
        rows = simulate(
            mock_test_split, mock_preds,
            contract_term_days=30, broker_spread=0.0
        )
        assert len(rows) == 2

    def test_simulation_day1_market_up(self, mock_test_split, mock_preds):
        # Day 1: Spot=10k, actual h30=12k, broker=10k (spread=0)
        # Model P50 = 11k -> Ceiling = 11k -> quote (10k) <= ceiling (11k) -> LOCK
        # Oracle: quote (10k) <= actual (12k) -> LOCK
        # Savings: actual (12k) - quote (10k) = +2k
        rows = simulate(
            mock_test_split, mock_preds,
            contract_term_days=30, broker_spread=0.0
        )
        r1 = rows[0]
        assert r1.date == date(2025, 1, 1)
        assert r1.quote_usd == pytest.approx(10_000)
        assert r1.realised_spot == pytest.approx(12_000)
        assert r1.ceiling_usd == pytest.approx(11_000)
        assert r1.action_optimizer == "LOCK"
        assert r1.action_oracle == "LOCK"
        assert r1.savings_optimizer == pytest.approx(2_000)
        assert r1.savings_always_lock == pytest.approx(2_000)
        assert r1.savings_oracle == pytest.approx(2_000)

    def test_simulation_day2_market_down(self, mock_test_split, mock_preds):
        # Day 2: Spot=10k, actual h30=8k, broker=10k
        # Model P50 = 9.5k -> Ceiling = 9.5k -> quote (10k) > ceiling (9.5k) -> WAIT
        # Oracle: quote (10k) > actual (8k) -> WAIT
        # Savings: actual (8k) - quote (10k) = -2k for locking
        # Optimizer savings = 0 (since it waited)
        rows = simulate(
            mock_test_split, mock_preds,
            contract_term_days=30, broker_spread=0.0
        )
        r2 = rows[1]
        assert r2.date == date(2025, 1, 2)
        assert r2.action_optimizer == "WAIT"
        assert r2.action_oracle == "WAIT"
        assert r2.savings_optimizer == pytest.approx(0.0)
        assert r2.savings_always_lock == pytest.approx(-2_000)
        assert r2.savings_oracle == pytest.approx(0.0)

    def test_broker_spread_applied(self, mock_test_split, mock_preds):
        rows = simulate(
            mock_test_split, mock_preds,
            contract_term_days=30, broker_spread=0.05
        )
        assert rows[0].quote_usd == pytest.approx(9_500) # 10k * 0.95


class TestSummarise:
    def test_summarise_computes_correct_metrics(self, mock_test_split, mock_preds):
        rows = simulate(
            mock_test_split, mock_preds,
            contract_term_days=30, broker_spread=0.0
        )
        # We only have Supramax
        summaries = summarise(rows, contract_term_days=30, classes=["ALL"])
        
        # Verify optimizer
        opt = next(s for s in summaries if s.strategy == "optimizer")
        assert opt.n_decisions == 2
        # Day 1: locked (+2k), Day 2: waited (0) -> mean = 1k
        assert opt.savings_mean == pytest.approx(1_000)
        assert opt.lock_rate == pytest.approx(0.5)
        # Hit rate: Locked 1 time, and it was correct -> 1.0
        assert opt.hit_rate == pytest.approx(1.0)
        
        # Verify always_lock
        al = next(s for s in summaries if s.strategy == "always_lock")
        # Day 1: +2k, Day 2: -2k -> mean = 0
        assert al.savings_mean == pytest.approx(0.0, abs=1e-5)
        assert al.lock_rate == pytest.approx(1.0)
        
        # Decision value: opt (1000) - always_lock (0) = 1000
        assert opt.decision_value == pytest.approx(1_000)
        
        # Verify oracle
        oracle = next(s for s in summaries if s.strategy == "oracle")
        # Same actions as optimizer in this mock data
        assert oracle.savings_mean == pytest.approx(1_000)
        
        # Regret = oracle (1000) - opt (1000) = 0
        assert opt.regret == pytest.approx(0.0)

    def test_to_dataframe(self, mock_test_split, mock_preds):
        rows = simulate(
            mock_test_split, mock_preds,
            contract_term_days=30, broker_spread=0.0
        )
        summaries = summarise(rows, contract_term_days=30, classes=["ALL"])
        df = to_dataframe(summaries)
        assert isinstance(df, pl.DataFrame)
        assert len(df) == 5 # 5 strategies
        assert "decision_value" in df.columns


class TestMcInformedDecisions:
    """When mc_config is provided the optimizer decision comes from the
    simulated spot-cost distribution instead of the analytic ceiling."""

    def test_mc_config_returns_same_rows(self, mock_test_split, mock_preds):
        cfg = MonteCarloConfig(theta=0.0, num_simulations=200)
        rows_mc = simulate(
            mock_test_split, mock_preds,
            contract_term_days=30, broker_spread=0.0,
            mc_config=cfg, mc_rng=random.Random(7),
        )
        rows_analytic = simulate(
            mock_test_split, mock_preds,
            contract_term_days=30, broker_spread=0.0,
        )
        assert len(rows_mc) == len(rows_analytic) == 2

    def test_mc_config_reproducible_with_seeded_rng(self, mock_test_split, mock_preds):
        kwargs = {
            "eval_split": mock_test_split, "ml_predictions": mock_preds,
            "contract_term_days": 30, "broker_spread": 0.0,
            "risk_tolerance": 0.3,
            "mc_config": MonteCarloConfig(theta=0.2, sigma_long=0.4, num_simulations=300),
        }
        rows_1 = simulate(**kwargs, mc_rng=random.Random(7))  # type: ignore[arg-type]
        rows_2 = simulate(**kwargs, mc_rng=random.Random(7))  # type: ignore[arg-type]
        assert [r.ceiling_usd for r in rows_1] == pytest.approx([r.ceiling_usd for r in rows_2])
        assert [r.action_optimizer for r in rows_1] == [r.action_optimizer for r in rows_2]

    def test_sigma_long_affects_threshold(self, mock_test_split, mock_preds):
        """Higher volatility raises E[exp(path)] (Jensen) → higher LOCK threshold."""
        low = simulate(
            mock_test_split, mock_preds,
            contract_term_days=30, broker_spread=0.0,
            mc_config=MonteCarloConfig(theta=0.2, sigma_long=0.05, num_simulations=800),
            mc_rng=random.Random(7),
        )
        high = simulate(
            mock_test_split, mock_preds,
            contract_term_days=30, broker_spread=0.0,
            mc_config=MonteCarloConfig(theta=0.2, sigma_long=0.90, num_simulations=800),
            mc_rng=random.Random(7),
        )
        thresholds_low = [r.ceiling_usd for r in low]
        thresholds_high = [r.ceiling_usd for r in high]
        assert any(h > l + 1.0 for l, h in zip(thresholds_low, thresholds_high)), (
            f"Expected higher threshold with higher sigma_long: "
            f"low={thresholds_low}, high={thresholds_high}"
        )

    def test_theta_affects_risk_blended_threshold(self, mock_test_split, mock_preds):
        """Stronger mean reversion compresses tail uncertainty → the P10 side of
        the blended threshold moves toward P50, changing decisions."""
        weak = simulate(
            mock_test_split, mock_preds,
            contract_term_days=30, broker_spread=0.0, risk_tolerance=0.5,
            mc_config=MonteCarloConfig(theta=0.02, sigma_long=0.40, num_simulations=800),
            mc_rng=random.Random(7),
        )
        strong = simulate(
            mock_test_split, mock_preds,
            contract_term_days=30, broker_spread=0.0, risk_tolerance=0.5,
            mc_config=MonteCarloConfig(theta=0.95, sigma_long=0.40, num_simulations=800),
            mc_rng=random.Random(7),
        )
        thresholds_weak = [r.ceiling_usd for r in weak]
        thresholds_strong = [r.ceiling_usd for r in strong]
        assert thresholds_weak != pytest.approx(thresholds_strong), (
            "theta must influence the risk-blended LOCK threshold"
        )

    def test_mc_decisions_can_differ_from_analytic(self, mock_test_split, mock_preds):
        """The MC-informed rule is a genuinely different decision function."""
        analytic = simulate(
            mock_test_split, mock_preds,
            contract_term_days=30, broker_spread=0.0, risk_tolerance=0.5,
        )
        mc = simulate(
            mock_test_split, mock_preds,
            contract_term_days=30, broker_spread=0.0, risk_tolerance=0.5,
            mc_config=MonteCarloConfig(theta=0.1, sigma_long=0.35, num_simulations=800),
            mc_rng=random.Random(7),
        )
        assert [r.action_optimizer for r in analytic] != [r.action_optimizer for r in mc] or (
            [r.ceiling_usd for r in analytic] != pytest.approx([r.ceiling_usd for r in mc])
        )
