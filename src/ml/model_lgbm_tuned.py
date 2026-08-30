import logging
from typing import Final

import lightgbm as lgb
import polars as pl

from ml.baselines import CLASS_CODES, EXCLUDE, QUANTILES

LOGGER = logging.getLogger(__name__)

LGB_PARAMS_TUNED: Final[dict[int, dict[str, object]]] = {
    7: {
        "objective": "quantile",
        "learning_rate": 0.04,
        "num_leaves": 20,
        "min_child_samples": 25,
        "feature_fraction": 0.7,
        "verbosity": -1,
    },
    30: {
        "objective": "quantile",
        "learning_rate": 0.05,
        "num_leaves": 31,
        "min_child_samples": 25,
        "feature_fraction": 0.9,
        "verbosity": -1,
    },
    90: {
        "objective": "quantile",
        "learning_rate": 0.04,
        "num_leaves": 25,
        "min_child_samples": 30,
        "feature_fraction": 0.8,
        "verbosity": -1,
    },
}


def make_matrix_tuned(df: pl.DataFrame, h: int) -> tuple[list[str], object]:
    """Feature matrix (numpy) plus feature names for LightGBM at horizon h."""
    feats = [c for c in df.columns if c not in EXCLUDE]
    mat = (
        df.with_columns(
            pl.col("target_class").replace_strict(CLASS_CODES).alias("target_class")
        )
        .select(feats)
        .to_numpy()
    )
    return feats, mat


def enhance_features(df: pl.DataFrame, h: int) -> pl.DataFrame:
    """Add advanced features based on horizon."""
    if h == 30:
        return df.with_columns(
            (pl.col("ret_30") - pl.col("ret_7")).alias("momentum_30_7"),
            (pl.col("lag_1") - pl.col("rolling_mean_30")).alias("basis_30"),
            (pl.col("rolling_mean_30") - pl.col("rolling_mean_90")).alias("trend_30_90"),
            (pl.col("congestion_origin_calls") - pl.col("congestion_dest_calls")).alias("congestion_diff"),
        )
    elif h == 90:
        return df.with_columns(
            (pl.col("ret_90") if "ret_90" in df.columns else pl.col("ret_30")).alias("momentum_long"),
            (pl.col("lag_1") - pl.col("rolling_mean_90")).alias("basis_90"),
        )
    return df


def predict(
    train: pl.DataFrame, eval_frames: dict[str, pl.DataFrame], h: int
) -> dict[str, pl.DataFrame]:
    """Tuned LightGBM quantile models."""
    
    # Feature engineering
    train_eng = enhance_features(train, h)
    eval_frames_eng = {k: enhance_features(v, h) for k, v in eval_frames.items()}
    
    core_n = max(int(train_eng.height * 0.85), train_eng.height - 60)
    tr = train_eng.sort("date").head(core_n)
    es = train_eng.sort("date").tail(train_eng.height - core_n)
    
    names, x_tr = make_matrix_tuned(tr, h)
    _, x_es = make_matrix_tuned(es, h)
    
    y_tr = (tr[f"y_step_h{h}"] - tr["log_value"]).to_numpy()
    y_es = (es[f"y_step_h{h}"] - es["log_value"]).to_numpy()
    
    cat_idx = [names.index("target_class")]
    per_quantile: dict[str, list[pl.DataFrame]] = {}
    
    median_booster: lgb.Booster | None = None
    
    for q in QUANTILES:
        params = dict(LGB_PARAMS_TUNED[h])
        params["alpha"] = q
        
        dtrain = lgb.Dataset(
            x_tr, label=y_tr, feature_name=names,
            categorical_feature=cat_idx, free_raw_data=True,
        )
        dval = lgb.Dataset(
            x_es, label=y_es, reference=dtrain,
            feature_name=names, categorical_feature=cat_idx, free_raw_data=True,
        )
        
        boost_rounds = 2000 if h == 7 else 3000
        stop_rounds = 30 if h == 7 else 100
        
        booster = lgb.train(
            params, dtrain,
            num_boost_round=boost_rounds,
            valid_sets=[dval],
            callbacks=[lgb.early_stopping(stop_rounds, verbose=False)],
        )
        if q == 0.5:
            median_booster = booster
            
        for split_name, frame in eval_frames_eng.items():
            _, x = make_matrix_tuned(frame, h)
            # Add predictions back to the original level
            p = frame["log_value"].to_numpy() + booster.predict(x)
            
            per_quantile.setdefault(split_name, []).append(
                frame.select(
                    pl.col("date"), pl.col("target_class"),
                    pl.lit(h).cast(pl.Int64).alias("h"),
                    pl.lit(p).alias(f"p_{q}"),
                )
            )
            
    if median_booster is not None:
        gains = sorted(
            zip(names, median_booster.feature_importance("gain").tolist()),
            key=lambda kv: kv[1], reverse=True,
        )[:12]
        LOGGER.info(f"h={h} top gains: {[k for k, _ in gains]}")
        
    out: dict[str, pl.DataFrame] = {}
    for split_name, frames in per_quantile.items():
        joined = frames[0]
        for fr in frames[1:]:
            joined = joined.join(fr, on=["date", "target_class", "h"], how="inner")
        out[split_name] = joined
        
    return out
