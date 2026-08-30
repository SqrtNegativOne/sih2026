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

import hashlib
import math
import random
from dataclasses import dataclass

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
    """Get (P10, P50, P90) for a specific day using nearest/linear interpolation.

    Callers that need this for a whole range of days -- every simulation loop in
    this module, and the entry-window scan in opt.api -- should use
    :func:`precompute_daily_fan` instead and index into the result. This function
    re-sorts ``fans`` on every call, and the result depends only on ``day``: called
    inside a Monte Carlo loop it was being evaluated ``num_simulations`` times per
    day for an answer that is identical across every simulation. Measured on 20
    representative rows, precomputing cut this module's share of a calibration
    backtest run by roughly half.
    """
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


def precompute_daily_fan(
    fans: list[ForecastFan], contract_term_days: int
) -> list[tuple[float, float, float]]:
    """(P10, P50, P90) for every day 1..contract_term_days, computed once.

    Index ``i`` of the result is day ``i + 1``. The interpolation is path- and
    simulation-independent, so anything that needs it across a day range -- a
    Monte Carlo sweep, an entry-window scan -- should call this once rather than
    calling :func:`_interpolate_fan_for_day` inside the hot loop.
    """
    sorted_fans = sorted(fans, key=lambda f: f.horizon_days)
    return [_interpolate_fan_for_day(sorted_fans, day) for day in range(1, contract_term_days + 1)]


def estimate_savings_distribution(
    forecasts: list[ForecastFan],
    vessel_class: VesselClass,
    contract_term_days: int,
    today_quote_usd_per_day: float,
    num_simulations: int = 5000,
    config: MonteCarloConfig | None = None,
    rng: random.Random | None = None,
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
    rng:
        Optional seeded ``random.Random`` instance. When None (the common
        case -- every real call from ``opt.api.run_optimizer`` omits it),
        a seed is derived deterministically from the real inputs below
        (F-04 fix) rather than falling back to the process-global
        ``random`` module. That used to mean the identical request
        (same cargo, same route, same date) returned a different headline
        savings figure and confidence percentage on every call -- verified
        live: four repeats of one quote swung the total by 42%. Deriving
        the seed from the inputs makes identical requests reproducible
        while still giving different requests (a different date, class, or
        contract term) their own, different-but-stable noise draw -- not
        one fixed pattern reused everywhere. Pass an explicit ``rng`` to
        opt out (e.g. PSO calibration, which wants many independent draws
        for the *same* inputs on purpose).
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

    if rng is None:
        # Sorted by horizon so the seed doesn't depend on the caller's list
        # order -- only on the actual (class, term, quote, fan) identity of
        # this request.
        ordered = sorted(class_fans, key=lambda f: f.horizon_days)
        seed_key = "|".join([
            vessel_class.value, str(contract_term_days), f"{today_quote_usd_per_day:.4f}",
            str(cfg.theta), str(cfg.sigma_long), str(cfg.floor_usd),
            *(f"{f.horizon_days}:{f.p10:.4f}:{f.p50:.4f}:{f.p90:.4f}" for f in ordered),
        ])
        seed = int.from_bytes(hashlib.sha256(seed_key.encode("utf-8")).digest()[:8], "big")
        rng = random.Random(seed)

    _gauss = rng.gauss

    # Standard normal z-score for the 10th and 90th percentiles
    Z_90 = 1.28155

    # Precompute the day-indexed fan ONCE. It depends only on `day`, not on the
    # simulation path, so evaluating it inside the sim loop (as this used to)
    # repeated identical work num_simulations times over -- see
    # _interpolate_fan_for_day's docstring for the measured cost of that.
    daily_fan = precompute_daily_fan(class_fans, contract_term_days)
    p10_0, p50_0, p90_0 = daily_fan[0]
    log_spot_0 = math.log(max(p50_0, cfg.floor_usd))

    daily_sigma = cfg.sigma_long / math.sqrt(252) if cfg.theta > 0 else 0.0

    # theta > 0 also reuses log(max(p50_t, floor)) every simulation; precompute it
    # alongside the fan rather than recomputing math.log num_simulations times.
    daily_mu = [math.log(max(p50, cfg.floor_usd)) for _, p50, _ in daily_fan] if cfg.theta > 0 else []

    simulated_savings: list[float] = []

    for _ in range(n_sims):
        # ----------------------------------------------------------------
        # theta == 0 : original single-shock behaviour (backward compat)
        # ----------------------------------------------------------------
        if cfg.theta == 0.0:
            market_shock_z = _gauss(0, 1)

            path_spot_sum = 0.0
            for p10, p50, p90 in daily_fan:
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
            for mu_t in daily_mu:
                # Euler-Maruyama discretisation of dx = θ(μ−x)dt + σ dW
                x_t = x_t + cfg.theta * (mu_t - x_t) + daily_sigma * _gauss(0, 1)

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
