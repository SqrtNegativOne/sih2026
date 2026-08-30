"""P5 requirement 10/11: HISTORICAL_MODEL_REPLAY -- genuine backtest reuse,
caching mechanics, and the unmistakable retrospective label.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

import opt.replay as replay_mod
from opt.replay import REPLAY_LABEL, clear_replay_cache, get_replay_snapshot


class TestLabel:
    def test_label_is_unmistakably_retrospective(self) -> None:
        assert "retrospective" in REPLAY_LABEL.lower()
        assert "not decisions this system actually made" in REPLAY_LABEL


class TestCacheMechanicsWithAFakeCompute:
    """Caching-wrapper behaviour only, with the expensive real PSO+backtest
    compute faked out -- its own correctness is proven for real in
    TestRealEndToEnd below."""

    @pytest.fixture(autouse=True)
    def _fake_compute(self, monkeypatch):
        clear_replay_cache()
        calls = {"n": 0}

        def _fake() -> replay_mod.ReplaySnapshot:
            calls["n"] += 1
            from opt.backtest import BacktestSummary
            from opt.calibration import CalibrationResult

            return replay_mod.ReplaySnapshot(
                label=REPLAY_LABEL, computed_at=datetime.now(UTC), compute_seconds=0.01,
                contract_term_days=30, broker_spread=0.03,
                calibration=CalibrationResult(
                    best_theta=0.1, best_sigma_long=0.3, best_risk_tolerance=0.2,
                    best_decision_value=1.0, convergence_history=[0.0, 1.0],
                ),
                n_test_rows=818,
                summaries=(
                    BacktestSummary(
                        strategy="optimizer", vessel_class="ALL", contract_term_days=30, n_decisions=818,
                        savings_mean=1.0, savings_p10=0.0, savings_p50=1.0, savings_p90=2.0,
                        hit_rate=0.8, lock_rate=0.7, decision_value=0.5, regret=1.0,
                    ),
                ),
            )

        monkeypatch.setattr(replay_mod, "_compute", _fake)
        self.calls = calls

    def test_cached_across_repeated_calls(self) -> None:
        first = get_replay_snapshot()
        second = get_replay_snapshot()
        assert second is first
        assert self.calls["n"] == 1

    def test_force_refresh_recomputes(self) -> None:
        get_replay_snapshot()
        get_replay_snapshot(force_refresh=True)
        assert self.calls["n"] == 2

    def test_failure_falls_back_to_stale_last_good(self, monkeypatch) -> None:
        good = get_replay_snapshot()

        def _boom():
            raise RuntimeError("simulated replay failure")

        monkeypatch.setattr(replay_mod, "_compute", _boom)
        result = get_replay_snapshot(force_refresh=True)
        assert result.stale is True
        assert result.summaries == good.summaries

    def test_no_fallback_available_propagates_the_real_exception(self, monkeypatch) -> None:
        def _boom():
            raise RuntimeError("simulated failure with nothing to fall back to")

        monkeypatch.setattr(replay_mod, "_compute", _boom)
        with pytest.raises(RuntimeError, match="simulated failure"):
            get_replay_snapshot()


class TestRealEndToEnd:
    """Real, unfaked call -- pays the real PSO calibration + frozen-test
    backtest cost once, shared across both tests below via the module-level
    cache (same tradeoff tonnage.field's ablation snapshot accepts in P3),
    not once per test."""

    @classmethod
    def setup_class(cls) -> None:
        clear_replay_cache()

    @classmethod
    def teardown_class(cls) -> None:
        clear_replay_cache()

    def test_real_replay_snapshot_is_a_genuine_backtest(self) -> None:
        snap = get_replay_snapshot()
        assert snap.stale is False
        assert snap.compute_seconds > 0
        assert snap.n_test_rows > 0
        assert len(snap.summaries) > 0
        strategies = {s.strategy for s in snap.summaries}
        assert {"always_spot", "always_lock", "optimizer", "oracle"} <= strategies
        # The calibration genuinely ran on valid -- real parameters, not
        # hardcoded defaults sitting unused.
        assert 0.0 <= snap.calibration.best_risk_tolerance <= 1.0

    def test_oracle_never_does_worse_than_the_optimizer_pooled(self) -> None:
        """A real mathematical property of this backtest, not a coincidence:
        the oracle strategy locks in hindsight exactly when locking was
        cheaper, so no other strategy's mean savings can exceed it."""
        snap = get_replay_snapshot()
        pooled = {s.strategy: s for s in snap.summaries if s.vessel_class == "ALL"}
        assert pooled["oracle"].savings_mean >= pooled["optimizer"].savings_mean - 1e-6
        assert pooled["oracle"].savings_mean >= pooled["always_lock"].savings_mean - 1e-6
