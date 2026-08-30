"""Decision-value backtest for the ceiling optimizer.

Answers the *real* question: does acting on LOCK/WAIT recommendations
save money vs naive baselines, measured against actual historical data?

``simulate()`` is split-agnostic: it runs against whatever ``eval_split`` frame it
is handed, each row needing:
  - log_value  : actual spot TCE at date t
  - y_step_h30      : actual spot TCE at t + 30 calendar days (log-level)
  - y_mean_h30      : actual avg spot TCE over next 30 days (log-level)
  - y_step_h90      : actual spot TCE at t + 90 calendar days (log-level)
  - y_mean_h90      : actual avg spot TCE over next 90 days (log-level)

That agnosticism is deliberate but has a sharp edge: pass ``valid`` while tuning
anything (opt.calibration.calibrate_pso does exactly this), and pass ``test``
exactly once, for the final report, loaded through
``ml.frozen_test.load_frozen_test()`` inside an ``allow_test_set_access(...)``
block. See ``run_optimizer_backtest.py`` for that two-phase shape end to end.

And the ML model predictions (from baseline_metrics.csv or a predict()
call) give us P10/P50/P90 at those horizons.

We simulate:
  - TC quote  = spot_t × (1 − broker_spread)   [what a broker would offer]
  - Realised spot cost over the contract term   [exp(y_mean_h30) or exp(y_mean_h90)]
  - Savings from LOCK vs SPOT                  [quote - realised_spot per day]

Five strategies are compared for each (date, class) observation:
  always_spot   : never lock, pay realised spot. Baseline = 0 savings.
  always_lock   : always lock at today's quote, regardless of optimizer.
  calendar_lock : lock on the first trading day of each month.
  optimizer     : lock iff quote ≤ ceiling(fan, risk_tolerance).
  oracle        : hindsight-optimal (locks only when locking was actually cheaper).

By default the optimizer decision is the analytic ceiling rule from
``ceiling.lock_or_wait``.  Passing ``mc_config`` switches the optimizer to an
MC-informed decision: the spot-cost distribution over the contract term is
simulated under the configured dynamics (MonteCarloConfig.theta / sigma_long),
and the LOCK threshold becomes the risk_tolerance blend of its P50/P90 (F-42
fix: this said P50/P10 -- the actual blend below, `(1 - risk_tolerance) *
spot_p50 + risk_tolerance * spot_p90`, always used P90; same correction as
opt.ceiling's matching docstring, same underlying reasoning: risk-averse
should weight the higher, more-pessimistic-about-a-rate-rise scenario, not
the lower one) — so the simulation parameters genuinely affect decisions
and decision value.

Key outputs (all in $/day relative to always_spot):
  hit_rate      : fraction of LOCK decisions where locking was correct
  savings_mean  : E[savings/day] over the evaluated split
  savings_p10   : 10th-percentile savings (worst realistic outcome)
  decision_value: savings_mean(optimizer) − savings_mean(always_lock)
  regret        : savings_mean(oracle) − savings_mean(optimizer)
"""
from __future__ import annotations

import logging
import math
import random
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Literal

import polars as pl

from ml.quantiles import repair_crossed_quantiles
from opt.ceiling import lock_or_wait
from opt.monte_carlo import MonteCarloConfig, estimate_savings_distribution
from opt.types import BasisEntry, ForecastFan, VesselClass

LOGGER: Final[logging.Logger] = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

Strategy = Literal["always_spot", "always_lock", "calendar_lock", "optimizer", "oracle"]

_CLASS_MAP: dict[str, VesselClass] = {vc.value: vc for vc in VesselClass}


@dataclass
class BacktestRow:
    """One decision point in the backtest simulation."""
    date: object          # datetime.date
    vessel_class: str
    spot_usd: float       # exp(log_value) — current market rate USD/day
    quote_usd: float      # simulated TC quote = spot × (1 - broker_spread)
    realised_spot: float  # exp(y_step_h{term}) — actual avg spot over contract term
    ceiling_usd: float    # optimizer's LOCK threshold (analytic ceiling, or
                          # risk-blended simulated spot cost when mc_config is set)

    # Decision under each strategy
    action_optimizer: Literal["LOCK", "WAIT"]
    action_oracle: Literal["LOCK", "WAIT"]    # hindsight: lock iff quote < realised_spot

    # $/day savings vs always_spot (positive = cheaper than always_spot)
    savings_always_lock: float    # quote cheaper than realised_spot? lock saves money
    savings_optimizer: float      # only non-zero if optimizer says LOCK
    savings_oracle: float         # maximum possible savings


@dataclass
class BacktestSummary:
    """Aggregate metrics for one (strategy, class, contract_term) combination."""
    strategy: Strategy
    vessel_class: str         # "ALL" or specific class
    contract_term_days: int
    n_decisions: int

    # Savings distribution vs always_spot ($/day)
    savings_mean: float
    savings_p10: float
    savings_p50: float
    savings_p90: float

    # Decision quality
    hit_rate: float | None    # fraction of LOCKs that were correct (None for always_spot/oracle)
    lock_rate: float          # fraction of periods where we actually locked

    # Relative metrics (filled in after computing all strategies)
    decision_value: float | None = None   # savings_mean(this) - savings_mean(always_lock)
    regret: float | None = None           # savings_mean(oracle) - savings_mean(this)


# ---------------------------------------------------------------------------
# Core simulation
# ---------------------------------------------------------------------------

def _build_fan(
    row: dict,
    preds: pl.DataFrame,
    horizon: int,
    cls: VesselClass,
) -> tuple[ForecastFan | None, bool]:
    """Look up ML prediction for (date, class, horizon) and build a ForecastFan.

    Returns ``(fan, was_repaired)``. ``fan`` is None only when no prediction exists
    for that combination -- a prediction whose quantiles cross is REPAIRED (see
    ml.quantiles), not dropped. Dropping it used to bias the backtest toward the rows
    the model was most confident about: measured on the frozen test split,
    LightGBM's h=90 crosses on 23% of rows, and those rows were silently vanishing
    from every reported metric. ``was_repaired`` lets the caller report how often
    that happened instead of hiding it.

    pred DataFrame must have columns: date, target_class, h, p_0.1, p_0.5, p_0.9
    in LOG-LEVEL space (same as model output contract).
    """
    match = preds.filter(
        (pl.col("date") == pl.lit(row["date"])) &
        (pl.col("target_class") == cls.value) &
        (pl.col("h") == horizon)
    )
    if match.is_empty():
        return None, False
    r = match.row(0, named=True)
    p10 = math.exp(r["p_0.1"])
    p50 = math.exp(r["p_0.5"])
    p90 = math.exp(r["p_0.9"])
    triple = repair_crossed_quantiles(p10, p50, p90)
    if triple.was_crossed:
        LOGGER.debug(
            f"repaired crossed quantiles for {cls.value} h={horizon} on {row['date']}: "
            f"({p10:.0f},{p50:.0f},{p90:.0f}) -> "
            f"({triple.p10:.0f},{triple.p50:.0f},{triple.p90:.0f})"
        )
    # p10<=0 after sorting means the model's whole spread is non-positive -- a
    # genuinely broken forecast, not a crossing to repair. Drop it.
    if triple.p10 <= 0:
        return None, triple.was_crossed
    fan = ForecastFan(
        vessel_class=cls, horizon_days=horizon, p10=triple.p10, p50=triple.p50, p90=triple.p90
    )
    return fan, triple.was_crossed


def simulate(
    eval_split: pl.DataFrame,
    ml_predictions: pl.DataFrame,
    contract_term_days: int = 30,
    broker_spread: float = 0.03,
    risk_tolerance: float = 0.0,
    basis: BasisEntry | None = None,
    mc_config: MonteCarloConfig | None = None,
    mc_rng: random.Random | None = None,
) -> list[BacktestRow]:
    """Run the full backtest simulation and return per-row results.

    Parameters
    ----------
    eval_split:
        The split to backtest against — columns date, target_class, log_value,
        y_step_h30, y_step_h90. This function is split-agnostic by design: pass
        ``valid`` while tuning decision parameters (risk_tolerance, or theta/
        sigma_long via calibration.calibrate_pso) and ``test`` exactly once for
        the final report, loaded through ml.frozen_test.load_frozen_test() inside
        an allow_test_set_access(...) block. The parameter used to be named
        ``test_split``, which was itself part of the problem: it made "hand this
        the frozen test set" look like the obviously correct thing to do.
    ml_predictions:
        Combined P10/P50/P90 predictions from any model's predict() output,
        for all horizons. Columns: date, target_class, h, p_0.1, p_0.5, p_0.9
        (all in LOG-LEVEL space).
    contract_term_days:
        Length of TC contract to simulate. Only 30 and 90 are supported
        (must match an available y_step_h* column).
    broker_spread:
        Fraction by which TC quote is discounted below spot.
        quote = spot × (1 - broker_spread).
    risk_tolerance:
        Passed to lock_or_wait. 0 = risk-neutral.
    basis:
        Optional route-family basis adjustment.
    mc_config:
        When provided, the optimizer's LOCK threshold is derived from a
        Monte Carlo simulation of spot paths over the contract term
        (threshold = risk_tolerance blend of simulated P50/P90 spot cost)
        instead of the analytic ceiling. This makes ``mc_config.theta`` and
        ``mc_config.sigma_long`` genuinely affect decisions and decision
        value. Keep ``num_simulations`` modest — runtime scales with
        rows × simulations × contract days.
    mc_rng:
        Seeded ``random.Random`` passed through to the Monte Carlo layer for
        reproducible results. Ignored when ``mc_config`` is None.

    Returns
    -------
    List of BacktestRow, one per (date, class) row in eval_split for which
    both a realised outcome and ML prediction are available.
    """
    if contract_term_days not in (30, 90):
        raise ValueError(
            f"contract_term_days must be 30 or 90 (matching y_step_h* columns); "
            f"got {contract_term_days}."
        )
    if not (0.0 <= broker_spread < 1.0):
        raise ValueError(f"broker_spread must be in [0, 1); got {broker_spread}.")
    if not (0.0 <= risk_tolerance <= 1.0):
        raise ValueError(f"risk_tolerance must be in [0, 1], got {risk_tolerance}.")

    y_col = f"y_mean_h{contract_term_days}"
    if y_col not in eval_split.columns:
        raise ValueError(f"Column {y_col!r} not found in eval_split.")

    # Determine which horizons to use for the fan
    # For a 30-day contract: use h=7 and h=30 (h=90 has zero weight anyway)
    # For a 90-day contract: use all three
    if contract_term_days == 30:
        fan_horizons = [7, 30]
    else:
        fan_horizons = [7, 30, 90]

    rows: list[BacktestRow] = []
    n_fans_built = 0
    n_repaired = 0

    for record in eval_split.iter_rows(named=True):
        cls_str = record["target_class"]
        if cls_str not in _CLASS_MAP:
            continue
        cls = _CLASS_MAP[cls_str]

        spot_usd = math.exp(record["log_value"])
        realised_spot = math.exp(record[y_col])
        quote_usd = spot_usd * (1.0 - broker_spread)

        # Build multi-horizon fan from ML predictions
        fans: list[ForecastFan] = []
        for h in fan_horizons:
            fan, was_repaired = _build_fan(record, ml_predictions, h, cls)
            if fan is not None:
                fans.append(fan)
            if was_repaired:
                n_repaired += 1
            n_fans_built += 1

        # Skip rows where we have no forecast (e.g. beginning of test set)
        if not fans:
            continue
        if quote_usd <= 0:
            continue

        # Compute the optimizer's decision threshold and action.
        if mc_config is None:
            # Analytic decision: horizon-weighted ceiling rule (ceiling.py).
            try:
                result = lock_or_wait(
                    forecasts=fans,
                    vessel_class=cls,
                    contract_term_days=contract_term_days,
                    today_quote_usd_per_day=quote_usd,
                    risk_tolerance=risk_tolerance,
                    basis=basis,
                )
            except ValueError:
                continue
            ceiling: float = result.ceiling_usd_per_day
            action_opt: Literal["LOCK", "WAIT"] = result.action  # type: ignore[assignment]
        else:
            # MC-informed decision: simulate spot paths over the contract term
            # under mc_config dynamics, then use the risk-blended simulated
            # spot cost as the LOCK threshold.
            dist = estimate_savings_distribution(
                forecasts=fans,
                vessel_class=cls,
                contract_term_days=contract_term_days,
                today_quote_usd_per_day=quote_usd,
                config=mc_config,
                rng=mc_rng,
            )
            spot_p50 = quote_usd + dist.expected_p50_savings
            spot_p90 = quote_usd + dist.best_case_p90_savings
            ceiling = (1.0 - risk_tolerance) * spot_p50 + risk_tolerance * spot_p90
            action_opt = "LOCK" if quote_usd <= ceiling else "WAIT"

        # Oracle: lock iff it was actually cheaper than realised spot
        action_oracle: Literal["LOCK", "WAIT"] = (
            "LOCK" if quote_usd <= realised_spot else "WAIT"
        )

        # Savings vs always_spot ($/day):
        #   If LOCK: savings = realised_spot - quote  (positive = locking saved money)
        #   If WAIT/SPOT: savings = 0 by definition (you just paid whatever spot was)
        savings_always_lock = realised_spot - quote_usd
        savings_optimizer = savings_always_lock if action_opt == "LOCK" else 0.0
        savings_oracle = savings_always_lock if action_oracle == "LOCK" else 0.0

        rows.append(BacktestRow(
            date=record["date"],
            vessel_class=cls_str,
            spot_usd=spot_usd,
            quote_usd=quote_usd,
            realised_spot=realised_spot,
            ceiling_usd=ceiling,
            action_optimizer=action_opt,
            action_oracle=action_oracle,
            savings_always_lock=savings_always_lock,
            savings_optimizer=savings_optimizer,
            savings_oracle=savings_oracle,
        ))

    if n_repaired:
        LOGGER.warning(
            f"repaired {n_repaired} of {n_fans_built} forecast fan(s) "
            f"({100 * n_repaired / n_fans_built:.1f}%) with crossed quantiles instead "
            f"of dropping them; see ml.quantiles for why dropping biases the backtest"
        )
    return rows


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def _summarise_strategy(
    rows: list[BacktestRow],
    strategy: Strategy,
    contract_term_days: int,
    vessel_class: str = "ALL",
) -> BacktestSummary:
    """Compute summary stats for one strategy across a list of backtest rows."""
    if vessel_class != "ALL":
        rows = [r for r in rows if r.vessel_class == vessel_class]

    if not rows:
        return BacktestSummary(
            strategy=strategy, vessel_class=vessel_class,
            contract_term_days=contract_term_days, n_decisions=0,
            savings_mean=0.0, savings_p10=0.0, savings_p50=0.0, savings_p90=0.0,
            hit_rate=None, lock_rate=0.0,
        )

    savings_col = {
        "always_spot":   [0.0] * len(rows),
        "always_lock":   [r.savings_always_lock for r in rows],
        "calendar_lock": [r.savings_always_lock if r.date.day <= 7 else 0.0  # type: ignore[union-attr]
                          for r in rows],
        "optimizer":     [r.savings_optimizer for r in rows],
        "oracle":        [r.savings_oracle for r in rows],
    }[strategy]

    lock_flags = {
        "always_spot":   [False] * len(rows),
        "always_lock":   [True] * len(rows),
        "calendar_lock": [r.date.day <= 7 for r in rows],  # type: ignore[union-attr]
        "optimizer":     [r.action_optimizer == "LOCK" for r in rows],
        "oracle":        [r.action_oracle == "LOCK" for r in rows],
    }[strategy]

    n = len(savings_col)
    sorted_savings = sorted(savings_col)
    p10_idx = max(0, int(0.10 * n) - 1)
    p50_idx = max(0, int(0.50 * n) - 1)
    p90_idx = min(n - 1, int(0.90 * n))

    locked_rows = [s for s, locked in zip(savings_col, lock_flags) if locked]
    correct_locks = sum(
        1 for r, locked in zip(rows, lock_flags)
        if locked and r.action_oracle == "LOCK"
    )
    hit_rate = (correct_locks / len(locked_rows)) if locked_rows else None

    return BacktestSummary(
        strategy=strategy,
        vessel_class=vessel_class,
        contract_term_days=contract_term_days,
        n_decisions=n,
        savings_mean=sum(savings_col) / n,
        savings_p10=sorted_savings[p10_idx],
        savings_p50=sorted_savings[p50_idx],
        savings_p90=sorted_savings[p90_idx],
        hit_rate=hit_rate,
        lock_rate=sum(lock_flags) / n,
    )


def summarise(
    rows: list[BacktestRow],
    contract_term_days: int,
    classes: Sequence[str] = ("ALL", "Capesize", "Panamax", "Supramax", "Handysize"),
) -> list[BacktestSummary]:
    """Compute summary stats for all strategies and vessel classes.

    Also fills in decision_value and regret relative to always_lock and oracle.
    """
    strategies: list[Strategy] = ["always_spot", "always_lock", "calendar_lock",
                                   "optimizer", "oracle"]
    all_summaries: list[BacktestSummary] = []

    for cls in classes:
        sums: dict[Strategy, BacktestSummary] = {}
        for strat in strategies:
            s = _summarise_strategy(rows, strat, contract_term_days, cls)
            sums[strat] = s
            all_summaries.append(s)

        # Fill in relative metrics
        always_lock_mean = sums["always_lock"].savings_mean
        oracle_mean = sums["oracle"].savings_mean
        for s in sums.values():
            s.decision_value = s.savings_mean - always_lock_mean
            s.regret = oracle_mean - s.savings_mean

    return all_summaries


def to_dataframe(summaries: list[BacktestSummary]) -> pl.DataFrame:
    """Convert summary list to a Polars DataFrame for display or saving."""
    return pl.DataFrame([
        {
            "strategy": s.strategy,
            "vessel_class": s.vessel_class,
            "contract_term_days": s.contract_term_days,
            "n_decisions": s.n_decisions,
            "savings_mean": round(s.savings_mean, 2),
            "savings_p10": round(s.savings_p10, 2),
            "savings_p50": round(s.savings_p50, 2),
            "savings_p90": round(s.savings_p90, 2),
            "hit_rate": round(s.hit_rate, 3) if s.hit_rate is not None else None,
            "lock_rate": round(s.lock_rate, 3),
            "decision_value": round(s.decision_value, 2) if s.decision_value is not None else None,
            "regret": round(s.regret, 2) if s.regret is not None else None,
        }
        for s in summaries
    ])
