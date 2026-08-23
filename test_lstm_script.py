import polars as pl
from sih.baselines import load_split
from sih.model_lstm import predict

def test_lstm():
    print("Loading data...")
    train = load_split("train")
    valid = load_split("valid")
    
    # Run on a smaller subset of train for faster test
    print("Running predict...")
    out = predict(train, {"valid": valid.head(500)}, h=7)
    
    print("Output dict keys:", out.keys())
    print("Valid predictions shape:", out["valid"].shape)
    print("Valid head:")
    print(out["valid"].head())

if __name__ == "__main__":
    test_lstm()
