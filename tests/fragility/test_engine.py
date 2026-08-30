"""Integration tests for analyze_fragility against the real engines -- real
register data (berth_truth), real PortEnum limits, real trained forecasts.
No mocking of opt.quote/opt.fleetmix/opt.voyage: these tests are the actual
proof the sweep works end to end, not just that search.py's algorithm is
sound (see test_search.py for that, against synthetic evaluators).

Kept deliberately scoped per test (searching only the variables each test
actually needs, via ``variables=[...]``) to keep total suite runtime
reasonable -- Tier 3 evaluations are real CP-SAT + LSMC solves, measured at
roughly 1-3 seconds each against real data.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from fragility.engine import analyze_fragility
from fragility.models import Provenance, Tier, VariableId
from ml.live_forecast import latest_available_date
from opt.network import PortEnum

TODAY = latest_available_date()
LAYCAN_START = TODAY + timedelta(days=21)
LAYCAN_END = TODAY + timedelta(days=35)


class TestTier1CargoClassBoundary:
    def test_flip_matches_class_reference_dwt_exactly(self) -> None:
        """75,000 dwt derives Panamax (58,000 < 75,000 <= 82,000). Increasing
        must flip to Capesize exactly at the real CLASS_REFERENCE_DWT[PANAMAX]
        boundary (82,000), not an approximation of it."""
        report = analyze_fragility(
            cargo_volume_dwt=75_000, origin_port=PortEnum.NEWCASTLE_AU, dest_port=PortEnum.PARADIP,
            laycan_start=LAYCAN_START, laycan_end=LAYCAN_END, as_of=TODAY,
            variables=[VariableId.CARGO_VOLUME_DWT],
        )
        fp = report.findings[0]
        assert fp.flip_found is True
        assert fp.tier is Tier.TIER1_CLOSED_FORM
        assert 82_000.0 < fp.flip_value <= 82_000.0 + 500.0  # within the 500 dwt tolerance, on the flipped side
        assert fp.changed_components == ("target_vessel_class",)
        assert fp.flipped_signature.target_vessel_class == "Capesize"

    def test_evaluations_used_is_small_for_an_exact_closed_form_boundary(self) -> None:
        report = analyze_fragility(
            cargo_volume_dwt=75_000, origin_port=PortEnum.NEWCASTLE_AU, dest_port=PortEnum.PARADIP,
            laycan_start=LAYCAN_START, laycan_end=LAYCAN_END, as_of=TODAY,
            variables=[VariableId.CARGO_VOLUME_DWT],
        )
        assert report.findings[0].evaluations_used < 20


class TestTier1DraftMatchesRegisterArithmeticExactly:
    """The literal requirement: 'a draft flip found in closed form matches
    register arithmetic exactly.' Covers both real sources the resolver can
    land on: a PortEnum-fallback literal (Richards Bay, 17.5m, no register
    entry) and an actual berth_truth register value (Gangavaram berth B5,
    18.0m, Dry Bulk) -- each pairing is chosen so that specific source is
    unambiguously the binding constraint, so the flip value is traceable to
    one known real number either way.
    """

    def test_flip_value_matches_richards_bay_portenum_literal(self) -> None:
        assert PortEnum.RICHARDS_BAY.value.max_draft_m == 17.5  # the premise, checked directly
        report = analyze_fragility(
            cargo_volume_dwt=75_000, origin_port=PortEnum.RICHARDS_BAY, dest_port=PortEnum.GANGAVARAM,
            laycan_start=LAYCAN_START, laycan_end=LAYCAN_END, as_of=TODAY,
            variables=[VariableId.VESSEL_DRAFT_M],
        )
        fp = report.findings[0]
        assert fp.flip_found is True
        assert abs(fp.flip_value - 17.5) <= 0.05 + 1e-9  # exactly the search tolerance
        assert fp.flip_value > 17.5  # on the flipped (infeasible) side, never the feasible side

    def test_flip_value_matches_a_real_gangavaram_register_berth(self) -> None:
        """Pairing Gangavaram with itself (origin == dest) makes the tie-break
        in _binding_port_verdict pick Gangavaram unambiguously, so this
        exercises an actual LimitSource.REGISTER value (berth B5, Dry Bulk,
        margin 3.5m over the 14.5m Panamax draft used here == a 18.0m
        permissible draft) rather than a PortEnum fallback -- confirmed
        directly against opt.voyage._vessel_can_call before writing this
        assertion: berth_id='B5', limit_source=REGISTER, draft_margin_m=3.5.
        """
        report = analyze_fragility(
            cargo_volume_dwt=75_000, origin_port=PortEnum.GANGAVARAM, dest_port=PortEnum.GANGAVARAM,
            laycan_start=LAYCAN_START, laycan_end=LAYCAN_END, as_of=TODAY,
            variables=[VariableId.VESSEL_DRAFT_M, VariableId.PERMISSIBLE_DRAFT_M],
        )
        vessel_fp, limit_fp = report.findings
        assert vessel_fp.flip_found is True
        assert abs(vessel_fp.flip_value - 18.0) <= 0.05 + 1e-9  # B5's real register limit, not a PortEnum literal
        assert abs(limit_fp.base_value - 18.0) <= 1e-9  # the resolved current limit itself, exactly
        assert limit_fp.provenance is Provenance.OBSERVED  # a cited register value -- NOT a PortEnum fallback

    def test_permissible_draft_flip_is_symmetric_with_vessel_draft(self) -> None:
        """Perturbing the limit downward from exactly the vessel's own draft
        must flip at (approximately) the same point perturbing the vessel's
        draft upward does -- same margin, viewed from the other side."""
        report = analyze_fragility(
            cargo_volume_dwt=75_000, origin_port=PortEnum.NEWCASTLE_AU, dest_port=PortEnum.GANGAVARAM,
            laycan_start=LAYCAN_START, laycan_end=LAYCAN_END, as_of=TODAY,
            variables=[VariableId.VESSEL_DRAFT_M, VariableId.PERMISSIBLE_DRAFT_M],
        )
        vessel_fp, limit_fp = report.findings
        assert vessel_fp.direction == "increase"
        assert limit_fp.direction == "decrease"
        assert abs(vessel_fp.flip_value - limit_fp.base_value) <= 0.1
        assert limit_fp.provenance is Provenance.ASSUMPTION  # Newcastle is a PortEnum-fallback port


class TestTier1UnavailableNeverSearchesTier3:
    """Requirement 3, literally: NOT_PUBLISHED / STALE_OR_UNAVAILABLE draft
    triggers no search at all -- Dhamra at both ends guarantees the binding
    port's own draft is genuinely unresolved (BT-2's own design: no
    declarations are wired into this call chain), not merely untested by
    coincidence of which port 'won' the binding comparison.
    """

    def test_dhamra_both_ends_reports_unavailable_with_zero_evaluations(self) -> None:
        report = analyze_fragility(
            cargo_volume_dwt=75_000, origin_port=PortEnum.DHAMRA, dest_port=PortEnum.DHAMRA,
            laycan_start=LAYCAN_START, laycan_end=LAYCAN_END, as_of=TODAY,
            variables=[VariableId.VESSEL_DRAFT_M, VariableId.PERMISSIBLE_DRAFT_M],
        )
        for fp in report.findings:
            assert fp.unavailable_reason is not None
            assert "STALE_OR_UNAVAILABLE" in fp.unavailable_reason
            assert fp.evaluations_used == 0
            assert fp.flip_found is False

    def test_unavailable_is_never_silently_reported_as_no_flip_found_in_range(self) -> None:
        """An UNAVAILABLE finding must be distinguishable from a genuine
        'searched the whole range and found nothing' result -- the former
        has unavailable_reason set and zero range searched; the latter has
        a real, non-trivial range and no unavailable_reason."""
        report = analyze_fragility(
            cargo_volume_dwt=75_000, origin_port=PortEnum.DHAMRA, dest_port=PortEnum.DHAMRA,
            laycan_start=LAYCAN_START, laycan_end=LAYCAN_END, as_of=TODAY,
            variables=[VariableId.VESSEL_DRAFT_M],
        )
        fp = report.findings[0]
        assert fp.unavailable_reason is not None
        assert fp.range_searched_low is None
        assert fp.range_searched_high is None


class TestWaitDaysAlwaysUnavailable:
    def test_origin_and_dest_wait_days_are_unavailable_with_zero_evaluations(self) -> None:
        report = analyze_fragility(
            cargo_volume_dwt=75_000, origin_port=PortEnum.NEWCASTLE_AU, dest_port=PortEnum.PARADIP,
            laycan_start=LAYCAN_START, laycan_end=LAYCAN_END, as_of=TODAY,
            variables=[VariableId.ORIGIN_WAIT_DAYS, VariableId.DEST_WAIT_DAYS],
        )
        assert report.evaluations_used == 1  # only the one baseline Tier 3 call, no per-variable search
        for fp in report.findings:
            assert fp.unavailable_reason is not None
            assert fp.evaluations_used == 0
            # F-30: the reason is user-facing (rendered verbatim on the
            # Fragility screen), so it must read as plain English, not name
            # internal modules or functions -- check the substance (it's
            # about wait-time, and it says there's no lever to search) not
            # a specific code identifier.
            assert "wait" in fp.unavailable_reason.lower()
            assert "no lever" in fp.unavailable_reason.lower() or "can't be tested" in fp.unavailable_reason.lower()

    def test_still_unavailable_after_re_verification(self) -> None:
        """Origin and dest wait-days independently resolve UNAVAILABLE, not
        just when swept together -- re-verified directly against the
        current opt.stopping/opt.fleetmix signatures (no wait-day
        parameter on either), same result as the combined sweep above."""
        report = analyze_fragility(
            cargo_volume_dwt=75_000, origin_port=PortEnum.NEWCASTLE_AU, dest_port=PortEnum.PARADIP,
            laycan_start=LAYCAN_START, laycan_end=LAYCAN_END, as_of=TODAY,
            variables=[VariableId.ORIGIN_WAIT_DAYS],
        )
        reason = report.findings[0].unavailable_reason
        assert reason is not None
        assert "wait" in reason.lower()


class TestEmpiricalWaitContextOnUnavailableFindings:
    """P5 requirement 1: empirical berth wait (P50 and P90 separately),
    attached as real context even though the variable stays UNAVAILABLE for
    search -- 'empirical waits only where evidence exists' is the literal
    requirement; this proves both directions (real evidence, and none)."""

    def test_paradip_has_a_real_sufficient_empirical_sample(self) -> None:
        """P1's real backfill covers Paradip specifically (1,224+ real rows)."""
        report = analyze_fragility(
            cargo_volume_dwt=75_000, origin_port=PortEnum.NEWCASTLE_AU, dest_port=PortEnum.PARADIP,
            laycan_start=LAYCAN_START, laycan_end=LAYCAN_END, as_of=TODAY,
            variables=[VariableId.DEST_WAIT_DAYS],
        )
        ctx = report.findings[0].berth_truth_context
        assert ctx is not None
        assert ctx.empirical_wait_sample_n is not None and ctx.empirical_wait_sample_n > 100
        assert ctx.empirical_wait_is_sufficient is True
        assert ctx.empirical_wait_p50_hours is not None
        assert ctx.empirical_wait_p90_hours is not None
        assert ctx.empirical_wait_p50_hours <= ctx.empirical_wait_p90_hours  # p50 never exceeds p90

    def test_a_port_with_no_fact_port_call_ingestion_reports_zero_not_fabricated(self) -> None:
        """Gangavaram has a real BT-1 register but no P1 fact_port_call
        backfill -- the honest real answer is n=0, is_sufficient=False, never
        a guessed percentile."""
        report = analyze_fragility(
            cargo_volume_dwt=75_000, origin_port=PortEnum.GANGAVARAM, dest_port=PortEnum.NEWCASTLE_AU,
            laycan_start=LAYCAN_START, laycan_end=LAYCAN_END, as_of=TODAY,
            variables=[VariableId.ORIGIN_WAIT_DAYS],
        )
        ctx = report.findings[0].berth_truth_context
        assert ctx is not None
        assert ctx.empirical_wait_sample_n == 0
        assert ctx.empirical_wait_is_sufficient is False
        assert ctx.empirical_wait_p50_hours is None
        assert ctx.empirical_wait_p90_hours is None

    def test_still_flip_found_false_regardless_of_how_rich_the_context_is(self) -> None:
        """The richest possible empirical context (Paradip) must not weaken
        the UNAVAILABLE-for-search verdict -- context and searchability are
        deliberately independent."""
        report = analyze_fragility(
            cargo_volume_dwt=75_000, origin_port=PortEnum.NEWCASTLE_AU, dest_port=PortEnum.PARADIP,
            laycan_start=LAYCAN_START, laycan_end=LAYCAN_END, as_of=TODAY,
            variables=[VariableId.DEST_WAIT_DAYS],
        )
        fp = report.findings[0]
        assert fp.flip_found is False
        assert fp.unavailable_reason is not None
        assert fp.berth_truth_context is not None
        assert fp.berth_truth_context.empirical_wait_is_sufficient is True


class TestDraftAndTideContext:
    def test_a_register_backed_port_gets_real_draft_and_tide_context(self) -> None:
        report = analyze_fragility(
            cargo_volume_dwt=75_000, origin_port=PortEnum.GANGAVARAM, dest_port=PortEnum.GANGAVARAM,
            laycan_start=LAYCAN_START, laycan_end=LAYCAN_END, as_of=TODAY,
            variables=[VariableId.VESSEL_DRAFT_M],
        )
        ctx = report.findings[0].berth_truth_context
        assert ctx is not None
        assert ctx.draft_source is not None
        assert ctx.tide_impact in ("NONE", "CONDITIONAL", "BLOCKING")

    def test_a_portenum_fallback_port_gets_no_context(self) -> None:
        """No berth-level document exists for a PortEnum-fallback port --
        None, not a context with every field empty."""
        report = analyze_fragility(
            cargo_volume_dwt=75_000, origin_port=PortEnum.RICHARDS_BAY, dest_port=PortEnum.NEWCASTLE_AU,
            laycan_start=LAYCAN_START, laycan_end=LAYCAN_END, as_of=TODAY,
            variables=[VariableId.VESSEL_DRAFT_M],
        )
        assert report.findings[0].berth_truth_context is None


class TestContingentInfeasibleIsARealReachableStatus:
    """Requirement 1 / the TESTS list's 'a feasible -> contingent_infeasible
    transition is recorded as a flip' is proven at the mechanism level in
    test_search.py::TestFeasibleToContingentInfeasibleIsAFlip, against the
    exact same DecisionSignature.changed_fields comparison engine.py uses
    for real Tier 3 signatures (see _search_variable in engine.py -- there
    is no second comparison path for 'real' vs 'synthetic' signatures).

    What that test does not show is a real base value in analyze_fragility's
    own v1 variable set actually crossing that boundary. This test grounds
    the other half: that contingent_infeasible is a real, reachable status
    quote_envelope produces from real data (not a value only test_search.py's
    synthetic evaluator can produce) -- Beira (PortEnum max_dwt=30,000) is
    exceeded by every one of the four real vessel classes, so the direct
    solve has zero feasible configurations and only the widen+transshipment
    relaxation rescues it.

    Searched but NOT found within this task: a real port pair/cargo where
    one of the two variables that actually reach quote_envelope's fleet-mix
    feasibility (laycan_width_days does not -- enumerate_fleet_mix takes no
    laycan argument at all, confirmed by reading its signature; risk_tolerance
    is hardcoded to 0.0 inside fleet-mix pricing, confirmed in BT-2/DF-1;
    only contract_term_days plausibly could, via forecast-horizon exhaustion)
    drives a *search-discovered* crossing of this specific boundary. Every
    contract_term_days value tried up to _MAX_TERM_DAYS (1,830 days) on
    multiple real routes stayed feasible. Documented as a real limitation in
    the DF-1 completion report rather than papered over with a synthetic-only
    claim.
    """

    def test_beira_dwt_cap_makes_every_class_infeasible_direct_but_transshipment_rescues_it(self) -> None:
        from fragility.tiers import tier3_full_quote_signature
        from ml.live_forecast import forecast_all_classes
        from opt.fleetmix import enumerate_fleet_mix

        assert PortEnum.BEIRA.value.max_dwt == 30_000.0  # the premise, checked directly

        fans_by_class, _ = forecast_all_classes(TODAY, None)
        all_fans = [f for cf in fans_by_class.values() for f in cf]
        frontier = enumerate_fleet_mix(75_000, PortEnum.BEIRA, PortEnum.GANGAVARAM, all_fans, contract_term_days=30)
        assert frontier.configurations == ()  # every class rejected direct -- a real, empty frontier
        assert len(frontier.rejected_configurations) == 4
        assert all("exceeds port max" in c.infeasible_reason for c in frontier.rejected_configurations)

        sig = tier3_full_quote_signature(
            cargo_volume_dwt=75_000, origin_port=PortEnum.BEIRA, dest_port=PortEnum.GANGAVARAM,
            laycan_start=LAYCAN_START, laycan_end=LAYCAN_START + timedelta(days=14),
            contract_term_days=30, commodity="Dry Bulk", as_of=TODAY, risk_tolerance=0.0,
        )
        assert sig.envelope_status == "contingent_infeasible"  # the relaxed (widened+transshipment) solve rescues it


class TestDeterminism:
    """Requirement 6: two identical sweeps return identical flip values --
    the pinned seed=0 default every solve in this codebase already uses
    (verified directly against opt.stopping.solve_lock_or_wait's own
    signature: seed defaults to 0 and nothing in opt.api/opt.quote ever
    overrides it) means this should hold without analyze_fragility doing
    anything special to force it -- this test is the proof, not an
    assumption.
    """

    def test_two_identical_sweeps_produce_byte_identical_results(self) -> None:
        kwargs = {
            "cargo_volume_dwt": 75_000, "origin_port": PortEnum.NEWCASTLE_AU, "dest_port": PortEnum.GANGAVARAM,
            "laycan_start": LAYCAN_START, "laycan_end": LAYCAN_END, "as_of": TODAY,
            "variables": [VariableId.CARGO_VOLUME_DWT, VariableId.RISK_TOLERANCE],
        }
        first = analyze_fragility(**kwargs)
        second = analyze_fragility(**kwargs)
        assert first.model_dump() == second.model_dump()

    def test_the_current_decision_baseline_itself_is_deterministic(self) -> None:
        kwargs = {
            "cargo_volume_dwt": 50_000, "origin_port": PortEnum.BALIKPAPAN, "dest_port": PortEnum.VIZAG,
            "laycan_start": LAYCAN_START, "laycan_end": LAYCAN_END, "as_of": TODAY,
            "variables": [VariableId.CARGO_VOLUME_DWT],
        }
        first = analyze_fragility(**kwargs)
        second = analyze_fragility(**kwargs)
        assert first.current_decision == second.current_decision


class TestNoFlipIsHonestNotFabricated:
    def test_risk_tolerance_no_flip_reports_the_full_valid_range(self) -> None:
        """A real query where risk_tolerance turns out not to move the
        decision anywhere in [0, 1] -- verified live before being written
        into this test, not assumed."""
        report = analyze_fragility(
            cargo_volume_dwt=75_000, origin_port=PortEnum.NEWCASTLE_AU, dest_port=PortEnum.GANGAVARAM,
            laycan_start=LAYCAN_START, laycan_end=LAYCAN_END, as_of=TODAY,
            variables=[VariableId.RISK_TOLERANCE],
        )
        fp = report.findings[0]
        if fp.flip_found:
            pytest.skip("real market data moved since this test was written; risk_tolerance now flips")
        assert fp.flip_value is None
        assert fp.range_searched_low == 0.0
        assert fp.range_searched_high == 1.0


class TestEvaluationsUsedStaysUnderCap:
    def test_full_eight_variable_sweep_stays_within_a_sane_total_budget(self) -> None:
        """Requirement: 'evaluations_used stays under the cap on a realistic
        cargo.' Per-variable caps are enforced by search.py itself (see
        test_search.py); this confirms the whole-report total for a real
        query stays bounded, not just each individual variable."""
        report = analyze_fragility(
            cargo_volume_dwt=75_000, origin_port=PortEnum.NEWCASTLE_AU, dest_port=PortEnum.GANGAVARAM,
            laycan_start=LAYCAN_START, laycan_end=LAYCAN_END, as_of=TODAY,
        )
        assert report.evaluations_used < 200
        assert len(report.findings) == len(VariableId)


class TestSingleVariableOnlyIsDisclosed:
    def test_limitations_state_the_single_variable_scope(self) -> None:
        report = analyze_fragility(
            cargo_volume_dwt=75_000, origin_port=PortEnum.NEWCASTLE_AU, dest_port=PortEnum.PARADIP,
            laycan_start=LAYCAN_START, laycan_end=LAYCAN_END, as_of=TODAY,
            variables=[VariableId.CARGO_VOLUME_DWT],
        )
        assert any("Single-variable" in limitation for limitation in report.limitations)

    def test_berth_truth_context_is_null_for_variables_with_no_berth_truth_relevance(self) -> None:
        """P5: cargo_volume_dwt has no draft/tide/wait relevance at all, so it
        must stay null even though other findings now populate real context.
        vessel_draft_m at a PortEnum-fallback port (Newcastle_AU, no register
        entry) also stays null -- there is no berth-level document to report
        on (see _draft_and_tide_context)."""
        report = analyze_fragility(
            cargo_volume_dwt=75_000, origin_port=PortEnum.NEWCASTLE_AU, dest_port=PortEnum.PARADIP,
            laycan_start=LAYCAN_START, laycan_end=LAYCAN_END, as_of=TODAY,
            variables=[VariableId.CARGO_VOLUME_DWT, VariableId.VESSEL_DRAFT_M],
        )
        cargo_fp, draft_fp = report.findings
        assert cargo_fp.berth_truth_context is None
        assert draft_fp.berth_truth_context is None  # Newcastle_AU: PortEnum fallback, no register row


class TestProgressCallback:
    def test_on_progress_is_called_with_the_existing_progress_stage_type(self) -> None:
        from opt.types import ProgressStage

        stages: list[ProgressStage] = []
        analyze_fragility(
            cargo_volume_dwt=75_000, origin_port=PortEnum.NEWCASTLE_AU, dest_port=PortEnum.PARADIP,
            laycan_start=LAYCAN_START, laycan_end=LAYCAN_END, as_of=TODAY,
            variables=[VariableId.CARGO_VOLUME_DWT],
            on_progress=stages.append,
        )
        assert len(stages) >= 2  # at least baseline start/done
        assert all(isinstance(s, ProgressStage) for s in stages)
        assert any(s.status == "start" for s in stages)
        assert any(s.status == "done" for s in stages)
