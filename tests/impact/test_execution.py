"""Analytical/property tests for the Almgren-Chriss execution scheduler.

There is no historical ground truth for a normative optimization framework the way
there is for a forecast -- the correct validation standard is proving the closed
form satisfies its own well-known mathematical properties (AC00), which is what
these tests check, plus one end-to-end run on real reconstructed elasticity numbers
for a plausibility check (see test_execution_end_to_end.py).
"""
import numpy as np
import pytest

from impact.execution import efficient_frontier, solve_execution_schedule


def test_zero_risk_aversion_gives_the_uniform_twap_schedule():
    # Well-known AC00 limit: risk-neutral execution minimizes sum(n_j^2) for a
    # fixed total, which by convexity is achieved uniquely by equal-sized trades.
    sched = solve_execution_schedule(
        total_dwt=100_000.0, horizon_days=10.0, n_periods=5,
        permanent_impact_usd_per_dwt2=1e-9, temporary_impact_usd_per_dwt2=1e-7,
        daily_volatility_usd_per_dwt=50.0, risk_aversion=0.0,
    )
    assert sched.trade_dwt == pytest.approx(np.full(5, 20_000.0), rel=1e-9)


def test_trades_sum_to_total_and_endpoints_are_exact():
    for risk_aversion in (0.0, 1e-12, 1e-6, 1.0, 1000.0):
        sched = solve_execution_schedule(
            total_dwt=228_000.0, horizon_days=21.0, n_periods=7,
            permanent_impact_usd_per_dwt2=1e-9, temporary_impact_usd_per_dwt2=5e-8,
            daily_volatility_usd_per_dwt=80.0, risk_aversion=risk_aversion,
        )
        assert sched.trade_dwt.sum() == pytest.approx(228_000.0, rel=1e-9)
        assert sched.remaining_dwt[0] == 228_000.0
        assert sched.remaining_dwt[-1] == 0.0


def test_remaining_trajectory_is_monotonically_nonincreasing():
    sched = solve_execution_schedule(
        total_dwt=500_000.0, horizon_days=30.0, n_periods=10,
        permanent_impact_usd_per_dwt2=1e-9, temporary_impact_usd_per_dwt2=1e-7,
        daily_volatility_usd_per_dwt=100.0, risk_aversion=5.0,
    )
    assert np.all(np.diff(sched.remaining_dwt) <= 1e-6)


def test_higher_risk_aversion_front_loads_execution():
    # A risk-averse trader gets rid of inventory faster -- the first trade should
    # grow (as a share of total) as risk_aversion rises.
    shares_first_trade = []
    for risk_aversion in (0.0, 1.0, 100.0, 100_000.0):
        sched = solve_execution_schedule(
            total_dwt=100_000.0, horizon_days=20.0, n_periods=10,
            permanent_impact_usd_per_dwt2=1e-9, temporary_impact_usd_per_dwt2=1e-7,
            daily_volatility_usd_per_dwt=60.0, risk_aversion=risk_aversion,
        )
        shares_first_trade.append(sched.trade_dwt[0] / sched.total_dwt)
    assert shares_first_trade == sorted(shares_first_trade)
    assert shares_first_trade[-1] > shares_first_trade[0]


def test_variance_decreases_and_cost_increases_along_the_frontier():
    # The defining trade-off of the efficient frontier: paying more in expected
    # cost buys strictly less variance, monotonically, across the grid.
    frontier = efficient_frontier(
        total_dwt=300_000.0, horizon_days=15.0, n_periods=8,
        permanent_impact_usd_per_dwt2=1e-9, temporary_impact_usd_per_dwt2=8e-8,
        daily_volatility_usd_per_dwt=90.0,
        risk_aversion_grid=(0.0, 0.01, 0.1, 1.0, 10.0, 1000.0),
    )
    variances = [s.cost_variance_usd2 for s in frontier]
    costs = [s.expected_cost_usd for s in frontier]
    assert all(variances[i] >= variances[i + 1] - 1e-6 for i in range(len(variances) - 1))
    assert all(costs[i] <= costs[i + 1] + 1e-6 for i in range(len(costs) - 1))
    # Uniform (risk_aversion=0) achieves the minimum possible expected cost.
    assert costs[0] == min(costs)


def test_zero_volatility_collapses_to_uniform_regardless_of_risk_aversion():
    # With no timing risk to hedge against, there is nothing for risk aversion to
    # trade off -- every setting should degenerate to the cost-minimizing uniform
    # schedule.
    base = solve_execution_schedule(
        total_dwt=50_000.0, horizon_days=10.0, n_periods=5,
        permanent_impact_usd_per_dwt2=1e-9, temporary_impact_usd_per_dwt2=1e-7,
        daily_volatility_usd_per_dwt=0.0, risk_aversion=500.0,
    )
    assert base.trade_dwt == pytest.approx(np.full(5, 10_000.0), rel=1e-9)
    assert base.cost_variance_usd2 == 0.0


@pytest.mark.parametrize(
    "kwargs",
    [
        {"total_dwt": 0.0},
        {"total_dwt": -1.0},
        {"n_periods": 0},
        {"horizon_days": 0.0},
        {"permanent_impact_usd_per_dwt2": -1.0},
        {"temporary_impact_usd_per_dwt2": 0.0},
        {"daily_volatility_usd_per_dwt": -1.0},
        {"risk_aversion": -1.0},
    ],
)
def test_invalid_inputs_rejected(kwargs):
    defaults = {
        "total_dwt": 100_000.0, "horizon_days": 10.0, "n_periods": 5,
        "permanent_impact_usd_per_dwt2": 1e-9, "temporary_impact_usd_per_dwt2": 1e-7,
        "daily_volatility_usd_per_dwt": 50.0, "risk_aversion": 1.0,
    }
    defaults.update(kwargs)
    with pytest.raises(ValueError):
        solve_execution_schedule(**defaults)
