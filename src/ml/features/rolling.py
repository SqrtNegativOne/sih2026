from __future__ import annotations

import polars as pl


class RollingFeature:
    """Compute rolling mean, std, and z-score features."""

    def __init__(self, windows: list[int], col_name: str = "log_value"):
        self.windows = windows
        self.col_name = col_name

    def transform(self, df: pl.DataFrame) -> pl.DataFrame:
        """Apply rolling aggregations to the dataframe."""
        exprs = []
        for w in self.windows:
            mean_expr = pl.col(self.col_name).rolling_mean(window_size=w, min_samples=1)
            std_expr = pl.col(self.col_name).rolling_std(window_size=w, min_samples=1)
            
            exprs.extend([
                mean_expr.alias(f"rolling_mean_{w}"),
                std_expr.alias(f"rolling_std_{w}"),
                ((pl.col(self.col_name) - mean_expr) / (std_expr + 1e-9)).alias(f"rolling_zscore_{w}")
            ])
        return df.with_columns(exprs)

    def feature_names_out(self) -> list[str]:
        """Return the names of the generated features."""
        names = []
        for w in self.windows:
            names.extend([f"rolling_mean_{w}", f"rolling_std_{w}", f"rolling_zscore_{w}"])
        return names
