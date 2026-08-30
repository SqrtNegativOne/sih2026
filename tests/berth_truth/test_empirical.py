"""P2 §3 -- empirical wait/handling distributions.

Exercised against two real regimes: P1's real Paradip backfill (rich,
n=1200+) and Gangavaram (thin -- 3 archived HTML snapshots only, no
fact_port_call history at all), plus a hand-built fixture for exact
percentile arithmetic.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from berth_truth.empirical import (
    MINIMUM_SAMPLE_SIZE,
    WaitInterval,
    compute_handling_distribution,
    compute_wait_distribution,
    effective_handling_rate_tph,
    probability_wait_exceeds,
)
from berth_truth.fact_port_call import FactPortCall, FactPortCallStore
from berth_truth.sources import SourceQuality
from opt.network import PortEnum

_NAIVE_BASE = datetime(2026, 8, 1, 0, 0)


def _call(*, sha: str, idx: int, arrival_hour_offset: float, berth_hour_offset: float, **overrides) -> FactPortCall:
    """arrival_ts/berth_ts are naive, matching every real timestamp this
    codebase's real parsers produce (see providers.pdf_report's own
    documented reasoning: the real source documents never state a
    timezone)."""
    defaults = {
        "port": PortEnum.PARADIP,
        "arrival_ts": _NAIVE_BASE + timedelta(hours=arrival_hour_offset),
        "berth_ts": _NAIVE_BASE + timedelta(hours=berth_hour_offset),
        "source_url": "http://example.test", "source_quality": SourceQuality.OFFICIAL_PORT_AUTHORITY,
        "retrieved_at": datetime(2026, 8, 28, 0, 0, tzinfo=UTC), "content_sha256": sha, "row_index": idx,
        "parser_version": "test/1",
        # F-10 fix: compute_handling_distribution now filters to real
        # dry-bulk cargo (classify_cargo(cargo_raw) == "dry_bulk") before
        # computing a throughput rate, since the real store mixes in
        # liquid/gas cargo at a completely different berth type -- an
        # unset cargo_raw classifies as "unknown" and would be silently
        # excluded, which is correct for real data but would make these
        # synthetic fixtures (which exist to test the rate arithmetic
        # itself, not cargo classification) mysteriously come up empty.
        "cargo_raw": "COAL",
    }
    defaults.update(overrides)
    return FactPortCall(**defaults)


class TestHandBuiltPercentiles:
    """Exact arithmetic on a fixture of known values, so the percentile
    computation itself is checked independent of real-data noise."""

    def _store_with_known_waits(self, tmp_path) -> FactPortCallStore:
        store = FactPortCallStore(path=tmp_path / "fact_port_call.jsonl")
        # 20 calls, arrival-to-berth waits of exactly 1..20 hours -- clears
        # MINIMUM_SAMPLE_SIZE=20 exactly, with a known, hand-computable
        # median and tail.
        rows = [
            _call(sha=f"s{i}", idx=0, arrival_hour_offset=0.0, berth_hour_offset=float(i))
            for i in range(1, 21)
        ]
        store.append_many(rows)
        return store

    def test_p50_matches_hand_computed_median(self, tmp_path) -> None:
        store = self._store_with_known_waits(tmp_path)
        d = compute_wait_distribution(PortEnum.PARADIP, WaitInterval.ARRIVAL_TO_BERTH, store=store)
        assert d.is_sufficient
        assert d.n == 20
        # values 1..20, linear-interpolation median of an even count = (10+11)/2
        assert d.p50_hours == 10.5

    def test_p90_matches_hand_computed_value(self, tmp_path) -> None:
        store = self._store_with_known_waits(tmp_path)
        d = compute_wait_distribution(PortEnum.PARADIP, WaitInterval.ARRIVAL_TO_BERTH, store=store)
        # linear-interpolation P90 of 1..20 (0-indexed k = 19*0.9 = 17.1 -> between index 17 (18) and 18 (19))
        assert abs(d.p90_hours - 18.1) < 1e-9

    def test_probability_wait_exceeds_matches_hand_count(self, tmp_path) -> None:
        store = self._store_with_known_waits(tmp_path)
        p = probability_wait_exceeds(PortEnum.PARADIP, WaitInterval.ARRIVAL_TO_BERTH, 15.0, store=store)
        # values > 15: {16,17,18,19,20} = 5 of 20
        assert abs(p - 5 / 20) < 1e-9


class TestSampleFloorEnforced:
    def test_below_minimum_sample_size_reports_insufficient_not_a_percentile(self, tmp_path) -> None:
        store = FactPortCallStore(path=tmp_path / "fact_port_call.jsonl")
        rows = [
            _call(sha=f"s{i}", idx=0, arrival_hour_offset=0.0, berth_hour_offset=float(i))
            for i in range(1, MINIMUM_SAMPLE_SIZE)  # exactly one short of the floor
        ]
        store.append_many(rows)
        d = compute_wait_distribution(PortEnum.PARADIP, WaitInterval.ARRIVAL_TO_BERTH, store=store)
        assert d.n == MINIMUM_SAMPLE_SIZE - 1
        assert d.is_sufficient is False
        assert d.p50_hours is None
        assert d.p90_hours is None

    def test_at_exactly_minimum_sample_size_is_sufficient(self, tmp_path) -> None:
        store = FactPortCallStore(path=tmp_path / "fact_port_call.jsonl")
        rows = [
            _call(sha=f"s{i}", idx=0, arrival_hour_offset=0.0, berth_hour_offset=float(i))
            for i in range(1, MINIMUM_SAMPLE_SIZE + 1)
        ]
        store.append_many(rows)
        d = compute_wait_distribution(PortEnum.PARADIP, WaitInterval.ARRIVAL_TO_BERTH, store=store)
        assert d.n == MINIMUM_SAMPLE_SIZE
        assert d.is_sufficient is True

    def test_negative_interval_from_bad_data_is_excluded_not_included(self, tmp_path) -> None:
        """A row where berth_ts precedes arrival_ts (real data-quality
        artifact, e.g. a continuation row inheriting mismatched timestamps)
        must never contribute a nonsensical negative wait."""
        store = FactPortCallStore(path=tmp_path / "fact_port_call.jsonl")
        good = [
            _call(sha=f"s{i}", idx=0, arrival_hour_offset=0.0, berth_hour_offset=float(i))
            for i in range(1, MINIMUM_SAMPLE_SIZE + 1)
        ]
        bad = _call(sha="bad", idx=0, arrival_hour_offset=10.0, berth_hour_offset=5.0)  # berth BEFORE arrival
        store.append_many([*good, bad])
        d = compute_wait_distribution(PortEnum.PARADIP, WaitInterval.ARRIVAL_TO_BERTH, store=store)
        assert d.n == MINIMUM_SAMPLE_SIZE  # the bad row excluded, not counted


class TestSegmentationHierarchyFallback:
    def test_falls_back_to_coarser_level_when_specific_segment_is_thin(self, tmp_path) -> None:
        store = FactPortCallStore(path=tmp_path / "fact_port_call.jsonl")
        # Only 5 Capesize calls (too thin), but 25 total across all classes.
        capesize = [
            _call(sha=f"cape{i}", idx=0, arrival_hour_offset=0.0, berth_hour_offset=float(i), vessel_class_inferred="Capesize")
            for i in range(1, 6)
        ]
        other = [
            _call(sha=f"other{i}", idx=0, arrival_hour_offset=0.0, berth_hour_offset=float(i), vessel_class_inferred="Panamax")
            for i in range(1, 21)
        ]
        store.append_many([*capesize, *other])
        d = compute_wait_distribution(PortEnum.PARADIP, WaitInterval.ARRIVAL_TO_BERTH, vessel_class="Capesize", store=store)
        assert d.is_sufficient is True
        assert d.source_level == "port"  # coarsened away from the too-thin vessel_class level
        assert d.n == 25  # the full port-level sample, not just the 5 Capesize calls


class TestThinPortNeverFabricatesAPercentile:
    """The exact scoping rule the spec calls out: a port with insufficient
    queue history is not an unsupported port -- it just gets an honest,
    zero-sample-or-thin insufficient result here."""

    def test_gangavaram_real_thin_history_is_honestly_insufficient(self) -> None:
        d = compute_wait_distribution(PortEnum.GANGAVARAM, WaitInterval.ARRIVAL_TO_BERTH)
        assert d.is_sufficient is False
        assert d.p50_hours is None


class TestRealParadipData:
    """Sanity checks against P1's real backfill -- not exact-value
    assertions (the store can grow with future backfills), but structural
    honesty checks."""

    def test_paradip_has_a_real_sufficient_sample(self) -> None:
        d = compute_wait_distribution(PortEnum.PARADIP, WaitInterval.ARRIVAL_TO_BERTH)
        assert d.n > MINIMUM_SAMPLE_SIZE
        assert d.is_sufficient is True
        assert d.p50_hours is not None
        assert d.p90_hours >= d.p75_hours >= d.p50_hours  # percentiles must be ordered

    def test_paradip_handling_distribution_is_real_and_sufficient(self) -> None:
        h = compute_handling_distribution(PortEnum.PARADIP)
        assert h.is_sufficient is True
        assert h.norm_tpd_median is not None
        assert h.actual_tpd_median is not None
        assert h.actual_over_norm_ratio is not None

    def test_paradip_effective_rate_is_empirical_and_matches_the_conversion(self) -> None:
        """P6: real, live confirmation that the substitution actually
        happens for the one port with real, sufficient coverage today."""
        h = compute_handling_distribution(PortEnum.PARADIP)
        eff = effective_handling_rate_tph(PortEnum.PARADIP)
        assert eff.is_empirical is True
        assert eff.rate_tph == pytest.approx(h.actual_tpd_median / 24.0)
        assert eff.static_literal_tph == PortEnum.PARADIP.value.handling_rate_tph
        # The real, disclosed gap this feature exists to surface (see
        # src/opt/landed_cost.py's module docstring for the same finding
        # from the landed-cost angle): real Paradip productivity runs well
        # under the port's own declared norm.
        assert eff.rate_tph < eff.static_literal_tph

    def test_a_port_with_no_fact_port_call_coverage_falls_back_to_the_literal(self) -> None:
        eff = effective_handling_rate_tph(PortEnum.SINGAPORE)
        assert eff.is_empirical is False
        assert eff.rate_tph == PortEnum.SINGAPORE.value.handling_rate_tph
        assert eff.handling.n == 0
        assert "insufficient" in eff.reason


class TestEffectiveHandlingRateWithAFixture:
    def _store_with_known_handling(self, tmp_path, *, n: int, norm: float, actual: float) -> FactPortCallStore:
        store = FactPortCallStore(path=tmp_path / "fact_port_call.jsonl")
        rows = [
            _call(sha=f"h{i}", idx=0, arrival_hour_offset=0.0, berth_hour_offset=1.0, norm_tpd=norm, actual_tpd=actual)
            for i in range(n)
        ]
        store.append_many(rows)
        return store

    def test_sufficient_sample_uses_the_empirical_rate(self, tmp_path) -> None:
        store = self._store_with_known_handling(tmp_path, n=MINIMUM_SAMPLE_SIZE, norm=18_000.0, actual=6_000.0)
        eff = effective_handling_rate_tph(PortEnum.PARADIP, store=store)
        assert eff.is_empirical is True
        assert eff.rate_tph == pytest.approx(6_000.0 / 24.0)  # 250.0 tph
        assert eff.static_literal_tph == PortEnum.PARADIP.value.handling_rate_tph

    def test_insufficient_sample_keeps_the_static_literal_unchanged(self, tmp_path) -> None:
        store = self._store_with_known_handling(tmp_path, n=MINIMUM_SAMPLE_SIZE - 1, norm=18_000.0, actual=6_000.0)
        eff = effective_handling_rate_tph(PortEnum.PARADIP, store=store)
        assert eff.is_empirical is False
        assert eff.rate_tph == PortEnum.PARADIP.value.handling_rate_tph
        assert "insufficient" in eff.reason

    def test_empty_actual_tpd_falls_back_even_with_a_large_sample(self, tmp_path) -> None:
        """A real edge case the sufficiency gate alone would miss: plenty of
        rows, but none carry a real actual_tpd value (e.g. a source that
        only ever reports norm_tpd)."""
        store = FactPortCallStore(path=tmp_path / "fact_port_call.jsonl")
        rows = [
            _call(sha=f"h{i}", idx=0, arrival_hour_offset=0.0, berth_hour_offset=1.0, norm_tpd=18_000.0, actual_tpd=None)
            for i in range(MINIMUM_SAMPLE_SIZE + 5)
        ]
        store.append_many(rows)
        eff = effective_handling_rate_tph(PortEnum.PARADIP, store=store)
        assert eff.is_empirical is False
        assert eff.rate_tph == PortEnum.PARADIP.value.handling_rate_tph
