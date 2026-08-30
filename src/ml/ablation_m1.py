"""P3 requirement 7 -- the ablation that decides how M1 ships.

    A: existing forecast model (ml.model_xgb, unmodified feature set)
    B: existing forecast model + M1 physical-pressure features
       (tonnage.mlfeatures.build_m1_feature_frame, left-joined on)

Both variants call the exact same ``ml.model_xgb.predict`` -- same
hyperparameters (``XGB_PARAMS``), same early-stopping procedure, same
train/valid/test splits loaded through the same sanctioned paths. The ONLY
difference between A and B is which columns are present in the frame handed to
``make_matrix``: B's frame carries two extra columns
(``tonnage.mlfeatures.M1_FEATURE_COLUMNS``); A's does not. This is the entire
ablation -- no second training pipeline, no second backtest system.

Out-of-sample: evaluated on ``valid`` (already never used for anything but
model comparison) AND, once, on the real frozen ``test`` split via
``ml.frozen_test.allow_test_set_access`` -- the one sanctioned entry point, per
the P3 prompt's explicit instruction to use it rather than build a second one.

Decision rule (fixed here, before this module was ever run against real
numbers -- see git history / the P3 completion report for confirmation this
was not adjusted after seeing results): B is adopted only if, on the real
frozen test split, POOLED scope, averaged across the three horizons, B's
median pinball loss (``pinball_0.5``) improves on A's by more than
``ADOPTION_MIN_RELATIVE_IMPROVEMENT`` (1%) -- AND the same-direction
improvement also holds on ``valid``, so a single-split fluke cannot pass the
gate alone. Both outcomes (adopt / do not adopt) are acceptable and this
module reports whichever one the real numbers produce.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import polars as pl

from ml.baselines import CLASSES, HORIZONS, evaluate, load_split
from ml.frozen_test import allow_test_set_access, load_frozen_test
from ml.model_xgb import predict as predict_xgb
from tonnage.mlfeatures import M1_FEATURE_COLUMNS, build_m1_feature_frame
from tonnage.stockflow import reconstruct

__all__ = [
    "ADOPTION_MIN_RELATIVE_IMPROVEMENT",
    "AblationMetricRow",
    "AblationReport",
    "run_ablation",
]

#: Minimum relative improvement in POOLED, horizon-averaged test pinball_0.5
#: B must show over A to be considered "measurably better" -- fixed before
#: this module was run against real results (see module docstring).
ADOPTION_MIN_RELATIVE_IMPROVEMENT: Final[float] = 0.01


@dataclass(frozen=True)
class AblationMetricRow:
    split: str  # "valid" | "test"
    scope: str  # "POOLED" | one of ml.baselines.CLASSES
    h: int
    n: int
    pinball_0_1_a: float
    pinball_0_5_a: float
    pinball_0_9_a: float
    mae_p50_a: float
    dir_hit_a: float
    pinball_0_1_b: float
    pinball_0_5_b: float
    pinball_0_9_b: float
    mae_p50_b: float
    dir_hit_b: float

    @property
    def pinball_0_5_relative_improvement(self) -> float:
        """(A - B) / A -- positive means B has LOWER (better) loss than A."""
        if self.pinball_0_5_a == 0:
            return 0.0
        return (self.pinball_0_5_a - self.pinball_0_5_b) / abs(self.pinball_0_5_a)


@dataclass(frozen=True)
class AblationReport:
    """Generic A/B ablation result -- not M1-specific despite living in this
    module (the first, and still primary, caller). ``ml.macro_features``
    reuses this exact shape for its own (unrelated) candidate features
    rather than defining a second, near-identical dataclass."""

    rows: list[AblationMetricRow]
    feature_valid_coverage: float  # fraction of valid rows with real (non-null) candidate feature(s)
    feature_test_coverage: float
    test_pooled_mean_relative_improvement: float
    valid_pooled_mean_relative_improvement: float
    adopt_b: bool
    reasoning: str

    def table(self) -> pl.DataFrame:
        return pl.DataFrame([r.__dict__ for r in self.rows])


def _augment(frame: pl.DataFrame, m1: pl.DataFrame) -> pl.DataFrame:
    return frame.join(m1, on=["date", "target_class"], how="left")


def _coverage(frame: pl.DataFrame, m1: pl.DataFrame) -> float:
    joined = _augment(frame, m1)
    if joined.height == 0:
        return 0.0
    return 1.0 - (joined[M1_FEATURE_COLUMNS[0]].null_count() / joined.height)


def run_ablation() -> AblationReport:
    train = load_split("train")
    valid = load_split("valid")
    with allow_test_set_access("P3 M1 ablation: A (existing) vs B (existing + M1) final report"):
        test = load_frozen_test()

    m1 = build_m1_feature_frame(reconstruct())
    train_b = _augment(train, m1)
    valid_b = _augment(valid, m1)
    test_b = _augment(test, m1)

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
            f"ADOPT B. Pooled, horizon-averaged test pinball_0.5 improves by "
            f"{test_improve:.1%} (threshold {ADOPTION_MIN_RELATIVE_IMPROVEMENT:.1%}), "
            f"and valid agrees in direction ({valid_improve:.1%}). Both splits "
            "point the same way -- wiring M1 into ml.live_forecast is justified "
            "by this result."
        )
    else:
        reasoning = (
            f"DO NOT ADOPT B. Pooled, horizon-averaged test pinball_0.5 change is "
            f"{test_improve:+.1%} (need > {ADOPTION_MIN_RELATIVE_IMPROVEMENT:.1%} "
            f"AND valid agreement; valid change is {valid_improve:+.1%}). Per the "
            "P3 instruction, this is not forced into the pricing path -- M1 ships "
            "as an honestly-labelled decision-support signal with this ablation "
            "result surfaced, not silently wired into ml.live_forecast."
        )

    return AblationReport(
        rows=rows,
        feature_valid_coverage=_coverage(valid, m1),
        feature_test_coverage=_coverage(test, m1),
        test_pooled_mean_relative_improvement=test_improve,
        valid_pooled_mean_relative_improvement=valid_improve,
        adopt_b=adopt,
        reasoning=reasoning,
    )
