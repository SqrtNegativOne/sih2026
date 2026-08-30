"""P2 §2 -- tide authority separation, against the real Gangavaram/Vizag
tide fields BT-1 seeded and no decision path read before this task."""
from __future__ import annotations

from berth_truth.models import LimitStatus, PortId
from berth_truth.registry import rows_for_port
from berth_truth.tide import (
    TideAuthority,
    TideImpact,
    assess_tide,
    assess_tide_advisory_only,
)


def _gangavaram_berth(berth_id: str):
    rows = {r.berth_id: r for r in rows_for_port(PortId.GANGAVARAM)}
    return rows[berth_id]


def _dhamra_berth(berth_id: str):
    rows = {r.berth_id: r for r in rows_for_port(PortId.DHAMRA)}
    return rows[berth_id]


def _vizag_berth(berth_id: str):
    rows = {r.berth_id: r for r in rows_for_port(PortId.VISAKHAPATNAM)}
    return rows[berth_id]


class TestNoTideDataMeansNoImpact:
    def test_a_berth_with_neither_field_set_is_none_impact(self) -> None:
        # Real: none of Dhamra's berths have any tide field seeded at all
        # (checked directly against every row) -- unlike Gangavaram, where
        # every berth carries at least a non-restrictive tide_rule string.
        bb1 = _dhamra_berth("BB1")
        assert bb1.tide_rule is None
        assert bb1.tide_allowance_m is None
        result = assess_tide(bb1)
        assert result.impact is TideImpact.NONE
        assert result.authority is None

    def test_a_non_restrictive_real_tide_rule_is_disclosed_but_still_none_impact(self) -> None:
        """Gangavaram B1's real tide_rule text ('POB: at any time') states
        no restriction -- disclosed in the assessment, but must not itself
        produce anything other than NONE impact."""
        b1 = _gangavaram_berth("B1")
        assert b1.tide_rule is not None  # real text exists
        result = assess_tide(b1, vessel_class="Capesize", vessel_is_laden=True)
        assert result.impact is TideImpact.NONE
        assert result.rule_text == b1.tide_rule


class TestPortRuleConditionalRestriction:
    """Gangavaram B5's real BPTS §21 text: 'except for loaded Cape size
    vessels, which are subject to tidal restriction'."""

    def test_laden_capesize_is_conditional_not_a_silent_pass(self) -> None:
        b5 = _gangavaram_berth("B5")
        assert "Cape size" in b5.tide_rule  # the real text, sanity check
        result = assess_tide(b5, vessel_class="Capesize", vessel_is_laden=True)
        assert result.impact is TideImpact.CONDITIONAL
        assert result.authority is TideAuthority.PORT_RULE
        assert result.source_doc_id == b5.source_doc_id
        assert "no tide-timetable data" in result.reason

    def test_ballast_capesize_does_not_trigger_the_restriction(self) -> None:
        b5 = _gangavaram_berth("B5")
        result = assess_tide(b5, vessel_class="Capesize", vessel_is_laden=False)
        assert result.impact is TideImpact.NONE

    def test_laden_panamax_does_not_trigger_a_capesize_specific_restriction(self) -> None:
        b5 = _gangavaram_berth("B5")
        result = assess_tide(b5, vessel_class="Panamax", vessel_is_laden=True)
        assert result.impact is TideImpact.NONE

    def test_unknown_vessel_state_does_not_trigger_the_restriction(self) -> None:
        """No vessel_class/vessel_is_laden supplied -- cannot confirm the
        restriction applies, so it must not gate (a caller with no vessel
        context gets NONE, not a spurious CONDITIONAL)."""
        b5 = _gangavaram_berth("B5")
        result = assess_tide(b5)
        assert result.impact is TideImpact.NONE
        assert result.rule_text is not None  # still disclosed, just not gating


class TestPortRuleStaticAllowance:
    """Vizag's real tide_allowance_m figures -- an unconditional published
    margin, but on a SUPERSEDED document."""

    def test_stale_source_document_makes_it_conditional(self) -> None:
        vgcb = _vizag_berth("VGCB")
        assert vgcb.tide_allowance_m == 1.0  # the real seeded value
        assert vgcb.limit_status is LimitStatus.SUPERSEDED  # the real seeded status
        result = assess_tide(vgcb)
        assert result.impact is TideImpact.CONDITIONAL
        assert result.source_is_current is False


class TestAdvisoryModelCanNeverClear:
    """The literal enforcement requirement: an ADVISORY_MODEL input can
    never produce TideImpact.NONE, however confident it is."""

    def test_advisory_input_cannot_produce_none_even_when_confident(self) -> None:
        result = assess_tide_advisory_only(model_name="pyTMD", suggests_clear=True)
        assert result.impact is not TideImpact.NONE
        assert result.authority is TideAuthority.ADVISORY_MODEL

    def test_advisory_input_is_always_conditional(self) -> None:
        for suggests_clear in (True, False):
            result = assess_tide_advisory_only(model_name="Open-Meteo", suggests_clear=suggests_clear)
            assert result.impact is TideImpact.CONDITIONAL

    def test_advisory_authority_is_distinct_from_port_rule(self) -> None:
        assert TideAuthority.ADVISORY_MODEL is not TideAuthority.PORT_RULE
        real_rule_result = assess_tide(_gangavaram_berth("B5"), vessel_class="Capesize", vessel_is_laden=True)
        assert real_rule_result.authority is TideAuthority.PORT_RULE
        advisory_result = assess_tide_advisory_only(model_name="FES", suggests_clear=True)
        assert advisory_result.authority is not real_rule_result.authority
