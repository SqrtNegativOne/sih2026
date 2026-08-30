"""Train and export the best model (XGBoost) for external use."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Final

import xgboost as xgb

from ml.baselines import load_split, make_matrix
from ml.model_xgb import XGB_PARAMS

LOGGER: Final[logging.Logger] = logging.getLogger(__name__)

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
DATA: Final[Path] = REPO_ROOT / "src" / "data"
MODELS_DIR: Final[Path] = DATA / "models"
HORIZONS: Final[tuple[int, ...]] = (7, 30, 90)


def _setup_logging() -> None:
    """Configure root logging once for CLI runs."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def main() -> None:
    """Train XGBoost on train set and export models to src/data/models."""
    _setup_logging()
    MODELS_DIR.mkdir(parents=True, exist_ok=True)

    train_df = load_split("train")
    valid_df = load_split("valid")

    # Combine train and valid for maximum data coverage
    # (or you could just train on `train_df` and early stop on `valid_df`)
    for h in HORIZONS:
        LOGGER.info(f"Training XGBoost model for h={h}...")
        
        train_h = train_df.drop_nulls([f"y_step_h{h}"])
        valid_h = valid_df.drop_nulls([f"y_step_h{h}"])
        
        names, x_tr = make_matrix(train_h, h)
        _, x_val = make_matrix(valid_h, h)
        
        y_tr = (train_h[f"y_step_h{h}"] - train_h["log_value"]).to_numpy()
        y_val = (valid_h[f"y_step_h{h}"] - valid_h["log_value"]).to_numpy()

        feature_types = ["c" if n == "target_class" else "q" for n in names]

        dtrain = xgb.DMatrix(x_tr, label=y_tr, feature_names=names, feature_types=feature_types)
        dval = xgb.DMatrix(x_val, label=y_val, feature_names=names, feature_types=feature_types)

        booster = xgb.train(
            XGB_PARAMS,
            dtrain,
            num_boost_round=2000,
            evals=[(dval, "val")],
            early_stopping_rounds=50,
            verbose_eval=False,
        )

        model_path = MODELS_DIR / f"xgb_h{h}.ubj"
        booster.save_model(model_path)
        LOGGER.info(f"Saved model to {model_path}")
        
        meta_path = MODELS_DIR / f"xgb_h{h}_features.json"
        with open(meta_path, "w") as f:
            json.dump(names, f)
        LOGGER.info(f"Saved feature metadata to {meta_path}")


if __name__ == "__main__":
    main()
