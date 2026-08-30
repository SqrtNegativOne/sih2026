from __future__ import annotations

from datetime import date, timedelta

import polars as pl


def add_calendar_features(df: pl.DataFrame, cny_dates: list[str]) -> pl.DataFrame:
    """Add calendar based features to the dataframe."""
    cny_parsed = [date.fromisoformat(d) for d in cny_dates]
    
    df = df.with_columns([
        pl.col("date").dt.weekday().alias("dayofweek"),
        pl.col("date").dt.month().alias("month"),
        pl.col("date").dt.week().alias("weekofyear"),
    ])
    
    df = df.with_columns([
        pl.when(pl.col("month") <= 3).then(4)
          .when(pl.col("month") <= 6).then(1)
          .when(pl.col("month") <= 9).then(2)
          .otherwise(3).alias("indian_fiscal_quarter"),
        
        pl.col("month").is_between(6, 9).cast(pl.Int32).alias("is_monsoon"),
        pl.col("month").is_between(10, 12).cast(pl.Int32).alias("is_cyclone_season"),
    ])
    
    cny_window_dates = set()
    for d in cny_parsed:
        for offset in range(-15, 16):
            cny_window_dates.add(d + timedelta(days=offset))
            
    cny_series = pl.Series("cny_dates", list(cny_window_dates), dtype=pl.Date)
    # P7: polars 1.x deprecated bare is_in(Series) as ambiguous (it now reads
    # as a possible row-wise comparison, not "is this date in this whole
    # set") -- .implode() makes the broadcast-membership intent explicit
    # again, exactly as the deprecation warning itself recommends. Verified
    # directly against real master_long dates: identical is_cny_window
    # output before and after this change (see P7 completion report).
    df = df.with_columns(
        pl.col("date").is_in(cny_series.implode()).cast(pl.Int32).alias("is_cny_window")
    )
    
    return df

def calendar_feature_names() -> list[str]:
    """Return the names of the generated calendar features."""
    return [
        "dayofweek", "month", "weekofyear", "indian_fiscal_quarter",
        "is_monsoon", "is_cyclone_season", "is_cny_window"
    ]
