"""P1 -- port network audit and operational-model classification."""
from __future__ import annotations

from berth_truth.port_master import (
    CORE_PS_NETWORK,
    EXTENDED_NETWORK,
    PORT_PROFILES,
    PS_REQUIRED_COUNTRIES,
    NetworkTier,
    OperationalModel,
    ps_country_coverage,
)
from opt.network import PortEnum


class TestPsCoverageAudit:
    def test_every_ps_named_country_has_a_core_ps_covering_port(self) -> None:
        coverage = ps_country_coverage()
        for country in PS_REQUIRED_COUNTRIES:
            assert coverage[country] is not None, f"no CORE_PS port covers {country!r}"

    def test_all_seven_indian_discharge_ports_present(self) -> None:
        indian_discharge = {
            PortEnum.PARADIP, PortEnum.VIZAG, PortEnum.GANGAVARAM, PortEnum.GOPALPUR,
            PortEnum.DHAMRA, PortEnum.SAGAR_SANDHEADS, PortEnum.HALDIA,
        }
        assert indian_discharge <= set(CORE_PS_NETWORK)

    def test_extended_port_cannot_satisfy_a_ps_country_requirement(self) -> None:
        """Richards Bay (South Africa) must never be reported as covering
        Mozambique -- the literal failure mode this audit exists to prevent."""
        coverage = ps_country_coverage()
        assert coverage["Mozambique"] != PortEnum.RICHARDS_BAY
        assert PORT_PROFILES[PortEnum.RICHARDS_BAY].network_tier is NetworkTier.EXTENDED

    def test_russia_gap_is_closed(self) -> None:
        """The literal defect found this session: the PS opening names
        Russia as an origin; the pre-P1 network had none at all."""
        coverage = ps_country_coverage()
        assert coverage["Russia"] == PortEnum.VOSTOCHNY_RU

    def test_no_extended_port_double_counted_as_core(self) -> None:
        assert set(CORE_PS_NETWORK).isdisjoint(set(EXTENDED_NETWORK))


class TestPortProfiles:
    def test_every_port_enum_member_has_a_profile(self) -> None:
        missing = set(PortEnum) - set(PORT_PROFILES)
        assert not missing, f"PortEnum members with no PortProfile: {missing}"

    def test_sandheads_is_lighterage_not_a_berth(self) -> None:
        profile = PORT_PROFILES[PortEnum.SAGAR_SANDHEADS]
        assert profile.operational_model is OperationalModel.LIGHTERAGE
        assert profile.parent_or_hub is PortEnum.HALDIA

    def test_muara_pantai_is_anchorage_transfer(self) -> None:
        assert PORT_PROFILES[PortEnum.MUARA_PANTAI].operational_model is OperationalModel.ANCHORAGE_TRANSFER

    def test_hampton_roads_is_multi_terminal_with_terminals_listed(self) -> None:
        profile = PORT_PROFILES[PortEnum.HAMPTON_ROADS]
        assert profile.operational_model is OperationalModel.MULTI_TERMINAL
        assert len(profile.terminals) >= 1
        assert any("Lamberts Point" in t.name for t in profile.terminals)

    def test_singapore_is_hub_transship(self) -> None:
        assert PORT_PROFILES[PortEnum.SINGAPORE].operational_model is OperationalModel.HUB_TRANSSHIP

    def test_conventional_berthed_ports_are_not_misclassified(self) -> None:
        for port in (PortEnum.PARADIP, PortEnum.DHAMRA, PortEnum.GANGAVARAM, PortEnum.NEWCASTLE_AU):
            assert PORT_PROFILES[port].operational_model is OperationalModel.BERTHED
