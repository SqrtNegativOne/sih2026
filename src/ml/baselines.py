"""Pooled quantile baselines over the sample splits.

Benchmarks:
  rw    persistence: point = last log level, bands from train RW residual std
  ar1   pooled AR(1) on logs, same band construction
  lgbm  pooled LightGBM quantile models (one per horizon x quantile)

Writes data/baseline_metrics.csv and logs a compact summary.
Run with `uv run baselines` from the repository root.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Final

import lightgbm as lgb
import polars as pl

LOGGER: Final[logging.Logger] = logging.getLogger(__name__)

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
DATA: Final[Path] = REPO_ROOT / "src" / "data"
HORIZONS: Final[tuple[int, ...]] = (7, 30, 90)
QUANTILES: Final[tuple[float, ...]] = (0.1, 0.5, 0.9)
ZSCORES: Final[dict[float, float]] = {0.1: -1.2816, 0.5: 0.0, 0.9: 1.2816}
CLASSES: Final[tuple[str, ...]] = ("Capesize", "Panamax", "Supramax", "Handysize")
CLASS_CODES: Final[dict[str, int]] = {c: i for i, c in enumerate(CLASSES)}
EXCLUDE: Final[frozenset[str]] = frozenset(
    {"date", "target_value", "log_value",
     "y_step_h7", "y_step_h30", "y_step_h90",
     "y_mean_h7", "y_mean_h30", "y_mean_h90"}
)
LGB_PARAMS: Final[dict[str, object]] = {
    "objective": "quantile",
    "learning_rate": 0.05,
    "num_leaves": 31,
    "min_child_samples": 20,
    "verbosity": -1,
}


def _setup_logging() -> None:
    """Configure root logging once for CLI runs."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def load_split(name: str) -> pl.DataFrame:
    """Load one sample split, normalize NaN to null, drop unusable target rows."""
    df = pl.read_parquet(DATA / f"samples_{name}.parquet")
    floats = [c for c, t in df.schema.items() if t == pl.Float64]
    df = df.with_columns([pl.col(c).fill_nan(None) for c in floats])
    return df.drop_nulls(["log_value", "y_step_h7", "y_step_h30", "y_step_h90"])


def pinball(y: pl.Series, p: pl.Series, q: float) -> float:
    """Mean pinball (quantile) loss for quantile level q."""
    err = y - p
    return float(
        pl.select(pl.when(err >= 0).then(q * err).otherwise((q - 1.0) * err).mean()).item()
    )


def _clean_sigma(s: object, fallback: float) -> float:
    """Return a usable sigma or the fallback; handles None and NaN."""
    if s is None:
        return fallback
    s_f = float(s)
    return s_f if s_f == s_f and s_f > 0 else fallback


def fit_rw_sigmas(train: pl.DataFrame, h: int) -> dict[str, float]:
    """Per-class random-walk residual sigma on train, pooled fallback."""
    res = train.select(
        pl.col("target_class"),
        (pl.col(f"y_step_h{h}") - pl.col("log_value")).alias("e"),
    ).drop_nulls()
    pooled = float(res["e"].std())
    out = {
        row["target_class"]: _clean_sigma(row["s"], pooled)
        for row in res.group_by("target_class").agg(pl.col("e").std().alias("s"))
        .iter_rows(named=True)
    }
    out["POOLED"] = pooled
    return out


def predict_rw(df: pl.DataFrame, h: int, sigmas: dict[str, float]) -> pl.DataFrame:
    """Point + quantile columns for the persistence baseline."""
    sigma = pl.col("target_class").replace_strict(sigmas, default=sigmas["POOLED"])
    return df.select(
        pl.col("date"),
        pl.col("target_class"),
        pl.lit(h).cast(pl.Int64).alias("h"),
        pl.col("log_value").alias("p_0.5"),
        (pl.col("log_value") + ZSCORES[0.1] * sigma).alias("p_0.1"),
        (pl.col("log_value") + ZSCORES[0.9] * sigma).alias("p_0.9"),
    )


def fit_ar1(train: pl.DataFrame, h: int) -> dict[str, object]:
    """Pooled AR(1) coefficients plus per-class residual sigmas."""
    d = train.drop_nulls([f"y_step_h{h}"])
    x = d["log_value"].to_numpy()
    y = d[f"y_step_h{h}"].to_numpy()
    b = float(((x * y).mean() - x.mean() * y.mean()) / ((x * x).mean() - x.mean() ** 2))
    a = float(y.mean() - b * x.mean())
    resid = y - (a + b * x)
    pooled = float(resid.std())
    sigmas = {
        row["target_class"]: _clean_sigma(row["s"], pooled)
        for row in d.with_columns(pl.lit(resid).alias("r"))
        .group_by("target_class").agg(pl.col("r").std().alias("s"))
        .iter_rows(named=True)
    }
    sigmas["POOLED"] = pooled
    return {"a": a, "b": b, "sigmas": sigmas}


def predict_ar1(df: pl.DataFrame, h: int, model: dict[str, object]) -> pl.DataFrame:
    """Point + quantile columns for the AR(1) baseline."""
    a, b = float(model["a"]), float(model["b"])
    sigmas: dict[str, float] = model["sigmas"]  # type: ignore[assignment]
    mean = pl.lit(a) + pl.lit(b) * pl.col("log_value")
    sigma = pl.col("target_class").replace_strict(sigmas, default=sigmas["POOLED"])
    return df.select(
        pl.col("date"),
        pl.col("target_class"),
        pl.lit(h).cast(pl.Int64).alias("h"),
        mean.alias("p_0.5"),
        (mean + ZSCORES[0.1] * sigma).alias("p_0.1"),
        (mean + ZSCORES[0.9] * sigma).alias("p_0.9"),
    )


def make_matrix(df: pl.DataFrame, h: int) -> tuple[list[str], object]:
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


def predict_lgbm(
    train: pl.DataFrame, eval_frames: dict[str, pl.DataFrame], h: int
) -> dict[str, pl.DataFrame]:
    """Train pooled LightGBM quantile models on RETURNS with early stopping.

    Targets are log-returns (y - log_value); predictions are added back to
    the current level before evaluation. Early stopping uses a chronological
    holdout carved from the tail of train, so outer valid/test stay untouched.
    """
    core_n = max(int(train.height * 0.85), train.height - 60)
    tr = train.sort("date").head(core_n)
    es = train.sort("date").tail(train.height - core_n)
    names, x_tr = make_matrix(tr, h)
    _, x_es = make_matrix(es, h)
    y_tr = (tr[f"y_step_h{h}"] - tr["log_value"]).to_numpy()
    y_es = (es[f"y_step_h{h}"] - es["log_value"]).to_numpy()
    cat_idx = [names.index("target_class")]
    per_quantile: dict[str, list[pl.DataFrame]] = {}
    median_booster: lgb.Booster | None = None
    for q in QUANTILES:
        params = dict(LGB_PARAMS)
        params["alpha"] = q
        dtrain = lgb.Dataset(
            x_tr, label=y_tr, feature_name=names,
            categorical_feature=cat_idx, free_raw_data=True,
        )
        dval = lgb.Dataset(
            x_es, label=y_es, reference=dtrain,
            feature_name=names, categorical_feature=cat_idx, free_raw_data=True,
        )
        booster = lgb.train(
            params, dtrain,
            num_boost_round=2000,
            valid_sets=[dval],
            callbacks=[lgb.early_stopping(50, verbose=False)],
        )
        if q == 0.5:
            median_booster = booster
        for split_name, frame in eval_frames.items():
            _, x = make_matrix(frame, h)
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


def evaluate(
    pred: pl.DataFrame, source: pl.DataFrame, split: str, model: str
) -> list[dict[str, object]]:
    """Pinball per quantile, median MAE, directional hit-rate; pooled + per class."""
    h = int(pred["h"][0])
    truth = source.select(
        "date", "target_class",
        pl.col(f"y_step_h{h}").alias("y"), pl.col("log_value"),
    )
    j = pred.join(truth, on=["date", "target_class"], how="inner")
    rows: list[dict[str, object]] = []
    scopes: list[tuple[str, pl.DataFrame | None]] = [("POOLED", None)]
    scopes += [(c, c) for c in CLASSES]
    for scope, cls in scopes:
        sub = j if cls is None else j.filter(pl.col("target_class") == cls)
        if sub.is_empty():
            continue
        y = sub["y"]
        row: dict[str, object] = {
            "split": split, "model": model, "scope": scope, "h": h, "n": sub.height,
        }
        for q in QUANTILES:
            row[f"pinball_{q}"] = pinball(y, sub[f"p_{q}"], q)
        row["mae_p50"] = float((y - sub["p_0.5"]).abs().mean())
        hits = (sub["p_0.5"] > sub["log_value"]) == (y > sub["log_value"])
        row["dir_hit"] = float(hits.mean())
        rows.append(row)
    return rows


def main() -> None:
    """Run all baselines, evaluate on valid/test, write metrics csv."""
    from ml.model_lgbm_tuned import predict as predict_lgbm_tuned
    from ml.model_lstm import predict as predict_lstm
    from ml.model_xgb import predict as predict_xgb

    _setup_logging()
    splits = {name: load_split(name) for name in ("train", "valid", "test")}
    all_rows: list[dict[str, object]] = []
    for h in HORIZONS:
        rw_s = fit_rw_sigmas(splits["train"], h)
        ar1_m = fit_ar1(splits["train"], h)
        candidates: dict[str, dict[str, pl.DataFrame]] = {
            "rw": {
                s: predict_rw(splits[s], h, rw_s) for s in ("valid", "test")
            },
            "ar1": {
                s: predict_ar1(splits[s], h, ar1_m) for s in ("valid", "test")
            },
            "lgbm": predict_lgbm(
                splits["train"],
                {"valid": splits["valid"], "test": splits["test"]},
                h,
            ),
            "lstm": predict_lstm(
                splits["train"],
                {"valid": splits["valid"], "test": splits["test"]},
                h,
            ),
            "xgb": predict_xgb(
                splits["train"],
                {"valid": splits["valid"], "test": splits["test"]},
                h,
            ),
            "lgbm_tuned": predict_lgbm_tuned(
                splits["train"],
                {"valid": splits["valid"], "test": splits["test"]},
                h,
            ),
        }
        for model, split_preds in candidates.items():
            for split_name, pred in split_preds.items():
                all_rows.extend(evaluate(pred, splits[split_name], split_name, model))
        LOGGER.info(f"h={h}: done ({len(candidates)} models)")
    metrics = pl.DataFrame(all_rows).sort(["split", "h", "model", "scope"])
    out_path = DATA / "baseline_metrics.csv"
    metrics.write_csv(out_path)
    LOGGER.info(f"wrote {out_path} ({metrics.height} rows)")
    pooled_valid = metrics.filter(
        (pl.col("split") == "valid") & (pl.col("scope") == "POOLED")
    ).select("h", "model", "pinball_0.5", "mae_p50", "dir_hit")
    LOGGER.info(f"\n{pooled_valid}")


if __name__ == "__main__":
    main()
