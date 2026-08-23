import polars as pl
from sih.baselines import load_split, evaluate, HORIZONS
from sih.model_lgbm_tuned import predict
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
)
LOGGER = logging.getLogger(__name__)

def main():
    splits = {name: load_split(name) for name in ("train", "valid", "test")}
    all_rows = []
    
    for h in HORIZONS:
        out = predict(splits["train"], {"valid": splits["valid"]}, h)
        for split_name, pred in out.items():
            all_rows.extend(evaluate(pred, splits[split_name], split_name, "lgbm_tuned"))
            
    metrics = pl.DataFrame(all_rows).sort(["split", "h", "model", "scope"])
    
    pooled_valid = metrics.filter(
        (pl.col("split") == "valid") & (pl.col("scope") == "POOLED")
    ).select("h", "model", "pinball_0.5", "mae_p50", "dir_hit")
    
    LOGGER.info(f"\n{pooled_valid}")

if __name__ == "__main__":
    main()
