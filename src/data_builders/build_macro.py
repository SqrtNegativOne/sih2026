"""Build the macro/commodity signal table -- P4 requirement 4.

Reads three real, freely-licensed sources from raw_data/macro/ (see
raw_data/macro/sources.md for exact URLs, retrieval date, and license):

  - CMO-Historical-Data-Monthly.xlsx  (World Bank Pink Sheet: Brent crude,
    Australian coal, iron ore -- monthly)
  - fred_DEXINUS.csv                  (FRED: USD/INR, daily)
  - fred_INDPRO.csv                   (FRED: US industrial production, monthly)

Emits data/macro_long.parquet in the SAME [series_id, date, value, unit,
source] schema data_builders.build_master.MASTER_SCHEMA uses -- a familiar
shape, not a new convention -- but as a SEPARATE file, not appended into
master_long.parquet. master_long.parquet is an established, widely-consumed
artifact (every existing ml/ feature and opt/ signal reads it); adding new
series to it is riskier and less reviewable than a parallel file joined in
only at the one feature-transform (ml.features.macro) that consumes it.

**Leakage guard, not a formality**: World Bank Pink Sheet publishes monthly
averages a few days into the FOLLOWING month (this file's own "Updated on
August 04, 2026" note covers July 2026 -- July's average was not knowable
until August). Every monthly value here is therefore dated to the 1st of the
month AFTER the one it describes, never the month itself -- a row for
"2026-07" pricing is stamped 2026-08-01. This is intentionally conservative
(the true publication lag is a few days, not a full month-to-month-start
gap) rather than fitted to the exact lag, since the exact historical
publication date for every past month is not available in this file.
"""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Final

import openpyxl
import polars as pl

LOGGER: Final[logging.Logger] = logging.getLogger(__name__)

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
DATA_RAW: Final[Path] = REPO_ROOT / "raw_data" / "macro"
DATA_OUT: Final[Path] = REPO_ROOT / "src" / "data"

MACRO_SCHEMA: Final[dict[str, pl.DataType]] = {
    "series_id": pl.String(),
    "date": pl.Date(),
    "value": pl.Float64(),
    "unit": pl.String(),
    "source": pl.String(),
}

#: (workbook column header substring -> (series_id, unit)). Matched against
#: the real header row read from the file, not hardcoded column indices --
#: the Pink Sheet's column order has changed across vintages before (see
#: raw_data/sources.md's own note about route-vintage churn in
#: baltic_routes.csv), so matching by label is the more durable choice.
WORLDBANK_SERIES: Final[dict[str, tuple[str, str]]] = {
    "Crude oil, Brent": ("MACRO_BRENT_CRUDE", "usd_per_bbl"),
    "Coal, Australian": ("MACRO_COAL_AUSTRALIAN", "usd_per_mt"),
    # P6 correction: the Pink Sheet's own "Description" sheet states the SPOT
    # series (this one) is "US dollar/dry ton" -- dmtu (dry metric ton unit,
    # 1% Fe-unit) applies only to the separate CONTRACT series in "US
    # cents/dmtu", which this harvest does not read. Verified directly
    # against raw_data/macro/CMO-Historical-Data-Monthly.xlsx's Description
    # sheet, not assumed. Originally mislabeled usd_per_dmtu in P4; caught in
    # P6 while building src/opt/landed_cost.py, which is the first consumer
    # to branch on this field's actual unit string rather than treat value
    # as an opaque level. Both series are for a standard 62% Fe (post-2008)
    # / 63.5% Fe (pre-2008) benchmark grade -- an off-benchmark real cargo's
    # true price would differ, and this harvest has no grade-adjustment data
    # to correct for that; landed_cost.py discloses the benchmark-grade
    # caveat rather than attempting an unevidenced adjustment.
    "Iron ore, cfr spot": ("MACRO_IRON_ORE", "usd_per_mt"),
}

#: The earliest date any of this project's real training data covers
#: (samples_train.parquet / master_long.parquet both start well before this,
#: but nothing before it is ever used) -- rows older than this are dropped
#: rather than carried as dead weight.
EARLIEST_USEFUL_DATE: Final[date] = date(2010, 1, 1)


def _month_string_to_publish_date(month_str: str) -> date:
    """'2026M07' -> date(2026, 8, 1) -- the 1st of the FOLLOWING month (the
    leakage guard described in the module docstring)."""
    year, month = int(month_str[:4]), int(month_str[5:7])
    if month == 12:
        return date(year + 1, 1, 1)
    return date(year, month + 1, 1)


def read_worldbank_pink_sheet(path: Path) -> pl.DataFrame:
    """Parse the real 'Monthly Prices' sheet into long rows for the three
    series in WORLDBANK_SERIES."""
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb["Monthly Prices"]
    rows = list(ws.iter_rows(min_row=1, max_row=10, values_only=True))
    header_row = next(r for r in rows if r[1] is not None and isinstance(r[1], str))
    col_by_label: dict[str, int] = {label: i for i, label in enumerate(header_row) if label}

    missing = [label for label in WORLDBANK_SERIES if label not in col_by_label]
    if missing:
        raise ValueError(
            f"{path.name}: expected column(s) not found in the real header row: {missing}. "
            "The Pink Sheet's layout may have changed -- update WORLDBANK_SERIES."
        )

    header_row_idx = rows.index(header_row) + 1  # 1-indexed openpyxl row number
    data_rows = list(ws.iter_rows(min_row=header_row_idx + 2, values_only=True))  # skip the units row too

    out: dict[str, list] = {"series_id": [], "date": [], "value": [], "unit": [], "source": []}
    n_bad = 0
    for row in data_rows:
        month_str = row[0]
        if not isinstance(month_str, str) or "M" not in month_str:
            continue
        publish_date = _month_string_to_publish_date(month_str)
        if publish_date < EARLIEST_USEFUL_DATE:
            continue
        for label, (series_id, unit) in WORLDBANK_SERIES.items():
            raw_value = row[col_by_label[label]]
            if not isinstance(raw_value, (int, float)):
                n_bad += 1
                continue
            out["series_id"].append(series_id)
            out["date"].append(publish_date)
            out["value"].append(float(raw_value))
            out["unit"].append(unit)
            out["source"].append("worldbank_pink_sheet")
    if n_bad:
        LOGGER.info(f"{path.name}: {n_bad} non-numeric cells skipped (real gaps in the published series, e.g. '..')")
    df = pl.DataFrame(out, schema=MACRO_SCHEMA)
    LOGGER.info(f"{path.name}: {df.height} rows across {df['series_id'].n_unique()} series")
    return df


#: (fred CSV filename -> (series_id, unit)).
FRED_SERIES: Final[dict[str, tuple[str, str]]] = {
    "fred_DEXINUS.csv": ("MACRO_USD_INR", "inr_per_usd"),
    "fred_INDPRO.csv": ("MACRO_US_INDUSTRIAL_PRODUCTION", "index_2017_100"),
}


def read_fred_csv(path: Path, series_id: str, unit: str) -> pl.DataFrame:
    """FRED's fredgraph.csv export: columns [observation_date, <series_id>],
    '.' for a missing observation (holidays/no-release days)."""
    raw = pl.read_csv(path)
    value_col = raw.columns[1]
    out = (
        raw.select(
            pl.col("observation_date").str.to_date("%Y-%m-%d").alias("date"),
            pl.col(value_col).cast(pl.Float64, strict=False).alias("value"),
        )
        .drop_nulls()
        .filter(pl.col("date") >= EARLIEST_USEFUL_DATE)
        .with_columns(
            pl.lit(series_id).alias("series_id"),
            pl.lit(unit).alias("unit"),
            pl.lit("fred").alias("source"),
        )
        .select(list(MACRO_SCHEMA))
    )
    LOGGER.info(f"{path.name}: {out.height} rows -> {series_id}")
    return out


def _setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def main() -> None:
    """Assemble every real macro source into data/macro_long.parquet."""
    _setup_logging()
    frames = [read_worldbank_pink_sheet(DATA_RAW / "CMO-Historical-Data-Monthly.xlsx")]
    for filename, (series_id, unit) in FRED_SERIES.items():
        path = DATA_RAW / filename
        if path.exists():
            frames.append(read_fred_csv(path, series_id, unit))
        else:
            LOGGER.warning(f"{filename} not found, skipping {series_id}")

    macro = pl.concat(frames, how="vertical_relaxed").sort(["series_id", "date"])
    DATA_OUT.mkdir(parents=True, exist_ok=True)
    out_path = DATA_OUT / "macro_long.parquet"
    macro.write_parquet(out_path)
    LOGGER.info(f"wrote {out_path} ({macro.height} rows)")

    summary = macro.group_by("series_id").agg(
        pl.len().alias("rows"), pl.col("date").min().alias("first"), pl.col("date").max().alias("last"),
    ).sort("series_id")
    LOGGER.info(f"\n{summary}")


if __name__ == "__main__":
    main()
