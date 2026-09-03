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

import hashlib
import json
import logging
import threading
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
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
    "snapshot_cache_path",
    "snapshot_key",
    "write_snapshot_to_disk",
]

LOGGER: Final[logging.Logger] = logging.getLogger(__name__)

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


# ---------------------------------------------------------------------------
# Disk persistence -- turning a 22-minute request cost into a build cost
# ---------------------------------------------------------------------------
#
# The cache above is process-lifetime only, and that is a demo-killer rather
# than a performance note: this computation takes about twenty-two minutes,
# every backend restart throws it away, and the launchers run uvicorn with
# ``--reload``, so saving any Python file discards it too. The first person to
# open the Replay screen pays the full twenty-two minutes -- and at a
# competition, that person is a judge.
#
# The numbers do not change. They are simply computed once, at build time,
# instead of once per process. That is faithful to CLAUDE.md's network policy
# in spirit: a build-time artefact under ``src/data/``, consumed offline, with
# the consumer degrading to "compute it now" rather than failing when it is
# absent.
#
# The key is the whole safety argument
# ------------------------------------
# A cached snapshot served after its inputs have changed would be a
# fabricated number of the worst kind -- one that used to be true. The key
# therefore covers everything that can change the result:
#
#   * every constant that defines the run (seeds, particle and iteration
#     counts, simulation counts, contract term, broker spread);
#   * the trained model files, by modification time (``current_model_version``);
#   * the market data vintage (``current_data_version``);
#   * the frozen test file's own size and mtime -- WITHOUT reading it, so
#     computing a cache key never counts as touching the test set.
#
# Any of those moving produces a different key, and a different key means the
# stored file is ignored and the work is redone. There is no "close enough".

CACHE_DIR: Final[Path] = Path(__file__).resolve().parents[2] / "src" / "data" / "snapshots"
FROZEN_TEST_PATH: Final[Path] = (
    Path(__file__).resolve().parents[2] / "src" / "data" / "samples_test.parquet"
)

#: Bumped by hand when the SHAPE of the stored file changes, so an old file is
#: never deserialised into a newer dataclass that has different fields.
SNAPSHOT_FORMAT: Final[int] = 1


def _frozen_test_fingerprint() -> str:
    """Identify the frozen test split without opening it.

    Reading ``samples_test.parquet`` is gated by ``ml.frozen_test`` and is
    meant to happen exactly once, for the report. Computing a cache key is not
    a report, so this uses the file's size and modification time -- enough to
    notice the split changing, and not a read.
    """
    if not FROZEN_TEST_PATH.exists():
        return "missing"
    st = FROZEN_TEST_PATH.stat()
    return f"{st.st_size}:{int(st.st_mtime)}"


def snapshot_key() -> str:
    """A short hash of everything that can change the snapshot's contents."""
    from opt.ledger import current_data_version, current_model_version

    material = json.dumps(
        {
            "format": SNAPSHOT_FORMAT,
            "contract_term_days": CONTRACT_TERM_DAYS,
            "broker_spread": BROKER_SPREAD,
            "n_particles": N_PARTICLES,
            "n_iterations": N_ITERATIONS,
            "mc_calibrate": MC_NUM_SIMULATIONS_CALIBRATE,
            "mc_report": MC_NUM_SIMULATIONS_REPORT,
            "pso_seed": PSO_SEED,
            "mc_seed": MC_SEED,
            "model": current_model_version(),
            "data": current_data_version(),
            "frozen_test": _frozen_test_fingerprint(),
        },
        sort_keys=True,
    )
    return hashlib.sha256(material.encode()).hexdigest()[:16]


def snapshot_cache_path(key: str | None = None) -> Path:
    return CACHE_DIR / f"replay_{key or snapshot_key()}.json"


def _to_json(snap: ReplaySnapshot) -> dict:
    return {
        "format": SNAPSHOT_FORMAT,
        "key": snapshot_key(),
        "label": snap.label,
        "computed_at": snap.computed_at.isoformat(),
        "compute_seconds": snap.compute_seconds,
        "contract_term_days": snap.contract_term_days,
        "broker_spread": snap.broker_spread,
        "calibration": asdict(snap.calibration),
        "n_test_rows": snap.n_test_rows,
        "summaries": [asdict(s) for s in snap.summaries],
    }


def _from_json(raw: dict) -> ReplaySnapshot:
    return ReplaySnapshot(
        label=raw["label"],
        computed_at=datetime.fromisoformat(raw["computed_at"]),
        compute_seconds=raw["compute_seconds"],
        contract_term_days=raw["contract_term_days"],
        broker_spread=raw["broker_spread"],
        calibration=CalibrationResult(**raw["calibration"]),
        n_test_rows=raw["n_test_rows"],
        summaries=tuple(BacktestSummary(**s) for s in raw["summaries"]),
        # `stale` describes a failed recompute, not an old file. A snapshot
        # loaded from disk under a MATCHING key is exactly as true as one
        # computed a second ago -- the key is what guarantees that.
        stale=False,
    )


def _load_from_disk() -> ReplaySnapshot | None:
    path = snapshot_cache_path()
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if raw.get("format") != SNAPSHOT_FORMAT:
            LOGGER.info("Replay snapshot %s is an older format; ignoring it.", path.name)
            return None
        snap = _from_json(raw)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        # A corrupt cache must never take the app down, and must never be
        # partially believed. Ignore it and recompute.
        LOGGER.warning("Replay snapshot %s unreadable (%s); recomputing.", path.name, exc)
        return None
    LOGGER.info(
        "Replay snapshot loaded from disk (computed %s, %.0fs of work skipped)",
        snap.computed_at.date().isoformat(),
        snap.compute_seconds,
    )
    return snap


def write_snapshot_to_disk(snap: ReplaySnapshot) -> Path:
    """Store a computed snapshot under the current key.

    Written to a temp file and moved into place, so an interrupted write
    cannot leave a half-file that the loader would then have to distrust.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = snapshot_cache_path()
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(_to_json(snap), indent=1), encoding="utf-8")
    tmp.replace(path)
    LOGGER.info("Replay snapshot written to %s", path)
    return path


def get_replay_snapshot(force_refresh: bool = False) -> ReplaySnapshot:
    """Memory, then disk, then compute.

    PSO calibration plus the test-split report take about twenty-two real
    minutes. Process-lifetime caching alone meant every restart paid that
    again; the disk layer (see above) makes it a build cost paid once, keyed
    so a snapshot whose inputs have moved is never served.

    Falls back to the last-good snapshot, marked stale, on a real recompute
    failure rather than raising into a caller that already has a real -- if
    older -- answer.
    """
    global _snapshot
    with _lock:
        if _snapshot is not None and not force_refresh:
            return _snapshot
        if not force_refresh:
            from_disk = _load_from_disk()
            if from_disk is not None:
                _snapshot = from_disk
                return _snapshot
        try:
            _snapshot = _compute()
            try:
                write_snapshot_to_disk(_snapshot)
            except OSError as exc:
                # Failing to CACHE a real result must not discard it.
                LOGGER.warning("Could not persist the replay snapshot: %s", exc)
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
