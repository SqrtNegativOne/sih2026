"""Tests for opt.calibration — PSO hyperparameter calibration.

Uses small synthetic fixtures (n_particles=5, n_iterations=10) to keep
runtime under 30 seconds.
"""
from __future__ import annotations

import math
from datetime import date

import polars as pl
import pytest

from opt.calibration import (
    CalibrationBounds,
    CalibrationResult,
    _evaluate,
    calibrate_pso,
)

# ---------------------------------------------------------------------------
# Fixtures — synthetic data mirroring test_backtest.py
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_test_split() -> pl.DataFrame:
    """Four decision points across two vessel classes.

    Two rows where market goes up (locking is good) and two where it goes
    down (locking is bad) — ensures the backtest produces non-trivial
    strategy differences for the PSO to optimise over.
    """
    return pl.DataFrame({
        "date": [
            date(2025, 1, 1), date(2025, 1, 2),
            date(2025, 1, 3), date(2025, 1, 4),
        ],
        "target_class": ["Supramax", "Supramax", "Supramax", "Supramax"],
        "log_value": [
            math.log(10_000), math.log(10_000),
            math.log(10_000), math.log(10_000),
        ],
        "y_step_h30": [
            math.log(13_000), math.log(8_000),
            math.log(14_000), math.log(7_000),
        ],
        "y_mean_h30": [
            math.log(13_000), math.log(8_000),
            math.log(14_000), math.log(7_000),
        ],
        "y_step_h90": [
            math.log(15_000), math.log(6_000),
            math.log(16_000), math.log(5_000),
        ],
        "y_mean_h90": [
            math.log(15_000), math.log(6_000),
            math.log(16_000), math.log(5_000),
        ],
    })


@pytest.fixture
def mock_preds() -> pl.DataFrame:
    """ML predictions for h=7 and h=30 for each row in mock_test_split."""
    rows: list[dict] = []
    for d, p50 in [
        (date(2025, 1, 1), 11_000),  # bullish
        (date(2025, 1, 2), 9_500),   # bearish
        (date(2025, 1, 3), 11_500),  # bullish
        (date(2025, 1, 4), 9_000),   # bearish
    ]:
        for h in [7, 30]:
            rows.append({
                "date": d,
                "target_class": "Supramax",
                "h": h,
                "p_0.1": math.log(p50 - 1_500),
                "p_0.5": math.log(p50),
                "p_0.9": math.log(p50 + 2_000),
            })
    return pl.DataFrame(rows)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCalibratePSO:
    """Core correctness tests for calibrate_pso."""

    def test_returns_calibration_result(
        self, mock_test_split: pl.DataFrame, mock_preds: pl.DataFrame
    ) -> None:
        result = calibrate_pso(
            eval_split=mock_test_split,
            ml_predictions=mock_preds,
            contract_term_days=30,
            broker_spread=0.03,
            n_particles=5,
            n_iterations=10,
            seed=42,
        )
        assert isinstance(result, CalibrationResult)

    def test_all_params_in_bounds(
        self, mock_test_split: pl.DataFrame, mock_preds: pl.DataFrame
    ) -> None:
        bounds = CalibrationBounds(
            theta_range=(0.0, 0.5),
            sigma_long_range=(0.1, 0.8),
            risk_tol_range=(0.0, 1.0),
        )
        result = calibrate_pso(
            eval_split=mock_test_split,
            ml_predictions=mock_preds,
            contract_term_days=30,
            broker_spread=0.03,
            bounds=bounds,
            n_particles=5,
            n_iterations=10,
            seed=7,
        )
        assert bounds.theta_range[0] <= result.best_theta <= bounds.theta_range[1], (
            f"best_theta={result.best_theta} out of bounds {bounds.theta_range}"
        )
        assert bounds.sigma_long_range[0] <= result.best_sigma_long <= bounds.sigma_long_range[1], (
            f"best_sigma_long={result.best_sigma_long} out of bounds {bounds.sigma_long_range}"
        )
        assert bounds.risk_tol_range[0] <= result.best_risk_tolerance <= bounds.risk_tol_range[1], (
            f"best_risk_tolerance={result.best_risk_tolerance} out of bounds {bounds.risk_tol_range}"
        )

    def test_custom_bounds_respected(
        self, mock_test_split: pl.DataFrame, mock_preds: pl.DataFrame
    ) -> None:
        tight_bounds = CalibrationBounds(
            theta_range=(0.2, 0.3),
            sigma_long_range=(0.25, 0.35),
            risk_tol_range=(0.4, 0.6),
        )
        result = calibrate_pso(
            eval_split=mock_test_split,
            ml_predictions=mock_preds,
            contract_term_days=30,
            broker_spread=0.03,
            bounds=tight_bounds,
            n_particles=5,
            n_iterations=10,
            seed=99,
        )
        assert tight_bounds.theta_range[0] <= result.best_theta <= tight_bounds.theta_range[1]
        assert tight_bounds.sigma_long_range[0] <= result.best_sigma_long <= tight_bounds.sigma_long_range[1]
        assert tight_bounds.risk_tol_range[0] <= result.best_risk_tolerance <= tight_bounds.risk_tol_range[1]

    def test_convergence_history_length(
        self, mock_test_split: pl.DataFrame, mock_preds: pl.DataFrame
    ) -> None:
        n_iter = 10
        result = calibrate_pso(
            eval_split=mock_test_split,
            ml_predictions=mock_preds,
            contract_term_days=30,
            broker_spread=0.03,
            n_particles=5,
            n_iterations=n_iter,
            seed=42,
        )
        assert len(result.convergence_history) == n_iter, (
            f"Expected {n_iter} history entries, got {len(result.convergence_history)}"
        )

    def test_convergence_history_non_decreasing(
        self, mock_test_split: pl.DataFrame, mock_preds: pl.DataFrame
    ) -> None:
        """Global best must be monotonically non-decreasing across iterations."""
        result = calibrate_pso(
            eval_split=mock_test_split,
            ml_predictions=mock_preds,
            contract_term_days=30,
            broker_spread=0.03,
            n_particles=5,
            n_iterations=15,
            seed=0,
        )
        history = result.convergence_history
        for i in range(1, len(history)):
            assert history[i] >= history[i - 1] - 1e-9, (
                f"convergence_history decreased at step {i}: "
                f"{history[i-1]:.4f} → {history[i]:.4f}"
            )

    def test_best_decision_value_matches_last_history(
        self, mock_test_split: pl.DataFrame, mock_preds: pl.DataFrame
    ) -> None:
        result = calibrate_pso(
            eval_split=mock_test_split,
            ml_predictions=mock_preds,
            contract_term_days=30,
            broker_spread=0.03,
            n_particles=5,
            n_iterations=10,
            seed=42,
        )
        assert result.best_decision_value == pytest.approx(
            result.convergence_history[-1], abs=1e-6
        )

    def test_pso_at_least_as_good_as_default_params(
        self, mock_test_split: pl.DataFrame, mock_preds: pl.DataFrame
    ) -> None:
        """PSO should find a decision value >= the decision value at default params."""
        from opt.backtest import simulate, summarise

        # Baseline: default risk_tolerance=0.0
        rows_default = simulate(
            mock_test_split, mock_preds, contract_term_days=30, broker_spread=0.03
        )
        summaries_default = summarise(rows_default, contract_term_days=30, classes=["ALL"])
        default_dv = next(
            s.decision_value
            for s in summaries_default
            if s.strategy == "optimizer" and s.vessel_class == "ALL"
        )

        result = calibrate_pso(
            eval_split=mock_test_split,
            ml_predictions=mock_preds,
            contract_term_days=30,
            broker_spread=0.03,
            n_particles=5,
            n_iterations=10,
            seed=42,
        )
        assert result.best_decision_value >= default_dv - 1e-6, (
            f"PSO ({result.best_decision_value:.2f}) should be >= default ({default_dv:.2f})"
        )

    def test_reproducibility_with_same_seed(
        self, mock_test_split: pl.DataFrame, mock_preds: pl.DataFrame
    ) -> None:
        """Same seed must produce identical results."""
        kwargs = {
            "eval_split": mock_test_split,
            "ml_predictions": mock_preds,
            "contract_term_days": 30,
            "broker_spread": 0.03,
            "n_particles": 5,
            "n_iterations": 8,
            "seed": 123,
        }
        r1 = calibrate_pso(**kwargs)  # type: ignore[arg-type]
        r2 = calibrate_pso(**kwargs)  # type: ignore[arg-type]
        assert r1.best_theta == pytest.approx(r2.best_theta)
        assert r1.best_sigma_long == pytest.approx(r2.best_sigma_long)
        assert r1.best_risk_tolerance == pytest.approx(r2.best_risk_tolerance)
        assert r1.best_decision_value == pytest.approx(r2.best_decision_value)
        assert r1.convergence_history == pytest.approx(r2.convergence_history)


class TestObjectiveWiring:
    """theta and sigma_long must genuinely influence the backtest objective
    (regression test for the old wiring gap where only risk_tolerance did).

    Fixture: one decision point at spot=10k with quote=9.7k (3% spread) and a
    flat forecast fan (P50=10k, ±2k). The quote sits inside the band where
    the MC-informed LOCK threshold crosses it, so parameter changes flip the
    decision (verified robust across RNG seeds).
    """

    @pytest.fixture
    def boundary_split(self) -> pl.DataFrame:
        """Single decision point whose quote sits near the LOCK threshold."""
        return pl.DataFrame({
            "date": [date(2025, 1, 1)],
            "target_class": ["Supramax"],
            "log_value": [math.log(10_824.74)],
            "y_step_h30": [math.log(12_000)],
            "y_mean_h30": [math.log(12_000)],
            "y_step_h90": [math.log(14_000)],
            "y_mean_h90": [math.log(14_000)],
        })

    @pytest.fixture
    def boundary_preds(self) -> pl.DataFrame:
        """Flat fan: P50 = today's spot, ±2k width, h=7 and h=30."""
        return pl.DataFrame({
            "date": [date(2025, 1, 1)] * 2,
            "target_class": ["Supramax"] * 2,
            "h": [7, 30],
            "p_0.1": [math.log(8_000)] * 2,
            "p_0.5": [math.log(10_000)] * 2,
            "p_0.9": [math.log(12_000)] * 2,
        })

    def test_theta_influences_objective(
        self, boundary_split: pl.DataFrame, boundary_preds: pl.DataFrame
    ) -> None:
        common = {
            "eval_split": boundary_split,
            "ml_predictions": boundary_preds,
            "contract_term_days": 30,
            "broker_spread": 0.03,
            "sigma_long": 0.40,
            "risk_tolerance": 0.5,
            "mc_num_simulations": 600,
            "mc_seed": 2025,
        }
        dv_weak = _evaluate(**common, theta=0.01)  # type: ignore[arg-type]
        dv_strong = _evaluate(**common, theta=0.49)  # type: ignore[arg-type]
        assert dv_weak != pytest.approx(dv_strong), (
            f"objective must respond to theta (weak={dv_weak}, strong={dv_strong})"
        )

    def test_sigma_long_influences_objective(
        self, boundary_split: pl.DataFrame, boundary_preds: pl.DataFrame
    ) -> None:
        common = {
            "eval_split": boundary_split,
            "ml_predictions": boundary_preds,
            "contract_term_days": 30,
            "broker_spread": 0.03,
            "theta": 0.05,
            "risk_tolerance": 0.85,
            "mc_num_simulations": 600,
            "mc_seed": 2025,
        }
        dv_low = _evaluate(**common, sigma_long=0.11)  # type: ignore[arg-type]
        dv_high = _evaluate(**common, sigma_long=0.45)  # type: ignore[arg-type]
        assert dv_low != pytest.approx(dv_high), (
            f"objective must respond to sigma_long (low={dv_low}, high={dv_high})"
        )

    def test_calibrate_pso_accepts_mc_kwargs(
        self, mock_test_split: pl.DataFrame, mock_preds: pl.DataFrame
    ) -> None:
        result = calibrate_pso(
            eval_split=mock_test_split,
            ml_predictions=mock_preds,
            contract_term_days=30,
            broker_spread=0.03,
            n_particles=4,
            n_iterations=3,
            seed=11,
            mc_num_simulations=150,
            mc_seed=777,
        )
        assert isinstance(result, CalibrationResult)
        assert result.best_decision_value > -1e8
