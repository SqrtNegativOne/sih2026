"""Freight chartering optimizer package.

Sub-modules
-----------
types       Dataclasses for all inputs/outputs (no logic).
voyage      Voyage cost and transit-time estimator.
ceiling     Lock/wait decision — ceiling rate calculator.
api         Unified black-box entry point.
static_ref  Real port specs, distances, and bunker prices.
"""
from __future__ import annotations

from opt.api import OptimizerRecommendation, run_optimizer
from opt.ceiling import compute_ceiling, lock_or_wait
from opt.static_ref import (
    BLENDED_BUNKER_USD_PER_TONNE,
    BUNKER_PRICES,
    PORT_DISTANCES,
    PORT_SPECS,
)
from opt.types import (
    BasisEntry,
    CargoParcel,
    ForecastFan,
    LockWaitResult,
    OptimizerInputs,
    PortSpec,
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
    "PortSpec",
    "Vessel",
    "VesselClass",
    # static reference
    "PORT_SPECS",
    "PORT_DISTANCES",
    "BUNKER_PRICES",
    "BLENDED_BUNKER_USD_PER_TONNE",
]
