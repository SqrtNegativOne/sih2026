"""Build the long-format master table from all collected raw sources.

Reads four source families from data_raw/:
  - investing_com/*.csv   (index points, daily, deep but partly frozen)
  - handybulk_index_levels.csv (index points + TC averages in usd/day)
  - portwatch/*.csv       (daily dry bulk port calls and estimated tonnage per port)
  - signal_weekly/*.extraction.csv (weekly route-level assessments)

Emits a single tidy table with columns [series_id, date, value, unit, source]
to data/master_long.parquet, plus overlap cross-checks in the log.

Run with `uv run build-master` from the repository root.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Final

import polars as pl

LOGGER: Final[logging.Logger] = logging.getLogger(__name__)

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
DATA_RAW: Final[Path] = REPO_ROOT / "raw_data"
DATA_OUT: Final[Path] = REPO_ROOT / "src" / "data"

SOURCE_RANK: Final[dict[str, int]] = {"handybulk": 2, "investing": 1}
INVESTING_SERIES: Final[dict[str, str]] = {
    "dry": "BD_INDEX",
    "capesize": "BC_INDEX",
    "panamax": "BPI_INDEX",
    "supramax": "BSI_INDEX",
    "handysize": "BHSI_INDEX",
}

MASTER_SCHEMA: Final[dict[str, pl.DataType]] = {
    "series_id": pl.String(),
    "date": pl.Date(),
    "value": pl.Float64(),
    "unit": pl.String(),
    "source": pl.String(),
}


def _setup_logging() -> None:
    """Configure root logging once for CLI runs."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def read_investing_dir(path: Path) -> pl.DataFrame:
    """Parse every investing.com export into long rows keyed by series id.

    Files carry US-style dates (mm/dd/yyyy) and quoted prices with thousands
    separators; series identity comes from filename substrings so chunked
    exports (bdi_1.csv etc.) are picked up automatically.
    """
    frames: list[pl.DataFrame] = []
    for file in sorted(path.glob("*.csv")):
        match = next((v for k, v in INVESTING_SERIES.items() if k in file.stem.lower()), None)
        if match is None:
            LOGGER.warning(f"skipping unrecognized investing file: {file.name}")
            continue
        raw = pl.read_csv(
            file,
            schema_overrides={"Date": pl.String, "Price": pl.String},
            columns=["Date", "Price"],
        )
        out = (
            raw.with_columns(
                pl.col("Date").str.strptime(pl.Date, "%m/%d/%Y").alias("date"),
                pl.col("Price").str.replace_all(",", "").cast(pl.Float64).alias("value"),
            )
            .with_columns(
                pl.lit(match).alias("series_id"),
                pl.lit("index_pts").alias("unit"),
                pl.lit("investing").alias("source"),
            )
            .select(list(MASTER_SCHEMA))
        )
        n_bad = out.filter(pl.col("date").is_null() | pl.col("value").is_null()).height
        if n_bad:
            LOGGER.warning(f"{file.name}: dropped {n_bad} unparseable rows")
        frames.append(out.drop_nulls())
        LOGGER.info(f"{file.name}: {out.height} rows -> {match}")
    return pl.concat(frames) if frames else pl.DataFrame(schema=MASTER_SCHEMA)


def read_handybulk(path: Path) -> pl.DataFrame:
    """Melt the HandyBulk wide extract into index-point and TC-average series."""
    wide = pl.read_csv(path, schema_overrides={c: pl.Float64 for c in [
        "bdi", "bci", "bpi", "bsi", "bhsi",
        "capesize_tc_avg_usd_day", "panamax_tc_avg_usd_day",
        "supramax_tc_avg_usd_day", "handysize_tc_avg_usd_day",
    ]})
    mapping: Final[dict[str, tuple[str, str]]] = {
        "bdi": ("BD_INDEX", "index_pts"),
        "bci": ("BC_INDEX", "index_pts"),
        "bpi": ("BPI_INDEX", "index_pts"),
        "bsi": ("BSI_INDEX", "index_pts"),
        "bhsi": ("BHSI_INDEX", "index_pts"),
        "capesize_tc_avg_usd_day": ("CAPESIZE_TCAVG", "usd/day"),
        "panamax_tc_avg_usd_day": ("PANAMAX_TCAVG", "usd/day"),
        "supramax_tc_avg_usd_day": ("SUPRAMAX_TCAVG", "usd/day"),
        "handysize_tc_avg_usd_day": ("HANDYSIZE_TCAVG", "usd/day"),
    }
    frames: list[pl.DataFrame] = []
    for col, (series_id, unit) in mapping.items():
        frames.append(
            wide.select(
                pl.lit(series_id).alias("series_id"),
                pl.col("date").str.to_date("%Y-%m-%d"),
                pl.col(col).alias("value"),
                pl.lit(unit).alias("unit"),
                pl.lit("handybulk").alias("source"),
            ).drop_nulls()
        )
    df = pl.concat(frames)
    LOGGER.info(f"handybulk: {df.height} rows across {df['series_id'].n_unique()} series")
    return df


def read_portwatch_dir(path: Path) -> pl.DataFrame:
    """Extract daily dry bulk calls and tonnage estimates per port."""
    skip = {"ports_index.csv", "pull_summary.csv"}
    frames: list[pl.DataFrame] = []
    for file in sorted(path.glob("*_daily_portcalls.csv")):
        if file.name.lower() in skip:
            continue
        raw = pl.read_csv(
            file,
            schema_overrides={
                "date": pl.String,
                "portcalls_dry_bulk": pl.Int64,
                "import_dry_bulk": pl.Float64,
                "export_dry_bulk": pl.Float64,
            },
        )
        slug = re.sub(r"[^A-Z0-9]+", "_", file.stem.split("_daily")[0].upper()).strip("_")
        keep = raw.select(
            pl.col("date").str.to_date("%Y-%m-%d").alias("date"),
            pl.col("portcalls_dry_bulk").cast(pl.Float64).alias("calls"),
            pl.col("import_dry_bulk").alias("imp"),
            pl.col("export_dry_bulk").alias("exp"),
        )
        parts: list[pl.DataFrame] = []
        for col, sid, unit in (
            ("calls", f"PW_{slug}_CALLS", "calls"),
            ("imp", f"PW_{slug}_IMPORT_T", "mt"),
            ("exp", f"PW_{slug}_EXPORT_T", "mt"),
        ):
            parts.append(
                keep.select(
                    pl.lit(sid).alias("series_id"),
                    "date",
                    pl.col(col).cast(pl.Float64).alias("value"),
                    pl.lit(unit).alias("unit"),
                    pl.lit("portwatch").alias("source"),
                ).drop_nulls()
            )
        df = pl.concat(parts)
        frames.append(df)
        LOGGER.info(f"{file.name}: {df.height} rows -> PW_{slug}_*")
    return pl.concat(frames) if frames else pl.DataFrame(schema=MASTER_SCHEMA)


def read_signal_dir(path: Path) -> pl.DataFrame:
    """Load best-effort Signal weekly route extractions as sparse anchor rows."""
    frames: list[pl.DataFrame] = []
    for file in sorted(path.glob("*.extraction.csv")):
        raw = pl.read_csv(file, schema_overrides={"value": pl.String}, truncate_ragged_lines=True)
        unit_tag = (
            pl.when(pl.col("unit").str.to_lowercase().str.contains("day"))
            .then(pl.lit("USD_DAY"))
            .otherwise(pl.lit("USD_T"))
        )
        out = (
            raw.with_columns(
                pl.col("week_end_date").str.to_date("%Y-%m-%d").alias("date"),
                pl.col("value").str.replace_all(r"[,$]", "").cast(pl.Float64, strict=False).alias("value_num"),
                (
                    pl.lit("SG_")
                    + pl.col("route_code").str.to_uppercase().str.replace_all(r"[^A-Z0-9]+", "_")
                    + pl.lit("_")
                    + unit_tag
                ).alias("series_id"),
            )
            .with_columns(pl.lit("signal").alias("source"))
            .select(
                pl.col("series_id"), pl.col("date"), pl.col("value_num").alias("value"),
                pl.col("unit").str.to_lowercase().alias("unit"), pl.col("source"),
            )
            .drop_nulls()
        )
        n_drop = raw.height - out.height
        if n_drop:
            LOGGER.info(f"{file.name}: {n_drop} rows without numeric values dropped")
        frames.append(out)
    df = pl.concat(frames) if frames else pl.DataFrame(schema=MASTER_SCHEMA)
    LOGGER.info(f"signal: {df.height} anchor rows total")
    return df


CONFLICT_TOLERANCE: Final[float] = 0.20


def dedupe(master: pl.DataFrame) -> pl.DataFrame:
    """Collapse duplicate (series_id, date) keeping the highest-ranked source.

    Dates where multiple sources disagree beyond CONFLICT_TOLERANCE are
    dropped entirely rather than silently trusted to either side.
    """
    ranked = master.with_columns(
        pl.col("source").replace(SOURCE_RANK, default=0).alias("_rank")
    ).sort(["series_id", "date", "_rank"], descending=[False, False, True])
    spread = (
        master.group_by(["series_id", "date"])
        .agg(pl.col("value").max().alias("_hi"), pl.col("value").min().alias("_lo"), pl.len().alias("_n"))
        .filter(
            (pl.col("_n") > 1)
            & ((pl.col("_hi") - pl.col("_lo")).abs() / pl.col("_hi") > CONFLICT_TOLERANCE)
        )
        .select(["series_id", "date"])
    )
    if not spread.is_empty():
        LOGGER.warning(f"dropping {spread.height} conflicting dates: {spread.rows()}")
        ranked = ranked.join(spread, on=["series_id", "date"], how="anti")
    return ranked.unique(subset=["series_id", "date"], keep="first").drop("_rank")


def crosscheck(master: pl.DataFrame, series_ids: list[str]) -> None:
    """Log max absolute disagreement between sources on shared series dates."""
    for sid in series_ids:
        sub = master.filter(pl.col("series_id") == sid)
        pivots = {
            src: sub.filter(pl.col("source") == src).select("date", "value").rename({"value": src})
            for src in sub["source"].unique().to_list()
        }
        if len(pivots) < 2:
            continue
        names = list(pivots)
        joined = pivots[names[0]].join(pivots[names[1]], on="date", how="inner")
        if joined.is_empty():
            LOGGER.info(f"crosscheck {sid}: no overlapping dates between {names}")
            continue
        diff = joined.select((pl.col(names[0]) - pl.col(names[1])).abs().max()).item()
        LOGGER.info(f"crosscheck {sid}: max |diff| {names[0]} vs {names[1]} = {diff}")


def main() -> None:
    """Assemble every raw source into data/master_long.parquet."""
    _setup_logging()
    master_raw = pl.concat(
        [
            read_investing_dir(DATA_RAW / "investing_com"),
            read_handybulk(DATA_RAW / "handybulk_index_levels.csv"),
            read_portwatch_dir(DATA_RAW / "portwatch"),
            read_signal_dir(DATA_RAW / "signal_weekly"),
        ],
        how="vertical_relaxed",
    )
    master = dedupe(master_raw).sort(["series_id", "date"])
    DATA_OUT.mkdir(parents=True, exist_ok=True)
    out_path = DATA_OUT / "master_long.parquet"
    master.write_parquet(out_path)
    LOGGER.info(f"wrote {out_path} ({master.height} rows)")
    summary = master.group_by("series_id").agg(
        pl.len().alias("rows"),
        pl.col("date").min().alias("first"),
        pl.col("date").max().alias("last"),
    ).sort("series_id")
    summary.write_csv(DATA_OUT / "master_summary.csv")
    LOGGER.info(f"\n{summary}")
    crosscheck(master_raw, ["BD_INDEX", "BC_INDEX"])


if __name__ == "__main__":
    main()
