from __future__ import annotations

import polars as pl


class LagsFeature:
    """Compute lagged features over a target column."""

    def __init__(self, lags: list[int], col_name: str = "log_value"):
        self.lags = lags
        self.col_name = col_name

    def transform(self, df: pl.DataFrame) -> pl.DataFrame:
        """Apply lag shifts to the dataframe."""
        exprs = [
            pl.col(self.col_name).shift(lag).alias(f"lag_{lag}")
            for lag in self.lags
        ]
        return df.with_columns(exprs)

    def feature_names_out(self) -> list[str]:
        """Return the names of the generated features."""
        return [f"lag_{lag}" for lag in self.lags]
