"""Ceiling rate calculator and lock/wait decision engine.

This is Sub-problem 1 of the optimizer (see docs/02_overview.md and the
optimizer_design artifact).

The core question: given today's TC broker quote and a P10/P50/P90 forecast
fan from the ML model, what is the maximum hire rate (the "ceiling") at which
locking a TC contract now beats staying in the spot market?

Design choices
--------------
*   Physics-free: all voyage cost computation is deliberately NOT here. The ML
    model forecasts BASE TCE (gross $/day market rate). The ceiling is computed
    purely from those forecast quantiles, the basis table, and the user's risk
    tolerance. Distance/fuel/port costs belong in the voyage estimator.

*   Basis is applied optimizer-side: per 02_overview.md,
        route_tce(r, c, t+h) = BASE(c, t+h) × (1 + basis_mean(r))
    and the fan is widened by basis_std.

*   Blending across horizons: a TC contract of N days spans multiple ML
    forecast horizons (7, 30, 90). We interpolate expected cost as a
    weighted average over those horizons.

*   Risk tolerance: 0 = use P50 (risk-neutral), 1 = use P10 (maximally
    risk-averse, i.e. assume the market never improves). Intermediate values
    linearly blend P10 and P50.
"""
from __future__ import annotations

from collections.abc import Sequence

from opt.types import (
    BasisEntry,
    ForecastFan,
    LockWaitResult,
    VesselClass,
)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _blend_quantile(p10: float, p50: float, risk_tolerance: float) -> float:
    """Blend P50 toward P10 by risk_tolerance in [0, 1].

    risk_tolerance=0 → P50 (risk-neutral expected value).
    risk_tolerance=1 → P10 (fully pessimistic about spot rate rising).
    """
    if not 0.0 <= risk_tolerance <= 1.0:
        raise ValueError(f"risk_tolerance must be in [0, 1], got {risk_tolerance}.")
    return (1.0 - risk_tolerance) * p50 + risk_tolerance * p10


def _apply_basis(
    base_p10: float,
    base_p50: float,
    base_p90: float,
    basis: BasisEntry | None,
) -> tuple[float, float, float]:
    """Apply route basis to BASE quantiles, widening the fan by basis_std.

    Returns (route_p10, route_p50, route_p90) in USD/day.

    When basis is None, the BASE quantiles pass through unchanged.
    Per 02_overview.md:
        route_P50 = BASE_P50 × (1 + basis_mean)
        route_fan_half_width = (BASE_P90 - BASE_P10) / 2 × sqrt(1 + (basis_std / basis_mean)^2)
    We use a simpler additive widening that is numerically more stable when
    basis_mean ≈ 0 (synthetic routes):
        route_P50 = BASE_P50 × (1 + basis_mean)
        widening  = BASE_P50 × basis_std          (absolute USD/day)
        route_P10 = BASE_P10 × (1 + basis_mean) - widening
        route_P90 = BASE_P90 × (1 + basis_mean) + widening
    """
    if basis is None:
        return base_p10, base_p50, base_p90

    m = basis.basis_mean
    s = basis.basis_std
    p50 = base_p50 * (1.0 + m)
    widening = base_p50 * s  # additive absolute USD/day widening
    p10 = max(base_p10 * (1.0 + m) - widening, 1.0)  # never go negative
    p90 = base_p90 * (1.0 + m) + widening
    return p10, p50, p90


def _horizon_weight(horizon_days: int, contract_term_days: int) -> float:
    """Fraction of the contract period that a given horizon 'covers'.

    Segments use half-open intervals [lo, hi) so they tile perfectly with no
    overlap or gap:
        h=7  → [1, 19)  i.e. days 1–18  (midpoint between 0↔7 and 7↔30)
        h=30 → [19, 61) i.e. days 19–60 (midpoint between 7↔30 and 30↔90)
        h=90 → [61, ∞)  i.e. days 61+

    For a contract_term_days shorter than a segment start, the weight is 0.
    Weights are *not* normalised here; compute_ceiling divides by total_raw_weight
    so that missing horizons are handled gracefully.
    """
    # Half-open intervals [lo, hi) for each horizon segment
    _BREAKS: dict[int, tuple[int, int]] = {
        7:  (0,  19),
        30: (19, 61),
        90: (61, 10_001),
    }
    if horizon_days not in _BREAKS:
        raise ValueError(f"Unsupported horizon_days={horizon_days}; expected 7, 30, or 90.")
    lo, hi = _BREAKS[horizon_days]
    effective_lo = min(lo, contract_term_days)
    effective_hi = min(hi, contract_term_days)
    covered = max(effective_hi - effective_lo, 0)
    return covered / contract_term_days if contract_term_days > 0 else 0.0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_ceiling(
    forecasts: Sequence[ForecastFan],
    vessel_class: VesselClass,
    contract_term_days: int,
    risk_tolerance: float = 0.0,
    basis: BasisEntry | None = None,
) -> dict[str, float]:
    """Compute the ceiling TC hire rate for locking vs waiting in the spot market.

    Parameters
    ----------
    forecasts:
        List of ForecastFan objects from the ML model. Must contain at least
        one entry for ``vessel_class``. Missing horizons are skipped (weight
        redistributed to available horizons).
    vessel_class:
        The vessel class to evaluate (Capesize / Panamax / Supramax / Handysize).
    contract_term_days:
        Length of the TC contract being considered, in calendar days.
        Typical values: 30 (1 month), 90 (3 months), 180 (6 months), 365 (12 months).
    risk_tolerance:
        0.0 = risk-neutral (weight P50 fully). 1.0 = fully risk-averse (weight P10).
    basis:
        Optional BasisEntry for the route family. When provided, adjusts BASE
        TCE to route-level TCE. When None, BASE TCE is used directly.

    Returns
    -------
    dict with keys:
        ceiling_usd_per_day       The max hire rate at which locking beats expected spot.
        expected_spot_p50         Horizon-weighted P50 spot cost (no risk adj).
        expected_spot_blended     Risk-adjusted blended spot cost (the ceiling).
        expected_spot_p10         Horizon-weighted P10 (worst realistic spot level).
        route_adjusted            True if a basis adjustment was applied.
        total_weight              Sum of horizon weights (should be ~1.0 if all horizons present).

    Raises
    ------
    ValueError
        If no forecast fan entries exist for the requested vessel_class.
    """
    if contract_term_days <= 0:
        raise ValueError(f"contract_term_days must be positive, got {contract_term_days}.")

    # Filter to the requested class
    relevant = [f for f in forecasts if f.vessel_class == vessel_class]
    if not relevant:
        raise ValueError(
            f"No forecast entries found for vessel class {vessel_class!r}. "
            f"Available classes: {sorted({f.vessel_class for f in forecasts})}."
        )

    total_raw_weight = sum(_horizon_weight(f.horizon_days, contract_term_days) for f in relevant)
    if total_raw_weight <= 0:
        raise ValueError(
            f"All horizon weights are zero for contract_term_days={contract_term_days}. "
            "This means the contract is shorter than 1 day, which is not supported."
        )

    weighted_p10 = 0.0
    weighted_p50 = 0.0
    weighted_blended = 0.0

    for fan in relevant:
        w = _horizon_weight(fan.horizon_days, contract_term_days)
        if w == 0.0:
            continue

        # Apply route basis (if any) to this horizon's fan
        rp10, rp50, _ = _apply_basis(fan.p10, fan.p50, fan.p90, basis)

        normalised_w = w / total_raw_weight
        weighted_p10 += normalised_w * rp10
        weighted_p50 += normalised_w * rp50
        weighted_blended += normalised_w * _blend_quantile(rp10, rp50, risk_tolerance)

    return {
        "ceiling_usd_per_day": weighted_blended,
        "expected_spot_p50": weighted_p50,
        "expected_spot_blended": weighted_blended,
        "expected_spot_p10": weighted_p10,
        "route_adjusted": basis is not None,
        "total_weight": total_raw_weight,
    }


def lock_or_wait(
    forecasts: Sequence[ForecastFan],
    vessel_class: VesselClass,
    contract_term_days: int,
    today_quote_usd_per_day: float,
    risk_tolerance: float = 0.0,
    basis: BasisEntry | None = None,
) -> LockWaitResult:
    """Evaluate whether to lock a TC contract today or wait.

    The rule is simple:
        today_quote ≤ ceiling  →  LOCK
        today_quote >  ceiling →  WAIT

    Parameters
    ----------
    forecasts:
        ML model output fan for the relevant vessel class.
    vessel_class:
        Vessel class being evaluated.
    contract_term_days:
        Length of TC contract in calendar days.
    today_quote_usd_per_day:
        Today's real-world broker TC quote in USD/day (NOT a forecast).
    risk_tolerance:
        0=risk-neutral, 1=fully risk-averse.
    basis:
        Optional route-family basis calibration.

    Returns
    -------
    LockWaitResult with the action ("LOCK" or "WAIT") and supporting numbers.
    """
    if today_quote_usd_per_day <= 0:
        raise ValueError(
            f"today_quote_usd_per_day must be positive, got {today_quote_usd_per_day}."
        )

    ceiling_info = compute_ceiling(
        forecasts=forecasts,
        vessel_class=vessel_class,
        contract_term_days=contract_term_days,
        risk_tolerance=risk_tolerance,
        basis=basis,
    )

    ceiling = ceiling_info["ceiling_usd_per_day"]
    p50_spot = ceiling_info["expected_spot_p50"]
    p10_spot = ceiling_info["expected_spot_p10"]
    action: str = "LOCK" if today_quote_usd_per_day <= ceiling else "WAIT"

    # Savings = expected spot cost MINUS what you'd pay under the TC.
    # Positive savings = locking is cheaper than expected spot.
    savings_p50 = p50_spot - today_quote_usd_per_day
    savings_p10 = p10_spot - today_quote_usd_per_day

    return LockWaitResult(
        vessel_class=vessel_class,
        contract_term_days=contract_term_days,
        ceiling_usd_per_day=ceiling,
        today_quote_usd_per_day=today_quote_usd_per_day,
        action=action,  # type: ignore[arg-type]
        expected_spot_cost_usd_per_day=p50_spot,
        savings_p50_usd_per_day=savings_p50,
        savings_p10_usd_per_day=savings_p10,
        route_adjusted=ceiling_info["route_adjusted"],
    )
