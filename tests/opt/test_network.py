"""Tests for opt.network's origin -> route family derivation.

route_family_for_origin backs opt.ceiling.lock_or_wait_for_cargo: given only
an origin port (what a charterer actually knows), it must resolve the same
RouteFamily label callers previously had to supply by hand on CargoParcel.
"""
from __future__ import annotations

import pytest

from opt.network import (
    ORIGIN_PORT_ROUTE_FAMILY,
    PortEnum,
    RouteFamily,
    route_family_for_origin,
)


class TestRouteFamilyForOrigin:
    def test_every_port_has_a_mapping(self):
        """All 15 real ports in this system resolve -- no silent gap."""
        for port in PortEnum:
            assert route_family_for_origin(port) is not None

    def test_coverage_matches_port_enum_exactly(self):
        assert set(ORIGIN_PORT_ROUTE_FAMILY.keys()) == set(PortEnum)

    @pytest.mark.parametrize(
        ("origin", "expected"),
        [
            (PortEnum.NEWCASTLE_AU, RouteFamily.AUSTRALIA_EC_INDIA),
            (PortEnum.GLADSTONE_AU, RouteFamily.AUSTRALIA_EC_INDIA),
            (PortEnum.RICHARDS_BAY, RouteFamily.SOUTH_AFRICA_EC_INDIA),
            (PortEnum.BEIRA, RouteFamily.MOZAMBIQUE_EC_INDIA),
            (PortEnum.BALIKPAPAN, RouteFamily.INDONESIA_EC_INDIA),
            (PortEnum.MUARA_PANTAI, RouteFamily.INDONESIA_EC_INDIA),
            (PortEnum.HAMPTON_ROADS, RouteFamily.US_EC_INDIA),
            (PortEnum.SINGAPORE, RouteFamily.SINGAPORE_EC_INDIA),
        ],
    )
    def test_foreign_origins_map_to_their_real_region(self, origin, expected):
        assert route_family_for_origin(origin) == expected

    @pytest.mark.parametrize(
        "origin",
        [
            PortEnum.PARADIP, PortEnum.VIZAG, PortEnum.GANGAVARAM,
            PortEnum.GOPALPUR, PortEnum.DHAMRA, PortEnum.SAGAR_SANDHEADS,
            PortEnum.HALDIA,
        ],
    )
    def test_ec_india_origins_are_intra(self, origin):
        """A move starting at an EC-India port itself is coastal repositioning."""
        assert route_family_for_origin(origin) == RouteFamily.INTRA_EC_INDIA
