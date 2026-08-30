"""P6 backhaul opportunity scoring: opportunity score on real observed pairs;
$/MT credit applied only when documented validation passes (it does not,
today -- structural, see evaluate_credit_evidence); no pairs -> no score."""
from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from berth_truth.fact_port_call import FactPortCall, FactPortCallStore
from berth_truth.sources import SourceQuality
from opt.backhaul import (
    CREDIT_EVIDENCE,
    MIN_PAIRING_OBS,
    backhaul_opportunity_score,
    evaluate_credit_evidence,
    observed_pairing_evidence,
)
from opt.network import PortEnum
from opt.types import Vessel, VesselClass


def _row(*, sha: str, idx: int = 0, **overrides) -> FactPortCall:
    defaults = {
        "port": PortEnum.PARADIP,
        "vessel_name": "TEST VESSEL",
        "source_url": "http://example.test",
        "source_quality": SourceQuality.OFFICIAL_PORT_AUTHORITY,
        "retrieved_at": datetime(2026, 8, 28, 12, 0, 0, tzinfo=UTC),
        "content_sha256": sha,
        "row_index": idx,
        "parser_version": "test/1",
    }
    defaults.update(overrides)
    return FactPortCall(**defaults)


def _vessel(vessel_class: VesselClass = VesselClass.SUPRAMAX, port: PortEnum = PortEnum.PARADIP) -> Vessel:
    return Vessel(
        vessel_id="V1", vessel_class=vessel_class, current_port=port, status="idle",
        available_from=date(2026, 1, 1), available_until=None,
        dwt=55_000, draft_m=12.0, loa_m=190.0, beam_m=32.0, speed_kn=12.0,
        laden_fuel_consumption_tpd=30.0, ballast_fuel_consumption_tpd=25.0,
    )


class TestCreditEvidence:
    """The prompt's own required check: does the data support converting the
    score into a $/MT credit? Live schema introspection, not an assertion."""

    def test_no_rate_field_exists_on_fact_port_call_today(self) -> None:
        verdict = evaluate_credit_evidence()
        assert verdict.sufficient_for_credit is False
        assert verdict.rate_bearing_fields_found == ()
        assert "no rate/freight/$ field" in verdict.reason

    def test_module_level_constant_matches_a_fresh_call(self) -> None:
        """CREDIT_EVIDENCE is computed once at import time -- confirm it
        agrees with calling the function again live, not stale."""
        fresh = evaluate_credit_evidence()
        assert CREDIT_EVIDENCE.sufficient_for_credit == fresh.sufficient_for_credit
        assert CREDIT_EVIDENCE.rate_bearing_fields_found == fresh.rate_bearing_fields_found


class TestPairingEvidenceOnRealData:
    """Real berth_truth data: only Paradip has any coverage today (confirmed
    by direct query -- 1224/1224 rows), which is itself a real, load-bearing
    fact this module must handle honestly, not paper over."""

    def test_same_port_pairing_is_computed_from_real_data(self) -> None:
        ev = observed_pairing_evidence(PortEnum.PARADIP, PortEnum.PARADIP)
        assert ev.n_total_vessels > 0
        assert ev.load_port_has_coverage is True
        assert ev.pairing_rate is not None
        assert 0.0 <= ev.pairing_rate <= 1.0
        assert ev.n_paired_vessels <= ev.n_total_vessels
        assert ev.is_sufficient == (ev.n_total_vessels >= MIN_PAIRING_OBS)

    def test_cross_port_pairing_against_an_uncovered_port_is_honestly_flagged(self) -> None:
        """Gangavaram has zero real fact_port_call rows -- pairing_rate must
        not read as "observed and zero" (that would misrepresent a coverage
        gap as evidence)."""
        ev = observed_pairing_evidence(PortEnum.PARADIP, PortEnum.GANGAVARAM)
        assert ev.cross_port is True
        assert ev.n_total_vessels > 0  # Paradip's own population is real and nonzero
        assert ev.n_paired_vessels == 0  # no vessel can match against zero load-port rows
        assert ev.load_port_has_coverage is False  # the honest reason why
        assert ev.is_sufficient is False  # must not be trusted despite n_total >= MIN_PAIRING_OBS

    def test_same_port_pairing_is_symmetric_with_itself(self) -> None:
        a = observed_pairing_evidence(PortEnum.PARADIP, PortEnum.PARADIP)
        b = observed_pairing_evidence(PortEnum.PARADIP, PortEnum.PARADIP)
        assert a == b


class TestPairingEvidenceWithAControlledFixture:
    """Isolated from real data (tmp_path store) -- the precedent already
    established by tests/berth_truth/test_fact_port_call.py and
    tests/berth_truth/test_empirical.py for exactly this reason."""

    def test_no_pairs_reports_zero_not_a_fabricated_rate(self, tmp_path) -> None:
        store = FactPortCallStore(path=tmp_path / "fact_port_call.jsonl")
        # 6 vessels, each discharges only -- never loads. Real "no pairs" case.
        rows = [
            _row(sha=f"v{i}", vessel_name=f"VESSEL {i}", load_discharge="DISCHARGE")
            for i in range(6)
        ]
        store.append_many(rows)
        ev = observed_pairing_evidence(PortEnum.PARADIP, PortEnum.PARADIP, store=store)
        assert ev.n_total_vessels == 6
        assert ev.n_paired_vessels == 0
        assert ev.pairing_rate == 0.0
        assert ev.is_sufficient is True  # 6 >= MIN_PAIRING_OBS=5, a real, trustworthy zero

    def test_a_real_turnaround_is_counted(self, tmp_path) -> None:
        store = FactPortCallStore(path=tmp_path / "fact_port_call.jsonl")
        rows = [
            _row(sha="a", idx=0, vessel_name="TURNAROUND VESSEL", load_discharge="DISCHARGE"),
            _row(sha="a", idx=1, vessel_name="TURNAROUND VESSEL", load_discharge="LOAD"),
            _row(sha="b", idx=0, vessel_name="DISCHARGE ONLY VESSEL", load_discharge="DISCHARGE"),
        ]
        store.append_many(rows)
        ev = observed_pairing_evidence(PortEnum.PARADIP, PortEnum.PARADIP, store=store)
        assert ev.n_total_vessels == 2
        assert ev.n_paired_vessels == 1
        assert ev.pairing_rate == 0.5

    def test_no_vessel_name_is_excluded_not_miscounted(self, tmp_path) -> None:
        store = FactPortCallStore(path=tmp_path / "fact_port_call.jsonl")
        store.append_many([_row(sha="x", vessel_name=None, load_discharge="LOAD")])
        ev = observed_pairing_evidence(PortEnum.PARADIP, PortEnum.PARADIP, store=store)
        assert ev.n_total_vessels == 0
        assert ev.pairing_rate is None  # nothing to divide by -- never a fabricated 0.0

    def test_below_threshold_is_flagged_insufficient(self, tmp_path) -> None:
        store = FactPortCallStore(path=tmp_path / "fact_port_call.jsonl")
        store.append_many([_row(sha="only-one", vessel_name="LONE VESSEL", load_discharge="DISCHARGE")])
        ev = observed_pairing_evidence(PortEnum.PARADIP, PortEnum.PARADIP, store=store)
        assert ev.n_total_vessels == 1
        assert ev.is_sufficient is False

    def test_genuine_cross_port_pairing_is_counted_when_dates_confirm_the_sequence(self, tmp_path) -> None:
        """The real cross-port path (discharge at A, then load at B) is
        never exercised by real data today (single-port BT coverage, see
        module docstring) -- this fixture is the only place that ordering
        logic is actually tested end to end."""
        store = FactPortCallStore(path=tmp_path / "fact_port_call.jsonl")
        rows = [
            # Discharges at Paradip on day 1, loads at Dhamra on day 5 -- a
            # real backhaul pattern.
            _row(sha="a", idx=0, port=PortEnum.PARADIP, vessel_name="BACKHAUL VESSEL",
                 load_discharge="DISCHARGE", arrival_ts=datetime(2026, 1, 1, 0, 0)),
            _row(sha="a", idx=1, port=PortEnum.DHAMRA, vessel_name="BACKHAUL VESSEL",
                 load_discharge="LOAD", arrival_ts=datetime(2026, 1, 5, 0, 0)),
            # Present at both ports, but LOADS at Dhamra BEFORE discharging
            # at Paradip -- not a valid backhaul sequence, must not count.
            _row(sha="b", idx=0, port=PortEnum.PARADIP, vessel_name="WRONG ORDER VESSEL",
                 load_discharge="DISCHARGE", arrival_ts=datetime(2026, 1, 10, 0, 0)),
            _row(sha="b", idx=1, port=PortEnum.DHAMRA, vessel_name="WRONG ORDER VESSEL",
                 load_discharge="LOAD", arrival_ts=datetime(2026, 1, 2, 0, 0)),
            # Only ever at Paradip -- not present at Dhamra at all.
            _row(sha="c", idx=0, port=PortEnum.PARADIP, vessel_name="PARADIP ONLY VESSEL",
                 load_discharge="DISCHARGE", arrival_ts=datetime(2026, 1, 3, 0, 0)),
        ]
        store.append_many(rows)
        ev = observed_pairing_evidence(PortEnum.PARADIP, PortEnum.DHAMRA, store=store)
        assert ev.cross_port is True
        assert ev.load_port_has_coverage is True
        assert ev.n_total_vessels == 3
        assert ev.n_paired_vessels == 1  # only BACKHAUL VESSEL has a real discharge-then-load sequence
        assert ev.pairing_rate == pytest.approx(1 / 3)


class TestBackhaulOpportunityScoreEndToEnd:
    def test_score_is_bounded_and_credit_is_always_none(self) -> None:
        v = _vessel(port=PortEnum.PARADIP)
        result = backhaul_opportunity_score(v, PortEnum.PARADIP, PortEnum.DHAMRA)
        assert 0.0 <= result.score <= 1.0
        assert result.credit_usd_per_mt is None
        assert result.credit_evidence_reason == CREDIT_EVIDENCE.reason
        assert CREDIT_EVIDENCE.reason in result.limitations

    def test_no_pairs_with_sufficient_evidence_zeroes_the_score(self, tmp_path, monkeypatch) -> None:
        """The one real veto path: sufficient, real evidence of zero
        turnaround at the discharge==load port zeroes the score outright,
        regardless of how strong the hazard/feasibility inputs are."""
        store = FactPortCallStore(path=tmp_path / "fact_port_call.jsonl")
        rows = [
            _row(sha=f"v{i}", vessel_name=f"VESSEL {i}", load_discharge="DISCHARGE")
            for i in range(6)
        ]
        store.append_many(rows)
        v = _vessel(port=PortEnum.PARADIP)
        result = backhaul_opportunity_score(v, PortEnum.PARADIP, PortEnum.PARADIP, store=store)
        assert result.pairing_evidence.n_paired_vessels == 0
        assert result.pairing_evidence.is_sufficient is True
        assert result.score == 0.0
        assert any("score zeroed" in note for note in result.limitations)

    def test_infeasible_class_at_port_zeroes_the_score(self) -> None:
        """A vessel whose DWT (400,000t) exceeds Paradip's real, published
        max_dwt (75,000t, opt.network.Port, PORTENUM_FALLBACK path -- Paradip
        carries no berth_truth register entry so this check is deterministic,
        unlike the 3 register ports where an unpublished-declaration berth can
        pass on untested dimensions). Reuses opt.voyage's real feasibility
        check, not a second one -- confirmed infeasible directly before
        relying on it here, so this is not a defensive branch that never
        actually fires."""
        huge = Vessel(
            vessel_id="V2", vessel_class=VesselClass.CAPESIZE, current_port=PortEnum.PARADIP,
            status="idle", available_from=date(2026, 1, 1), available_until=None,
            dwt=400_000, draft_m=25.0, loa_m=340.0, beam_m=60.0, speed_kn=12.0,
            laden_fuel_consumption_tpd=45.0, ballast_fuel_consumption_tpd=38.0,
        )
        result = backhaul_opportunity_score(huge, PortEnum.PARADIP, PortEnum.PARADIP)
        assert result.class_feasibility.is_feasible is False
        assert result.score == 0.0
        assert any("feasibility" in note for note in result.limitations)

    def test_timing_infeasible_zeroes_the_score(self) -> None:
        v = _vessel(port=PortEnum.PARADIP)
        result = backhaul_opportunity_score(v, PortEnum.PARADIP, PortEnum.HAMPTON_ROADS, assumed_window_days=1)
        assert result.timing_feasible is False
        assert result.score == 0.0

    def test_limitations_mention_uncovered_load_port_for_a_cross_port_candidate(self) -> None:
        v = _vessel(port=PortEnum.PARADIP)
        result = backhaul_opportunity_score(v, PortEnum.PARADIP, PortEnum.GANGAVARAM)
        assert result.pairing_evidence.load_port_has_coverage is False
        assert any("no real berth_truth" in note for note in result.limitations)


class TestScoreUsd:
    """F-18: the score used to be a bare probability -- no distance, no cost,
    no money. score_usd puts real economics behind it, the same shape
    opt.repositioning already uses for the equivalent idle-vessel decision."""

    def test_no_base_tce_leaves_score_usd_none_not_fabricated(self) -> None:
        v = _vessel(port=PortEnum.PARADIP)
        result = backhaul_opportunity_score(v, PortEnum.PARADIP, PortEnum.DHAMRA)
        assert result.base_tce_usd_per_day is None
        assert result.score_usd is None
        assert result.ballast_cost_usd > 0.0
        assert any("score_usd could not be computed" in note for note in result.limitations)

    def test_a_real_base_tce_produces_a_real_dollar_score(self) -> None:
        v = _vessel(port=PortEnum.PARADIP)
        result = backhaul_opportunity_score(
            v, PortEnum.PARADIP, PortEnum.DHAMRA, base_tce_usd_per_day=20_000.0,
        )
        assert result.base_tce_usd_per_day == 20_000.0
        assert result.score_usd is not None
        expected = result.cargo_probability * 20_000.0 * result.assumed_window_days - result.ballast_cost_usd
        assert result.score_usd == pytest.approx(expected)

    def test_ballast_cost_matches_real_distance_and_fuel_consumption(self) -> None:
        v = _vessel(port=PortEnum.PARADIP)
        result = backhaul_opportunity_score(v, PortEnum.PARADIP, PortEnum.DHAMRA)
        from opt.network import BLENDED_BUNKER_USD_PER_TONNE

        expected = result.ballast_days * v.ballast_fuel_consumption_tpd * BLENDED_BUNKER_USD_PER_TONNE
        assert result.ballast_cost_usd == pytest.approx(expected)

    def test_infeasible_zeroes_score_usd_too_not_just_score(self) -> None:
        v = _vessel(port=PortEnum.PARADIP)
        result = backhaul_opportunity_score(
            v, PortEnum.PARADIP, PortEnum.HAMPTON_ROADS,
            assumed_window_days=1, base_tce_usd_per_day=20_000.0,
        )
        assert result.timing_feasible is False
        assert result.score == 0.0
        assert result.score_usd == 0.0
