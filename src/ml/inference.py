"""Inference API for the exported XGBoost models."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Final

import polars as pl
import xgboost as xgb

from ml.baselines import CLASS_CODES, QUANTILES

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
MODELS_DIR: Final[Path] = REPO_ROOT / "src" / "data" / "models"


class FreightPredictor:
    """Wrapper to load and run predictions with the exported XGBoost models."""
    
    def __init__(self, h: int) -> None:
        self.h = h
        self.model_path = MODELS_DIR / f"xgb_h{h}.ubj"
        self.meta_path = MODELS_DIR / f"xgb_h{h}_features.json"
        
        if not self.model_path.exists() or not self.meta_path.exists():
            raise FileNotFoundError(
                f"Model or metadata for h={h} not found in {MODELS_DIR}. "
                "Run `uv run export-models` first."
            )
        
        self.booster = xgb.Booster()
        self.booster.load_model(self.model_path)
        
        with open(self.meta_path, "r") as f:
            self.feature_names = json.load(f)
            
        self.feature_types = ["c" if n == "target_class" else "q" for n in self.feature_names]

    def predict(self, df: pl.DataFrame) -> pl.DataFrame:
        """
        Predict log returns and add back to `log_value` to get absolute predictions.
        df must contain `target_class`, `date`, `log_value`, and all required features.
        """
        # Ensure target_class is properly encoded to integers
        if df["target_class"].dtype == pl.String:
            df_encoded = df.with_columns(
                pl.col("target_class").replace_strict(CLASS_CODES).alias("target_class")
            )
        else:
            df_encoded = df
            
        # Extract features in the exact order expected by the model
        try:
            x_mat = df_encoded.select(self.feature_names).to_numpy()
        except pl.exceptions.ColumnNotFoundError as e:
            raise ValueError(f"Missing required features for h={self.h} prediction: {e}")
            
        dmatrix = xgb.DMatrix(
            x_mat, 
            feature_names=self.feature_names, 
            feature_types=self.feature_types
        )
        
        preds = self.booster.predict(dmatrix)
        
        # Format the predictions similar to the training outputs
        if len(QUANTILES) == 1:
            preds = preds.reshape(-1, 1)
            
        log_val = df["log_value"].to_numpy()
        
        exprs = [
            pl.col("date"), 
            pl.col("target_class"),
            pl.lit(self.h).cast(pl.Int64).alias("h")
        ]
        
        for i, q in enumerate(QUANTILES):
            p_q = log_val + preds[:, i]
            exprs.append(pl.lit(p_q).alias(f"p_{q}"))
            
        return df.select(*exprs)
