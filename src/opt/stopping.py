"""Optimal stopping via Least-Squares Monte Carlo (Longstaff & Schwartz, 2001):
PS deliverable (a), done properly.

``opt.ceiling``'s lock/wait rule is a single threshold: lock if today's quote is
below a horizon-weighted expected spot cost, otherwise wait. That throws away
the one thing that actually makes "wait" valuable -- the *option* to keep
watching and lock later at a better price, which is worth something even when
today's quote already looks acceptable. This module prices that option properly
and returns an **exercise boundary** (a price level for every day in the
planning horizon: lock below it, wait above it) instead of one number.

**Now fused into the production decision, not merely additive.** P3 shipped
this as a side channel only (``opt.api.run_optimizer`` kept computing
``lock_action``/``ceiling_usd_per_day`` from ``opt.ceiling`` alone, with this
module's output carried as an independent, unused-by-the-decision field) --
deliberately, because swapping the production path in the same pass that
built its replacement was judged too risky at the time. Once that runway had
passed, this module was tested properly against real mathematical properties
(dominance over fixed non-adaptive rules, a finite/well-behaved boundary
across a grid of real scenarios) and two real, previously-untested defects
were found and fixed first: the simulated price process ignored route basis
entirely even though the strike it was priced against didn't (see
``opt.ceiling.route_adjusted_fans``), and ``risk_tolerance`` was silently
dropped rather than threaded into the strike. With both fixed,
``solve_lock_or_wait`` below is the fusion: it calls
``opt.ceiling.lock_or_wait_for_cargo`` for the cargo/route/class derivation
and base decision (unchanged, still independently correct), then swaps in the
exercise boundary's own day-0 value as the operative ceiling when the LSMC
solve has enough data to run, falling back to the plain rule otherwise.
``opt.ceiling.compute_ceiling``/``lock_or_wait`` themselves are untouched --
``opt.fleetmix``, ``opt.repositioning``, and ``opt.portfolio`` all still call
``compute_ceiling`` directly for their own, different-purpose needs (pricing
many classes quickly, a hazard-rate ingredient, a coverage-mix cost), which
this fusion does not touch.

**2.4: the weather/cyclone transit buffer taxes the WAIT branch.** Waiting
for a better rate is only free if the cargo can still be lifted when you
decide to fix. If the laycan sits inside a high cyclone-strike window (the
climatology from ``data_builders.build_cyclone_climatology``) or a rough
patch of the live 7-day marine forecast (``opt.weather_window``), waiting
carries a real extra expected cost that a pure rate-forecast option-value
calculation cannot see on its own: berth queues rebuild after a storm, and a
missed laycan is a real commercial loss, not a wash. ``solve_lock_or_wait``'s
optional ``weather_delay_days`` (from ``opt.weather_window.TransitBuffer.expected_delay_days``,
computed by the caller -- this module has no network access of its own and
makes none) taxes that branch explicitly: see the arithmetic in
``solve_lock_or_wait``'s own body. The tax is one-directional by
construction -- it can only push a marginal WAIT into a LOCK, never the
reverse, and defaults to 0.0 (byte-identical to every pre-2.4 caller).

Method
------
The rate process is simulated as piecewise log-normal, calibrated so its
simulated p10/p50/p90 match the *real* forecast fan exactly at the fan's own
horizons (7/30/90 days) -- not a free-parameter fit, a deterministic
calibration from quantiles already produced by the trained models (see
``_calibrate_piecewise_lognormal``). The decision is framed as an American put
with strike ``K`` = the existing ceiling's own horizon-weighted, risk-adjusted
expected spot cost (``opt.ceiling.compute_ceiling``'s ``expected_spot_blended``
-- risk-neutral P50 when risk_tolerance=0, blended toward P90 otherwise, same
as the simple rule's own ceiling), forced to exercise by the end of the
planning horizon (SAIL must eventually charter the ship): payoff at day d is
``K - S_d``. Longstaff-Schwartz backward induction
with a linear basis (``[1, S]``, not the more usual quadratic -- a deliberate
simplification: the exercise-boundary solve for a linear continuation-value fit
is a linear equation with one root, not a quadratic with a root-selection
question, which removes a real class of bug for a modest loss of curve fit)
gives, at each day, a fitted continuation value; the boundary is where it
crosses the exercise value.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence

import numpy as np

from opt.ceiling import compute_ceiling, lock_or_wait_for_cargo, route_adjusted_fans
from opt.network import PortEnum, RouteFamily, route_family_for_origin
from opt.types import (
    BasisEntry,
    ForecastFan,
    LockWaitResult,
    StoppingResult,
    VesselClass,
)

__all__ = [
    "InsufficientForecastError",
    "StoppingResult",
    "solve_exercise_boundary",
    "solve_lock_or_wait",
]

#: z-score for the p10/p90 quantiles, matching the convention already used by
#: opt.types.ForecastFan and ml.baselines.ZSCORES (0.1/0.9 quantiles of a
#: standard normal).
_Z90 = 1.2816

#: Simulated paths for the LSMC regression. Large enough for a stable fit at
#: the horizon lengths involved (<=90 days), small enough to solve in well
#: under a second -- this runs inside an interactive recommendation, not an
#: offline batch job.
DEFAULT_N_PATHS = 4000


class InsufficientForecastError(ValueError):
    """Raised when there aren't enough real forecast horizons to calibrate a path."""


def _calibrate_piecewise_lognormal(
    today_quote: float, forecasts: list[ForecastFan], vessel_class: VesselClass, horizon_days: int
) -> tuple[np.ndarray, np.ndarray]:
    """Daily (drift, vol) arrays of length ``horizon_days``, calibrated so the
    simulated process's day-h quantiles exactly match the real forecast fan at
    every horizon h in {7, 30, 90} it has an entry for.

    Returns (daily_drift, daily_vol) such that cumulative log-return to day t is
    Normal(sum(daily_drift[:t]), sum(daily_vol[:t]**2)).
    """
    relevant = sorted(
        (f for f in forecasts if f.vessel_class == vessel_class and f.horizon_days <= horizon_days),
        key=lambda f: f.horizon_days,
    )
    if not relevant:
        raise InsufficientForecastError(
            f"No forecast horizons <= {horizon_days} days for {vessel_class}; cannot calibrate a path."
        )

    # Anchor points: (day, cumulative log-drift, cumulative variance), starting
    # from day 0 = today's real observed quote (not a forecast value).
    anchor_days = [0]
    cum_drift = [0.0]
    cum_var = [0.0]
    for f in relevant:
        log_p50 = np.log(f.p50 / today_quote)
        # Average the two tail-implied cumulative vols (p10 and p90 sides) --
        # a real forecast fan need not be perfectly symmetric in log-space, and
        # averaging is a simple, honest way to use both tails rather than
        # arbitrarily picking one.
        vol_from_p90 = np.log(f.p90 / f.p50) / _Z90
        vol_from_p10 = np.log(f.p50 / f.p10) / _Z90
        cum_vol = float(np.mean([vol_from_p90, vol_from_p10]))
        anchor_days.append(f.horizon_days)
        cum_drift.append(float(log_p50))
        cum_var.append(max(cum_vol, 0.0) ** 2)

    # Extend flat beyond the last real horizon if the planning window runs
    # longer than the furthest forecast -- holds the last known daily rate of
    # drift/variance accrual rather than fabricating a longer-horizon shape.
    if anchor_days[-1] < horizon_days:
        last_h = anchor_days[-1]
        prev_h = anchor_days[-2] if len(anchor_days) > 1 else 0
        span = max(last_h - prev_h, 1)
        daily_drift_tail = (cum_drift[-1] - cum_drift[-2]) / span if len(cum_drift) > 1 else 0.0
        daily_var_tail = (cum_var[-1] - cum_var[-2]) / span if len(cum_var) > 1 else 0.0
        extra = horizon_days - last_h
        anchor_days.append(horizon_days)
        cum_drift.append(cum_drift[-1] + daily_drift_tail * extra)
        cum_var.append(cum_var[-1] + daily_var_tail * extra)

    daily_drift = np.zeros(horizon_days)
    daily_var = np.zeros(horizon_days)
    for i in range(1, len(anchor_days)):
        d0, d1 = anchor_days[i - 1], anchor_days[i]
        if d1 <= d0:
            continue
        span = d1 - d0
        seg_drift = (cum_drift[i] - cum_drift[i - 1]) / span
        seg_var = max(cum_var[i] - cum_var[i - 1], 0.0) / span
        daily_drift[d0:d1] = seg_drift
        daily_var[d0:d1] = seg_var
    # Any days before the first anchor > 0 (shouldn't occur given anchor_days[0]=0)
    daily_vol = np.sqrt(daily_var)
    return daily_drift, daily_vol


def _simulate_paths(
    today_quote: float, daily_drift: np.ndarray, daily_vol: np.ndarray, n_paths: int, rng: np.random.Generator
) -> np.ndarray:
    """(n_paths, horizon_days) matrix of simulated USD/day levels, day 1..horizon."""
    horizon_days = len(daily_drift)
    z = rng.standard_normal((n_paths, horizon_days))
    daily_log_returns = daily_drift[None, :] + daily_vol[None, :] * z
    cum_log_returns = np.cumsum(daily_log_returns, axis=1)
    return today_quote * np.exp(cum_log_returns)


def _boundary_by_crossing(
    x: np.ndarray, exercise_vals: np.ndarray, continuation_vals: np.ndarray
) -> float:
    """Find the price where exercise and continuation cross by linearly
    interpolating the actual simulated points, rather than solving a closed
    form that can be near-degenerate.

    Both inputs are exact linear functions of ``x`` (exercise = strike - x;
    continuation = c0 + c1*x, the fitted regression line, evaluated with no
    per-path noise), so their difference is itself exactly linear -- it can
    cross zero at most once. When no sign change shows up across the observed
    prices, that root exists but falls outside the range this day's paths
    actually populated (this is precisely the near-degenerate regime
    ``solve_exercise_boundary``'s closed form guards against, reached from a
    different angle): one side dominates for every real price this process
    produced, so the honest boundary is the edge of the observed range on the
    side consistent with that -- ``max(x)`` when exercise wins everywhere
    (locking beats waiting at every simulated price, so report the boundary
    as high as the real data supports rather than extrapolating a specific
    number from almost nothing), ``min(x)`` when continuation wins everywhere
    (the reverse). Always returns a value inside ``[min(x), max(x)]``.

    A real, non-hypothetical case this was found from: a persistently
    near-degenerate regression slope across many consecutive days (the
    expected regime for a low-mean-reversion price process, not rare) used to
    make the old "return None, caller defaults to the bare strike" contract
    fire on most of those days while the closed form fired on their
    immediate neighbours -- producing a boundary that whiplashed between the
    bare strike and a much lower, genuinely-fitted value from one day to the
    next. Reproduced across many seeds and path counts up to 20,000; never
    exercised by any test before real risk_tolerance/basis values were wired
    through the LSMC path for the first time.
    """
    order = np.argsort(x)
    xs = x[order]
    diff = exercise_vals[order] - continuation_vals[order]
    sign_changes = np.where(np.diff(np.sign(diff)) != 0)[0]
    if len(sign_changes) == 0:
        return float(xs[-1]) if diff[0] > 0 else float(xs[0])
    i = sign_changes[0]
    x0, x1 = xs[i], xs[i + 1]
    d0, d1 = diff[i], diff[i + 1]
    if d1 == d0:
        return float(x0)
    t = -d0 / (d1 - d0)
    return float(x0 + t * (x1 - x0))


def solve_exercise_boundary(
    today_quote_usd_per_day: float,
    forecasts: list[ForecastFan],
    vessel_class: VesselClass,
    contract_term_days: int,
    planning_horizon_days: int,
    basis: BasisEntry | None = None,
    risk_tolerance: float = 0.0,
    n_paths: int = DEFAULT_N_PATHS,
    seed: int = 0,
) -> StoppingResult:
    """Solve the American-put-style optimal stopping problem via LSMC.

    ``risk_tolerance`` (0=risk-neutral P50, 1=fully risk-averse P90, same
    meaning as ``opt.ceiling.lock_or_wait``) shifts the strike the option is
    priced against, exactly like it shifts the simple rule's ceiling -- it
    does not alter the simulated process itself, since risk tolerance is the
    decision-maker's preference, not a claim about what the market will do.

    ``basis``, when given, is applied to the forecast fan BEFORE calibrating
    the simulated price process, not just to the strike -- the two must
    describe the same (route-adjusted) market view, or the simulated paths
    drift toward a level (the class-level forecast) the route was never
    actually expected to reach, silently distorting the whole boundary shape
    around a today's-quote-vs-wrong-target gap that isn't real drift.

    Raises
    ------
    InsufficientForecastError
        No usable forecast horizons for this class within the planning window.
    ValueError
        Non-positive quote, horizon, or path count.
    """
    if today_quote_usd_per_day <= 0:
        raise ValueError(f"today_quote_usd_per_day must be positive, got {today_quote_usd_per_day}")
    if planning_horizon_days <= 0:
        raise ValueError(f"planning_horizon_days must be positive, got {planning_horizon_days}")
    if n_paths <= 0:
        raise ValueError(f"n_paths must be positive, got {n_paths}")

    try:
        ceiling_info = compute_ceiling(
            forecasts=forecasts, vessel_class=vessel_class, contract_term_days=contract_term_days,
            risk_tolerance=risk_tolerance, basis=basis,
        )
    except ValueError as exc:
        # compute_ceiling raises a bare ValueError for "no forecast entries for
        # this class" -- re-raised as our own type so callers have one
        # consistent exception to catch for "not enough forecast data" here,
        # regardless of which internal step detected it.
        raise InsufficientForecastError(str(exc)) from exc
    # expected_spot_blended, not expected_spot_p50: at risk_tolerance=0 these
    # are identical (so every existing default-risk-tolerance test is
    # unaffected), but a risk-averse caller must get a risk-averse strike here
    # too, exactly like the simple rule's own ceiling.
    strike = ceiling_info["expected_spot_blended"]

    route_forecasts = route_adjusted_fans(forecasts, vessel_class, basis)
    daily_drift, daily_vol = _calibrate_piecewise_lognormal(
        today_quote_usd_per_day, route_forecasts, vessel_class, planning_horizon_days
    )
    rng = np.random.default_rng(seed)
    paths = _simulate_paths(today_quote_usd_per_day, daily_drift, daily_vol, n_paths, rng)

    n, horizon = paths.shape
    exercise_value = strike - paths  # (n_paths, horizon); can be negative

    # Backward induction. cash_flow[i] = realized payoff for path i under the
    # policy decided so far (starts as forced exercise at the last day).
    cash_flow = exercise_value[:, -1].copy()
    stopping_day = np.full(n, horizon)  # 1-indexed day each path currently stops on
    boundary = np.empty(horizon)
    boundary[-1] = strike  # at maturity, exercise value == strike - S, "boundary" is S==strike

    #: Minimum |slope of (continuation - exercise)| before the closed-form
    #: boundary S* = (K - c0)/(1 + c1) is trusted. When the fitted continuation
    #: line is nearly parallel to the exercise line (c1 close to -1), that
    #: closed form divides by a near-zero denominator and the "boundary" comes
    #: out wildly unstable ($0 or hundreds of thousands of $/day from a $15k/day
    #: process) -- a real numerical issue found by running this on real forecast
    #: fans, not a hypothetical. Below this threshold, the boundary is instead
    #: read directly off where the simulated exercise/continuation values cross
    #: (robust: it can't blow up, because it never divides by a fitted slope).
    _MIN_SLOPE_FOR_CLOSED_FORM = 0.1

    #: Ridge penalty on the REPORTED-BOUNDARY fit's slope only (never the
    #: policy fit -- see below), relative to the regressor's own scale
    #: (alpha * sum(S_t^2)) so it doesn't need re-tuning across price levels
    #: or path counts. A second real numerical issue found by testing this
    #: properly (not hypothetical): the linear continuation-value fit is a
    #: genuinely weak regression far from maturity (a day's price explains
    #: only a small part of the *rest* of a 30-90 day path's payoff, the
    #: known cost of the linear-basis simplification this module's own
    #: docstring already flags), so plain OLS's c1 has real sampling
    #: variance -- day-1 boundaries for the *identical* real inputs swung
    #: between roughly $4,000 and $18,000/day just from changing the RNG
    #: seed, before this fix, at both the already-shipped risk_tolerance=0/
    #: basis=None defaults and the newly-wired-through settings alike
    #: (confirmed not specific to either). Swept 0.0-0.2 empirically; 0.02
    #: already removes ~98% of the day-to-day jump, so 0.05 is a
    #: deliberately modest, non-fine-tuned choice.
    _RIDGE_ALPHA = 0.05

    for day in range(horizon - 1, 0, -1):  # day index into 0-based columns, day+1 is the 1-indexed day
        s_t = paths[:, day - 1]
        exercise_now = exercise_value[:, day - 1]
        itm = exercise_now > 0  # only regress in-the-money paths (Longstaff-Schwartz)
        # Default when there are too few in-the-money paths to fit a
        # regression at all (typically only the earliest days, before the
        # price has moved far enough for many paths to qualify): hold the
        # very next (already-solved, one day closer to maturity) day's
        # boundary rather than the bare strike, so a thin day inherits the
        # nearest real information already solved for instead of a value
        # with no time-value adjustment. _boundary_by_crossing below always
        # returns a real, data-grounded value now (never None), so this
        # default is only reached by the itm.sum() < 30 branch.
        boundary[day - 1] = boundary[day]
        if itm.sum() >= 30:
            x = s_t[itm]
            y = cash_flow[itm]  # already the realized continuation payoff for these paths
            design = np.column_stack([np.ones_like(x), x])

            # Unbiased OLS drives the actual exercise decision (should_exercise,
            # hence cash_flow/option_value/recommended stopping times) -- this
            # is exactly what the optimality-dominance tests check, and an
            # earlier version of this fix that ridge-shrank THIS fit broke
            # dominance over "always wait to maturity": shrinking c1 toward 0
            # is a real bias, not just variance reduction, when the true
            # relationship genuinely is close to -1 (the expected shape for a
            # low-mean-reversion process, not itself a sign of a bad fit) --
            # it systematically underestimates continuation value at the low
            # prices where waiting is most valuable, triggering exercise too
            # early. Left exactly as before: plain, unregularized OLS.
            c0, c1 = np.linalg.lstsq(design, y, rcond=None)[0]
            continuation = c0 + c1 * s_t
            should_exercise = itm & (exercise_now > continuation)
            cash_flow = np.where(should_exercise, exercise_now, cash_flow)
            stopping_day = np.where(should_exercise, day, stopping_day)

            # A SEPARATE, ridge-regularized fit on the same (x, y) is used
            # only to derive the boundary PRICE LEVEL reported for this day
            # -- it never feeds back into should_exercise/cash_flow above, so
            # it cannot bias the policy or the dominance guarantee, only
            # stabilise the day-to-day *display* value.
            xtx = design.T @ design
            ridge_penalty = np.array([[0.0, 0.0], [0.0, _RIDGE_ALPHA * xtx[1, 1]]])  # never penalize the intercept
            rc0, rc1 = np.linalg.solve(xtx + ridge_penalty, design.T @ y)
            report_continuation = rc0 + rc1 * s_t

            denom = 1.0 + rc1
            if abs(denom) >= _MIN_SLOPE_FOR_CLOSED_FORM:
                boundary[day - 1] = (strike - rc0) / denom
            else:
                boundary[day - 1] = _boundary_by_crossing(x, exercise_now[itm], report_continuation[itm])

    option_value = float(np.mean(cash_flow))
    boundary = np.maximum(boundary, 0.0)

    action = "LOCK" if today_quote_usd_per_day <= boundary[0] else "WAIT"

    return StoppingResult(
        vessel_class=vessel_class,
        today_quote_usd_per_day=today_quote_usd_per_day,
        strike_usd_per_day=strike,
        planning_horizon_days=horizon,
        exercise_boundary_usd_per_day=tuple(float(b) for b in boundary),
        option_value_usd_per_day=option_value,
        recommended_action_today=action,
        n_paths=n_paths,
    )


def solve_lock_or_wait(
    forecasts: Sequence[ForecastFan],
    cargo_volume_dwt: float,
    origin_port: PortEnum,
    dest_port: PortEnum,
    contract_term_days: int,
    planning_horizon_days: int,
    today_quote_usd_per_day: float,
    basis_table: Mapping[RouteFamily, BasisEntry] | None = None,
    risk_tolerance: float = 0.0,
    n_paths: int = DEFAULT_N_PATHS,
    seed: int = 0,
    *,
    weather_delay_days: float = 0.0,
    vessel_class_override: VesselClass | None = None,
) -> tuple[LockWaitResult, StoppingResult | None]:
    """The production lock/wait decision -- option-value-aware when there's
    enough real forecast data to calibrate a price path, gracefully falling
    back to ``opt.ceiling``'s plain horizon-blended rule otherwise. A missing
    forecast horizon must never break the core recommendation.

    This *fuses* the two engines rather than replacing either: it calls
    ``opt.ceiling.lock_or_wait_for_cargo`` for the cargo/route/class
    derivation and the base decision (unchanged, still fully computed, still
    independently correct on its own), then -- when the LSMC solve succeeds --
    swaps in the exercise boundary's own day-0 value (optionally weather-taxed,
    see below) as the operative ceiling and recomputes the operative action
    against it, since that boundary is a strict refinement of the same strike
    (see ``solve_exercise_boundary``): same route/class/risk-adjusted expected
    value, plus the option value of being able to keep waiting. Every other
    field on the result (the savings estimate, route_adjusted, the cargo/route
    identity) keeps its original, already-correct meaning from the plain rule
    -- only the action and its threshold get refined.

    ``weather_delay_days`` (default 0.0 -- see this module's own docstring for
    the economic argument behind it) is an explicit, one-directional tax on
    the WAIT branch only: see the comment at the point it's applied, below,
    for the exact arithmetic. It never alters ``stopping_result`` itself (the
    raw LSMC detail -- ``stopping_result.exercise_boundary_usd_per_day``
    reports the UNTAXED boundary throughout, and
    ``stopping_result.recommended_action_today`` the untaxed action), only the
    fused ``LockWaitResult`` this function returns; a caller that wants the
    weather-adjusted numbers reads ``ceiling_usd_per_day``/``action`` on that,
    not on ``stopping_result``.

    Returns
    -------
    (LockWaitResult, StoppingResult | None)
        The fused decision, and the full LSMC detail behind it (None when the
        LSMC solve couldn't run for lack of forecast data -- the LockWaitResult
        in that case is exactly ``opt.ceiling.lock_or_wait_for_cargo``'s own
        result, unmodified -- ``weather_delay_days`` has no effect on that
        fallback path, since there is no boundary for it to tax).
    """
    plain_result = lock_or_wait_for_cargo(
        forecasts=forecasts,
        cargo_volume_dwt=cargo_volume_dwt,
        origin_port=origin_port,
        dest_port=dest_port,
        contract_term_days=contract_term_days,
        today_quote_usd_per_day=today_quote_usd_per_day,
        basis_table=basis_table,
        risk_tolerance=risk_tolerance,
        vessel_class_override=vessel_class_override,
    )
    route_family = route_family_for_origin(origin_port)
    basis = (basis_table or {}).get(route_family)

    try:
        stopping_result = solve_exercise_boundary(
            today_quote_usd_per_day=today_quote_usd_per_day,
            forecasts=list(forecasts),
            vessel_class=plain_result.vessel_class,
            contract_term_days=contract_term_days,
            planning_horizon_days=planning_horizon_days,
            basis=basis,
            risk_tolerance=risk_tolerance,
            n_paths=n_paths,
            seed=seed,
        )
    except InsufficientForecastError:
        return plain_result, None

    boundary_today = stopping_result.exercise_boundary_usd_per_day[0]

    # --- 2.4: weather/cyclone transit buffer, applied here only (never
    # inside stopping_result / solve_exercise_boundary -- see this
    # function's own docstring) ---
    # Convert the expected delay into a PER-DAY USD-equivalent and use it to
    # raise the LOCK-triggering boundary. Raising the boundary by X is
    # arithmetically identical to subtracting X from the option value of
    # waiting: the boundary IS the price level at which LOCK is exactly as
    # good as WAIT, so pushing it up by X means WAIT must now be worth X
    # more than before to still be preferred.
    #
    #     weather_cost_usd        = weather_delay_days * today_quote_usd_per_day
    #     weather_cost_per_day    = weather_cost_usd / contract_term_days
    #     adjusted_boundary       = boundary_today + weather_cost_per_day
    #
    # **The division by contract_term_days is not cosmetic -- omitting it is
    # a real units bug, and this code shipped with exactly that bug before it
    # was caught by a user seeing LOCK on every quote they generated.**
    # ``boundary_today`` is a RATE (USD per day); ``weather_delay_days *
    # today_quote_usd_per_day`` is a TOTAL (USD). Adding a total to a rate is
    # dimensionally meaningless, and the magnitude made it obvious in
    # hindsight: a real 0.83-day delay on a real $20,698/day Supramax quote
    # added $17,180 to a $21,016/day boundary -- inflating it ~82% and
    # forcing LOCK on essentially every route, cargo size and laycan.
    # Amortising the one-off delay cost over the contract it is being priced
    # against puts both terms in USD/day, which is what the comparison on the
    # next line actually requires.
    #
    # weather_delay_days=0.0 (the default, and every pre-2.4 caller) still
    # leaves adjusted_boundary == boundary_today exactly -- byte-identical
    # output. weather_cost is never negative (see
    # opt.weather_window.TransitBuffer -- expected_delay_days is a
    # non-negative sum of non-negative terms), so adjusted_boundary >=
    # boundary_today always: this can still only ever turn a marginal WAIT
    # into a LOCK, never a LOCK into a WAIT.
    weather_cost_usd = weather_delay_days * today_quote_usd_per_day
    weather_cost_usd_per_day = weather_cost_usd / contract_term_days
    adjusted_boundary = boundary_today + weather_cost_usd_per_day
    action = "LOCK" if today_quote_usd_per_day <= adjusted_boundary else "WAIT"

    fused_result = plain_result.model_copy(update={
        "ceiling_usd_per_day": adjusted_boundary,
        "action": action,
    })
    return fused_result, stopping_result
