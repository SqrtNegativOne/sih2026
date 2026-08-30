"""P3 requirement 1 + test list item 1: the absolute-vs-relative determination
is explicit and recorded; no arbitrary calibration factor is applied.

Runs the real reconstruction once per module (it takes several seconds) and
reuses it across tests, same pattern as the other tonnage test modules.
"""
from __future__ import annotations

import pytest

from tonnage.identification import (
    IndexType,
    MethodStatus,
    diagnose_all_signs,
    evaluate_identification_gate,
    evaluate_iv_verdict,
    evaluate_kalman_verdict,
    survey_available_tonnage_evidence,
)
from tonnage.stockflow import reconstruct
from tonnage.supplycurve import (
    build_basin_tightness_index,
    build_tightness_index,
    fit_all,
)


@pytest.fixture(scope="module")
def real_result():
    return reconstruct()


@pytest.fixture(scope="module")
def real_gate(real_result):
    return evaluate_identification_gate(real_result)


class TestEvidenceSurvey:
    def test_every_real_raw_data_source_is_assessed(self) -> None:
        survey = survey_available_tonnage_evidence()
        assert len(survey.sources) >= 4
        names = {s.source for s in survey.sources}
        assert any("PortWatch" in n for n in names)
        assert any("fact_port_call" in n for n in names)
        assert any("Signal" in n for n in names)

    def test_no_source_currently_on_disk_supports_the_total_available_split(self) -> None:
        """A real, checkable claim -- if this ever flips True, a new evidence
        source was added and the gate logic (not just this test) needs review."""
        survey = survey_available_tonnage_evidence()
        assert survey.any_source_supports_split is False
        for s in survey.sources:
            assert s.supports_total_available_split is False
            assert s.reason  # every assessment carries a real, non-empty reason


class TestIdentificationGate:
    def test_gate_is_explicit_and_recorded(self, real_gate) -> None:
        assert real_gate.index_type in (IndexType.ABSOLUTE, IndexType.RELATIVE)
        assert real_gate.reasoning
        assert real_gate.index_type.value in real_gate.reasoning  # the verdict is named, not just implied

    def test_no_arbitrary_calibration_factor_is_applied(self, real_gate) -> None:
        assert real_gate.calibration_factor_applied is False

    def test_gate_reuses_the_existing_signal_validation_conclusion(self, real_gate) -> None:
        """identification.py must not re-derive its own, possibly-diverging
        validation number -- it consumes tonnage.validate's."""
        assert real_gate.signal_validation.absolute_scale_validated is False
        assert real_gate.signal_validation.n_points > 0

    def test_current_real_data_yields_relative(self, real_gate) -> None:
        """Both preconditions for ABSOLUTE fail on the real evidence on disk
        today (no supporting source, no validated scale) -- this is the
        expected, real, current outcome, not a hardcoded assumption: the gate
        function itself derives it from evaluate_identification_gate's two
        real inputs, asserted separately above."""
        assert real_gate.index_type is IndexType.RELATIVE


class TestIVAndKalmanVerdicts:
    def test_iv_verdict_is_not_attempted_with_a_real_reason(self) -> None:
        verdict = evaluate_iv_verdict()
        assert verdict.status == MethodStatus.NOT_ATTEMPTED
        assert verdict.candidate_instruments_considered
        assert "exclusion" in verdict.reason or "relevance" in verdict.reason

    def test_kalman_verdict_names_the_shipped_alternative(self) -> None:
        verdict = evaluate_kalman_verdict()
        assert verdict.status == MethodStatus.REJECTED_ALTERNATIVE_SHIPPED
        assert "flow-conservation" in verdict.implemented_alternative
        assert verdict.reason

    def test_all_three_method_status_values_are_distinct(self) -> None:
        assert len({MethodStatus.NOT_ATTEMPTED, MethodStatus.REJECTED_ALTERNATIVE_SHIPPED, MethodStatus.IMPLEMENTED_VALIDATED}) == 3


class TestSignDiagnosis:
    def test_diagnosis_runs_for_every_class_with_a_real_fit(self, real_result) -> None:
        tight = build_tightness_index(real_result)
        basin_tight = build_basin_tightness_index(real_result)
        fits = fit_all(tight)
        diagnoses = diagnose_all_signs(fits, tight, basin_tight)
        assert set(diagnoses) == set(fits)  # one diagnosis per class that has a fit
        for cls, d in diagnoses.items():
            assert d.vessel_class == cls
            assert d.explanation
            assert cls.value in d.explanation

    def test_diagnosis_does_not_force_the_sign(self, real_result) -> None:
        """The explanation text must reflect the REAL measured sign, not a
        desired one -- checked by cross-referencing the real fit object each
        diagnosis carries."""
        tight = build_tightness_index(real_result)
        basin_tight = build_basin_tightness_index(real_result)
        fits = fit_all(tight)
        diagnoses = diagnose_all_signs(fits, tight, basin_tight)
        for d in diagnoses.values():
            wrong_signed_in_text = "wrong-signed" in d.explanation
            assert wrong_signed_in_text == (d.fit.pearson_r < 0)
