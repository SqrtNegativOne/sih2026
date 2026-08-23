"""Tests for sih.optimizer.types — dataclass validation."""
from __future__ import annotations

import pytest
from datetime import date

from sih.optimizer.types import (
    BasisEntry,
    CargoParcel,
    ForecastFan,
    LockWaitResult,
    PortSpec,
    Vessel,
    VesselClass,
)


class TestVesselClass:
    def test_all_four_classes_present(self):
        classes = {vc.value for vc in VesselClass}
        assert classes == {"Capesize", "Panamax", "Supramax", "Handysize"}

    def test_string_comparison(self):
        assert VesselClass.SUPRAMAX == "Supramax"


class TestForecastFan:
    def test_valid_fan(self):
        fan = ForecastFan(VesselClass.SUPRAMAX, 30, p10=12_000.0, p50=15_000.0, p90=19_000.0)
        assert fan.p50 == 15_000.0

    def test_quantile_order_violation_raises(self):
        with pytest.raises(ValueError, match="Quantile order violated"):
            ForecastFan(VesselClass.PANAMAX, 7, p10=16_000.0, p50=14_000.0, p90=18_000.0)

    def test_p10_equals_p50_is_ok(self):
        # Degenerate fan (zero lower spread) is allowed
        fan = ForecastFan(VesselClass.CAPESIZE, 90, p10=20_000.0, p50=20_000.0, p90=25_000.0)
        assert fan.p10 == fan.p50

    def test_negative_tce_raises(self):
        with pytest.raises(ValueError, match="positive"):
            ForecastFan(VesselClass.HANDYSIZE, 7, p10=-100.0, p50=5_000.0, p90=10_000.0)

    def test_invalid_horizon_raises(self):
        with pytest.raises(ValueError, match="horizon_days"):
            ForecastFan(VesselClass.SUPRAMAX, 14, p10=10_000.0, p50=12_000.0, p90=14_000.0)

    def test_all_valid_horizons(self):
        for h in (7, 30, 90):
            fan = ForecastFan(VesselClass.PANAMAX, h, p10=10_000.0, p50=12_000.0, p90=15_000.0)
            assert fan.horizon_days == h


class TestBasisEntry:
    def test_valid_basis(self):
        b = BasisEntry("indo_ec_india", basis_mean=-0.08, basis_std=0.06)
        assert b.basis_mean == pytest.approx(-0.08)

    def test_zero_basis_mean(self):
        # Synthetic routes have 0 mean basis — should not raise
        b = BasisEntry("synthetic_route", basis_mean=0.0, basis_std=0.12)
        assert b.basis_std == pytest.approx(0.12)


class TestPortSpec:
    def test_defaults_are_none(self):
        ps = PortSpec("INPAV")
        assert ps.max_draft_m is None
        assert ps.expected_wait_days == 0.0

    def test_full_spec(self):
        ps = PortSpec(
            "INPAV",
            max_loa_m=280.0,
            max_beam_m=45.0,
            max_draft_m=14.5,
            max_dwt=180_000.0,
            handling_rate_tph=3_000.0,
            expected_wait_days=1.5,
        )
        assert ps.max_draft_m == pytest.approx(14.5)


class TestLockWaitResult:
    def test_lock_result(self):
        result = LockWaitResult(
            vessel_class=VesselClass.SUPRAMAX,
            contract_term_days=180,
            ceiling_usd_per_day=18_500.0,
            today_quote_usd_per_day=17_000.0,
            action="LOCK",
            expected_spot_cost_usd_per_day=19_000.0,
            savings_p50_usd_per_day=2_000.0,
            savings_p10_usd_per_day=500.0,
            route_adjusted=True,
        )
        assert result.action == "LOCK"
        assert result.savings_p50_usd_per_day > 0
