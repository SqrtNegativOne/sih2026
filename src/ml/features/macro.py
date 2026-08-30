"""Macro/commodity signal features -- P4 requirement 4.

Real data only: every value here traces to data_builders.build_macro's real,
sourced series (raw_data/macro/sources.md). The publication-lag leakage guard
lives in build_macro.py (each monthly value is dated to the 1st of the
FOLLOWING month) -- by the time a row here carries a date, that value was
genuinely knowable as of that date, so a plain as-of join (forward-filling
the last known real value across the gap to the next real observation, same
semantics as ``join_asof(strategy="backward")`` used throughout
``tonnage.supplycurve``) introduces no further leakage.

Two features per series: the forward-filled LEVEL, and a trailing
percent-change over ``CHANGE_WINDOW_DAYS`` -- commodity price levels are
highly non-stationary (a raw level mostly encodes "which decade is this,"
not information a same-day forecast can use), so the change is the more
likely-useful signal; both are exposed and the ablation in
``ml.macro_features`` decides which (if either) actually helps.
"""
from __future__ import annotations

from typing import Final

import polars as pl

#: Trailing window for the percent-change feature. 90 days: long enough that
#: a single stale/missing observation doesn't dominate the denominator,
#: short enough to reflect a real recent move rather than a multi-year drift.
CHANGE_WINDOW_DAYS: Final[int] = 90

#: (macro_long series_id -> short feature-name stem).
MACRO_SERIES_STEMS: Final[dict[str, str]] = {
    "MACRO_BRENT_CRUDE": "brent",
    "MACRO_COAL_AUSTRALIAN": "coal_au",
    "MACRO_IRON_ORE": "iron_ore",
    "MACRO_USD_INR": "usd_inr",
    "MACRO_US_INDUSTRIAL_PRODUCTION": "us_indpro",
}


class MacroFeature:
    """One macro series (by its macro_long.parquet series_id) -> level +
    trailing-change features, joined onto a date-indexed frame.

    ``macro_long`` is the long-format frame ``data_builders.build_macro``
    produces (or a filtered/loaded copy of ``src/data/macro_long.parquet``),
    not re-read from disk here -- callers own the I/O, matching every other
    feature transform in this package (``CongestionFeature`` et al. take
    already-loaded frames, never a path).
    """

    def __init__(self, macro_long: pl.DataFrame, series_id: str, change_window_days: int = CHANGE_WINDOW_DAYS):
        if series_id not in MACRO_SERIES_STEMS:
            raise ValueError(f"Unknown macro series_id {series_id!r}; expected one of {sorted(MACRO_SERIES_STEMS)}.")
        self.series_id = series_id
        self.stem = MACRO_SERIES_STEMS[series_id]
        self.change_window_days = change_window_days
        self._series = (
            macro_long.filter(pl.col("series_id") == series_id)
            .select("date", "value")
            .sort("date")
            .drop_nulls()
        )

    def transform(self, df: pl.DataFrame) -> pl.DataFrame:
        """Left-join level + trailing-change onto ``df`` by date (as-of,
        backward-filled to the last real observation). Dates before the
        series' own first real observation get null -- never a fabricated
        pre-history value. ``df`` must be sorted by date (every existing
        feature transform in this package assumes the same of its input)."""
        level_col = f"macro_{self.stem}_level"
        change_col = f"macro_{self.stem}_chg_{self.change_window_days}d"

        if self._series.is_empty():
            return df.with_columns(
                pl.lit(None, dtype=pl.Float64).alias(level_col),
                pl.lit(None, dtype=pl.Float64).alias(change_col),
            )

        # The real series is rarely daily (monthly commodities, business-day
        # FX) -- reindex onto a dense daily calendar, forward-filling the last
        # known real value, so a fixed CALENDAR-day change window is well
        # defined regardless of the source's native cadence.
        full_range = pl.date_range(self._series["date"].min(), self._series["date"].max(), interval="1d", eager=True)
        dense = (
            pl.DataFrame({"date": full_range})
            .join(self._series, on="date", how="left")
            .with_columns(pl.col("value").forward_fill())
            .with_columns(pl.col("value").shift(self.change_window_days).alias("_lagged_value"))
            .with_columns(
                pl.when(pl.col("_lagged_value").abs() > 0)
                .then((pl.col("value") - pl.col("_lagged_value")) / pl.col("_lagged_value").abs())
                .otherwise(None)
                .alias(change_col)
            )
            .select("date", pl.col("value").alias(level_col), change_col)
        )
        return df.join_asof(dense, on="date", strategy="backward")

    def feature_names_out(self) -> list[str]:
        return [f"macro_{self.stem}_level", f"macro_{self.stem}_chg_{self.change_window_days}d"]
