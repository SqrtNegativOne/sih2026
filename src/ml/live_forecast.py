"""Real, callable forecast loading -- promoted out of ``run_live_scenario.py``.

That demo script's ``_build_feature_table``/``forecast_all_classes`` were the
one place this whole codebase actually turns "today's real Baltic/PortWatch
history on disk" into a real forecast fan via the trained XGBoost models --
proven correct by the demo, but living as private, copy-pastable functions
inside a script rather than an importable module. That is exactly the gap the
final-lap sub-plan calls out: a caller who wants a real quote (``opt.quote``,
the FastAPI backend) needs this logic as a real function, not a script they'd
have to re-derive. Moved here verbatim (no behaviour change), with
``run_live_scenario.py`` now importing it instead of defining its own copy.
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Final

import polars as pl

from ml import units
from ml.baselines import QUANTILES
from ml.features import (
    CongestionFeature,
    CrossSeriesFeature,
    LagsFeature,
    ReturnsFeature,
    RollingFeature,
    add_calendar_features,
    calendar_feature_names,
)
from ml.inference import FreightPredictor
from ml.quantiles import repair_crossed_quantiles
from opt.types import ForecastFan, VesselClass

__all__ = ["default_master_path", "forecast_all_classes", "latest_available_date", "resolve_as_of"]

_REPO_ROOT = Path(__file__).resolve().parents[2]

CLASS_INDEX_COL: dict[VesselClass, str] = {
    VesselClass.CAPESIZE: "BC_INDEX",
    VesselClass.PANAMAX: "BPI_INDEX",
    VesselClass.SUPRAMAX: "BSI_INDEX",
    VesselClass.HANDYSIZE: "BHSI_INDEX",
}
ALL_INDEX_COLS = list(CLASS_INDEX_COL.values())

CNY_DATES = [
    "2020-01-25", "2021-02-12", "2022-02-01", "2023-01-22", "2024-02-10",
    "2025-01-29", "2026-02-17", "2027-02-06", "2028-01-26", "2029-02-13", "2030-02-03",
]
CONGESTION_ORIGIN_PORTS = [
    "NEWCASTLE_AU", "HAY_POINT_AU", "RICHARDS_BAY_ZA",
    "BEIRA_MZ", "MAPUTO_MZ", "NACALA_MZ", "BALIKPAPAN_ID", "SAMARINDA_ID",
]
CONGESTION_DEST_PORTS = ["PARADIP", "VISAKHAPATNAM", "GOPALPUR", "DHAMRA", "HALDIA", "KOLKATA"]
LAGS = [1, 2, 3, 5, 10, 21, 63]
WINDOWS = [30, 90]
RETURNS = [1, 7, 30]
HORIZONS = (7, 30, 90)


def default_master_path() -> Path:
    return _REPO_ROOT / "src" / "data" / "master_long.parquet"


def latest_available_date(master_path: Path | None = None) -> date:
    """The most recent date the real Baltic Capesize index has on disk --
    the same "today" every demo script and now ``opt.quote`` treats as "now"
    when the caller doesn't supply an explicit ``as_of``."""
    master = pl.read_parquet(master_path or default_master_path())
    return master.filter(pl.col("series_id") == "BC_INDEX")["date"].max()


#: How far back resolve_as_of() will look for a real trading day. Wide
#: enough to cross any real weekend or holiday cluster (the widest real gap
#: seen in the actual index history is about a week around major holidays),
#: narrow enough that a date genuinely far outside the data's real range
#: (a typo, or a deliberate "what if" scenario years out) is NOT silently
#: reinterpreted as "today" -- see the far-future carve-out below.
_MAX_BACKWARD_LOOKBACK_DAYS: Final[int] = 10


def resolve_as_of(requested: date, master_path: Path | None = None) -> date:
    """The most recent real trading day on or before ``requested``, within
    a bounded lookback -- a backward as-of resolution, the same convention
    every other as-of join in this codebase already uses
    (``ml.units.fit_unit_map``, ``opt.basis``, ``opt.landed_cost``'s macro
    lookups, ...).

    F-15 fix: callers that pass an explicit ``as_of`` used to need it to be
    an EXACT match to a real trading day in the data -- a weekend, a
    market holiday, or any other real gap day returned a 503
    "No real TC quote/forecast" with no explanation of why. Verified live:
    roughly one date in three that a plain date-picker would happily let a
    user select failed outright. This resolves the request the same way
    the rest of the system already treats "as of" everywhere else: the
    latest real data on or before that date, not an exact-match lookup.

    Deliberately bounded, not an unconditional "nearest real date, however
    far back": a request for a date genuinely outside the data's real
    range (years in the future, or long before the data starts) must
    still surface as missing data, not be silently reinterpreted as
    "today" -- that would misreport what was actually priced. Returns
    ``requested`` unchanged when nothing real falls within
    ``_MAX_BACKWARD_LOOKBACK_DAYS`` of it, so the caller's own downstream
    "insufficient data" handling (an exact-match lookup that then finds
    nothing) still fires exactly as before for those genuinely out-of-range
    dates.
    """
    master = pl.read_parquet(master_path or default_master_path())
    dates = master.filter(
        (pl.col("series_id") == "BC_INDEX")
        & (pl.col("date") <= requested)
        & (pl.col("date") >= requested - timedelta(days=_MAX_BACKWARD_LOOKBACK_DAYS))
    )["date"]
    if dates.is_empty():
        return requested
    return dates.max()


def _build_feature_table(master: pl.DataFrame) -> pl.DataFrame:
    """Real Baltic/PortWatch history -> the exact feature table build_samples.py
    builds for training, minus the target columns (which don't exist yet for
    today -- that's the whole point of forecasting)."""
    wide = master.pivot(values="value", index="date", on="series_id").sort("date")
    wide = add_calendar_features(wide, CNY_DATES)
    congestion = CongestionFeature(CONGESTION_ORIGIN_PORTS, CONGESTION_DEST_PORTS)
    wide = congestion.transform(wide)

    per_class_rows = []
    for cls, index_col in CLASS_INDEX_COL.items():
        if index_col not in wide.columns:
            continue
        df = wide.select(
            "date", pl.lit(cls.value).alias("target_class"), pl.col(index_col).alias("target_value")
        ).drop_nulls(subset=["target_value"]).sort("date")
        df = df.with_columns(pl.col("target_value").log().alias("log_value"))
        df = LagsFeature(LAGS).transform(df)
        df = RollingFeature(WINDOWS).transform(df)
        df = ReturnsFeature(RETURNS).transform(df)

        other = [c for c in ALL_INDEX_COLS if c != index_col]
        cross = CrossSeriesFeature(bdi_col="BD_INDEX", other_classes=other)
        wide_cross = cross.transform(wide)

        join_cols = ["date"] + calendar_feature_names() + congestion.feature_names_out() + cross.feature_names_out()
        df = df.join(wide_cross.select(join_cols), on="date", how="left")

        # y_slip_h{7,30,90}: only ever populated once the future has actually
        # happened. Today's row has no future yet -- that IS what we're forecasting
        # -- so these are genuinely unknown, not a bug to work around.
        df = df.with_columns([pl.lit(None, dtype=pl.Float64).alias(f"y_slip_h{h}") for h in HORIZONS])
        per_class_rows.append(df)

    return pl.concat(per_class_rows, how="diagonal").sort(["date", "target_class"])


def forecast_all_classes(
    as_of: date, master_path: Path | None = None
) -> tuple[dict[VesselClass, list[ForecastFan]], dict[VesselClass, float]]:
    """Real forecast fans + real today's TC quote per class, entirely computed
    from the trained models and real market history on disk -- nothing here
    is user-supplied or hand-typed. A class is silently absent from either
    dict when the real data needed for it (a feature row at ``as_of``, a real
    TC quote at ``as_of``, enough unit-map overlap) doesn't exist -- callers
    must not assume every ``VesselClass`` key is present.
    """
    path = master_path or default_master_path()
    master = pl.read_parquet(path)
    features = _build_feature_table(master)

    fans: dict[VesselClass, list[ForecastFan]] = {c: [] for c in VesselClass}
    quotes: dict[VesselClass, float] = {}

    predictors = {h: FreightPredictor(h) for h in HORIZONS}

    for cls in VesselClass:
        row = features.filter((pl.col("target_class") == cls.value) & (pl.col("date") == as_of))
        if row.is_empty():
            continue  # no data for this class at this date -- skip, don't fabricate

        tc_id = units.CLASS_SERIES[cls.value][1]
        tc_row = master.filter((pl.col("series_id") == tc_id) & (pl.col("date") == as_of))
        if tc_row.is_empty():
            continue  # no real TC quote for this class at this date -- skip, don't invent one
        tc_now = float(tc_row["value"][0])
        quotes[cls] = tc_now

        try:
            unit_map = units.fit_unit_map(master, cls.value, asof=as_of)
        except units.InsufficientOverlapError:
            continue

        log_value_now = float(row["log_value"][0])
        for h in HORIZONS:
            preds = predictors[h].predict(row)  # real XGBoost model, real features
            p_log = {q: float(preds[f"p_{q}"][0]) for q in QUANTILES}
            usd = {q: unit_map.project_return(tc_now, p_log[q] - log_value_now) for q in QUANTILES}
            triple = repair_crossed_quantiles(usd[0.1], usd[0.5], usd[0.9])
            fans[cls].append(
                ForecastFan(vessel_class=cls, horizon_days=h, p10=triple.p10, p50=triple.p50, p90=triple.p90)
            )

    return fans, quotes
