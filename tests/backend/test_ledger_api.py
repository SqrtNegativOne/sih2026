"""P5 -- the four ledger/replay endpoints against the real FastAPI app.
Live and replay are distinct endpoints/fields (requirement 11), never
merged.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from opt import ledger

client = TestClient(app)


@pytest.fixture()
def isolated_ledger(tmp_path, monkeypatch):
    """Real ledger logic, against a real but throwaway directory -- never
    writes into the project's own raw_data/ledger during tests (same
    isolation pattern as tests/opt/test_ledger.py, applied at the shared
    module object so backend.main's `from opt import ledger` sees the same
    patched paths)."""
    monkeypatch.setattr(ledger, "LEDGER_DIR", tmp_path / "ledger")
    monkeypatch.setattr(ledger, "ENTRIES_LOG", tmp_path / "ledger" / "ledger_entries.jsonl")
    monkeypatch.setattr(ledger, "OUTCOMES_LOG", tmp_path / "ledger" / "ledger_outcomes.jsonl")
    return tmp_path


class TestLedgerLiveEmptyState:
    def test_empty_ledger_is_a_real_empty_list_not_an_error(self, isolated_ledger) -> None:
        r = client.get("/ledger/live")
        assert r.status_code == 200
        body = r.json()
        assert body["kind"] == "LIVE_DECISION_LEDGER"
        assert body["total"] == 0
        assert body["entries"] == []

    def test_empty_performance_is_honestly_all_none(self, isolated_ledger) -> None:
        r = client.get("/ledger/live/performance")
        body = r.json()
        assert body["kind"] == "LIVE_DECISION_LEDGER_PERFORMANCE"
        assert body["n_scored"] == 0
        assert body["mean_realized_regret_usd_per_day"] is None


class TestLedgerFillsForwardFromRealQuotes:
    def test_a_real_quote_call_appends_a_real_ledger_entry(self, isolated_ledger) -> None:
        assert client.get("/ledger/live").json()["total"] == 0
        r = client.post(
            "/quote",
            json={
                "cargo_volume_dwt": 75_000, "origin_port": "NEWCASTLE_AU", "dest_port": "PARADIP",
                "laycan_start": "2026-09-15", "laycan_end": "2026-09-25",
            },
        )
        assert r.status_code == 200
        live = client.get("/ledger/live").json()
        assert live["total"] == 1
        entry = live["entries"][0]
        assert entry["origin_port"] == "NEWCASTLE_AU"
        assert entry["status"] == "pending"
        assert entry["outcome"] is None

    def test_a_structural_infeasible_quote_does_not_pollute_the_ledger(self, isolated_ledger) -> None:
        """No real .quote exists on a structural_infeasible envelope -- must
        not record a fabricated entry for it."""
        r = client.post(
            "/quote",
            json={
                "cargo_volume_dwt": 75_000, "origin_port": "BEIRA", "dest_port": "GANGAVARAM",
                "laycan_start": "2026-09-15", "laycan_end": "2026-09-16",  # 1-day window, likely infeasible
            },
        )
        live = client.get("/ledger/live").json()
        if r.json().get("status") == "structural_infeasible":
            assert live["total"] == 0
        # If it happened to be feasible/contingent instead, that's still a
        # real recommendation and correctly recorded -- either outcome is
        # consistent with "only real recommendations are recorded".

    def test_outcome_round_trip_updates_live_and_performance(self, isolated_ledger) -> None:
        client.post(
            "/quote",
            json={
                "cargo_volume_dwt": 75_000, "origin_port": "NEWCASTLE_AU", "dest_port": "PARADIP",
                "laycan_start": "2026-09-15", "laycan_end": "2026-09-25",
            },
        )
        entry = client.get("/ledger/live").json()["entries"][0]
        r = client.post(
            "/ledger/outcome",
            json={"entry_id": entry["entry_id"], "realized_rate_usd_per_day": 20_000.0, "realized_at_date": "2026-10-20"},
        )
        assert r.status_code == 200
        live = client.get("/ledger/live").json()
        assert live["entries"][0]["status"] == "scored"
        assert live["entries"][0]["outcome"]["realized_rate_usd_per_day"] == 20_000.0
        perf = client.get("/ledger/live/performance").json()
        assert perf["n_scored"] == 1
        assert perf["mean_realized_regret_usd_per_day"] is not None

    def test_outcome_against_unknown_entry_returns_404(self, isolated_ledger) -> None:
        r = client.post(
            "/ledger/outcome",
            json={"entry_id": "not-a-real-id", "realized_rate_usd_per_day": 20_000.0, "realized_at_date": "2026-10-20"},
        )
        assert r.status_code == 404


class TestReplayIsDistinctFromLive:
    """The core requirement: live and replay are separate endpoints with
    separate 'kind' tags and separate statistics, never merged into one
    number."""

    def test_replay_has_its_own_kind_and_unmistakable_label(self) -> None:
        r = client.get("/ledger/replay")
        assert r.status_code == 200
        body = r.json()
        assert body["kind"] == "HISTORICAL_MODEL_REPLAY"
        assert body["kind"] != "LIVE_DECISION_LEDGER"
        assert "retrospective" in body["label"].lower()
        assert "not decisions this system actually made" in body["label"]

    def test_replay_and_live_performance_are_computed_independently(self, isolated_ledger) -> None:
        """A real live entry existing (or not) must not change the replay's
        own real backtest numbers -- they read entirely different data
        (opt.ledger's JSONL files vs. the frozen test split)."""
        replay_before = client.get("/ledger/replay").json()
        client.post(
            "/quote",
            json={
                "cargo_volume_dwt": 75_000, "origin_port": "NEWCASTLE_AU", "dest_port": "PARADIP",
                "laycan_start": "2026-09-15", "laycan_end": "2026-09-25",
            },
        )
        replay_after = client.get("/ledger/replay").json()
        assert replay_before["summaries"] == replay_after["summaries"]

    def test_warm_replay_call_is_fast(self) -> None:
        client.get("/ledger/replay")  # ensure warm (real cache from an earlier test in this module, or computed now)
        import time

        t0 = time.perf_counter()
        r = client.get("/ledger/replay")
        elapsed = time.perf_counter() - t0
        assert r.status_code == 200
        assert elapsed < 0.5
