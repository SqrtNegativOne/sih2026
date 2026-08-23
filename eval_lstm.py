import polars as pl
from sih.baselines import load_split, evaluate, CLASSES
from sih.model_lstm import predict

def run_eval():
    print("Loading data...")
    splits = {name: load_split(name) for name in ("train", "valid")}
    
    for h in [7, 30, 90]:
        print(f"\n--- Horizon {h} ---")
        preds = predict(splits["train"], {"valid": splits["valid"]}, h=h)
        metrics = evaluate(preds["valid"], splits["valid"], "valid", "lstm")
        
        pooled = [m for m in metrics if m["scope"] == "POOLED"][0]
        print(f"h={h} POOLED pinball_0.5: {pooled['pinball_0.5']:.4f}, dir_hit: {pooled.get('dir_hit', 0):.2%}")
        
        for cls in CLASSES:
            cls_met = [m for m in metrics if m["scope"] == cls]
            if cls_met:
                print(f"  {cls} pinball_0.5: {cls_met[0]['pinball_0.5']:.4f}")

if __name__ == "__main__":
    run_eval()
