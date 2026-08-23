"""Freight chartering optimizer package.

Sub-modules
-----------
types       Dataclasses for all inputs/outputs (no logic).
voyage      Voyage cost and transit-time estimator.
ceiling     Lock/wait decision — ceiling rate calculator.
api         Unified black-box entry point.
network     Registry for ports and routes.
"""
from __future__ import annotations

from opt.api import OptimizerRecommendation, run_optimizer
from opt.ceiling import compute_ceiling, lock_or_wait
from opt.types import (
    BasisEntry,
    CargoParcel,
    ForecastFan,
    LockWaitResult,
    OptimizerInputs,
    Vessel,
    VesselClass,
)

__all__ = [
    # api
    "run_optimizer",
    "OptimizerRecommendation",
    # ceiling
    "compute_ceiling",
    "lock_or_wait",
    # types
    "BasisEntry",
    "CargoParcel",
    "ForecastFan",
    "LockWaitResult",
    "OptimizerInputs",
    "Vessel",
    "VesselClass",
]
