from __future__ import annotations

import polars as pl


class ReturnsFeature:
    """Compute returns over different horizons."""

    def __init__(self, horizons: list[int], col_name: str = "log_value", prefix: str = "ret"):
        self.horizons = horizons
        self.col_name = col_name
        self.prefix = prefix

    def transform(self, df: pl.DataFrame) -> pl.DataFrame:
        """Apply return calculations to the dataframe."""
        exprs = [
            (pl.col(self.col_name) - pl.col(self.col_name).shift(h)).alias(f"{self.prefix}_{h}")
            for h in self.horizons
        ]
        return df.with_columns(exprs)

    def feature_names_out(self) -> list[str]:
        """Return the names of the generated features."""
        return [f"{self.prefix}_{h}" for h in self.horizons]
