"""P3 requirement 8: cache the reconstruction. Process-lifetime cache, warm
responses fast, staleness surfaced on failure rather than silent zeros.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

import tonnage.field as field_mod
from tonnage.field import (
    clear_ablation_cache,
    clear_snapshot_cache,
    get_ablation_snapshot,
    get_snapshot,
)


@pytest.fixture(autouse=True)
def _reset_caches():
    clear_snapshot_cache()
    clear_ablation_cache()
    yield
    clear_snapshot_cache()
    clear_ablation_cache()


class TestSnapshotCache:
    def test_first_call_computes_a_real_snapshot(self) -> None:
        snap = get_snapshot()
        assert snap.compute_seconds > 0
        assert snap.stale is False
        assert snap.result.frame.height > 0
        assert snap.gate.index_type is not None
        assert len(snap.sign_diagnoses) > 0

    def test_second_call_is_cached_and_instant(self) -> None:
        import time

        first = get_snapshot()
        t0 = time.perf_counter()
        second = get_snapshot()
        elapsed = time.perf_counter() - t0
        assert second is first  # exact same cached object, not merely equal
        assert elapsed < 0.05

    def test_force_refresh_recomputes(self) -> None:
        first = get_snapshot()
        second = get_snapshot(force_refresh=True)
        assert second is not first
        assert second.computed_at >= first.computed_at

    def test_clear_cache_forces_a_fresh_object_next_call(self) -> None:
        first = get_snapshot()
        clear_snapshot_cache()
        second = get_snapshot()
        assert second is not first

    def test_reconstruction_failure_falls_back_to_last_good_snapshot_as_stale(self, monkeypatch) -> None:
        good = get_snapshot()  # populate the cache with a real, good snapshot

        def _boom():
            raise RuntimeError("simulated real-world failure -- e.g. a corrupt source file")

        monkeypatch.setattr(field_mod, "reconstruct", _boom)
        result = get_snapshot(force_refresh=True)
        assert result.stale is True
        assert result.result.frame.height == good.result.frame.height  # the real, previous data -- not zeros

    def test_no_fallback_available_propagates_the_real_exception(self, monkeypatch) -> None:
        def _boom():
            raise RuntimeError("simulated failure with nothing to fall back to")

        monkeypatch.setattr(field_mod, "reconstruct", _boom)
        with pytest.raises(RuntimeError, match="simulated failure"):
            get_snapshot()


class TestAblationSnapshotCacheMechanics:
    """Caching-wrapper behaviour only, with `run_ablation` faked out for
    speed -- the real ablation's own correctness is tested end-to-end in
    tests/ml/test_ablation_m1.py and in
    TestAblationSnapshotRealEndToEnd below."""

    @pytest.fixture(autouse=True)
    def _fake_ablation(self, monkeypatch):
        calls = {"n": 0}

        def _fake_run_ablation():
            calls["n"] += 1
            from ml.ablation_m1 import AblationReport

            return AblationReport(
                rows=[], feature_valid_coverage=1.0, feature_test_coverage=1.0,
                test_pooled_mean_relative_improvement=0.0, valid_pooled_mean_relative_improvement=0.0,
                adopt_b=False, reasoning="fake report for cache-mechanics testing",
            )

        monkeypatch.setattr(field_mod, "run_ablation", _fake_run_ablation)
        self.calls = calls

    def test_cached_across_repeated_calls(self) -> None:
        first = get_ablation_snapshot()
        second = get_ablation_snapshot()
        assert second is first
        assert self.calls["n"] == 1

    def test_force_refresh_recomputes(self) -> None:
        get_ablation_snapshot()
        get_ablation_snapshot(force_refresh=True)
        assert self.calls["n"] == 2

    def test_failure_falls_back_to_stale_last_good(self, monkeypatch) -> None:
        good = get_ablation_snapshot()

        def _boom():
            raise RuntimeError("simulated ablation failure")

        monkeypatch.setattr(field_mod, "run_ablation", _boom)
        result = get_ablation_snapshot(force_refresh=True)
        assert result.stale is True
        assert result.report.reasoning == good.report.reasoning


class TestAblationSnapshotRealEndToEnd:
    """Real end-to-end call -- pays the real ~45s ablation cost once, same as
    tests/ml/test_ablation_m1.py, to prove the caching wrapper genuinely works
    against the real function it wraps, not just the fake used above."""

    def test_real_ablation_snapshot_has_a_real_timestamp_and_report(self) -> None:
        snap = get_ablation_snapshot()
        assert snap.stale is False
        assert snap.compute_seconds > 0
        assert isinstance(snap.computed_at, datetime)
        assert snap.computed_at.tzinfo is UTC
        assert len(snap.report.rows) > 0
