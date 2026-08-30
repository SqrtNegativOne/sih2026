"""Calibrate on valid, report on test — exactly once.

This replaces a script that ran the backtest directly on samples_test.parquet with
a hand-picked risk_tolerance=0.3 ("Mildly risk-averse", per the old comment) and
called it a result. Two problems with that: the parameter was never actually tuned,
it was guessed, and there was nothing stopping the natural next step -- "let's
calibrate risk_tolerance instead of guessing" -- from wiring opt.calibration.calibrate_pso
straight onto the test split, which would tune hyperparameters and report performance
from the same rows. See ml.frozen_test for the guard this script now goes through.

Two phases:
  1. CALIBRATE on `valid` — opt.calibration.calibrate_pso searches (theta, sigma_long,
     risk_tolerance) for the combination that maximises decision value. Test is never
     touched here.
  2. REPORT on `test` — the calibrated parameters are applied once, inside an
     ml.frozen_test.allow_test_set_access(...) block, and the resulting numbers are
     the final report. If you re-run this script to "see if the numbers changed",
     you have already violated the one thing it exists to guarantee.

PSO settings below are sized to finish in single-digit minutes on a laptop CPU, not
tuned for the best possible search. Pure-Python Monte Carlo (see opt.monte_carlo) is
the bottleneck: each evaluation draws num_simulations x contract_term_days Gaussian
samples per row. For a more thorough calibration ahead of a real submission, raise
N_PARTICLES / N_ITERATIONS / MC_NUM_SIMULATIONS below and expect roughly linear
runtime growth in their product.
"""
from __future__ import annotations

import time

import polars as pl

from ml.baselines import load_split
from ml.frozen_test import allow_test_set_access, load_frozen_test
from ml.inference import FreightPredictor
from opt.backtest import simulate, summarise, to_dataframe
from opt.calibration import CalibrationBounds, calibrate_pso
from opt.monte_carlo import MonteCarloConfig

CONTRACT_TERM_DAYS = 30
BROKER_SPREAD = 0.03

# Calibration search budget -- see module docstring for the runtime tradeoff.
N_PARTICLES = 10
N_ITERATIONS = 12
MC_NUM_SIMULATIONS = 80
PSO_SEED = 42
MC_SEED = 2025


def _predict(split: pl.DataFrame) -> pl.DataFrame:
    """h=7 and h=30 XGBoost predictions, combined, for one split."""
    p7 = FreightPredictor(h=7).predict(split)
    p30 = FreightPredictor(h=30).predict(split)
    return pl.concat([p7, p30])


def _print_summary(df: pl.DataFrame, title: str) -> None:
    print("\n" + "=" * 80)
    print(title)
    print("=" * 80)
    with pl.Config(tbl_rows=20, tbl_width_chars=120, float_precision=1, tbl_formatting="ASCII_MARKDOWN"):
        print(df.select([
            "vessel_class", "strategy", "lock_rate", "hit_rate",
            "savings_p10", "savings_mean", "decision_value",
        ]))


def main() -> None:
    # -------------------------------------------------------------------
    # Phase 1 — CALIBRATE on valid. Test is not loaded in this phase.
    # -------------------------------------------------------------------
    print("Loading valid split for calibration (test is not touched in this phase)...")
    valid = load_split("valid")
    print(f"Generating ML predictions for {valid.height} valid rows...")
    valid_preds = _predict(valid)

    print(
        f"Calibrating (theta, sigma_long, risk_tolerance) via PSO on valid "
        f"(n_particles={N_PARTICLES}, n_iterations={N_ITERATIONS}, "
        f"mc_num_simulations={MC_NUM_SIMULATIONS})..."
    )
    t0 = time.time()
    result = calibrate_pso(
        eval_split=valid,
        ml_predictions=valid_preds,
        contract_term_days=CONTRACT_TERM_DAYS,
        broker_spread=BROKER_SPREAD,
        bounds=CalibrationBounds(),
        n_particles=N_PARTICLES,
        n_iterations=N_ITERATIONS,
        seed=PSO_SEED,
        mc_num_simulations=MC_NUM_SIMULATIONS,
        mc_seed=MC_SEED,
    )
    elapsed = time.time() - t0
    print(f"Calibration finished in {elapsed:.0f}s.")
    print(
        f"  best_theta={result.best_theta:.4f}  "
        f"best_sigma_long={result.best_sigma_long:.4f}  "
        f"best_risk_tolerance={result.best_risk_tolerance:.4f}"
    )
    print(f"  decision_value on valid = ${result.best_decision_value:,.2f}/day")

    # A quick sanity read on the search: did it move from where it started?
    history = result.convergence_history
    print(f"  convergence: {history[0]:.2f} -> {history[-1]:.2f} over {len(history)} iterations")

    # -------------------------------------------------------------------
    # Phase 2 — REPORT on test, exactly once, using the calibrated params.
    # -------------------------------------------------------------------
    print("\nLoading test split for the FINAL report (frozen-test guard opens now)...")
    with allow_test_set_access(
        "run_optimizer_backtest.py final decision-value report, using parameters "
        "calibrated on valid only"
    ):
        test = load_frozen_test()
        print(f"Generating ML predictions for {test.height} test rows...")
        test_preds = _predict(test)

        mc_config = MonteCarloConfig(
            theta=result.best_theta,
            sigma_long=result.best_sigma_long,
            num_simulations=max(MC_NUM_SIMULATIONS, 300),  # a steadier final estimate
        )
        rows = simulate(
            eval_split=test,
            ml_predictions=test_preds,
            contract_term_days=CONTRACT_TERM_DAYS,
            broker_spread=BROKER_SPREAD,
            risk_tolerance=result.best_risk_tolerance,
            basis=None,  # Running on BASE TCE directly
            mc_config=mc_config,
        )
        summaries = summarise(rows, contract_term_days=CONTRACT_TERM_DAYS, classes=["ALL", "Supramax"])
        df = to_dataframe(summaries)

    _print_summary(
        df,
        f"FINAL TEST REPORT ({CONTRACT_TERM_DAYS}-day TC) — test split read once, "
        f"params calibrated on valid",
    )

    print("\nInterpretation guide:")
    print(" - savings_mean  : E[savings] $/day vs staying spot (positive = saved money)")
    print(" - savings_p10   : Worst-case savings (the 'bad run' floor)")
    print(" - decision_value: Expected $/day gained by using the optimizer vs always locking")
    print(" - lock_rate     : % of days a TC contract was secured")
    print(" - hit_rate      : % of LOCK decisions that were actually cheaper than spot in hindsight")
    print(
        "\nParameters used for this report were selected on `valid`, never on the test rows "
        "shown above -- see ml.frozen_test.load_frozen_test() and the module docstring."
    )


if __name__ == "__main__":
    main()
