"""Freight chartering optimizer package.

Sub-modules
-----------
types       Dataclasses for all inputs/outputs (no logic).
voyage      Voyage cost and transit-time estimator.
ceiling     Lock/wait decision — ceiling rate calculator.
api         Unified black-box entry point.
network     Registry for ports and routes.
quote       Single-cargo front door (opt.quote.quote) -- import it directly
            (``from opt.quote import quote``), not from this package: it
            depends on ``ml.live_forecast``, which itself imports
            ``opt.types`` -- re-exporting it here would make importing
            ``opt`` at all require first fully importing ``ml``, and
            re-entering ``opt/__init__.py`` while ``ml.live_forecast`` is
            still mid-import raises ImportError (confirmed live while
            wiring this up).
"""
from __future__ import annotations

from opt.api import format_recommendation_text, run_optimizer, run_portfolio_analysis
from opt.ceiling import (
    compute_ceiling,
    lock_or_wait,
    lock_or_wait_for_cargo,
    select_vessel_class_for_cargo,
)
from opt.types import (
    BasisEntry,
    CargoParcel,
    FleetConfiguration,
    FleetMixFrontier,
    ForecastFan,
    LockWaitResult,
    OptimizerInputs,
    OptimizerRecommendation,
    PortCheck,
    PortfolioMix,
    QuoteResult,
    RateHorizon,
    RiskAlert,
    RiskAssessment,
    StoppingResult,
    Vessel,
    VesselClass,
)

__all__ = [  # noqa: RUF022 -- grouped by sub-module on purpose, not alphabetical
    # api
    "run_optimizer",
    "run_portfolio_analysis",
    "format_recommendation_text",
    "OptimizerRecommendation",
    # quote's result types (the function itself: import from opt.quote directly)
    "QuoteResult",
    "RateHorizon",
    "PortCheck",
    # ceiling
    "compute_ceiling",
    "lock_or_wait",
    "lock_or_wait_for_cargo",
    "select_vessel_class_for_cargo",
    # types
    "BasisEntry",
    "CargoParcel",
    "FleetConfiguration",
    "FleetMixFrontier",
    "ForecastFan",
    "LockWaitResult",
    "OptimizerInputs",
    "PortfolioMix",
    "RiskAlert",
    "RiskAssessment",
    "StoppingResult",
    "Vessel",
    "VesselClass",
]
