"""Tonnage Field orchestration + caching -- P3 requirement 8's "cache the
reconstruction" -- ties `stockflow`/`supplycurve`/`identification`/`forward`
into the one snapshot the API and frontend actually consume.

Two independent caches, process-lifetime, following the exact pattern
`tonnage.basins.load_port_index` already uses (`@functools.lru_cache` plus an
explicit `clear_cache()`), not a new invented TTL mechanism:

* `get_snapshot()` -- reconstruction + tightness + identification gate + sign
  diagnoses. Real measured cost ~3-9s (machine/cache-state dependent; the P3
  prompt's own estimate is ~7s). Cheap to keep warm.
* `get_ablation_snapshot()` -- the real A/B XGBoost ablation. Real measured
  cost ~45s (trains 6 real models). Deliberately NOT computed at import time
  or bundled into `get_snapshot()`: the validation endpoint is the only real
  caller, it is not needed for the primary `/tonnage-field` view, and forcing
  every process start to pay 45s before serving anything would be a worse
  trade than a one-time slow first call to `/tonnage-field/validation`.

Reconstruction failure: `get_snapshot()`/`get_ablation_snapshot()` return the
last good snapshot with `stale=True` if one exists rather than raising -- "no
silent zeros" per the P3 edge-case requirement means never fabricating a
result, not that a transient failure must take the endpoint down when a real,
previously-computed answer already exists. If no snapshot has ever succeeded,
the exception propagates -- there is nothing honest to fall back to.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

import polars as pl

from ml.ablation_m1 import AblationReport, run_ablation
from opt.types import VesselClass
from tonnage.identification import (
    IdentificationGateResult,
    IVVerdict,
    KalmanVerdict,
    SignDiagnosis,
    diagnose_all_signs,
    evaluate_identification_gate,
    evaluate_iv_verdict,
    evaluate_kalman_verdict,
)
from tonnage.stockflow import StockflowResult, reconstruct
from tonnage.supplycurve import (
    SupplyCurveFit,
    build_basin_tightness_index,
    build_tightness_index,
    fit_all,
)

LOGGER: Final[logging.Logger] = logging.getLogger(__name__)

__all__ = [
    "AblationSnapshot",
    "TonnageFieldSnapshot",
    "clear_ablation_cache",
    "clear_snapshot_cache",
    "get_ablation_snapshot",
    "get_snapshot",
]


@dataclass(frozen=True)
class TonnageFieldSnapshot:
    result: StockflowResult
    tightness: pl.DataFrame
    basin_tightness: pl.DataFrame
    gate: IdentificationGateResult
    iv_verdict: IVVerdict
    kalman_verdict: KalmanVerdict
    fits: dict[VesselClass, SupplyCurveFit]
    sign_diagnoses: dict[VesselClass, SignDiagnosis]
    computed_at: datetime
    compute_seconds: float
    stale: bool = False


_snapshot_lock = threading.Lock()
_snapshot: TonnageFieldSnapshot | None = None


def _compute_snapshot() -> TonnageFieldSnapshot:
    t0 = time.perf_counter()
    result = reconstruct()
    tightness = build_tightness_index(result)
    basin_tightness = build_basin_tightness_index(result)
    gate = evaluate_identification_gate(result)
    iv = evaluate_iv_verdict()
    kalman = evaluate_kalman_verdict()
    fits = fit_all(tightness)
    diagnoses = diagnose_all_signs(fits, tightness, basin_tightness)
    elapsed = time.perf_counter() - t0
    return TonnageFieldSnapshot(
        result=result, tightness=tightness, basin_tightness=basin_tightness,
        gate=gate, iv_verdict=iv, kalman_verdict=kalman, fits=fits, sign_diagnoses=diagnoses,
        computed_at=datetime.now(UTC), compute_seconds=elapsed, stale=False,
    )


def get_snapshot(force_refresh: bool = False) -> TonnageFieldSnapshot:
    """Process-lifetime cached snapshot. Thread-safe (FastAPI can serve
    concurrent requests on a threadpool); recomputes only on the first call or
    when `force_refresh=True`."""
    global _snapshot
    with _snapshot_lock:
        if _snapshot is not None and not force_refresh:
            return _snapshot
        try:
            _snapshot = _compute_snapshot()
        except Exception:
            if _snapshot is not None:
                LOGGER.exception("Tonnage Field reconstruction failed; serving last-good snapshot as stale.")
                _snapshot = _dataclass_replace_stale(_snapshot)
                return _snapshot
            raise
        return _snapshot


def _dataclass_replace_stale(snapshot: TonnageFieldSnapshot) -> TonnageFieldSnapshot:
    from dataclasses import replace

    return replace(snapshot, stale=True)


def clear_snapshot_cache() -> None:
    global _snapshot
    with _snapshot_lock:
        _snapshot = None


@dataclass(frozen=True)
class AblationSnapshot:
    report: AblationReport
    computed_at: datetime
    compute_seconds: float
    stale: bool = False


_ablation_lock = threading.Lock()
_ablation_snapshot: AblationSnapshot | None = None


def get_ablation_snapshot(force_refresh: bool = False) -> AblationSnapshot:
    global _ablation_snapshot
    with _ablation_lock:
        if _ablation_snapshot is not None and not force_refresh:
            return _ablation_snapshot
        t0 = time.perf_counter()
        try:
            report = run_ablation()
        except Exception:
            if _ablation_snapshot is not None:
                LOGGER.exception("M1 ablation re-run failed; serving last-good ablation snapshot as stale.")
                from dataclasses import replace

                _ablation_snapshot = replace(_ablation_snapshot, stale=True)
                return _ablation_snapshot
            raise
        elapsed = time.perf_counter() - t0
        _ablation_snapshot = AblationSnapshot(
            report=report, computed_at=datetime.now(UTC), compute_seconds=elapsed, stale=False,
        )
        return _ablation_snapshot


def clear_ablation_cache() -> None:
    global _ablation_snapshot
    with _ablation_lock:
        _ablation_snapshot = None
