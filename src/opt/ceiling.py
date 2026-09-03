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

*   Risk tolerance: 0 = use P50 (risk-neutral), 1 = use P90 (maximally
    risk-averse, i.e. assume the market could rise as high as its pessimistic
    scenario before you'd get the chance to lock). Intermediate values
    linearly blend P50 and P90. (F-42 fix: this comment previously said
    P10/"the market never improves" -- the actual behaviour, in
    _blend_quantile below and confirmed live, blends toward P90, the
    correct direction for "risk-averse" here: a charterer worried about
    being caught out by a rate INCREASE should weight the higher scenario
    more, which makes locking today look relatively more attractive, not
    less.)
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence

from opt.network import PortEnum, RouteFamily, route_family_for_origin
from opt.types import (
    CLASS_REFERENCE_DWT,
    BasisEntry,
    ForecastFan,
    LockWaitResult,
    VesselClass,
)

_CLASS_ORDER_ASCENDING: tuple[VesselClass, ...] = (
    VesselClass.HANDYSIZE,
    VesselClass.SUPRAMAX,
    VesselClass.PANAMAX,
    VesselClass.CAPESIZE,
)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _blend_quantile(p50: float, p90: float, risk_tolerance: float) -> float:
    """Blend P50 toward P90 by risk_tolerance in [0, 1].

    risk_tolerance=0 → P50 (risk-neutral expected value).
    risk_tolerance=1 → P90 (fully pessimistic about spot rate rising).
    """
    if not 0.0 <= risk_tolerance <= 1.0:
        raise ValueError(f"risk_tolerance must be in [0, 1], got {risk_tolerance}.")
    return (1.0 - risk_tolerance) * p50 + risk_tolerance * p90


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


def route_adjusted_fans(
    forecasts: Sequence[ForecastFan], vessel_class: VesselClass, basis: BasisEntry | None
) -> list[ForecastFan]:
    """Apply route basis to every real forecast fan entry for ``vessel_class``,
    returning new ForecastFan objects at the same horizons (other classes pass
    through unchanged).

    Used wherever a caller needs the ROUTE-level view of the *forecast itself*,
    not just a single blended ceiling number -- e.g. ``opt.stopping`` calibrates
    its simulated price process against this, so the process it simulates and
    the strike it prices against describe the same (route-adjusted) market
    view rather than silently mixing a class-level process with a route-level
    strike.

    Returns a new list equal to ``forecasts`` (not the same object) when
    ``basis`` is None.
    """
    if basis is None:
        return list(forecasts)
    adjusted: list[ForecastFan] = []
    for f in forecasts:
        if f.vessel_class != vessel_class:
            adjusted.append(f)
            continue
        p10, p50, p90 = _apply_basis(f.p10, f.p50, f.p90, basis)
        adjusted.append(ForecastFan(vessel_class=f.vessel_class, horizon_days=f.horizon_days, p10=p10, p50=p50, p90=p90))
    return adjusted


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
        0.0 = risk-neutral (weight P50 fully). 1.0 = fully risk-averse (weight P90 --
        see the module docstring's F-42 note for why P90, not P10, is correct here).
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
        rp10, rp50, rp90 = _apply_basis(fan.p10, fan.p50, fan.p90, basis)

        normalised_w = w / total_raw_weight
        weighted_p10 += normalised_w * rp10
        weighted_p50 += normalised_w * rp50
        weighted_blended += normalised_w * _blend_quantile(rp50, rp90, risk_tolerance)

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


def select_vessel_class_for_cargo(cargo_volume_dwt: float) -> VesselClass:
    """Pick the class that would naturally carry a cargo lot of this size in a
    single shipment: the smallest class whose representative capacity
    (``opt.types.CLASS_REFERENCE_DWT``) covers it, or Capesize -- the largest
    class this system prices -- if the lot exceeds even that. A lot that big
    would be split across multiple Capesize liftings in practice; the market
    rate quoted is still the Capesize rate.
    """
    if cargo_volume_dwt <= 0:
        raise ValueError(f"cargo_volume_dwt must be positive, got {cargo_volume_dwt}.")
    for cls in _CLASS_ORDER_ASCENDING:
        if cargo_volume_dwt <= CLASS_REFERENCE_DWT[cls]:
            return cls
    return VesselClass.CAPESIZE


def lock_or_wait_for_cargo(
    forecasts: Sequence[ForecastFan],
    cargo_volume_dwt: float,
    origin_port: PortEnum,
    dest_port: PortEnum,
    contract_term_days: int,
    today_quote_usd_per_day: float,
    basis_table: Mapping[RouteFamily, BasisEntry] | None = None,
    risk_tolerance: float = 0.0,
    vessel_class_override: VesselClass | None = None,
) -> LockWaitResult:
    """The main lock/wait decision, from what a charterer actually has: a
    cargo lot to move, where it's going, a contract length, and today's
    broker quote -- not a pre-chosen vessel class and not a pre-selected
    basis entry.

    ``vessel_class`` is derived from ``cargo_volume_dwt`` (see
    ``select_vessel_class_for_cargo``) and the route basis is looked up from
    ``origin_port`` (see ``opt.network.route_family_for_origin``) in
    ``basis_table``, so the ceiling priced here is the ROUTE's forecast, not
    just the class's BASE forecast -- the two coincide only when no basis
    entry exists for this route (``route_adjusted=False`` on the result says
    so explicitly).

    Parameters
    ----------
    forecasts, risk_tolerance:
        Same meaning as in ``lock_or_wait`` -- real ML model output and the
        caller's risk posture, not something a charterer types in by hand.
    cargo_volume_dwt:
        Size of the cargo lot to move, in dwt (e.g. 70,000).
    origin_port, dest_port:
        Where the cargo loads and discharges.
    contract_term_days:
        Length of the TC contract being considered (e.g. 30, 90).
    today_quote_usd_per_day:
        Today's real broker TC quote for the derived vessel class.
    basis_table:
        The route-family -> BasisEntry calibration table. When the derived
        route family has no entry, the BASE (class-level) forecast is used
        unadjusted -- same graceful behaviour as ``lock_or_wait`` itself.
    vessel_class_override:
        Price the decision as this class instead of the one derived from
        cargo size. Callers that have already run the fleet-mix frontier
        should pass its chosen class: the frontier accounts for real port
        limits and cost, the tonnage lookup does not, and the two genuinely
        disagree (75,000 dwt derives Panamax while the frontier picks
        Supramax for Newcastle->Paradip). Leaving this None derives as
        before.
    """
    # An explicit class wins over the tonnage lookup. The lookup answers
    # "what size ship naturally carries this lot" from cargo size alone; the
    # fleet-mix frontier answers "what size ship should actually carry it"
    # using real port limits, transshipment and cost. When a caller has run
    # the frontier it knows better, and passing that class keeps the forecast
    # fans, today's quote and the class shown to the user all describing the
    # same ship. Left None, behaviour is exactly as before.
    vessel_class = vessel_class_override or select_vessel_class_for_cargo(cargo_volume_dwt)
    route_family = route_family_for_origin(origin_port)
    basis = (basis_table or {}).get(route_family)

    result = lock_or_wait(
        forecasts=forecasts,
        vessel_class=vessel_class,
        contract_term_days=contract_term_days,
        today_quote_usd_per_day=today_quote_usd_per_day,
        risk_tolerance=risk_tolerance,
        basis=basis,
    )
    return result.model_copy(update={
        "origin_port": origin_port,
        "dest_port": dest_port,
        "cargo_volume_dwt": cargo_volume_dwt,
    })
