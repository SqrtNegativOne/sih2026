"""Monte Carlo Savings Estimator (Sub-problem 4)

Simulates thousands of potential market futures (price paths) drawn from the
ML model's uncertainty fans.

By running our decisions through these "parallel universes," we can extract
not just the expected (mean) savings, but the full risk distribution
(e.g., the P10 "bad run" floor).

When theta > 0, price paths follow a discretised Ornstein-Uhlenbeck process
that mean-reverts toward the P50 forecast path, compressing long-contract
uncertainty relative to the flat-shock (theta=0) baseline.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from opt.types import ForecastFan, VesselClass


@dataclass
class MonteCarloConfig:
    """Configuration for the Monte Carlo price-path generator.

    Attributes
    ----------
    theta:
        Mean-reversion speed in units of (per day).
        ``theta=0`` reproduces the original single-shock behaviour exactly.
        ``theta=0.10`` is a gentle daily pull back toward the forecast P50.
    sigma_long:
        Long-run annualised volatility used to compute the daily step-size
        for the OU process.  Ignored when ``theta=0``.
    floor_usd:
        Absolute price floor in USD/day.  No simulated rate may fall below
        this value.
    num_simulations:
        Number of Monte Carlo paths to draw.
    """
    theta: float = 0.10
    sigma_long: float = 0.30
    floor_usd: float = 4000.0
    num_simulations: int = 5000


@dataclass
class SavingsDistribution:
    vessel_class: VesselClass
    contract_term_days: int
    quote_usd_per_day: float

    # Savings ($/day) compared to staying spot
    expected_p50_savings: float
    worst_case_p10_savings: float
    best_case_p90_savings: float

    # Probability that locking beats staying spot
    prob_positive_savings: float


def _interpolate_fan_for_day(fans: list[ForecastFan], day: int) -> tuple[float, float, float]:
    """Get (P10, P50, P90) for a specific day using nearest/linear interpolation."""
    sorted_fans = sorted(fans, key=lambda f: f.horizon_days)

    if not sorted_fans:
        raise ValueError("Empty forecast fans list.")

    # Flat extrapolation before the first horizon
    if day <= sorted_fans[0].horizon_days:
        f = sorted_fans[0]
        return f.p10, f.p50, f.p90

    # Flat extrapolation after the last horizon
    if day >= sorted_fans[-1].horizon_days:
        f = sorted_fans[-1]
        return f.p10, f.p50, f.p90

    # Linear interpolation between horizons
    for i in range(len(sorted_fans) - 1):
        f1 = sorted_fans[i]
        f2 = sorted_fans[i + 1]
        if f1.horizon_days < day < f2.horizon_days:
            weight = (day - f1.horizon_days) / (f2.horizon_days - f1.horizon_days)
            p10 = f1.p10 + weight * (f2.p10 - f1.p10)
            p50 = f1.p50 + weight * (f2.p50 - f1.p50)
            p90 = f1.p90 + weight * (f2.p90 - f1.p90)
            return p10, p50, p90

    # Fallback (should be unreachable due to checks above)
    f = sorted_fans[-1]
    return f.p10, f.p50, f.p90


def estimate_savings_distribution(
    forecasts: list[ForecastFan],
    vessel_class: VesselClass,
    contract_term_days: int,
    today_quote_usd_per_day: float,
    num_simulations: int = 5000,
    config: MonteCarloConfig | None = None,
) -> SavingsDistribution:
    """Run a Monte Carlo simulation to generate the distribution of savings.

    Savings = Simulated Spot Rate Average - TC Quote.

    When ``config.theta == 0`` (the default), the function draws a single
    Gaussian shock per simulation and holds it flat for the entire contract
    term — identical to the original implementation.

    When ``config.theta > 0``, each simulation follows a discretised
    Ornstein-Uhlenbeck path that reverts toward the log-P50 forecast,
    compressing long-contract uncertainty.

    Parameters
    ----------
    forecasts:
        Forecast fans (P10/P50/P90) from the ML model.
    vessel_class:
        The vessel class to use (filters ``forecasts``).
    contract_term_days:
        Length of the TC contract to evaluate.
    today_quote_usd_per_day:
        Broker quote for the contract being evaluated.
    num_simulations:
        Number of paths (overrides ``config.num_simulations`` when
        ``config`` is None).
    config:
        Optional OU / floor configuration.  Defaults to
        ``MonteCarloConfig(theta=0)`` to preserve backward compatibility.
    """
    # Resolve config — use theta=0 default to preserve existing behaviour
    if config is None:
        cfg = MonteCarloConfig(theta=0.0)
        n_sims = num_simulations
    else:
        cfg = config
        n_sims = cfg.num_simulations

    class_fans = [f for f in forecasts if f.vessel_class == vessel_class]
    if not class_fans:
        raise ValueError(f"No forecasts provided for {vessel_class.value}")

    # Standard normal z-score for the 10th and 90th percentiles
    Z_90 = 1.28155

    # Pre-compute the initial log-spot (anchor for OU process)
    p10_0, p50_0, p90_0 = _interpolate_fan_for_day(class_fans, 1)
    log_spot_0 = math.log(max(p50_0, cfg.floor_usd))

    daily_sigma = cfg.sigma_long / math.sqrt(252) if cfg.theta > 0 else 0.0

    simulated_savings: list[float] = []

    for _ in range(n_sims):
        # ----------------------------------------------------------------
        # theta == 0 : original single-shock behaviour (backward compat)
        # ----------------------------------------------------------------
        if cfg.theta == 0.0:
            market_shock_z = random.gauss(0, 1)

            path_spot_sum = 0.0
            for day in range(1, contract_term_days + 1):
                p10, p50, p90 = _interpolate_fan_for_day(class_fans, day)

                # Map the Z-shock to the skewed P10/P50/P90 fan
                if market_shock_z >= 0:
                    day_spot = p50 + market_shock_z * ((p90 - p50) / Z_90)
                else:
                    day_spot = p50 + market_shock_z * ((p50 - p10) / Z_90)

                # Absolute price floor
                day_spot = max(cfg.floor_usd, day_spot)
                path_spot_sum += day_spot

            path_spot_avg = path_spot_sum / contract_term_days

        # ----------------------------------------------------------------
        # theta > 0 : OU mean-reverting path
        # ----------------------------------------------------------------
        else:
            x_t = log_spot_0  # start from today's log-spot estimate

            path_spot_sum = 0.0
            for day in range(1, contract_term_days + 1):
                _, p50_t, _ = _interpolate_fan_for_day(class_fans, day)
                # Ensure positive log mean
                mu_t = math.log(max(p50_t, cfg.floor_usd))

                # Euler-Maruyama discretisation of dx = θ(μ−x)dt + σ dW
                x_t = x_t + cfg.theta * (mu_t - x_t) + daily_sigma * random.gauss(0, 1)

                day_spot = max(cfg.floor_usd, math.exp(x_t))
                path_spot_sum += day_spot

            path_spot_avg = path_spot_sum / contract_term_days

        # Savings = What we would have paid on spot - what we pay on TC
        savings = path_spot_avg - today_quote_usd_per_day
        simulated_savings.append(savings)

    simulated_savings.sort()

    # Extract percentiles
    idx_p10 = int(n_sims * 0.10)
    idx_p50 = int(n_sims * 0.50)
    idx_p90 = int(n_sims * 0.90)

    prob_positive = sum(1 for s in simulated_savings if s > 0) / n_sims

    return SavingsDistribution(
        vessel_class=vessel_class,
        contract_term_days=contract_term_days,
        quote_usd_per_day=today_quote_usd_per_day,
        expected_p50_savings=simulated_savings[idx_p50],
        worst_case_p10_savings=simulated_savings[idx_p10],
        best_case_p90_savings=simulated_savings[idx_p90],
        prob_positive_savings=prob_positive,
    )
