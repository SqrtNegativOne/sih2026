"""HISTORICAL_MODEL_REPLAY -- P5 requirement 10/11. Retrospective backtest
simulation over real historical data, kept conceptually and visually
separate from the Live Decision Ledger (``opt.ledger``).

**Reuses ``opt.backtest``/``opt.calibration``/``ml.frozen_test`` -- no
second backtest system.** This module runs exactly the two-phase recipe
``run_optimizer_backtest.py`` already established and this repo already
treats as its own "final report" methodology: calibrate (theta, sigma_long,
risk_tolerance) via PSO on ``valid`` (test untouched), then report on
``test`` -- read exactly once, through
``ml.frozen_test.allow_test_set_access`` -- using the calibrated,
MC-informed decision. Same functions (``opt.calibration.calibrate_pso``,
``opt.backtest.simulate``/``summarise``), same PSO settings, same phases;
this module only adds process-lifetime caching (mirrors ``tonnage.field``'s
pattern, P3) so a backend doesn't re-run a multi-minute PSO search on every
request.

**Honest disclosure this module makes explicit, not silent:** the
MC-informed decision mode this recipe uses (spot-cost distribution
simulated under calibrated ``theta``/``sigma_long`` dynamics) is the closest
*existing* option-aware backtest ``opt.backtest`` offers -- it is still not
a bit-identical replay of the live production path, which fuses
``opt.stopping``'s LSMC exercise-boundary solve directly (see
``opt.stopping``'s own module docstring: "fused into the production
decision, not merely additive"). Extending ``opt.backtest`` to call the LSMC
path is real, separate work this task does not do -- recorded here as a
disclosed limitation, not hidden behind the "HISTORICAL_MODEL_REPLAY" label.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

import polars as pl

from ml.frozen_test import allow_test_set_access, load_frozen_test
from ml.inference import FreightPredictor
from opt.backtest import BacktestSummary, simulate, summarise
from opt.calibration import CalibrationBounds, CalibrationResult, calibrate_pso
from opt.monte_carlo import MonteCarloConfig

__all__ = [
    "REPLAY_LABEL",
    "ReplaySnapshot",
    "clear_replay_cache",
    "get_replay_snapshot",
]

#: The exact, unmistakable label the API/UI must show next to every replay
#: figure -- requirement 11, verbatim intent.
REPLAY_LABEL: Final[str] = "retrospective model simulation — not decisions this system actually made"

CONTRACT_TERM_DAYS: Final[int] = 30
BROKER_SPREAD: Final[float] = 0.03

# Identical to run_optimizer_backtest.py's own module-level constants -- the
# established, reviewed recipe, not a new one invented for this cache wrapper.
N_PARTICLES: Final[int] = 10
N_ITERATIONS: Final[int] = 12
MC_NUM_SIMULATIONS_CALIBRATE: Final[int] = 80
MC_NUM_SIMULATIONS_REPORT: Final[int] = 300
PSO_SEED: Final[int] = 42
MC_SEED: Final[int] = 2025


def _predict(split: pl.DataFrame) -> pl.DataFrame:
    """h=7 and h=30 XGBoost predictions, combined -- the same real function
    run_optimizer_backtest.py's own _predict() calls (FreightPredictor),
    restated here rather than imported from a top-level script."""
    p7 = FreightPredictor(h=7).predict(split)
    p30 = FreightPredictor(h=30).predict(split)
    return pl.concat([p7, p30])


@dataclass(frozen=True)
class ReplaySnapshot:
    label: str
    computed_at: datetime
    compute_seconds: float
    contract_term_days: int
    broker_spread: float
    calibration: CalibrationResult
    """Real PSO result from the valid-only calibration phase -- the
    parameters actually used for the test-split report below, disclosed so
    a caller can see what was tuned (and confirm it was tuned on valid, not
    test)."""
    n_test_rows: int
    summaries: tuple[BacktestSummary, ...]
    stale: bool = False


_lock = threading.Lock()
_snapshot: ReplaySnapshot | None = None


def _compute() -> ReplaySnapshot:
    from ml.baselines import load_split

    t0 = time.perf_counter()

    # Phase 1 -- CALIBRATE on valid. Test is not touched in this phase.
    valid = load_split("valid")
    valid_preds = _predict(valid)
    calibration = calibrate_pso(
        eval_split=valid, ml_predictions=valid_preds, contract_term_days=CONTRACT_TERM_DAYS,
        broker_spread=BROKER_SPREAD, bounds=CalibrationBounds(), n_particles=N_PARTICLES,
        n_iterations=N_ITERATIONS, seed=PSO_SEED, mc_num_simulations=MC_NUM_SIMULATIONS_CALIBRATE, mc_seed=MC_SEED,
    )

    # Phase 2 -- REPORT on test, exactly once, using the calibrated params.
    with allow_test_set_access("P5 HISTORICAL_MODEL_REPLAY: real backtest report for the ledger/replay screen"):
        test = load_frozen_test()
        test_preds = _predict(test)
        mc_config = MonteCarloConfig(
            theta=calibration.best_theta, sigma_long=calibration.best_sigma_long,
            num_simulations=MC_NUM_SIMULATIONS_REPORT,
        )
        rows = simulate(
            eval_split=test, ml_predictions=test_preds, contract_term_days=CONTRACT_TERM_DAYS,
            broker_spread=BROKER_SPREAD, risk_tolerance=calibration.best_risk_tolerance, basis=None, mc_config=mc_config,
        )
        summaries = summarise(rows, contract_term_days=CONTRACT_TERM_DAYS)
        n_rows = test.height

    elapsed = time.perf_counter() - t0
    return ReplaySnapshot(
        label=REPLAY_LABEL, computed_at=datetime.now(UTC), compute_seconds=elapsed,
        contract_term_days=CONTRACT_TERM_DAYS, broker_spread=BROKER_SPREAD, calibration=calibration,
        n_test_rows=n_rows, summaries=tuple(summaries), stale=False,
    )


def get_replay_snapshot(force_refresh: bool = False) -> ReplaySnapshot:
    """Process-lifetime cached (see tonnage.field for the identical pattern)
    -- PSO calibration plus the test-split report take real minutes; this
    runs once per process, not once per request. Falls back to the
    last-good snapshot, marked stale, on a real recompute failure rather
    than raising into a caller that already has a real (if older) answer."""
    global _snapshot
    with _lock:
        if _snapshot is not None and not force_refresh:
            return _snapshot
        try:
            _snapshot = _compute()
        except Exception:
            if _snapshot is not None:
                from dataclasses import replace

                _snapshot = replace(_snapshot, stale=True)
                return _snapshot
            raise
        return _snapshot


def clear_replay_cache() -> None:
    global _snapshot
    with _lock:
        _snapshot = None
