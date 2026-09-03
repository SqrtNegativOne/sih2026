"""SHAP-based Feature Selection using XGBoost.

Trains an XGBoost model on the dataset and extracts exact SHAP values natively
using `pred_contribs=True` to rank feature importance.

Run with `uv run python -m ml.feature_selection` from the repository root.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Final

import numpy as np
import polars as pl
import xgboost as xgb

from ml.baselines import DATA, EXCLUDE, make_matrix, HORIZONS

LOGGER: Final[logging.Logger] = logging.getLogger(__name__)


def _setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")


def main() -> None:
    _setup_logging()

    # Load training data
    train = pl.read_parquet(DATA / "samples_train.parquet")
    floats = [c for c, t in train.schema.items() if t == pl.Float64]
    train = train.with_columns([pl.col(c).fill_nan(None) for c in floats])

    out_dict = {}

    # We can evaluate feature importance per horizon, or just pick a medium one like h=30
    for h in [30]:
        LOGGER.info(f"Computing SHAP values for horizon h={h}...")

        # Drop rows with null targets
        df = train.drop_nulls(["log_value", f"y_step_h{h}"])
        if df.is_empty():
            LOGGER.warning(f"No valid data for h={h}")
            continue

        names, x_tr = make_matrix(df, h)
        y_tr = (df[f"y_step_h{h}"] - df["log_value"]).to_numpy()

        feature_types = ["c" if n == "target_class" else "q" for n in names]
        dtrain = xgb.DMatrix(x_tr, label=y_tr, feature_names=names, feature_types=feature_types)

        params = {
            "objective": "reg:squarederror",
            "learning_rate": 0.05,
            "max_depth": 5,
            "subsample": 0.8,
            "colsample_bytree": 0.8,
            "tree_method": "hist",
        }

        booster = xgb.train(params, dtrain, num_boost_round=100)

        # Calculate native SHAP values (pred_contribs=True)
        # Returns shape: (n_samples, n_features + 1) -> last column is the bias
        shap_values = booster.predict(dtrain, pred_contribs=True)

        # Mean absolute SHAP value across all training samples for each feature
        mean_abs_shap = np.abs(shap_values[:, :-1]).mean(axis=0)

        # Map to feature names and sort
        feature_importance = [
            {"feature": name, "shap_importance": float(val)}
            for name, val in zip(names, mean_abs_shap)
        ]
        feature_importance.sort(key=lambda x: x["shap_importance"], reverse=True)

        out_dict[f"h{h}"] = feature_importance

        LOGGER.info(f"Top 10 features for h={h}:")
        for f in feature_importance[:10]:
            LOGGER.info(f"  {f['feature']}: {f['shap_importance']:.4f}")

    out_path = DATA / "shap_feature_importance.json"
    with open(out_path, "w") as f:
        json.dump(out_dict, f, indent=2)

    LOGGER.info(f"Wrote SHAP importance to {out_path}")


if __name__ == "__main__":
    main()
