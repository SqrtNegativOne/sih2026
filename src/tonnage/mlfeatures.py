"""Bridge from the Tonnage Field's real reconstruction to a joinable ML feature
frame -- P3 requirement 7 (the ablation) needs M1's physical-pressure signal as
actual feature columns on the same (date, target_class) grid `ml.baselines`/
`ml.model_xgb` already train on.

Deliberately a separate, small module rather than editing
``data_builders.build_samples`` or ``ml.live_forecast`` directly: the ablation
(``ml.ablation_m1``) needs to build BOTH an unmodified feature table (model A,
byte-identical to what ships today) and an M1-augmented one (model B) from the
same base samples, in memory, without touching ``samples_train/valid/test.parquet``
on disk -- regenerating those files would itself be a "touch" of the frozen test
split's *construction*, which is exactly what ``ml.frozen_test`` exists to gate.
A left-join of this module's output onto an already-loaded split accomplishes
the same augmentation without going anywhere near that risk.

**Causality / leakage disclosure.** ``tightness`` (`tonnage.supplycurve.
build_tightness_index`) is causal in port-call FLOW events at the daily level:
every step of ``tonnage.stockflow.reconstruct`` (the cumulative net-flow sum,
the trailing residence-window bound, the per-day anchor correction) uses only
flows dated on or before that day -- verified by inspection of
``stockflow.reconstruct``, not assumed. Two STRUCTURAL weighting inputs,
however, are estimated once from each port's/basin's FULL observed history and
held static across the whole series: each port's class-attribution weights
(``tonnage.classmix.class_weights``, from that port's whole-history mean
parcel size) and each basin's share of the UNCTAD anchor correction
(``basin_activity_share`` inside ``stockflow.reconstruct``, from that basin's
whole-history total activity). Neither depends on the freight-rate TARGET
series at any point -- they are compositional priors over port/basin trade
structure, not price information -- so this is not target leakage. It is a
real, disclosed deviation from a strict trailing-only feature, inherited
unchanged from ``tonnage.stockflow`` (out of P3's scope to rewrite -- doing so
would require re-deriving and re-validating the whole reconstruction, which
P1/P2's validated Signal comparison and supply-curve correlations are pinned
to). Every existing feature in this pipeline (``ml.features.CongestionFeature``,
``CrossSeriesFeature``, etc.) is likewise computed once by pivoting the full
``master_long.parquet`` and reading off each date's row, so this is consistent
with -- not worse than -- the codebase's existing feature-computation practice,
not a new category of risk introduced here.
"""
from __future__ import annotations

from datetime import date
from typing import Final

import polars as pl

from opt.types import VesselClass
from tonnage.stockflow import StockflowResult, reconstruct
from tonnage.supplycurve import build_tightness_index

__all__ = ["M1_FEATURE_COLUMNS", "build_m1_feature_frame"]

#: The columns this module adds to a samples frame. Named distinctly from
#: every existing column in samples_*.parquet (checked: no collision with
#: EXCLUDE or any feature name in ml.baselines/ml.features) so a left-join
#: cannot silently shadow an existing column.
M1_FEATURE_COLUMNS: Final[tuple[str, ...]] = ("m1_tightness", "m1_stock_dwt_total")


def build_m1_feature_frame(result: StockflowResult | None = None) -> pl.DataFrame:
    """(date, target_class) -> M1 physical-pressure feature columns, ready for
    a left-join onto a samples frame (``.join(m1, on=["date", "target_class"],
    how="left")``). ``target_class`` uses the exact same string values as
    ``ml.baselines.CLASSES`` / ``opt.types.VesselClass.value`` -- confirmed
    identical ("Capesize", "Panamax", "Supramax", "Handysize"), not coerced.

    Rows with no M1 coverage for a given date (before the reconstruction's
    earliest date, or a date with zero activity for that class) are simply
    absent from this frame -- a left-join leaves ``null`` there, which
    XGBoost's ``hist`` tree method treats as a genuine missing value, the same
    way every other real-world-gappy feature in this pipeline already is.
    """
    result = result if result is not None else reconstruct()
    tightness = build_tightness_index(result)  # date, vessel_class, tightness

    stock_total = (
        result.frame.group_by(["date", "vessel_class"])
        .agg(pl.col("stock_dwt").sum().alias("m1_stock_dwt_total"))
    )

    out = (
        tightness.join(stock_total, on=["date", "vessel_class"], how="inner")
        .rename({"vessel_class": "target_class", "tightness": "m1_tightness"})
        .select("date", "target_class", "m1_tightness", "m1_stock_dwt_total")
        .sort(["target_class", "date"])
    )
    return out


def m1_feature_as_of(frame: pl.DataFrame, as_of: date, vessel_class: VesselClass) -> dict[str, float] | None:
    """Read off one (date, class) row's M1 features -- for live inference
    (``ml.live_forecast``-style "today's row"), not training. Returns ``None``
    (never a fabricated 0.0) when no M1 coverage exists for that exact date."""
    row = frame.filter((pl.col("date") == as_of) & (pl.col("target_class") == vessel_class.value))
    if row.is_empty():
        return None
    return {"m1_tightness": float(row["m1_tightness"][0]), "m1_stock_dwt_total": float(row["m1_stock_dwt_total"][0])}
