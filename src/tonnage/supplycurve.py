"""Tightness index and the structural rate = f(tightness) relationship.

Tightness is built entirely from data this package already reconstructs -- no
external demand series is invented. For each class, on each day:

    tightness = (trailing mean daily export/loading tonnage, all basins) /
                (free tonnage stock, all basins)

i.e. "how fast is currently-free capacity being drawn down by real loading
activity, relative to how much of it exists." High tightness (fast draw-down
against a thin pool) should mean a seller's market; low tightness a buyer's one.

The rate side uses the real, already-in-correct-units ``*_TCAVG`` series in
``src/data/master_long.parquet`` (Capesize/Panamax/Supramax/Handysize USD/day time-
charter averages) directly -- deliberately **not** back-converted from the longer
Baltic index history via ``ml.units``. That module's own docstring is explicit that
its fitted index<->USD map should not be extrapolated across regime changes it
can't see; stitching decades of index history into one long "USD/day" series for a
demo would be exactly the mistake it warns against. The cost is real: TCAVG
overlap is short for two of the four classes (185 rows / ~9 months for Supramax
and Handysize, vs 279 rows / ~6 years for Capesize) -- reported explicitly as
``n_obs`` and ``low_confidence`` on every fitted curve rather than hidden.

What this module does not attempt: the plan's IV identification strategy (weather-
and chokepoint-driven instruments to separate the causal effect of tightness on
rate from rate's own feedback into supply decisions -- scrapping, lay-up, newbuild
ordering). What's implemented here is a direct quantile regression of rate on
tightness: real, testable, and honestly associational rather than causal.

**Measured on real data, this relationship is weak and inconsistent across
classes, and that is reported rather than hidden.** Level correlation
(tightness vs TCAVG) came out +0.75 for Supramax, +0.31 for Panamax, but -0.02
for Capesize and -0.24 for Handysize -- the wrong sign for a "tighter supply
means higher rate" story. First-differencing (week-over-week changes, the
standard remedy when two trending/persistent series produce a misleading level
correlation) flips Capesize and Handysize positive but weak (+0.11, +0.22), and
*weakens* Panamax's level correlation to near zero -- consistent with genuine
short-run noise plus partial trend-confounding, not a clean structural signal.
This is exactly the simultaneity problem named above, showing up empirically: a
single global tightness feature regressed on a global TC average, with no
instrument to separate supply-driven tightness from rate's own feedback into
it, does not reliably recover a textbook supply curve. ``pearson_r`` on
``SupplyCurveFit`` carries this number so callers (``impact.elasticity`` in
particular) can gate on it rather than trust every fitted curve equally.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Final

import numpy as np
import polars as pl
from sklearn.linear_model import QuantileRegressor

from opt.types import VesselClass
from tonnage.stockflow import StockflowResult, _class_attributed_flows

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
MASTER_LONG_PATH: Final[Path] = REPO_ROOT / "src" / "data" / "master_long.parquet"

#: Trailing window (days) smoothing the daily export flow before it enters the
#: tightness ratio -- a single zero-call day would otherwise swing tightness to
#: zero for a port that is genuinely active on every other day.
FLOW_SMOOTHING_DAYS: Final[int] = 14

#: Below this many joined (tightness, rate) observations, a fitted curve is
#: flagged low_confidence rather than withheld -- still real, just thin.
MIN_OBS_FOR_CONFIDENCE: Final[int] = 60

_TCAVG_SERIES: Final[dict[VesselClass, str]] = {
    VesselClass.CAPESIZE: "CAPESIZE_TCAVG",
    VesselClass.PANAMAX: "PANAMAX_TCAVG",
    VesselClass.SUPRAMAX: "SUPRAMAX_TCAVG",
    VesselClass.HANDYSIZE: "HANDYSIZE_TCAVG",
}

QUANTILES: Final[tuple[float, ...]] = (0.1, 0.5, 0.9)


class NoOverlapError(RuntimeError):
    """Raised when a class has no real TCAVG rows overlapping the tightness index."""


@dataclass(frozen=True)
class SupplyCurveFit:
    vessel_class: VesselClass
    n_obs: int
    low_confidence: bool
    slope: dict[float, float]  # per quantile
    intercept: dict[float, float]  # per quantile
    pinball_loss: dict[float, float]  # in-sample, per quantile
    coverage: dict[float, float]  # empirical share of y below the fitted quantile
    tightness_range: tuple[float, float]
    pearson_r: float  # level correlation(tightness, rate) -- the honest signal-strength number

    @property
    def weak_signal(self) -> bool:
        """True when the level correlation is too weak/wrong-signed to trust the
        fitted slope for anything beyond description. Threshold is deliberately
        loose (|r| >= 0.3, the weakest of the four classes' real level
        correlations that still had the expected sign) -- this is a gate, not a
        quality score."""
        return abs(self.pearson_r) < 0.3

    def predict(self, tightness: float) -> dict[float, float]:
        return {q: self.slope[q] * tightness + self.intercept[q] for q in QUANTILES}


def build_tightness_index(result: StockflowResult) -> pl.DataFrame:
    """Class-level (all basins summed) daily tightness index from real flows/stock."""
    stock = (
        result.frame.group_by(["date", "vessel_class"])
        .agg(pl.col("stock_dwt").sum().alias("total_stock_dwt"))
        .sort(["vessel_class", "date"])
    )
    flows = (
        _class_attributed_flows()
        .group_by(["date", "vessel_class"])
        .agg(pl.col("outflow_t").sum().alias("total_outflow_t"))
        .sort(["vessel_class", "date"])
    )
    out_frames = []
    for cls in VesselClass:
        f = flows.filter(pl.col("vessel_class") == cls.value).sort("date")
        if f.is_empty():
            continue
        f = f.with_columns(
            pl.col("total_outflow_t")
            .rolling_mean(window_size=FLOW_SMOOTHING_DAYS, min_samples=1)
            .alias("smoothed_outflow_t")
        )
        s = stock.filter(pl.col("vessel_class") == cls.value).sort("date")
        joined = f.join(s, on=["date", "vessel_class"], how="inner").filter(pl.col("total_stock_dwt") > 0)
        joined = joined.with_columns(
            (pl.col("smoothed_outflow_t") / pl.col("total_stock_dwt")).alias("tightness")
        )
        out_frames.append(joined.select("date", "vessel_class", "tightness"))
    return pl.concat(out_frames) if out_frames else pl.DataFrame(
        schema={"date": pl.Date, "vessel_class": pl.Utf8, "tightness": pl.Float64}
    )


def _load_real_rate(vessel_class: VesselClass) -> pl.DataFrame:
    series_id = _TCAVG_SERIES[vessel_class]
    master = pl.read_parquet(MASTER_LONG_PATH)
    rate = master.filter(pl.col("series_id") == series_id).select(
        pl.col("date").cast(pl.Date), pl.col("value").alias("rate_usd_day")
    ).sort("date")
    return rate


def fit_supply_curve(vessel_class: VesselClass, tightness_index: pl.DataFrame) -> SupplyCurveFit:
    """Quantile-regress the real TCAVG rate on the tightness index, via an as-of
    join (rate observations are weekly-ish; tightness is daily)."""
    tight = tightness_index.filter(pl.col("vessel_class") == vessel_class.value).sort("date")
    rate = _load_real_rate(vessel_class)
    if tight.is_empty() or rate.is_empty():
        raise NoOverlapError(f"No tightness/rate data available for {vessel_class}.")

    joined = rate.join_asof(tight, on="date", strategy="backward").drop_nulls(["tightness"])
    n = joined.height
    if n < 5:
        raise NoOverlapError(f"Only {n} joined (tightness, rate) rows for {vessel_class}, too few to fit.")

    x = joined["tightness"].to_numpy().reshape(-1, 1)
    y = joined["rate_usd_day"].to_numpy()
    pearson_r = float(np.corrcoef(x.ravel(), y)[0, 1])

    slope, intercept, pinball, coverage = {}, {}, {}, {}
    for q in QUANTILES:
        model = QuantileRegressor(quantile=q, alpha=0.0, solver="highs")
        model.fit(x, y)
        slope[q] = float(model.coef_[0])
        intercept[q] = float(model.intercept_)
        pred = model.predict(x)
        pinball[q] = _pinball_loss(y, pred, q)
        coverage[q] = float(np.mean(y <= pred))

    return SupplyCurveFit(
        vessel_class=vessel_class,
        n_obs=n,
        low_confidence=n < MIN_OBS_FOR_CONFIDENCE,
        slope=slope,
        intercept=intercept,
        pinball_loss=pinball,
        coverage=coverage,
        tightness_range=(float(x.min()), float(x.max())),
        pearson_r=pearson_r,
    )


def _pinball_loss(y_true: np.ndarray, y_pred: np.ndarray, q: float) -> float:
    diff = y_true - y_pred
    return float(np.mean(np.maximum(q * diff, (q - 1) * diff)))


def fit_all(tightness_index: pl.DataFrame) -> dict[VesselClass, SupplyCurveFit]:
    fits = {}
    for cls in VesselClass:
        try:
            fits[cls] = fit_supply_curve(cls, tightness_index)
        except NoOverlapError:
            continue
    return fits


# ---------------------------------------------------------------------------
# P3 -- sign diagnosis. `fit_supply_curve` reports the wrong sign for two of
# four classes (Capesize r=-0.02, Handysize r=-0.24). The P3 prompt requires
# testing each candidate explanation with real evidence and reporting which
# one(s) actually explain it, rather than picking a story. Every function
# below reuses the exact same real (tightness, rate) joined series
# `fit_supply_curve` regresses on -- no new data source, no synthetic input.
# ---------------------------------------------------------------------------


def _joined_tightness_rate(vessel_class: VesselClass, tightness_index: pl.DataFrame) -> pl.DataFrame:
    """The same as-of join `fit_supply_curve` builds, exposed standalone so every
    diagnostic below operates on identically the same rows."""
    tight = tightness_index.filter(pl.col("vessel_class") == vessel_class.value).sort("date")
    rate = _load_real_rate(vessel_class)
    if tight.is_empty() or rate.is_empty():
        raise NoOverlapError(f"No tightness/rate data available for {vessel_class}.")
    joined = rate.join_asof(tight, on="date", strategy="backward").drop_nulls(["tightness"]).sort("date")
    return joined


@dataclass(frozen=True)
class FirstDifferenceCheck:
    """Tests the *confounding-by-shared-trend* explanation: if two persistent,
    trending level series produce a correlation that is really just "both drift
    the same way over the sample," first-differencing (period-over-period change,
    at the joined series' own native -- weekly-ish -- spacing) should weaken or
    flip it. If level and diff correlations agree in sign and are similar in
    magnitude, the level relationship is not merely a trend artifact."""

    vessel_class: VesselClass
    n_obs: int
    n_diff_obs: int
    pearson_r_level: float
    pearson_r_diff: float


def first_difference_diagnostic(vessel_class: VesselClass, tightness_index: pl.DataFrame) -> FirstDifferenceCheck:
    joined = _joined_tightness_rate(vessel_class, tightness_index)
    if joined.height < 6:
        raise NoOverlapError(f"Only {joined.height} joined rows for {vessel_class}, too few to diff.")
    x = joined["tightness"].to_numpy()
    y = joined["rate_usd_day"].to_numpy()
    level_r = float(np.corrcoef(x, y)[0, 1])
    dx, dy = np.diff(x), np.diff(y)
    diff_r = float(np.corrcoef(dx, dy)[0, 1]) if (np.std(dx) > 0 and np.std(dy) > 0) else float("nan")
    return FirstDifferenceCheck(
        vessel_class=vessel_class, n_obs=joined.height, n_diff_obs=len(dx),
        pearson_r_level=level_r, pearson_r_diff=diff_r,
    )


@dataclass(frozen=True)
class RegimeSplitCheck:
    """Tests the *regime-change* explanation: splits the joined series in half by
    date and refits the level correlation in each half separately. A relationship
    that only holds -- or only holds with the textbook sign -- in one half is
    evidence of non-stationarity (a structural break: COVID-era dry-bulk markets,
    a shift in trade patterns, etc.), not a single stable structural curve."""

    vessel_class: VesselClass
    first_half_n: int
    second_half_n: int
    first_half_r: float
    second_half_r: float
    split_date: date


def regime_split_diagnostic(vessel_class: VesselClass, tightness_index: pl.DataFrame) -> RegimeSplitCheck:
    joined = _joined_tightness_rate(vessel_class, tightness_index)
    n = joined.height
    if n < 10:
        raise NoOverlapError(f"Only {n} joined rows for {vessel_class}, too few to split.")
    mid = n // 2
    first, second = joined[:mid], joined[mid:]
    split_date = second["date"][0]

    def _r(frame: pl.DataFrame) -> float:
        x, y = frame["tightness"].to_numpy(), frame["rate_usd_day"].to_numpy()
        return float(np.corrcoef(x, y)[0, 1]) if (np.std(x) > 0 and np.std(y) > 0) else float("nan")

    return RegimeSplitCheck(
        vessel_class=vessel_class, first_half_n=first.height, second_half_n=second.height,
        first_half_r=_r(first), second_half_r=_r(second), split_date=split_date,
    )


#: Lags tested by `lead_lag_correlation`, in days. Negative = tightness LEADS rate
#: (today's tightness vs a future rate -- the "physical pressure predicts price"
#: story); positive = tightness LAGS rate (rate moves first, tightness responds --
#: consistent with the simultaneity/reverse-causation story `supplycurve`'s module
#: docstring already names). Also directly serves the P3 Validation requirement
#: ("lead/lag analysis"), not just the sign diagnosis.
LEAD_LAG_DAYS: Final[tuple[int, ...]] = (-30, -14, -7, 0, 7, 14, 30)


@dataclass(frozen=True)
class LeadLagCorrelation:
    vessel_class: VesselClass
    by_lag_days: dict[int, float]  # lag -> pearson r, negative lag = tightness leads
    n_obs: dict[int, int]

    @property
    def best_lag_days(self) -> int:
        """The lag with the strongest-magnitude correlation, ties broken toward 0
        (prefer the simplest/contemporaneous explanation)."""
        valid = {k: v for k, v in self.by_lag_days.items() if v == v}  # drop NaN
        if not valid:
            return 0
        return min(valid, key=lambda lag: (-abs(valid[lag]), abs(lag)))


def lead_lag_correlation(
    vessel_class: VesselClass, tightness_index: pl.DataFrame, lags_days: tuple[int, ...] = LEAD_LAG_DAYS
) -> LeadLagCorrelation:
    """Correlate tightness at date d against the real rate at date d + lag.

    Implemented as a date-shifted as-of join (not a row-shift) so it is correct
    regardless of the rate series' irregular ("weekly-ish") spacing: shifting the
    tightness index's own date column by -lag before the join is equivalent to
    reading the rate lag days after each tightness observation.
    """
    tight_base = tightness_index.filter(pl.col("vessel_class") == vessel_class.value).sort("date")
    rate = _load_real_rate(vessel_class)
    if tight_base.is_empty() or rate.is_empty():
        raise NoOverlapError(f"No tightness/rate data available for {vessel_class}.")

    by_lag: dict[int, float] = {}
    n_obs: dict[int, int] = {}
    for lag in lags_days:
        shifted = tight_base.with_columns((pl.col("date") + pl.duration(days=lag)).alias("date"))
        joined = rate.join_asof(shifted, on="date", strategy="backward").drop_nulls(["tightness"])
        x, y = joined["tightness"].to_numpy(), joined["rate_usd_day"].to_numpy()
        n_obs[lag] = joined.height
        if joined.height < 5 or np.std(x) == 0 or np.std(y) == 0:
            by_lag[lag] = float("nan")
        else:
            by_lag[lag] = float(np.corrcoef(x, y)[0, 1])
    return LeadLagCorrelation(vessel_class=vessel_class, by_lag_days=by_lag, n_obs=n_obs)


def build_basin_tightness_index(result: StockflowResult) -> pl.DataFrame:
    """Basin-preserving sibling of `build_tightness_index` -- tests the
    *aggregation-level* explanation (does pooling all three basins into one global
    tightness number wash out a real per-basin signal). A separate function, not a
    parameter on the existing one: `build_tightness_index`'s pooled output already
    has real callers (`fit_supply_curve`, tests) whose behaviour must not change."""
    stock = (
        result.frame.group_by(["date", "basin", "vessel_class"])
        .agg(pl.col("stock_dwt").sum().alias("total_stock_dwt"))
        .sort(["vessel_class", "basin", "date"])
    )
    flows = (
        _class_attributed_flows()
        .group_by(["date", "basin", "vessel_class"])
        .agg(pl.col("outflow_t").sum().alias("total_outflow_t"))
        .sort(["vessel_class", "basin", "date"])
    )
    out_frames = []
    for cls in VesselClass:
        for b in flows["basin"].unique().to_list():
            f = flows.filter((pl.col("vessel_class") == cls.value) & (pl.col("basin") == b)).sort("date")
            if f.is_empty():
                continue
            f = f.with_columns(
                pl.col("total_outflow_t").rolling_mean(window_size=FLOW_SMOOTHING_DAYS, min_samples=1)
                .alias("smoothed_outflow_t")
            )
            s = stock.filter((pl.col("vessel_class") == cls.value) & (pl.col("basin") == b)).sort("date")
            joined = f.join(s, on=["date", "basin", "vessel_class"], how="inner").filter(pl.col("total_stock_dwt") > 0)
            joined = joined.with_columns((pl.col("smoothed_outflow_t") / pl.col("total_stock_dwt")).alias("tightness"))
            out_frames.append(joined.select("date", "basin", "vessel_class", "tightness"))
    return pl.concat(out_frames) if out_frames else pl.DataFrame(
        schema={"date": pl.Date, "basin": pl.Utf8, "vessel_class": pl.Utf8, "tightness": pl.Float64}
    )


@dataclass(frozen=True)
class BasinAggregationCheck:
    vessel_class: VesselClass
    pooled_r: float
    by_basin_r: dict[str, float]
    by_basin_n: dict[str, int]

    @property
    def best_basin_beats_pooled(self) -> bool:
        valid = {b: r for b, r in self.by_basin_r.items() if r == r}
        if not valid:
            return False
        return max(abs(r) for r in valid.values()) > abs(self.pooled_r) + 0.05  # small margin, not noise


def basin_aggregation_diagnostic(
    vessel_class: VesselClass, tightness_index: pl.DataFrame, basin_tightness_index: pl.DataFrame
) -> BasinAggregationCheck:
    pooled = _joined_tightness_rate(vessel_class, tightness_index)
    pooled_r = float(np.corrcoef(pooled["tightness"].to_numpy(), pooled["rate_usd_day"].to_numpy())[0, 1])
    rate = _load_real_rate(vessel_class)
    by_basin_r: dict[str, float] = {}
    by_basin_n: dict[str, int] = {}
    sub = basin_tightness_index.filter(pl.col("vessel_class") == vessel_class.value)
    for b in sub["basin"].unique().to_list():
        bt = sub.filter(pl.col("basin") == b).sort("date")
        joined = rate.join_asof(bt, on="date", strategy="backward").drop_nulls(["tightness"])
        by_basin_n[b] = joined.height
        x, y = joined["tightness"].to_numpy(), joined["rate_usd_day"].to_numpy()
        by_basin_r[b] = float(np.corrcoef(x, y)[0, 1]) if (joined.height >= 5 and np.std(x) > 0 and np.std(y) > 0) else float("nan")
    return BasinAggregationCheck(vessel_class=vessel_class, pooled_r=pooled_r, by_basin_r=by_basin_r, by_basin_n=by_basin_n)


@dataclass(frozen=True)
class TargetTransformCheck:
    """Tests the *target-transformation* explanation: dry-bulk TC rates are
    right-skewed (spikes far above baseline more often than symmetric troughs
    below it) -- a log transform is the standard remedy, and a level correlation
    computed on raw USD/day can differ from one computed on log(USD/day)."""

    vessel_class: VesselClass
    pearson_r_raw: float
    pearson_r_log: float


def target_transform_diagnostic(vessel_class: VesselClass, tightness_index: pl.DataFrame) -> TargetTransformCheck:
    joined = _joined_tightness_rate(vessel_class, tightness_index)
    x = joined["tightness"].to_numpy()
    y = joined["rate_usd_day"].to_numpy()
    r_raw = float(np.corrcoef(x, y)[0, 1])
    y_log = np.log(y)
    r_log = float(np.corrcoef(x, y_log)[0, 1]) if np.std(y_log) > 0 else float("nan")
    return TargetTransformCheck(vessel_class=vessel_class, pearson_r_raw=r_raw, pearson_r_log=r_log)
