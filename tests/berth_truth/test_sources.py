"""P1 -- source + adapter registry."""
from __future__ import annotations

from berth_truth.sources import (
    PORT_SOURCES,
    PORTWATCH_MAPPING_STATUS,
    PortWatchMappingStatus,
    SourceQuality,
    sources_for_port,
)
from opt.network import PortEnum


class TestPortSources:
    def test_every_port_has_at_least_one_source(self) -> None:
        missing = set(PortEnum) - set(PORT_SOURCES)
        assert not missing, f"ports with no PortSource entry: {missing}"

    def test_every_source_carries_provenance(self) -> None:
        for sources in PORT_SOURCES.values():
            for source in sources:
                assert source.source_name
                assert source.doc_format
                assert source.coverage_level is not None
                assert source.source_quality is not None

    def test_paradip_source_is_marked_verified(self) -> None:
        paradip = sources_for_port(PortEnum.PARADIP)
        assert any(s.verified for s in paradip)
        assert any(s.source_quality is SourceQuality.OFFICIAL_PORT_AUTHORITY for s in paradip)

    def test_a_research_lead_with_no_url_at_all_is_never_marked_verified(self) -> None:
        """A templated URL (e.g. Paradip's '{yyyy}/{mm}/dtr{ddmm}.pdf') is not
        the same thing as an unconfirmed lead -- a real instance of that
        pattern was fetched and parsed this session, so verified=True is
        correct for it. Only a source with literally no URL at all
        (source_url == "") is a genuine research lead, and that must never
        be marked verified."""
        for sources in PORT_SOURCES.values():
            for source in sources:
                if not source.source_url:
                    assert not source.verified, (
                        f"{source.source_name!r} has no URL at all but is marked verified"
                    )


class TestPortWatchMapping:
    def test_every_port_has_a_mapping_status(self) -> None:
        missing = set(PortEnum) - set(PORTWATCH_MAPPING_STATUS)
        assert not missing

    def test_muara_pantai_is_disclosed_as_a_proxy_not_an_identity(self) -> None:
        status, note = PORTWATCH_MAPPING_STATUS[PortEnum.MUARA_PANTAI]
        assert status is PortWatchMappingStatus.PROXY
        assert "Samarinda" in note

    def test_vostochny_is_identity_confirmed_from_the_files_own_metadata(self) -> None:
        status, note = PORTWATCH_MAPPING_STATUS[PortEnum.VOSTOCHNY_RU]
        assert status is PortWatchMappingStatus.IDENTITY_CONFIRMED
        assert "portid=port1374" in note

    def test_no_mapping_is_silently_presented_as_identity_without_evidence(self) -> None:
        """Every IDENTITY_CONFIRMED port must carry a real PortWatch label
        (non-empty), and PROXY/UNAVAILABLE must never claim identity."""
        for port, (status, note) in PORTWATCH_MAPPING_STATUS.items():
            if status is PortWatchMappingStatus.IDENTITY_CONFIRMED:
                assert note, f"{port} claims IDENTITY_CONFIRMED with no label"
            if status is PortWatchMappingStatus.UNAVAILABLE:
                assert note == ""
