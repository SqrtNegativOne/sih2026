"""P4 requirement 4 -- macro/commodity signal research: lag analysis,
leakage review, and the real ablation. Real data throughout (World Bank Pink
Sheet + FRED, see raw_data/macro/sources.md).

Runs the real, full ablation for ONE representative candidate (Brent crude --
chosen for having the strongest a priori economic rationale, see
ml.macro_features's module docstring) to prove the mechanism end-to-end
against real data; the other four candidates are covered by the lighter,
fast checks below plus the real, already-executed run reported in the P4
completion report (identical code path, just not re-run inside every test
session at ~45s x 5 = ~4 minutes).
"""
from __future__ import annotations

import pytest

from ml.ablation_m1 import ADOPTION_MIN_RELATIVE_IMPROVEMENT
from ml.baselines import HORIZONS
from ml.macro_features import (
    MACRO_CANDIDATES,
    lag_correlation,
    run_macro_ablation,
)


class TestLagAnalysis:
    """The real cross-correlation step, run before (and independent of) the
    ablation -- must reflect the real measured relationship, not a desired
    one."""

    def test_every_candidate_produces_a_real_correlation_for_every_class(self) -> None:
        for series_id in MACRO_CANDIDATES:
            for cls in ("Capesize", "Panamax", "Supramax", "Handysize"):
                lc = lag_correlation(series_id, cls)
                assert lc.series_id == series_id
                assert lc.vessel_class == cls
                assert set(lc.by_lag_days) == set(lc.n_obs)
                # best_lag_days must be an actual tested lag, not a default.
                assert lc.best_lag_days in lc.by_lag_days

    def test_unknown_series_id_raises(self) -> None:
        with pytest.raises(ValueError):
            lag_correlation("NOT_A_REAL_SERIES", "Capesize")


class TestLeakageReview:
    """Structural leakage guard: every macro observation is dated to the
    month/day it was actually publishable, never the period it describes
    ahead of that -- see data_builders.build_macro's own docstring for the
    World Bank publication-lag reasoning."""

    def test_worldbank_series_are_dated_to_the_month_after_the_price_they_describe(self) -> None:
        from datetime import date

        from data_builders.build_macro import _month_string_to_publish_date

        assert _month_string_to_publish_date("2026M07") == date(2026, 8, 1)
        assert _month_string_to_publish_date("2026M12") == date(2027, 1, 1)

    def test_macro_long_parquet_has_no_observation_dated_before_its_own_earliest_useful_date(self) -> None:
        import polars as pl

        from data_builders.build_macro import DATA_OUT, EARLIEST_USEFUL_DATE

        path = DATA_OUT / "macro_long.parquet"
        if not path.exists():
            pytest.skip("macro_long.parquet not built -- run `python -m data_builders.build_macro` first")
        macro = pl.read_parquet(path)
        assert macro["date"].min() >= EARLIEST_USEFUL_DATE


class TestMacroAblation:
    """Real end-to-end run for one candidate -- same real mechanism as
    ml.ablation_m1 (ml.model_xgb.predict, ml.baselines.evaluate,
    ml.frozen_test's guard)."""

    @pytest.fixture(scope="module")
    def brent_report(self):
        return run_macro_ablation("MACRO_BRENT_CRUDE")

    def test_report_covers_every_horizon(self, brent_report) -> None:
        seen_h = {r.h for r in brent_report.rows if r.scope == "POOLED"}
        assert seen_h == set(HORIZONS)

    def test_coverage_is_full_on_both_evaluation_splits(self, brent_report) -> None:
        """valid/test both fall entirely inside 2010+ (macro data's real
        coverage window) -- measured live, not assumed."""
        assert brent_report.feature_valid_coverage == pytest.approx(1.0)
        assert brent_report.feature_test_coverage == pytest.approx(1.0)

    def test_decision_matches_the_shared_fixed_threshold(self, brent_report) -> None:
        """Macro candidates are judged by the SAME adoption bar M1 uses --
        not a bar invented per-feature to make a particular result land."""
        test_pooled = [r for r in brent_report.rows if r.split == "test" and r.scope == "POOLED"]
        improve = sum(r.pinball_0_5_relative_improvement for r in test_pooled) / len(test_pooled)
        assert brent_report.test_pooled_mean_relative_improvement == pytest.approx(improve)
        valid_pooled = [r for r in brent_report.rows if r.split == "valid" and r.scope == "POOLED"]
        valid_improve = sum(r.pinball_0_5_relative_improvement for r in valid_pooled) / len(valid_pooled)
        expected_adopt = improve > ADOPTION_MIN_RELATIVE_IMPROVEMENT and valid_improve > 0.0
        assert brent_report.adopt_b == expected_adopt

    def test_reasoning_names_the_series_and_the_decision(self, brent_report) -> None:
        assert "MACRO_BRENT_CRUDE" in brent_report.reasoning
        assert ("KEEP" in brent_report.reasoning) or ("DROP" in brent_report.reasoning)

    def test_unknown_series_id_raises(self) -> None:
        with pytest.raises(ValueError):
            run_macro_ablation("NOT_A_REAL_SERIES")


class TestNoSecondBacktestSystem:
    """Static check, mirroring tests/test_frozen_test_guard.py's own style:
    ml.macro_features must route its one real test-split read through the
    sanctioned guard, and must call the SAME model_xgb.predict / evaluate
    functions ml.ablation_m1 uses -- not a parallel implementation."""

    def test_frozen_test_access_is_gated(self) -> None:
        import re
        from pathlib import Path

        src = Path(__file__).resolve().parents[2] / "src" / "ml" / "macro_features.py"
        text = src.read_text(encoding="utf-8")
        assert re.search(r"""load_split\(\s*['"]test['"]\s*\)""", text) is None
        assert "allow_test_set_access(" in text
        assert "load_frozen_test()" in text

    def test_reuses_the_real_shared_training_and_evaluation_functions(self) -> None:
        from pathlib import Path

        src = Path(__file__).resolve().parents[2] / "src" / "ml" / "macro_features.py"
        text = src.read_text(encoding="utf-8")
        assert "from ml.model_xgb import predict as predict_xgb" in text
        assert "from ml.baselines import" in text and "evaluate" in text
