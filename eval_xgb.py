from sih.baselines import load_split, evaluate
from sih.model_xgb import predict
import polars as pl

def main():
    splits = {name: load_split(name) for name in ("train", "valid", "test")}
    
    for h in (7, 30, 90):
        print(f"\n--- Evaluation for h={h} ---")
        preds = predict(splits["train"], {"valid": splits["valid"]}, h)
        
        rows = evaluate(preds["valid"], splits["valid"], "valid", "xgb")
        
        metrics = pl.DataFrame(rows).filter(pl.col("scope") == "POOLED")
        print(metrics.select("h", "model", "pinball_0.5", "mae_p50", "dir_hit").to_dicts())

if __name__ == "__main__":
    main()
