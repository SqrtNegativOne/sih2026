from __future__ import annotations

import polars as pl


class CrossSeriesFeature:
    """Compute cross series features."""

    def __init__(self, bdi_col: str = "BD_INDEX", other_classes: list[str] | None = None):
        self.bdi_col = bdi_col
        self.other_classes = other_classes or []

    def transform(self, df: pl.DataFrame) -> pl.DataFrame:
        """Apply cross-series feature extraction."""
        exprs = []
        
        if self.bdi_col in df.columns:
            bdi_log = pl.col(self.bdi_col).log().alias("cross_BDI_log_level")
            exprs.append(bdi_log)
            exprs.append((pl.col(self.bdi_col).log() - pl.col(self.bdi_col).log().shift(1)).alias("cross_BDI_ret_1"))
            
        for cls in self.other_classes:
            if cls in df.columns:
                exprs.append(
                    (pl.col(cls).log() - pl.col(cls).log().shift(1)).alias(f"cross_{cls}_ret_1")
                )
                
        if exprs:
            return df.with_columns(exprs)
        return df

    def feature_names_out(self) -> list[str]:
        """Return the names of the generated features."""
        names = ["cross_BDI_log_level", "cross_BDI_ret_1"]
        names.extend([f"cross_{cls}_ret_1" for cls in self.other_classes])
        return names
