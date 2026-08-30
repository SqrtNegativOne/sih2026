"""P4 requirement 4 -- macro/commodity signal research and ablation.

For each real candidate (``raw_data/macro/sources.md``): economic rationale
(this module's docstrings, per-candidate below) -> lag analysis
(``lag_correlation``, real cross-correlation against each class's real
TCAVG) -> leakage review (structural: every macro value is dated to the
month AFTER the one it describes -- see ``data_builders.build_macro`` -- so
an as-of join never uses a not-yet-published figure; asserted by
``tests/ml/test_macro_features.py``) -> ablation + out-of-sample test
(``run_macro_ablation``, same real mechanism ``ml.ablation_m1`` uses: the
exact same ``ml.model_xgb.predict``, ``ml.baselines.evaluate``, and
``ml.frozen_test`` guard -- no second backtest system).

**Economic rationale, per candidate** (why each was chosen, stated before
any number was looked at):

- ``MACRO_BRENT_CRUDE`` (bunker/crude proxy): bunker fuel is a direct,
  material voyage cost; a rising bunker price raises an owner's break-even
  TC rate and a falling one lowers it. Plausible LEADING or CONTEMPORANEOUS
  relationship to TC rates.
- ``MACRO_COAL_AUSTRALIAN`` (coking coal proxy): coal is a dominant dry-bulk
  cargo (Newcastle_AU, one of this system's own real load ports, is a coal
  hub); a coal-price rally that reflects strong demand plausibly also lifts
  freight demand for the vessels that carry it. Cargo-specific, not a
  universal channel -- expected to matter more for Capesize/Panamax
  (typical coal-carrying classes) than Handysize/Supramax.
- ``MACRO_IRON_ORE`` (iron ore / steel-activity proxy): the dominant
  Capesize cargo; a real, standard financial-market proxy for steel-sector
  demand (~98% of mined iron ore becomes steel). Expected to matter most for
  Capesize specifically.
- ``MACRO_USD_INR`` (FX): weak expected channel, tested anyway rather than
  assumed away -- charter rates in this market are near-universally
  USD-quoted, so INR moves mostly affect an Indian buyer's domestic landed
  cost, not the international USD freight rate itself. Included precisely
  because a negative result here is a real, useful finding (confirms the
  channel is genuinely weak rather than merely untested).
- ``MACRO_US_INDUSTRIAL_PRODUCTION`` (global industrial activity proxy, US
  only -- see raw_data/macro/sources.md's disclosed limitation): industrial
  activity drives raw-material demand generally; expected to be a slow-
  moving, low-frequency signal (a monthly US index) more likely to show up
  (if at all) at the 90-day horizon than the 7-day one.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final

import numpy as np
import polars as pl

from ml.ablation_m1 import (
    ADOPTION_MIN_RELATIVE_IMPROVEMENT,
    AblationMetricRow,
    AblationReport,
)
from ml.baselines import CLASSES, HORIZONS, evaluate, load_split
from ml.features.macro import MACRO_SERIES_STEMS, MacroFeature
from ml.frozen_test import allow_test_set_access, load_frozen_test
from ml.model_xgb import predict as predict_xgb
from ml.units import CLASS_SERIES

__all__ = [
    "MACRO_CANDIDATES",
    "MacroLagCorrelation",
    "lag_correlation",
    "run_all_macro_ablations",
    "run_macro_ablation",
]

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
MACRO_LONG_PATH: Final[Path] = REPO_ROOT / "src" / "data" / "macro_long.parquet"
MASTER_LONG_PATH: Final[Path] = REPO_ROOT / "src" / "data" / "master_long.parquet"

MACRO_CANDIDATES: Final[tuple[str, ...]] = tuple(MACRO_SERIES_STEMS)

#: Lags tested (calendar days). Negative = macro series LEADS the rate
#: (macro today vs a FUTURE rate -- the economically interesting direction
#: for a forecasting feature); positive = macro LAGS (rate moves first).
LAG_DAYS: Final[tuple[int, ...]] = (-90, -30, -14, -7, 0, 7, 14, 30, 90)


def _load_macro_long() -> pl.DataFrame:
    return pl.read_parquet(MACRO_LONG_PATH)


@dataclass(frozen=True)
class MacroLagCorrelation:
    series_id: str
    vessel_class: str
    by_lag_days: dict[int, float]
    n_obs: dict[int, int]

    @property
    def best_lag_days(self) -> int:
        valid = {k: v for k, v in self.by_lag_days.items() if v == v}
        if not valid:
            return 0
        return min(valid, key=lambda lag: (-abs(valid[lag]), abs(lag)))


def lag_correlation(series_id: str, vessel_class: str, macro_long: pl.DataFrame | None = None) -> MacroLagCorrelation:
    """Cross-correlate the macro series' LEVEL against the real class TCAVG,
    at each lag in LAG_DAYS -- the real lag-analysis step, independent of
    (and run before) the ablation."""
    macro_long = macro_long if macro_long is not None else _load_macro_long()
    series = macro_long.filter(pl.col("series_id") == series_id).select("date", "value").sort("date")
    if series.is_empty():
        raise ValueError(f"No real data for macro series_id={series_id!r}.")

    tc_series_id = CLASS_SERIES[vessel_class][1]
    master = pl.read_parquet(MASTER_LONG_PATH)
    rate = master.filter(pl.col("series_id") == tc_series_id).select(
        pl.col("date"), pl.col("value").alias("rate_usd_day")
    ).sort("date")
    if rate.is_empty():
        raise ValueError(f"No real TCAVG data for {vessel_class}.")

    by_lag: dict[int, float] = {}
    n_obs: dict[int, int] = {}
    for lag in LAG_DAYS:
        shifted = series.with_columns((pl.col("date") + pl.duration(days=lag)).alias("date"))
        joined = rate.join_asof(shifted, on="date", strategy="backward").drop_nulls(["value"])
        x, y = joined["value"].to_numpy(), joined["rate_usd_day"].to_numpy()
        n_obs[lag] = joined.height
        if joined.height < 30 or np.std(x) == 0 or np.std(y) == 0:
            by_lag[lag] = float("nan")
        else:
            by_lag[lag] = float(np.corrcoef(x, y)[0, 1])
    return MacroLagCorrelation(series_id=series_id, vessel_class=vessel_class, by_lag_days=by_lag, n_obs=n_obs)


def _augment(frame: pl.DataFrame, macro_long: pl.DataFrame, series_id: str) -> pl.DataFrame:
    feat = MacroFeature(macro_long, series_id)
    return feat.transform(frame.sort("date"))


def _coverage(frame: pl.DataFrame, macro_long: pl.DataFrame, series_id: str) -> float:
    joined = _augment(frame, macro_long, series_id)
    level_col = f"macro_{MACRO_SERIES_STEMS[series_id]}_level"
    if joined.height == 0:
        return 0.0
    return 1.0 - (joined[level_col].null_count() / joined.height)


def run_macro_ablation(series_id: str) -> AblationReport:
    """The real A/B ablation for ONE macro candidate: A = existing forecast
    model, B = existing + this candidate's two features (level, trailing
    change). Same real mechanism as ``ml.ablation_m1.run_ablation`` -- the
    exact same ``ml.model_xgb.predict``, ``ml.baselines.evaluate``, and the
    ``ml.frozen_test`` guard for the one, real, final read of the frozen test
    split. Decision rule is the SAME fixed, pre-committed threshold
    ``ml.ablation_m1.ADOPTION_MIN_RELATIVE_IMPROVEMENT`` M1's ablation uses --
    one consistent bar for "measurably helps," not a different one invented
    per feature to make a particular result land.
    """
    if series_id not in MACRO_SERIES_STEMS:
        raise ValueError(f"Unknown macro series_id {series_id!r}; expected one of {sorted(MACRO_SERIES_STEMS)}.")

    train = load_split("train")
    valid = load_split("valid")
    with allow_test_set_access(f"P4 macro ablation ({series_id}): A (existing) vs B (existing + {series_id}) final report"):
        test = load_frozen_test()

    macro_long = _load_macro_long()
    train_b = _augment(train, macro_long, series_id)
    valid_b = _augment(valid, macro_long, series_id)
    test_b = _augment(test, macro_long, series_id)

    rows: list[AblationMetricRow] = []
    for h in HORIZONS:
        preds_a = predict_xgb(train, {"valid": valid, "test": test}, h)
        preds_b = predict_xgb(train_b, {"valid": valid_b, "test": test_b}, h)

        for split_name, source in (("valid", valid), ("test", test)):
            eval_a = {r["scope"] + str(r["h"]): r for r in evaluate(preds_a[split_name], source, split_name, "A")}
            eval_b = {r["scope"] + str(r["h"]): r for r in evaluate(preds_b[split_name], source, split_name, "B")}
            for scope in ("POOLED", *CLASSES):
                key = scope + str(h)
                ra, rb = eval_a.get(key), eval_b.get(key)
                if ra is None or rb is None:
                    continue
                rows.append(
                    AblationMetricRow(
                        split=split_name, scope=scope, h=h, n=int(ra["n"]),
                        pinball_0_1_a=ra["pinball_0.1"], pinball_0_5_a=ra["pinball_0.5"], pinball_0_9_a=ra["pinball_0.9"],
                        mae_p50_a=ra["mae_p50"], dir_hit_a=ra["dir_hit"],
                        pinball_0_1_b=rb["pinball_0.1"], pinball_0_5_b=rb["pinball_0.5"], pinball_0_9_b=rb["pinball_0.9"],
                        mae_p50_b=rb["mae_p50"], dir_hit_b=rb["dir_hit"],
                    )
                )

    test_pooled = [r for r in rows if r.split == "test" and r.scope == "POOLED"]
    valid_pooled = [r for r in rows if r.split == "valid" and r.scope == "POOLED"]
    test_improve = sum(r.pinball_0_5_relative_improvement for r in test_pooled) / len(test_pooled) if test_pooled else 0.0
    valid_improve = sum(r.pinball_0_5_relative_improvement for r in valid_pooled) / len(valid_pooled) if valid_pooled else 0.0

    adopt = test_improve > ADOPTION_MIN_RELATIVE_IMPROVEMENT and valid_improve > 0.0
    if adopt:
        reasoning = (
            f"KEEP {series_id}. Pooled, horizon-averaged test pinball_0.5 improves by "
            f"{test_improve:.1%} (threshold {ADOPTION_MIN_RELATIVE_IMPROVEMENT:.1%}), and valid "
            f"agrees in direction ({valid_improve:.1%})."
        )
    else:
        reasoning = (
            f"DROP {series_id}. Pooled, horizon-averaged test pinball_0.5 change is "
            f"{test_improve:+.1%} (need > {ADOPTION_MIN_RELATIVE_IMPROVEMENT:.1%} AND valid "
            f"agreement; valid change is {valid_improve:+.1%}). Not retained as a production feature."
        )

    return AblationReport(
        rows=rows,
        feature_valid_coverage=_coverage(valid, macro_long, series_id),
        feature_test_coverage=_coverage(test, macro_long, series_id),
        test_pooled_mean_relative_improvement=test_improve,
        valid_pooled_mean_relative_improvement=valid_improve,
        adopt_b=adopt,
        reasoning=reasoning,
    )


def run_all_macro_ablations() -> dict[str, AblationReport]:
    """Every real candidate, tested independently (isolates each one's own
    marginal contribution rather than a combined model whose per-feature
    attribution would be harder to defend)."""
    return {series_id: run_macro_ablation(series_id) for series_id in MACRO_CANDIDATES}
