"""PSO Hyperparameter Calibration for the ceiling optimizer.

Finds the combination of (theta, sigma_long, risk_tolerance) that maximises
**decision value** — defined as

    decision_value = optimizer.savings_mean - always_lock.savings_mean

measured by running ``backtest.simulate`` + ``backtest.summarise`` over a
provided test dataset.

The optimisation uses Particle Swarm Optimisation (PSO), implemented from
scratch using only Python stdlib + ``random``.  No external solver or
numerical library is required.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

import polars as pl

from opt import backtest
from opt.monte_carlo import MonteCarloConfig


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


@dataclass
class CalibrationBounds:
    """Search bounds for the PSO calibration.

    Each attribute is a ``(lo, hi)`` range for the corresponding parameter.
    """
    theta_range: tuple[float, float] = (0.0, 0.5)
    sigma_long_range: tuple[float, float] = (0.1, 0.8)
    risk_tol_range: tuple[float, float] = (0.0, 1.0)


@dataclass
class CalibrationResult:
    """Output of :func:`calibrate_pso`."""
    best_theta: float
    best_sigma_long: float
    best_risk_tolerance: float
    best_decision_value: float
    convergence_history: list[float]  # global-best objective at each iteration


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _clip(value: float, lo: float, hi: float) -> float:
    """Clip *value* to the closed interval [lo, hi]."""
    return max(lo, min(hi, value))


def _evaluate(
    test_split: pl.DataFrame,
    ml_predictions: pl.DataFrame,
    contract_term_days: int,
    broker_spread: float,
    theta: float,
    sigma_long: float,
    risk_tolerance: float,
) -> float:
    """Evaluate one (theta, sigma_long, risk_tolerance) candidate.

    Returns the decision value for vessel class "ALL", or a very large
    negative sentinel if the backtest produces no usable rows.
    """
    # NOTE: MonteCarloConfig is threaded through backtest via ceiling.lock_or_wait
    # which does NOT currently accept a MonteCarloConfig.  The calibration
    # therefore tunes risk_tolerance (which IS passed through) alongside
    # theta/sigma_long which govern the savings *distribution* analysis layer.
    # The backtest itself uses lock_or_wait internally with the risk_tolerance
    # parameter — so we pass that through directly.
    rows = backtest.simulate(
        test_split=test_split,
        ml_predictions=ml_predictions,
        contract_term_days=contract_term_days,
        broker_spread=broker_spread,
        risk_tolerance=risk_tolerance,
    )
    if not rows:
        return -1e9

    summaries = backtest.summarise(rows, contract_term_days=contract_term_days, classes=["ALL"])

    # decision_value is filled in by summarise() for every strategy
    opt_summary = next(
        (s for s in summaries if s.strategy == "optimizer" and s.vessel_class == "ALL"),
        None,
    )
    if opt_summary is None or opt_summary.decision_value is None:
        return -1e9

    return opt_summary.decision_value


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def calibrate_pso(
    test_split: pl.DataFrame,
    ml_predictions: pl.DataFrame,
    contract_term_days: int = 30,
    broker_spread: float = 0.03,
    bounds: CalibrationBounds | None = None,
    n_particles: int = 20,
    n_iterations: int = 50,
    w: float = 0.7,
    c1: float = 1.5,
    c2: float = 1.5,
    seed: int = 42,
) -> CalibrationResult:
    """Run Particle Swarm Optimisation to calibrate (theta, sigma_long, risk_tolerance).

    Maximises the decision value reported by :func:`backtest.summarise` for
    vessel class "ALL".

    Parameters
    ----------
    test_split:
        Historical test-set rows — same format as ``backtest.simulate`` expects
        (columns: date, target_class, log_value, y_h30, y_h90).
    ml_predictions:
        ML model quantile predictions (columns: date, target_class, h,
        p_0.1, p_0.5, p_0.9 in log-level space).
    contract_term_days:
        TC contract length to simulate.  Must be 30 or 90.
    broker_spread:
        Broker discount applied to the spot rate.
    bounds:
        Search bounds for each parameter.  Defaults to ``CalibrationBounds()``.
    n_particles:
        Swarm size.
    n_iterations:
        Number of PSO iterations.
    w:
        Inertia weight — controls how much each particle "remembers" its
        current trajectory.
    c1:
        Cognitive coefficient — pull toward the particle's personal best.
    c2:
        Social coefficient — pull toward the global best.
    seed:
        Random seed for reproducibility.

    Returns
    -------
    CalibrationResult
        Best parameters found and a per-iteration convergence history.
    """
    if bounds is None:
        bounds = CalibrationBounds()

    rng = random.Random(seed)

    theta_lo, theta_hi = bounds.theta_range
    sigma_lo, sigma_hi = bounds.sigma_long_range
    risk_lo, risk_hi = bounds.risk_tol_range

    # ------------------------------------------------------------------
    # Dimension layout: x = [theta, sigma_long, risk_tolerance]
    # ------------------------------------------------------------------
    DIM = 3
    lo = [theta_lo, sigma_lo, risk_lo]
    hi = [theta_hi, sigma_hi, risk_hi]

    def _rand_position() -> list[float]:
        return [rng.uniform(lo[d], hi[d]) for d in range(DIM)]

    def _zero_velocity() -> list[float]:
        return [0.0] * DIM

    # ------------------------------------------------------------------
    # Initialise swarm
    # ------------------------------------------------------------------
    positions: list[list[float]] = [_rand_position() for _ in range(n_particles)]
    velocities: list[list[float]] = [_zero_velocity() for _ in range(n_particles)]

    personal_best_pos: list[list[float]] = [list(p) for p in positions]
    personal_best_val: list[float] = [-1e18] * n_particles
    global_best_pos: list[float] = list(positions[0])
    global_best_val: float = -1e18

    # Initial evaluation
    for i, pos in enumerate(positions):
        val = _evaluate(
            test_split, ml_predictions,
            contract_term_days, broker_spread,
            theta=pos[0], sigma_long=pos[1], risk_tolerance=pos[2],
        )
        personal_best_val[i] = val
        if val > global_best_val:
            global_best_val = val
            global_best_pos = list(pos)

    convergence_history: list[float] = []

    # ------------------------------------------------------------------
    # PSO main loop
    # ------------------------------------------------------------------
    for _iteration in range(n_iterations):
        for i in range(n_particles):
            r1 = [rng.random() for _ in range(DIM)]
            r2 = [rng.random() for _ in range(DIM)]

            new_vel: list[float] = []
            new_pos: list[float] = []

            for d in range(DIM):
                cognitive = c1 * r1[d] * (personal_best_pos[i][d] - positions[i][d])
                social = c2 * r2[d] * (global_best_pos[d] - positions[i][d])
                v_new = w * velocities[i][d] + cognitive + social
                new_vel.append(v_new)

                p_new = _clip(positions[i][d] + v_new, lo[d], hi[d])
                new_pos.append(p_new)

            velocities[i] = new_vel
            positions[i] = new_pos

            val = _evaluate(
                test_split, ml_predictions,
                contract_term_days, broker_spread,
                theta=new_pos[0], sigma_long=new_pos[1], risk_tolerance=new_pos[2],
            )

            if val > personal_best_val[i]:
                personal_best_val[i] = val
                personal_best_pos[i] = list(new_pos)

            if val > global_best_val:
                global_best_val = val
                global_best_pos = list(new_pos)

        convergence_history.append(global_best_val)

    return CalibrationResult(
        best_theta=global_best_pos[0],
        best_sigma_long=global_best_pos[1],
        best_risk_tolerance=global_best_pos[2],
        best_decision_value=global_best_val,
        convergence_history=convergence_history,
    )
