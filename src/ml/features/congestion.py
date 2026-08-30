from __future__ import annotations

import polars as pl


class CongestionFeature:
    """Compute congestion features using origin and destination port calls, exports, and imports."""

    def __init__(self, origin_ports: list[str], dest_ports: list[str]):
        self.origin_ports = origin_ports
        self.dest_ports = dest_ports

    def transform(self, df: pl.DataFrame) -> pl.DataFrame:
        """Apply congestion feature extraction."""
        origin_calls_cols = [f"PW_{p}_CALLS" for p in self.origin_ports]
        dest_calls_cols = [f"PW_{p}_CALLS" for p in self.dest_ports]
        origin_exports_cols = [f"PW_{p}_EXPORT_T" for p in self.origin_ports]
        dest_imports_cols = [f"PW_{p}_IMPORT_T" for p in self.dest_ports]

        def sum_cols(cols: list[str]) -> pl.Expr:
            existing = [c for c in cols if c in df.columns]
            if not existing:
                return pl.lit(0.0)
            return pl.sum_horizontal([pl.col(c).fill_null(0.0) for c in existing])

        exprs = [
            sum_cols(origin_calls_cols).alias("congestion_origin_calls"),
            sum_cols(dest_calls_cols).alias("congestion_dest_calls"),
            sum_cols(origin_exports_cols).log1p().alias("congestion_origin_exports_log1p"),
            sum_cols(dest_imports_cols).log1p().alias("congestion_dest_imports_log1p"),
        ]
        return df.with_columns(exprs)

    def feature_names_out(self) -> list[str]:
        """Return the names of the generated features."""
        return [
            "congestion_origin_calls",
            "congestion_dest_calls",
            "congestion_origin_exports_log1p",
            "congestion_dest_imports_log1p",
        ]
