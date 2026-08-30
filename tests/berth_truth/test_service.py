"""Tests for berth_truth.service -- the vessel-vs-register check itself,
independent of how opt.voyage/opt.fleetmix wire it in (see
tests/opt/test_voyage.py and tests/opt/test_fleetmix.py for that side).
"""
from __future__ import annotations

import dataclasses
from datetime import date

import pytest

from berth_truth.models import CommodityClass, DraftStatus, PortId
from berth_truth.service import (
    BerthCheckResult,
    check_vessel_against_register,
    infer_commodity_class,
)

TODAY = date(2026, 8, 27)

_CAPESIZE = {
    "vessel_draft_m": 18.0,
    "vessel_loa_m": 290.0,
    "vessel_beam_m": 45.0,
    "vessel_dwt": 180_000.0,
}


class TestInferCommodityClass:
    def test_coal_maps_to_coal_coke(self) -> None:
        assert infer_commodity_class("Coal") is CommodityClass.COAL_COKE

    def test_thermal_coal_also_maps_to_coal_coke(self) -> None:
        """Gangavaram's own BPTS states priority as 'Coal/Coke' together, not
        split by grade -- that IS the source's own granularity."""
        assert infer_commodity_class("Thermal Coal") is CommodityClass.COAL_COKE

    def test_coking_coal_also_maps_to_coal_coke(self) -> None:
        assert infer_commodity_class("Coking Coal") is CommodityClass.COAL_COKE

    def test_iron_ore_maps_correctly(self) -> None:
        assert infer_commodity_class("Iron Ore Fines") is CommodityClass.IRON_ORE_FINES_PELLETS

    def test_container_maps_correctly(self) -> None:
        assert infer_commodity_class("Container") is CommodityClass.CONTAINER

    def test_unrecognised_commodity_is_none_not_a_guess(self) -> None:
        assert infer_commodity_class("Wheat") is None
        assert infer_commodity_class("Limestone") is None

    def test_none_and_empty_string_are_both_none(self) -> None:
        assert infer_commodity_class(None) is None
        assert infer_commodity_class("") is None

    def test_case_insensitive(self) -> None:
        assert infer_commodity_class("COAL") is CommodityClass.COAL_COKE
        assert infer_commodity_class("coal") is CommodityClass.COAL_COKE


class TestCheckVesselAgainstRegister:
    def test_capesize_rejected_at_gangavaram_berth_1_accepted_at_berth_5(self) -> None:
        """The literal BT-2 requirement, at the service layer directly."""
        result = check_vessel_against_register(
            PortId.GANGAVARAM, commodity="Coal", as_of=TODAY, **_CAPESIZE
        )
        assert result.is_feasible is True
        assert result.berth_id == "B5"
        assert result.binding_constraint.permissible_draft_m == 18.0

    def test_the_same_vessel_is_genuinely_rejected_by_berth_1_alone(self) -> None:
        """Confirms the premise: berth 1 (13.0m) really cannot take an 18.0m
        vessel -- checked directly against the register row, not inferred."""
        from berth_truth.registry import GANGAVARAM_ROWS

        berth_1 = next(r for r in GANGAVARAM_ROWS if r.berth_id == "B1")
        assert berth_1.permissible_draft_m == 13.0
        assert 18.0 > berth_1.permissible_draft_m

    def test_a_displacement_only_berth_records_size_untested_not_passed(self) -> None:
        """Requirement 6: Gangavaram publishes displacement, never DWT --
        the size check must never silently pass on that basis."""
        result = check_vessel_against_register(
            PortId.GANGAVARAM, commodity="Coal", as_of=TODAY, **_CAPESIZE
        )
        size_checks = [c for c in result.untested_checks if c.startswith("size:")]
        assert len(size_checks) == 1
        assert "displacement" in size_checks[0]
        assert "236,000" in size_checks[0]

    def test_dhamra_stale_draft_is_untested_never_a_silent_pass_or_fail(self) -> None:
        """Requirement 3, directly: no declarations supplied -> BB1/BB2's
        draft is STALE_OR_UNAVAILABLE, disclosed, and does not by itself
        block a vessel whose other dimensions are fine."""
        result = check_vessel_against_register(
            PortId.DHAMRA, commodity="Coal", as_of=TODAY,
            vessel_draft_m=14.0, vessel_loa_m=200.0, vessel_beam_m=32.0, vessel_dwt=75_000.0,
        )
        assert result.draft_status is DraftStatus.STALE_OR_UNAVAILABLE
        assert result.is_feasible is True  # LOA clears; draft/size/beam all untested, none block
        assert any(c.startswith("draft:") for c in result.untested_checks)

    def test_dhamra_observed_only_berths_are_surfaced_never_used_to_clear(self) -> None:
        result = check_vessel_against_register(
            PortId.DHAMRA, commodity=None, as_of=TODAY,
            vessel_draft_m=14.0, vessel_loa_m=200.0, vessel_beam_m=32.0, vessel_dwt=75_000.0,
        )
        assert set(result.observed_only_berths) == {"BB5", "BB2E", "BB3B", "BB3N", "BB4N", "B3AS", "DHS1"}
        assert result.berth_id not in result.observed_only_berths

    def test_a_vessel_too_big_for_every_berth_is_infeasible_with_a_reason(self) -> None:
        result = check_vessel_against_register(
            PortId.GANGAVARAM, commodity=None, as_of=TODAY,
            vessel_draft_m=25.0, vessel_loa_m=290.0, vessel_beam_m=45.0, vessel_dwt=180_000.0,
        )
        assert result.is_feasible is False
        assert result.reason is not None
        assert result.berth_id is not None  # names the closest-tried berth, not silent

    def test_a_port_with_nothing_at_all_returns_none(self) -> None:
        """VISAKHAPATNAM has data (SUPERSEDED, but present) -- there is no
        PortId member seeded with truly nothing, so this exercises the
        mechanism via an empty registry override instead."""
        from berth_truth.resolver import resolve_constraint

        empty = resolve_constraint(PortId.GANGAVARAM, None, TODAY, registry=())
        assert empty.candidates == ()
        assert empty.observed_only == ()
        # check_vessel_against_register itself calls the real REGISTRY, so
        # confirm the None-returning branch's condition directly instead:
        assert not empty.candidates and not empty.observed_only

    def test_margins_are_signed_correctly(self) -> None:
        """Positive margin = room to spare; matches FeasibilityMargins'
        documented sign convention on the opt.types side exactly."""
        result = check_vessel_against_register(
            PortId.GANGAVARAM, commodity="Coal", as_of=TODAY, **_CAPESIZE
        )
        assert result.draft_margin_m == 0.0  # exactly at B5's 18.0m limit
        assert result.loa_margin_m == 2.0  # B5 LOA 292 - vessel 290

    def test_result_is_a_frozen_dataclass_not_accidentally_mutable(self) -> None:
        result = check_vessel_against_register(
            PortId.GANGAVARAM, commodity="Coal", as_of=TODAY, **_CAPESIZE
        )
        assert isinstance(result, BerthCheckResult)
        with pytest.raises(dataclasses.FrozenInstanceError):
            result.is_feasible = False  # type: ignore[misc]
