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

class XGBModel:
    def fit(self, train: pl.DataFrame, horizons: list[int]) -> None:
        self.horizons = horizons
        self.boosters = {}
        self.names = None
        self.feature_types = None
        
        for h in horizons:
            valid_train = train.drop_nulls([f"y_step_h{h}"])
            core_n = max(int(valid_train.height * 0.85), valid_train.height - 60)
            tr = valid_train.sort("date").head(core_n)
            es = valid_train.sort("date").tail(valid_train.height - core_n)
            names, x_tr = make_matrix(tr, h)
            _, x_es = make_matrix(es, h)
            y_tr = (tr[f"y_step_h{h}"] - tr["log_value"]).to_numpy()
            y_es = (es[f"y_step_h{h}"] - es["log_value"]).to_numpy()

            feature_types = ["c" if n == "target_class" else "q" for n in names]
            self.names = names
            self.feature_types = feature_types

            dtrain = xgb.DMatrix(x_tr, label=y_tr, feature_names=names, feature_types=feature_types)
            dval = xgb.DMatrix(x_es, label=y_es, feature_names=names, feature_types=feature_types)

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
            
            self.boosters[h] = booster

    def predict(
        self, eval_frames: dict[str, pl.DataFrame]
    ) -> dict[str, dict[int, pl.DataFrame]]:
        out: dict[str, dict[int, pl.DataFrame]] = {name: {} for name in eval_frames}
        
        for h in self.horizons:
            booster = self.boosters[h]
            for split_name, frame in eval_frames.items():
                if frame.is_empty():
                    schema = {
                        "date": pl.Date,
                        "target_class": pl.String,
                        "h": pl.Int64,
                        **{f"p_{q}": pl.Float64 for q in QUANTILES},
                    }
                    out[split_name][h] = pl.DataFrame(schema=schema)
                    continue

                _, x = make_matrix(frame, h)
                dx = xgb.DMatrix(x, feature_names=self.names, feature_types=self.feature_types)
                
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
                    
                out[split_name][h] = frame.select(*exprs)

        return out


def predict(
    train: pl.DataFrame, eval_frames: dict[str, pl.DataFrame], h: int
) -> dict[str, pl.DataFrame]:
    """Train and predict for a single horizon using XGBModel."""
    model = XGBModel()
    model.fit(train, [h])
    res = model.predict(eval_frames)
    return {split_name: h_preds[h] for split_name, h_preds in res.items()}

