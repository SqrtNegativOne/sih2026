"""Pooled XGBoost quantile model over the sample splits (compatibility shim)."""
from __future__ import annotations

from ml.models.xgb import XGB_PARAMS, XGBModel, predict

__all__ = ["XGB_PARAMS", "XGBModel", "predict"]
