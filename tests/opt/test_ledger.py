"""P5 requirements 6-9: the Live Decision Ledger -- append-only, real regret
arithmetic, empty-state honesty, outcome linking.
"""
from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from opt import ledger
from opt.network import PortEnum
from opt.types import QuoteResult, RateHorizon, RouteEvidenceLevel, VesselClass


@pytest.fixture()
def isolated_ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger, "LEDGER_DIR", tmp_path / "ledger")
    monkeypatch.setattr(ledger, "ENTRIES_LOG", tmp_path / "ledger" / "ledger_entries.jsonl")
    monkeypatch.setattr(ledger, "OUTCOMES_LOG", tmp_path / "ledger" / "ledger_outcomes.jsonl")
    return tmp_path


def _fake_quote_result(*, lock_action: str = "LOCK", today_quote: float = 15_000.0) -> QuoteResult:
    """A real QuoteResult-shaped object, hand-built (not from a live solve)
    so ledger tests are fast and independent of real market data -- the
    ledger's own job (store/retrieve/score what it's handed) does not
    depend on how the QuoteResult was produced."""
    horizon = RateHorizon(
        horizon_days=30, p10_usd_per_day=14_000.0, p50_usd_per_day=15_500.0, p90_usd_per_day=17_000.0,
        p50_usd_per_mt=5.0, direction="up", confidence_pct=60.0,
        route_evidence=RouteEvidenceLevel.ROUTE_RATE_BASIS_UNAVAILABLE, route_adjustment=None,
    )
    return QuoteResult.model_construct(
        cargo_volume_dwt=75_000.0, commodity="Dry Bulk", origin_port=PortEnum.NEWCASTLE_AU, dest_port=PortEnum.PARADIP,
        laycan_start=date(2026, 9, 15), laycan_end=date(2026, 9, 25), contract_term_days=30, as_of=date(2026, 8, 20),
        today_quote_usd_per_day=today_quote, today_quote_usd_per_mt=5.0, assumed_transit_days=18.0,
        rate_forecast=(horizon,), route_evidence=RouteEvidenceLevel.ROUTE_RATE_BASIS_UNAVAILABLE, route_adjustment=None,
        target_vessel_class=VesselClass.PANAMAX, lock_action=lock_action, ceiling_usd_per_day=15_500.0,
        ceiling_usd_per_mt=5.1, optimal_entry_window_start_day=None, optimal_entry_window_end_day=None,
        expected_savings_usd_per_day=500.0, expected_savings_usd_total=15_000.0, prob_savings_positive=0.6,
        fleet_mix=None, origin_port_check=None, dest_port_check=None, risk_assessment=None,
        explanations=None, full_recommendation=None, route_exploration=None,
    )


class TestRecordAndRead:
    def test_recording_a_recommendation_round_trips(self, isolated_ledger) -> None:
        result = _fake_quote_result()
        entry = ledger.record_recommendation(result)
        entries = ledger.read_entries()
        assert len(entries) == 1
        assert entries[0].entry_id == entry.entry_id
        assert entries[0].origin_port == "NEWCASTLE_AU"
        assert entries[0].lock_action == "LOCK"
        assert entries[0].alternative_action == "WAIT"
        assert entries[0].rate_forecast[0].p50_usd_per_day == 15_500.0

    def test_model_and_data_version_are_real_not_placeholder_strings(self, isolated_ledger) -> None:
        entry = ledger.record_recommendation(_fake_quote_result())
        assert entry.model_version.startswith("xgb:")
        assert entry.data_version.startswith("master_long:") or entry.data_version.startswith("unavailable:")

    def test_empty_ledger_renders_as_empty_not_seeded(self, isolated_ledger) -> None:
        assert ledger.read_entries() == ()
        assert ledger.read_outcomes() == ()
        assert ledger.entries_with_outcomes() == ()


class TestAppendOnly:
    def test_two_recommendations_both_persist_independently(self, isolated_ledger) -> None:
        e1 = ledger.record_recommendation(_fake_quote_result(lock_action="LOCK"))
        e2 = ledger.record_recommendation(_fake_quote_result(lock_action="WAIT"))
        entries = ledger.read_entries()
        assert len(entries) == 2
        assert {e.entry_id for e in entries} == {e1.entry_id, e2.entry_id}

    def test_update_entry_always_raises(self, isolated_ledger) -> None:
        entry = ledger.record_recommendation(_fake_quote_result())
        with pytest.raises(ledger.MutationNotAllowedError):
            ledger.update_entry(entry.entry_id, lock_action="WAIT")
        # The real, already-written entry is untouched.
        assert ledger.read_entries()[0].lock_action == "LOCK"

    def test_delete_entry_always_raises(self, isolated_ledger) -> None:
        entry = ledger.record_recommendation(_fake_quote_result())
        with pytest.raises(ledger.MutationNotAllowedError):
            ledger.delete_entry(entry.entry_id)
        assert len(ledger.read_entries()) == 1

    def test_reset_ledger_clears_everything_at_once(self, isolated_ledger) -> None:
        """F-38: reset_ledger is deliberately a DIFFERENT operation from
        delete_entry/update_entry above -- all-or-nothing, never selective,
        so it can't be used to cherry-pick which entries survive."""
        e1 = ledger.record_recommendation(_fake_quote_result(lock_action="LOCK"))
        ledger.record_recommendation(_fake_quote_result(lock_action="WAIT"))
        ledger.record_outcome(e1.entry_id, realized_rate_usd_per_day=16_000.0, realized_at_date=date(2026, 10, 20))
        assert len(ledger.read_entries()) == 2
        assert len(ledger.read_outcomes()) == 1

        removed = ledger.reset_ledger()

        assert removed == 3  # 2 entries + 1 outcome
        assert len(ledger.read_entries()) == 0
        assert len(ledger.read_outcomes()) == 0

    def test_reset_ledger_on_an_already_empty_ledger_is_a_safe_no_op(self, isolated_ledger) -> None:
        assert ledger.reset_ledger() == 0
        assert len(ledger.read_entries()) == 0

    def test_ledger_is_usable_again_immediately_after_a_reset(self, isolated_ledger) -> None:
        ledger.record_recommendation(_fake_quote_result())
        ledger.reset_ledger()
        new_entry = ledger.record_recommendation(_fake_quote_result(lock_action="WAIT"))
        entries = ledger.read_entries()
        assert len(entries) == 1
        assert entries[0].entry_id == new_entry.entry_id

    def test_outcome_is_a_new_appended_record_not_an_edit(self, isolated_ledger) -> None:
        entry = ledger.record_recommendation(_fake_quote_result())
        ledger.record_outcome(entry.entry_id, realized_rate_usd_per_day=16_000.0, realized_at_date=date(2026, 10, 20))
        # The original entry's own fields are unchanged.
        reloaded = ledger.read_entries()[0]
        assert reloaded == entry
        # The outcome exists as its own, separate record.
        outcomes = ledger.read_outcomes()
        assert len(outcomes) == 1
        assert outcomes[0].entry_id == entry.entry_id


class TestOutcomeLinking:
    def test_outcome_must_reference_a_real_entry(self, isolated_ledger) -> None:
        with pytest.raises(KeyError):
            ledger.record_outcome("not-a-real-id", realized_rate_usd_per_day=16_000.0, realized_at_date=date(2026, 10, 20))

    def test_pending_entry_has_no_outcome(self, isolated_ledger) -> None:
        ledger.record_recommendation(_fake_quote_result())
        linked = ledger.entries_with_outcomes()
        assert len(linked) == 1
        assert linked[0].outcome is None

    def test_scored_entry_carries_its_outcome(self, isolated_ledger) -> None:
        entry = ledger.record_recommendation(_fake_quote_result())
        ledger.record_outcome(entry.entry_id, realized_rate_usd_per_day=16_000.0, realized_at_date=date(2026, 10, 20))
        linked = ledger.entries_with_outcomes()
        assert linked[0].outcome is not None
        assert linked[0].outcome.realized_rate_usd_per_day == 16_000.0

    def test_realized_rate_must_be_positive(self, isolated_ledger) -> None:
        entry = ledger.record_recommendation(_fake_quote_result())
        with pytest.raises(ValueError):
            ledger.record_outcome(entry.entry_id, realized_rate_usd_per_day=-1.0, realized_at_date=date(2026, 10, 20))


class TestPerformanceArithmetic:
    """Real regret arithmetic on a fixture -- same savings-vs-always-spot
    definition opt.backtest.BacktestRow uses."""

    def test_empty_ledger_performance_is_honestly_all_none(self, isolated_ledger) -> None:
        summary = ledger.compute_performance()
        assert summary.n_entries_total == 0
        assert summary.n_scored == 0
        assert summary.mean_realized_regret_usd_per_day is None
        assert summary.lock_accuracy is None

    def test_pending_entries_are_excluded_from_performance(self, isolated_ledger) -> None:
        ledger.record_recommendation(_fake_quote_result())  # no outcome ever recorded
        summary = ledger.compute_performance()
        assert summary.n_entries_total == 1
        assert summary.n_pending == 1
        assert summary.n_scored == 0
        assert summary.mean_realized_regret_usd_per_day is None

    def test_a_correct_lock_scores_full_marks_against_oracle(self, isolated_ledger) -> None:
        """Quote=15,000, realised=16,000: locking was the right call (quote <
        realised), so this system == oracle == always_lock. Regret must be 0."""
        entry = ledger.record_recommendation(_fake_quote_result(lock_action="LOCK", today_quote=15_000.0))
        ledger.record_outcome(entry.entry_id, realized_rate_usd_per_day=16_000.0, realized_at_date=date(2026, 10, 20))
        summary = ledger.compute_performance()
        assert summary.n_scored == 1
        assert summary.mean_realized_regret_usd_per_day == pytest.approx(0.0)
        assert summary.lock_accuracy == pytest.approx(1.0)
        assert summary.mean_savings_vs_always_lock_usd_per_day == pytest.approx(0.0)

    def test_a_wrong_lock_produces_real_measured_regret(self, isolated_ledger) -> None:
        """Quote=15,000, realised=10,000: locking LOST money (should have
        waited). Oracle savings=0 (oracle waits); this system's savings =
        realised - quote = -5,000. Regret = oracle - this = 0 - (-5000) = 5000."""
        entry = ledger.record_recommendation(_fake_quote_result(lock_action="LOCK", today_quote=15_000.0))
        ledger.record_outcome(entry.entry_id, realized_rate_usd_per_day=10_000.0, realized_at_date=date(2026, 10, 20))
        summary = ledger.compute_performance()
        assert summary.mean_realized_regret_usd_per_day == pytest.approx(5_000.0)
        assert summary.lock_accuracy == pytest.approx(0.0)

    def test_a_correct_wait_has_zero_savings_and_zero_regret(self, isolated_ledger) -> None:
        """WAIT always earns 0 savings by definition (opt.backtest's own
        convention); if realised < quote, waiting genuinely was correct, so
        oracle also earns 0 -- regret is 0."""
        entry = ledger.record_recommendation(_fake_quote_result(lock_action="WAIT", today_quote=15_000.0))
        ledger.record_outcome(entry.entry_id, realized_rate_usd_per_day=10_000.0, realized_at_date=date(2026, 10, 20))
        summary = ledger.compute_performance()
        assert summary.mean_realized_regret_usd_per_day == pytest.approx(0.0)
        # No LOCK decisions were made, so lock_accuracy is undefined (None),
        # not a fabricated 0% or 100%.
        assert summary.lock_accuracy is None

    def test_a_missed_wait_produces_real_measured_regret(self, isolated_ledger) -> None:
        """Quote=15,000, realised=20,000: waiting cost real money (should
        have locked). This system's savings = 0 (waited); oracle = realised -
        quote = 5,000. Regret = 5000 - 0 = 5000."""
        entry = ledger.record_recommendation(_fake_quote_result(lock_action="WAIT", today_quote=15_000.0))
        ledger.record_outcome(entry.entry_id, realized_rate_usd_per_day=20_000.0, realized_at_date=date(2026, 10, 20))
        summary = ledger.compute_performance()
        assert summary.mean_realized_regret_usd_per_day == pytest.approx(5_000.0)

    def test_baseline_comparison_is_real_arithmetic_over_multiple_entries(self, isolated_ledger) -> None:
        e1 = ledger.record_recommendation(_fake_quote_result(lock_action="LOCK", today_quote=15_000.0))
        ledger.record_outcome(e1.entry_id, realized_rate_usd_per_day=16_000.0, realized_at_date=date(2026, 10, 1))
        e2 = ledger.record_recommendation(_fake_quote_result(lock_action="WAIT", today_quote=15_000.0))
        ledger.record_outcome(e2.entry_id, realized_rate_usd_per_day=20_000.0, realized_at_date=date(2026, 10, 15))
        summary = ledger.compute_performance()
        assert summary.n_scored == 2
        # this_system savings: [1000 (locked, correct), 0 (waited)] -> mean 500
        # always_lock savings: [1000, 5000] -> mean 3000
        assert summary.mean_savings_vs_always_lock_usd_per_day == pytest.approx(500.0 - 3000.0)


class TestDateFiltering:
    def test_from_to_filters_by_decision_timestamp(self, isolated_ledger) -> None:
        old = ledger.record_recommendation(_fake_quote_result(), decision_timestamp=datetime(2025, 1, 1, tzinfo=UTC))
        new = ledger.record_recommendation(_fake_quote_result(), decision_timestamp=datetime(2026, 6, 1, tzinfo=UTC))
        recent = ledger.read_entries(date_from=date(2026, 1, 1))
        assert {e.entry_id for e in recent} == {new.entry_id}
        all_entries = ledger.read_entries()
        assert {e.entry_id for e in all_entries} == {old.entry_id, new.entry_id}
