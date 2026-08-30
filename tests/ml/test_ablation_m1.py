"""P3 requirement 7 -- the ablation that decides how M1 ships.

Runs the real ablation once per test session (module-scoped fixture): trains
real XGBoost models A and B for all three horizons and evaluates on the real
valid split and the real frozen test split (through the sanctioned
``allow_test_set_access`` gate). Takes real wall-clock time (~45s measured
live) -- this is accepted, same as the rest of this test suite's real-data
tonnage/ml tests.
"""
from __future__ import annotations

import pytest

from ml.ablation_m1 import ADOPTION_MIN_RELATIVE_IMPROVEMENT, run_ablation
from ml.baselines import CLASSES, HORIZONS


@pytest.fixture(scope="module")
def report():
    return run_ablation()


def test_report_covers_every_horizon_and_scope_on_both_splits(report):
    seen = {(r.split, r.scope, r.h) for r in report.rows}
    for split in ("valid", "test"):
        for h in HORIZONS:
            assert (split, "POOLED", h) in seen
    # Per-class rows exist for at least the classes with real overlap on each split.
    assert any(scope in CLASSES for (_split, scope, _h) in seen)


def test_test_split_was_read_through_the_sanctioned_guard_not_around_it():
    """Static check mirroring tests/test_frozen_test_guard.py's own philosophy:
    ablation_m1.py must call load_frozen_test() inside allow_test_set_access,
    not load_split("test") directly."""
    import re
    from pathlib import Path

    src = Path(__file__).resolve().parents[2] / "src" / "ml" / "ablation_m1.py"
    text = src.read_text(encoding="utf-8")
    assert re.search(r"""load_split\(\s*['"]test['"]\s*\)""", text) is None
    assert "allow_test_set_access(" in text
    assert "load_frozen_test()" in text


def test_m1_coverage_is_real_and_full_on_the_evaluation_splits(report):
    """valid and test both fall entirely inside the real PortWatch-derived M1
    coverage window (see tests/tonnage/test_mlfeatures.py) -- measured live,
    not assumed."""
    assert report.feature_valid_coverage == pytest.approx(1.0)
    assert report.feature_test_coverage == pytest.approx(1.0)


def test_decision_matches_the_fixed_pre_committed_rule(report):
    """Re-derive the adoption boolean from the report's own numbers and the
    fixed threshold constant -- catches a decision that silently drifted from
    the documented rule."""
    test_pooled = [r for r in report.rows if r.split == "test" and r.scope == "POOLED"]
    valid_pooled = [r for r in report.rows if r.split == "valid" and r.scope == "POOLED"]
    test_improve = sum(r.pinball_0_5_relative_improvement for r in test_pooled) / len(test_pooled)
    valid_improve = sum(r.pinball_0_5_relative_improvement for r in valid_pooled) / len(valid_pooled)
    expected = test_improve > ADOPTION_MIN_RELATIVE_IMPROVEMENT and valid_improve > 0.0
    assert report.adopt_b == expected
    assert report.test_pooled_mean_relative_improvement == pytest.approx(test_improve)
    assert report.valid_pooled_mean_relative_improvement == pytest.approx(valid_improve)


def test_reasoning_names_the_actual_decision(report):
    # "ADOPT B" is a substring of "DO NOT ADOPT B" -- check the unambiguous
    # negative phrasing first so both directions are actually distinguished.
    if report.adopt_b:
        assert report.reasoning.startswith("ADOPT B")
        assert "DO NOT ADOPT" not in report.reasoning
    else:
        assert report.reasoning.startswith("DO NOT ADOPT B")


def test_table_is_a_real_dataframe_with_one_row_per_metric_row(report):
    table = report.table()
    assert table.height == len(report.rows)
    assert "pinball_0_5_a" in table.columns
    assert "pinball_0_5_b" in table.columns


def test_a_and_b_use_identical_hyperparameters_and_procedure():
    """The ablation isolates the feature set as the ONLY difference -- both
    variants must go through the exact same ml.model_xgb.predict function,
    not a parallel training implementation."""
    import ast
    from pathlib import Path

    src = Path(__file__).resolve().parents[2] / "src" / "ml" / "ablation_m1.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    calls = [n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
    assert calls.count("predict_xgb") == 2  # exactly one call site for A, one for B -- same function
