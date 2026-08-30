"""Optimal charter execution: splitting a large requirement across time.

Transplants the Almgren & Chriss (2000, "Optimal Execution of Portfolio
Transactions") optimal-execution framework from equity markets to freight
chartering -- fixing a large requirement (e.g. 480kt over a quarter) as one clip
concentrates demand and moves the price against itself (``impact.elasticity``);
spreading it thin over time avoids that impact but exposes the requirement to
market drift for longer. This module computes the closed-form efficient
trajectory for that trade-off. A literature and web search during design turned up
no prior application of Almgren-Chriss to freight chartering -- this transfer
appears to be new, not merely unfamiliar to this team.

Model (linear impact, matching the AC00 closed-form case): executing ``n_j`` dwt in
period ``j`` of length ``tau`` incurs a *temporary* cost ``eta * n_j^2 / tau``
(this period's execution price only) and a *permanent* cost ``0.5 * gamma *
total_dwt^2`` (shifts the price for the whole remaining trajectory, so it depends
only on the total, not the schedule). Holding ``x_j`` dwt still unexecuted during
period ``j`` exposes that amount to rate volatility ``sigma`` for ``tau`` days,
contributing ``sigma^2 * tau * x_j^2`` to the variance of total cost. The optimal
trajectory minimizing ``E[cost] + risk_aversion * Var[cost]`` is the AC00 sinh
schedule; the well-known closed-form limits (risk_aversion -> 0 gives a uniform/TWAP
schedule; risk_aversion -> large front-loads execution) are what the test suite
checks against, since there is no historical "correct answer" to validate a
normative optimization framework against the way there is for a forecast.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Final

import numpy as np

#: Below this, kappa is treated as zero (uniform schedule) rather than risking
#: division/domain issues from an almost-degenerate cosh argument.
_KAPPA_EPS: Final[float] = 1e-9


@dataclass(frozen=True)
class ExecutionSchedule:
    total_dwt: float
    n_periods: int
    period_days: float
    risk_aversion: float
    remaining_dwt: np.ndarray  # length n_periods+1: remaining_dwt[0]=total, [-1]=0
    trade_dwt: np.ndarray  # length n_periods: executed in each period, sums to total
    expected_cost_usd: float
    cost_variance_usd2: float

    @property
    def cost_std_usd(self) -> float:
        return math.sqrt(max(self.cost_variance_usd2, 0.0))


def solve_execution_schedule(
    total_dwt: float,
    horizon_days: float,
    n_periods: int,
    permanent_impact_usd_per_dwt2: float,
    temporary_impact_usd_per_dwt2: float,
    daily_volatility_usd_per_dwt: float,
    risk_aversion: float,
) -> ExecutionSchedule:
    """Closed-form Almgren-Chriss trajectory for one risk-aversion setting.

    Parameters mirror AC00: ``permanent_impact_usd_per_dwt2`` is gamma,
    ``temporary_impact_usd_per_dwt2`` is eta, ``daily_volatility_usd_per_dwt`` is
    sigma (rate-move std per day per unit of remaining dwt), ``risk_aversion`` is
    lambda in ``E[cost] + lambda * Var[cost]``.
    """
    if total_dwt <= 0:
        raise ValueError("total_dwt must be positive")
    if n_periods < 1:
        raise ValueError("n_periods must be >= 1")
    if horizon_days <= 0:
        raise ValueError("horizon_days must be positive")
    if permanent_impact_usd_per_dwt2 < 0:
        raise ValueError("permanent_impact_usd_per_dwt2 must be nonnegative")
    if temporary_impact_usd_per_dwt2 <= 0:
        raise ValueError("temporary_impact_usd_per_dwt2 must be positive")
    if daily_volatility_usd_per_dwt < 0:
        raise ValueError("daily_volatility_usd_per_dwt must be nonnegative")
    if risk_aversion < 0:
        raise ValueError("risk_aversion must be nonnegative")

    tau = horizon_days / n_periods
    eta_tilde = temporary_impact_usd_per_dwt2 - 0.5 * permanent_impact_usd_per_dwt2 * tau
    if eta_tilde <= 0:
        # Permanent impact too large relative to temporary+tau for the AC00
        # eta_tilde correction to stay positive -- fall back to the uncorrected
        # temporary coefficient rather than a negative/degenerate kappa.
        eta_tilde = temporary_impact_usd_per_dwt2

    j = np.arange(n_periods + 1)
    kappa = 0.0
    if risk_aversion > 0 and daily_volatility_usd_per_dwt > 0:
        kappa_tilde_sq = (risk_aversion * daily_volatility_usd_per_dwt**2) / eta_tilde
        cosh_arg = 1 + 0.5 * kappa_tilde_sq * tau**2
        kappa = math.acosh(cosh_arg) / tau if cosh_arg > 1 else 0.0

    if kappa > _KAPPA_EPS:
        remaining = total_dwt * np.sinh(kappa * (horizon_days - j * tau)) / math.sinh(kappa * horizon_days)
    else:
        remaining = total_dwt * (1 - j / n_periods)
    remaining = np.asarray(remaining, dtype=float)
    remaining[0] = total_dwt
    remaining[-1] = 0.0
    trades = -np.diff(remaining)

    expected_cost = (
        0.5 * permanent_impact_usd_per_dwt2 * total_dwt**2
        + temporary_impact_usd_per_dwt2 * float(np.sum(trades**2)) / tau
    )
    # Var = sigma^2 * tau * sum_{j=1}^{N} x_j^2 (AC00): post-trade remaining
    # position in every period after the first, x_N=0 contributing nothing.
    cost_variance = daily_volatility_usd_per_dwt**2 * tau * float(np.sum(remaining[1:] ** 2))

    return ExecutionSchedule(
        total_dwt=total_dwt,
        n_periods=n_periods,
        period_days=tau,
        risk_aversion=risk_aversion,
        remaining_dwt=remaining,
        trade_dwt=trades,
        expected_cost_usd=float(expected_cost),
        cost_variance_usd2=float(cost_variance),
    )


@dataclass(frozen=True)
class ImpactParameters:
    permanent_impact_usd_per_dwt2: float
    temporary_impact_usd_per_dwt2: float
    daily_volatility_usd_per_dwt: float


def impact_parameters_from_elasticity(
    d_rate_d_demand_usd_per_day_per_dwt: float,
    historical_rate_std_usd_per_day: float,
    representative_dwt: float,
    assumed_charter_days: float = 30.0,
    temporary_to_permanent_ratio: float = 0.5,
) -> ImpactParameters:
    """Bridge real, measured freight-market numbers into AC's (gamma, eta, sigma).

    This is a modeling choice, not a derived identity -- flagged explicitly rather
    than presented as more rigorous than it is, since no prior application of
    Almgren-Chriss to chartering exists to borrow a convention from.

    AC's gamma/eta are *lump-sum cost per (unit of quantity)^2* -- in equities that
    quantity is shares, and price-per-share x shares = a dollar cost, which is what
    makes the squared term dimensionally a cost. A day-rate (USD/day) is not a
    price *per dwt*; it is a price per ship-day, roughly independent of a given
    ship's dwt within its class. Pairing a day-rate elasticity directly with raw
    dwt as AC's "shares" -- as an earlier version of this function did -- produces
    cost estimates inflated by a factor of (representative dwt), because dwt counts
    in the hundreds of thousands while equity share counts this formula was
    designed around are usually orders of magnitude smaller. Concretely: for a real
    Panamax elasticity of $0.015/day per dwt and a 480,000 dwt requirement, the
    unnormalized version priced permanent impact at **$52 billion** -- obviously
    wrong, caught by comparing against a back-of-envelope real freight cost for
    that volume (order $1-2M). The fix is to convert dwt into an economically
    meaningful "shipload" count first, dividing by ``representative_dwt`` (e.g.
    ``tonnage.classmix.CLASS_MIDPOINT_DWT[cls]``) so gamma's implied cost scales
    with *number of fixtures*, not raw tonnage -- the corrected version above
    prices the same scenario at roughly $680k, which is at least the right order
    of magnitude for a real freight cost.

    ``assumed_charter_days`` (how long the elevated rate is paid on a fixture) and
    ``temporary_to_permanent_ratio`` (this pipeline has no independent way to
    estimate a temporary/permanent split from PortWatch-derived data alone --
    see ``tonnage.supplycurve``'s note on not attempting the plan's IV
    identification) are both real, stated assumptions, not fitted values.
    """
    if assumed_charter_days <= 0:
        raise ValueError("assumed_charter_days must be positive")
    if temporary_to_permanent_ratio < 0:
        raise ValueError("temporary_to_permanent_ratio must be nonnegative")
    if representative_dwt <= 0:
        raise ValueError("representative_dwt must be positive")
    gamma = abs(d_rate_d_demand_usd_per_day_per_dwt) * assumed_charter_days / representative_dwt
    eta = gamma * temporary_to_permanent_ratio
    sigma = historical_rate_std_usd_per_day * assumed_charter_days / representative_dwt
    return ImpactParameters(
        permanent_impact_usd_per_dwt2=gamma,
        temporary_impact_usd_per_dwt2=max(eta, 1e-12),  # AC00 requires eta > 0
        daily_volatility_usd_per_dwt=sigma,
    )


def efficient_frontier(
    total_dwt: float,
    horizon_days: float,
    n_periods: int,
    permanent_impact_usd_per_dwt2: float,
    temporary_impact_usd_per_dwt2: float,
    daily_volatility_usd_per_dwt: float,
    risk_aversion_grid: tuple[float, ...],
) -> list[ExecutionSchedule]:
    """One schedule per risk-aversion value -- the (expected cost, cost variance)
    frontier the plan's demo wants rendered as a draggable risk point."""
    return [
        solve_execution_schedule(
            total_dwt, horizon_days, n_periods, permanent_impact_usd_per_dwt2,
            temporary_impact_usd_per_dwt2, daily_volatility_usd_per_dwt, la,
        )
        for la in risk_aversion_grid
    ]
