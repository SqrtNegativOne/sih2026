"""Pooled XGBoost quantile model over the sample splits."""
from __future__ import annotations

import logging
from typing import Final

import polars as pl
import xgboost as xgb

from ml.baselines import QUANTILES, make_matrix

LOGGER: Final[logging.Logger] = logging.getLogger(__name__)

XGB_PARAMS: Final[dict[str, object]] = {
    "objective": "reg:quantileerror",
    "quantile_alpha": list(QUANTILES),
    "learning_rate": 0.05,
    "max_depth": 5,
    "min_child_weight": 20,
    "subsample": 0.8,
    "colsample_bytree": 0.6,
    "tree_method": "hist",
}

def predict(
    train: pl.DataFrame, eval_frames: dict[str, pl.DataFrame], h: int
) -> dict[str, pl.DataFrame]:
    """Train pooled XGBoost quantile models on RETURNS with early stopping.

    Targets are log-returns (y - log_value); predictions are added back to
    the current level before evaluation. Early stopping uses a chronological
    holdout carved from the tail of train.
    """
    core_n = max(int(train.height * 0.85), train.height - 60)
    tr = train.sort("date").head(core_n)
    es = train.sort("date").tail(train.height - core_n)
    names, x_tr = make_matrix(tr, h)
    _, x_es = make_matrix(es, h)
    y_tr = (tr[f"y_step_h{h}"] - tr["log_value"]).to_numpy()
    y_es = (es[f"y_step_h{h}"] - es["log_value"]).to_numpy()

    feature_types = ["c" if n == "target_class" else "q" for n in names]

    dtrain = xgb.DMatrix(x_tr, label=y_tr, feature_names=names, feature_types=feature_types)
    dval = xgb.DMatrix(x_es, label=y_es, feature_names=names, feature_types=feature_types)

    # Use multi-quantile feature to train all quantiles in a single model
    booster = xgb.train(
        XGB_PARAMS,
        dtrain,
        num_boost_round=2000,
        evals=[(dval, "val")],
        early_stopping_rounds=50,
        verbose_eval=False,
    )

    gains = booster.get_score(importance_type="gain")
    sorted_gains = sorted(gains.items(), key=lambda kv: kv[1], reverse=True)[:12]
    LOGGER.info(f"h={h} top gains: {[k for k, _ in sorted_gains]}")

    out: dict[str, pl.DataFrame] = {}
    for split_name, frame in eval_frames.items():
        _, x = make_matrix(frame, h)
        dx = xgb.DMatrix(x, feature_names=names, feature_types=feature_types)
        
        preds = booster.predict(dx)
        log_val = frame["log_value"].to_numpy()
        
        exprs = [
            pl.col("date"), 
            pl.col("target_class"),
            pl.lit(h).cast(pl.Int64).alias("h")
        ]
        
        if len(QUANTILES) == 1:
            preds = preds.reshape(-1, 1)
            
        for i, q in enumerate(QUANTILES):
            p_q = log_val + preds[:, i]
            exprs.append(pl.lit(p_q).alias(f"p_{q}"))
            
        out[split_name] = frame.select(*exprs)
        
    return out
