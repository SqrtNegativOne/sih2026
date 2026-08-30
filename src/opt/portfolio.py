"""Portfolio mix optimization (Sub-problem 6): the problem statement's actual
stated objective, done properly -- "move from multiple single spot contracts to
short/medium-term multiple-voyage contracts." The existing lock/wait engine
only ever answers spot-vs-one-TC-contract for one class; this optimizes the
*mix* of spot, period TC, and COA (contract of affreightment) coverage against
a demand schedule, with a real stockout-risk penalty tied to how many days of
buffer stock the plant is carrying.

Method
------
Three coverage channels, each with a real expected cost and risk contribution:

- **Spot**: fully flexible, fully exposed to price uncertainty. Expected cost
  and its variance both come from the real forecast fan (via the same, already
  tested ``opt.ceiling.compute_ceiling``) -- the p10-p90 spread converted to a
  variance the same way the rest of this codebase already does (the z-score
  convention already used by ``ForecastFan``/``ml.baselines``).
- **Period TC**: locks today's real quote for the whole horizon. Eliminates
  price risk for the covered fraction, at the cost of losing flexibility --
  modelled here as zero cost variance, which is exact for the price-risk
  component (the commitment/liquidity risk of over-contracting is real but out
  of scope, not silently folded in).
- **COA**: a documented middle ground, not a fitted one. Real COA rates
  typically settle close to a spot-linked formula with a modest premium for
  the volume commitment; this uses ``spot_p50 * (1 + coa_premium)`` for cost
  and a fraction of spot's variance for risk, both explicit, overridable
  parameters -- there is no public COA rate-formula dataset to fit either
  against, and the module says so rather than pretending otherwise.

**Stockout risk, as a real hazard-model probability, not an arbitrary
penalty.** The uncovered (spot-reliant) fraction of demand is only actually at
risk of a stockout if sourcing takes longer than the plant's own burden cover
(``plant_burden_cover_days``) -- using the same Poisson hazard-rate framework
``opt.repositioning`` uses for cargo availability, applied here to *vessel*
sourcing: ``P(not sourced in time) = exp(-lambda * cover_days)``. The caller
supplies ``spot_sourcing_hazard_rate_per_day`` and ``stockout_cost_usd``
explicitly -- this module has no way to know SAIL's actual plant-specific
stockout cost or real sourcing speed, and does not invent one.

**Optimization: grid search over the coverage simplex, not a closed-form
solve.** ``opt.stopping`` found a real numerical-stability bug in a closed-form
optimum (dividing by a near-zero fitted slope); the lesson carried over here
directly. A 2-simplex (spot/TC/COA fractions summing to 1) with a well-behaved,
bounded objective is cheap to grid-search exhaustively at a fine resolution,
which is robust by construction -- it cannot diverge or blow up, it can only
be slightly less precise than a smooth solver, and precision is not the
binding constraint here.
"""
from __future__ import annotations

import math

import numpy as np

from opt.ceiling import compute_ceiling
from opt.types import BasisEntry, ForecastFan, PortfolioMix, VesselClass

#: COA settles at a documented premium over pure spot p50 -- an assumption,
#: not a fitted value (no public COA rate-formula dataset exists to fit
#: against). Override with a real figure if the caller has one.
DEFAULT_COA_PREMIUM: float = 0.03

#: COA absorbs some but not all spot price variance -- also a documented
#: assumption, not fitted.
DEFAULT_COA_VARIANCE_FRACTION: float = 0.30

#: Grid resolution over the coverage simplex. 1% steps over 3 fractions
#: summing to 1 is ~5,151 combinations -- solved in milliseconds, and fine
#: enough that the reported mix is meaningfully precise for a real decision.
GRID_STEP: float = 0.01


def _z80_sigma(p50: float, p10: float) -> float:
    """Std dev implied by an 80% central interval half-width, matching the
    z=1.2816 convention already used throughout this codebase (ForecastFan,
    ml.baselines.ZSCORES, tonnage.forward)."""
    return max(p50 - p10, 0.0) / 1.2816


def spot_cost_stats(
    forecasts: list[ForecastFan],
    vessel_class: VesselClass,
    contract_term_days: int,
    basis: BasisEntry | None = None,
) -> tuple[float, float]:
    """(spot_cost_usd, spot_cost_std_usd) for contract_term_days of pure spot
    exposure -- the same real forecast-derived figures optimize_portfolio_mix
    computes internally for its own w_spot=1 case, exposed separately so a
    caller can pick a scenario-appropriate risk_aversion scale. ``variance``
    in the objective is absolute $^2, so a single fixed risk_aversion grid
    means something completely different for e.g. a Capesize 180-day COA
    ($800K+ std) than a Handysize 30-day one ($10Ks std) -- a caller building
    an efficient frontier should normalize against this real, scenario-
    specific std instead of guessing a magnitude."""
    ceiling_info = compute_ceiling(
        forecasts=forecasts, vessel_class=vessel_class, contract_term_days=contract_term_days,
        risk_tolerance=0.0, basis=basis,
    )
    spot_p50 = ceiling_info["expected_spot_p50"]
    spot_p10 = ceiling_info["expected_spot_p10"]
    spot_cost = spot_p50 * contract_term_days
    spot_std = _z80_sigma(spot_p50, spot_p10) * contract_term_days
    return spot_cost, spot_std


def optimize_portfolio_mix(
    today_quote_usd_per_day: float,
    forecasts: list[ForecastFan],
    vessel_class: VesselClass,
    contract_term_days: int,
    plant_burden_cover_days: float,
    stockout_cost_usd: float,
    spot_sourcing_hazard_rate_per_day: float,
    risk_aversion: float = 0.0,
    basis: BasisEntry | None = None,
    coa_premium: float = DEFAULT_COA_PREMIUM,
    coa_variance_fraction: float = DEFAULT_COA_VARIANCE_FRACTION,
) -> PortfolioMix:
    """Grid-search the (spot, period-TC, COA) coverage mix minimizing
    ``expected_cost + risk_aversion * variance + stockout_penalty``.

    Raises
    ------
    ValueError
        Non-positive quote, contract term, or burden-cover days; negative
        hazard rate or stockout cost.
    """
    if today_quote_usd_per_day <= 0:
        raise ValueError(f"today_quote_usd_per_day must be positive, got {today_quote_usd_per_day}")
    if contract_term_days <= 0:
        raise ValueError(f"contract_term_days must be positive, got {contract_term_days}")
    if plant_burden_cover_days < 0:
        raise ValueError(f"plant_burden_cover_days must be nonnegative, got {plant_burden_cover_days}")
    if stockout_cost_usd < 0:
        raise ValueError(f"stockout_cost_usd must be nonnegative, got {stockout_cost_usd}")
    if spot_sourcing_hazard_rate_per_day < 0:
        raise ValueError("spot_sourcing_hazard_rate_per_day must be nonnegative")
    if risk_aversion < 0:
        raise ValueError(f"risk_aversion must be nonnegative, got {risk_aversion}")

    spot_cost, spot_sigma = spot_cost_stats(forecasts, vessel_class, contract_term_days, basis)
    spot_var = spot_sigma ** 2

    tc_cost = today_quote_usd_per_day * contract_term_days
    tc_var = 0.0  # locked -- no price risk left on the covered fraction

    coa_cost = spot_cost * (1.0 + coa_premium)
    coa_var = spot_var * coa_variance_fraction

    # P(the spot-reliant fraction fails to source in time): a real hazard-model
    # probability, not an arbitrary constant -- see module docstring.
    p_not_sourced_in_time = math.exp(-spot_sourcing_hazard_rate_per_day * plant_burden_cover_days)

    steps = np.arange(0.0, 1.0 + GRID_STEP / 2, GRID_STEP)
    best: PortfolioMix | None = None
    best_objective = float("inf")

    for w_spot in steps:
        for w_tc in steps:
            w_coa = 1.0 - w_spot - w_tc
            if w_coa < -1e-9:
                continue
            w_coa = max(w_coa, 0.0)

            expected_cost = w_spot * spot_cost + w_tc * tc_cost + w_coa * coa_cost
            variance = (w_spot ** 2) * spot_var + (w_tc ** 2) * tc_var + (w_coa ** 2) * coa_var
            stockout_probability = w_spot * p_not_sourced_in_time
            stockout_penalty = stockout_probability * stockout_cost_usd

            objective = expected_cost + risk_aversion * variance + stockout_penalty
            if objective < best_objective:
                best_objective = objective
                best = PortfolioMix(
                    spot_fraction=float(w_spot),
                    tc_fraction=float(w_tc),
                    coa_fraction=float(w_coa),
                    expected_cost_usd=float(expected_cost),
                    cost_variance_usd2=float(variance),
                    stockout_probability=float(stockout_probability),
                    stockout_penalty_usd=float(stockout_penalty),
                )

    assert best is not None  # the grid always includes (1,0,0) at minimum
    return best


def efficient_frontier(
    today_quote_usd_per_day: float,
    forecasts: list[ForecastFan],
    vessel_class: VesselClass,
    contract_term_days: int,
    plant_burden_cover_days: float,
    stockout_cost_usd: float,
    spot_sourcing_hazard_rate_per_day: float,
    risk_aversion_grid: tuple[float, ...],
    basis: BasisEntry | None = None,
) -> list[PortfolioMix]:
    """One optimal mix per risk-aversion value -- the cost/risk frontier."""
    return [
        optimize_portfolio_mix(
            today_quote_usd_per_day, forecasts, vessel_class, contract_term_days,
            plant_burden_cover_days, stockout_cost_usd, spot_sourcing_hazard_rate_per_day,
            risk_aversion=ra, basis=basis,
        )
        for ra in risk_aversion_grid
    ]
