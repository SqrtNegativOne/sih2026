"""Run the decision value backtest on the real test split.

This script executes the simulation across all 4 vessel classes for a 30-day TC.
It first gets the ML model's P10/P50/P90 predictions, then feeds them to the 
optimizer backtester.
"""

import math
import polars as pl
from ml.baselines import load_split
from ml.model_lgbm_tuned import predict as predict_lgbm
from opt.backtest import simulate, summarise, to_dataframe

def main():
    print("Loading test split (Jan 2025 - Apr 2026)...")
    train = load_split("train")
    test = load_split("test")

    print(f"Generating ML predictions for {test.height} rows...")
    # Get predictions for horizons 7 and 30
    preds_h7 = predict_lgbm(train, {"test": test}, h=7)["test"]
    preds_h30 = predict_lgbm(train, {"test": test}, h=30)["test"]
    
    # Combine predictions vertically
    ml_predictions = pl.concat([preds_h7, preds_h30])
    
    # Simulate a 30-day TC with a standard 3% broker discount below spot
    contract_term = 30
    broker_spread = 0.03
    risk_tol = 0.3  # Mildly risk-averse
    
    print(f"Simulating optimizer decisions (30-day TC, {broker_spread*100}% broker spread, risk_tol={risk_tol})...")
    rows = simulate(
        test_split=test,
        ml_predictions=ml_predictions,
        contract_term_days=contract_term,
        broker_spread=broker_spread,
        risk_tolerance=risk_tol,
        basis=None, # Running on BASE TCE directly
    )
    
    print("Summarising results...")
    # Summarise for ALL classes combined, plus Supramax specifically
    summaries = summarise(rows, contract_term_days=contract_term, classes=["ALL", "Supramax"])
    df = to_dataframe(summaries)
    
    # Print the clean summary
    print("\n" + "="*80)
    print(f"BACKTEST RESULTS: {contract_term}-day TC Contracts")
    print("="*80)
    
    # Format the polars output nicely using ASCII to avoid Windows console errors
    with pl.Config(tbl_rows=20, tbl_width_chars=120, float_precision=1, tbl_formatting="ASCII_MARKDOWN"):
        print(df.select([
            "vessel_class", "strategy", "lock_rate", "hit_rate",
            "savings_p10", "savings_mean", "decision_value"
        ]))
        
    print("\nInterpretation guide:")
    print(" - savings_mean  : E[savings] $/day vs staying spot (positive = saved money)")
    print(" - savings_p10   : Worst-case savings (the 'bad run' floor)")
    print(" - decision_value: Expected $/day gained by using the optimizer vs always locking")
    print(" - lock_rate     : % of days a TC contract was secured")
    print(" - hit_rate      : % of LOCK decisions that were actually cheaper than spot in hindsight\n")


if __name__ == "__main__":
    main()
