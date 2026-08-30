"""PSO Hyperparameter Calibration for the optimizer.

Finds the combination of (theta, sigma_long, risk_tolerance) that maximises
**decision value** — defined as

    decision_value = optimizer.savings_mean - always_lock.savings_mean

measured by running ``backtest.simulate`` + ``backtest.summarise`` over
whichever split is passed as ``eval_split``.

That split must be ``valid``, never the frozen ``test`` split. This function
searches for the parameters that make the objective look best on ``eval_split``;
handing it ``test`` and then reporting the resulting decision value as "how well
the optimizer performs" is the exact in-sample leak
``ml.frozen_test.load_frozen_test`` exists to make hard to do by accident. See
``run_optimizer_backtest.py`` for the intended two-phase shape: calibrate here on
``valid``, then run ``backtest.simulate`` once more on ``test`` with the winning
parameters for the actual report.

All three parameters are threaded into the backtest objective:

*   ``risk_tolerance`` blends the P50/P90 spot cost inside the LOCK rule
    (F-42 fix: this said P50/P10 in three places across this file,
    ``opt.backtest``, and ``opt.ceiling`` -- the actual blend has always
    used P90, matching the correct "risk-averse weights the pessimistic-
    about-a-rate-rise scenario" reasoning documented in ``opt.ceiling``'s
    own module docstring).
*   ``theta`` / ``sigma_long`` parameterise the Monte Carlo spot-path
    dynamics (``MonteCarloConfig``) that produce the simulated P50/P90 when
    the backtest runs in MC-informed decision mode
    (``backtest.simulate(..., mc_config=...)``).

Each candidate is evaluated with a freshly seeded RNG so the objective is a
deterministic function of the parameters (PSO requires this for stable
personal/global best tracking).

The optimisation uses Particle Swarm Optimisation (PSO), implemented from
scratch using only Python stdlib + ``random``.  No external solver or
numerical library is required.
"""
from __future__ import annotations

import random
from dataclasses import dataclass

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
    eval_split: pl.DataFrame,
    ml_predictions: pl.DataFrame,
    contract_term_days: int,
    broker_spread: float,
    theta: float,
    sigma_long: float,
    risk_tolerance: float,
    mc_num_simulations: int = 300,
    mc_seed: int = 2025,
) -> float:
    """Evaluate one (theta, sigma_long, risk_tolerance) candidate.

    Builds a ``MonteCarloConfig`` from (theta, sigma_long) and runs the
    backtest in MC-informed decision mode, so every searched parameter
    influences the LOCK/WAIT decisions and hence the objective.

    Returns the decision value for vessel class "ALL", or a very large
    negative sentinel if the backtest produces no usable rows.
    """
    cfg = MonteCarloConfig(
        theta=theta,
        sigma_long=sigma_long,
        num_simulations=mc_num_simulations,
    )
    rows = backtest.simulate(
        eval_split=eval_split,
        ml_predictions=ml_predictions,
        contract_term_days=contract_term_days,
        broker_spread=broker_spread,
        risk_tolerance=risk_tolerance,
        mc_config=cfg,
        mc_rng=random.Random(mc_seed),
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
    eval_split: pl.DataFrame,
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
    mc_num_simulations: int = 300,
    mc_seed: int = 2025,
) -> CalibrationResult:
    """Run Particle Swarm Optimisation to calibrate (theta, sigma_long, risk_tolerance).

    Maximises the decision value reported by :func:`backtest.summarise` for
    vessel class "ALL".  The backtest runs in MC-informed decision mode, so
    all three parameters influence the objective.

    Parameters
    ----------
    eval_split:
        Historical rows to tune against — same format as ``backtest.simulate``
        expects (columns: date, target_class, log_value, y_step_h30, y_step_h90).
        This MUST be the `valid` split, never the frozen `test` split: PSO
        searches for the parameters that maximise decision value on whatever is
        passed here, so calibrating and then reporting performance on the same
        rows is exactly the in-sample leak this module exists to prevent. Load
        `test` only in run_optimizer_backtest.py, for the final report, through
        ml.frozen_test.load_frozen_test().
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
        Random seed for the PSO swarm itself (initialisation + velocity noise).
    mc_num_simulations:
        Monte Carlo paths per backtest row during evaluation.  Runtime scales
        with n_particles × n_iterations × rows × simulations × contract days,
        so keep this modest for large test sets.
    mc_seed:
        Seed for the per-evaluation Monte Carlo RNG; keeps the objective a
        deterministic function of the candidate parameters.

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
            eval_split, ml_predictions,
            contract_term_days, broker_spread,
            theta=pos[0], sigma_long=pos[1], risk_tolerance=pos[2],
            mc_num_simulations=mc_num_simulations,
            mc_seed=mc_seed,
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
                eval_split, ml_predictions,
                contract_term_days, broker_spread,
                theta=new_pos[0], sigma_long=new_pos[1], risk_tolerance=new_pos[2],
                mc_num_simulations=mc_num_simulations,
                mc_seed=mc_seed,
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
