"""Fixed-point solve between the execution plan and the elasticity it is priced
against.

``impact.execution`` prices a schedule using a single constant marginal elasticity
(the local slope at today's tightness). But the schedule's own fixtures draw down
the same basin stock that elasticity is computed from -- ``d(tightness)/d(demand) =
1/stock`` (``impact.elasticity``) grows as stock shrinks, so a schedule that fixes
a meaningful share of a basin's free tonnage should see *rising* marginal impact in
its later periods, not the flat rate the single-slope AC model assumes. Solving for
a self-consistent plan means: price a schedule at slope S, work out what the
*average* marginal slope actually was along that schedule's own draw-down path,
and re-price at that average -- repeat until the slope used to price the schedule
and the slope implied by the schedule agree. This is exactly the plan's "optimizer's
plan perturbs local tightness -> shifts the clearing rate -> changes the optimal
plan" loop, with the mechanism spelled out rather than asserted.

Damped, per the plan's own language ("damped iteration; typically converges in 3-5
passes") -- each iteration blends the newly-implied slope with the previous one
rather than replacing it outright, since an undamped fixed-point update on a
feedback loop like this one is a textbook way to oscillate rather than converge.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Final

import numpy as np

from impact.elasticity import ElasticityEstimate, local_elasticity
from impact.execution import (
    ExecutionSchedule,
    impact_parameters_from_elasticity,
    solve_execution_schedule,
)
from tonnage.basins import Basin
from tonnage.stockflow import StockflowResult
from tonnage.supplycurve import SupplyCurveFit

#: Floor on remaining basin stock during the self-consistency update, as a fraction
#: of the starting stock -- prevents a division blow-up if a schedule (pathologically,
#: for a demand this basin's real stock cannot support) would draw the basin to zero.
_MIN_STOCK_FRACTION: Final[float] = 0.01


@dataclass(frozen=True)
class FixedPointResult:
    schedule: ExecutionSchedule
    base_elasticity: ElasticityEstimate
    effective_slope_usd_per_day_per_dwt: float
    n_iterations: int
    converged: bool
    final_relative_change: float
    slope_path: tuple[float, ...]  # the effective slope used at each iteration, for diagnostics


def _average_implied_slope(
    d_rate_d_tightness_usd_per_day: float, stock_at_start: float, schedule: ExecutionSchedule
) -> float:
    """Average marginal d(rate)/d(demand) along a schedule's own cumulative draw."""
    floor = stock_at_start * _MIN_STOCK_FRACTION
    already_fixed = schedule.total_dwt - schedule.remaining_dwt[:-1]  # before each period's trade
    stock_during_period = np.maximum(stock_at_start - already_fixed, floor)
    marginal_slopes = d_rate_d_tightness_usd_per_day / stock_during_period
    return float(np.mean(marginal_slopes))


def solve_self_consistent_schedule(
    stockflow_result: StockflowResult,
    supply_fit: SupplyCurveFit,
    basin: Basin,
    as_of: date,
    total_dwt: float,
    horizon_days: float,
    n_periods: int,
    representative_dwt: float,
    historical_rate_std_usd_per_day: float,
    risk_aversion: float,
    assumed_charter_days: float = 30.0,
    max_iterations: int = 10,
    damping: float = 0.5,
    tol: float = 1e-4,
    quantile: float = 0.5,
) -> FixedPointResult:
    if not (0 < damping <= 1):
        raise ValueError("damping must be in (0, 1]")
    if max_iterations < 1:
        raise ValueError("max_iterations must be >= 1")
    if not (0 < tol < 1):
        raise ValueError("tol must be in (0, 1)")

    base_est = local_elasticity(stockflow_result, supply_fit, basin, as_of, quantile=quantile)
    stock_at_start = base_est.stock_dwt
    d_rate_d_tightness = base_est.d_rate_d_tightness_usd_per_day

    effective_slope = base_est.d_rate_d_demand_usd_per_day_per_dwt
    slope_path = [effective_slope]
    schedule: ExecutionSchedule | None = None
    converged = False
    relative_change = float("inf")

    for _ in range(max_iterations):
        params = impact_parameters_from_elasticity(
            effective_slope, historical_rate_std_usd_per_day, representative_dwt,
            assumed_charter_days=assumed_charter_days,
        )
        schedule = solve_execution_schedule(
            total_dwt, horizon_days, n_periods,
            params.permanent_impact_usd_per_dwt2, params.temporary_impact_usd_per_dwt2,
            params.daily_volatility_usd_per_dwt, risk_aversion,
        )
        implied_slope = _average_implied_slope(d_rate_d_tightness, stock_at_start, schedule)
        new_slope = damping * implied_slope + (1 - damping) * effective_slope
        relative_change = abs(new_slope - effective_slope) / max(abs(effective_slope), 1e-15)
        effective_slope = new_slope
        slope_path.append(effective_slope)
        if relative_change < tol:
            converged = True
            break

    assert schedule is not None  # loop always runs >= 1 iteration
    return FixedPointResult(
        schedule=schedule,
        base_elasticity=base_est,
        effective_slope_usd_per_day_per_dwt=effective_slope,
        n_iterations=len(slope_path) - 1,
        converged=converged,
        final_relative_change=relative_change,
        slope_path=tuple(slope_path),
    )
