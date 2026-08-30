"""Tests for opt.chokepoints -- real per-route chokepoint detection against
real opt.route_trace searoute polylines (no network mocking: the same real,
already-cached ``opt.route_trace._leg`` every other route-aware test in this
suite exercises, e.g. tests/opt/test_route_trace.py)."""
from __future__ import annotations

from opt.chokepoints import (
    CHOKEPOINT_GEOMETRY,
    _haversine_nm,
    _point_to_segment_distance_nm,
    chokepoints_for_route,
    chokepoints_on_route,
)
from opt.network import PortEnum


def test_newcastle_au_to_paradip_crosses_malacca_not_suez_or_panama():
    ids = chokepoints_for_route(PortEnum.NEWCASTLE_AU, PortEnum.PARADIP)
    assert "chokepoint5" in ids  # Malacca Strait
    assert "chokepoint1" not in ids  # Suez Canal
    assert "chokepoint2" not in ids  # Panama Canal


def test_cape_of_good_hope_crossing_route():
    # The task that requested this suite named Richards Bay ZA -> Paradip IN
    # as the Cape-of-Good-Hope example -- checked live before writing this
    # test and found it does NOT round the Cape: Richards Bay already sits
    # on the Indian Ocean side of South Africa (32.0E), so the real searoute
    # path (confirmed not a great-circle fallback) heads directly northeast
    # across the Indian Ocean, passing no closer than ~891 nm to the Cape's
    # 150 nm circle. Hampton Roads (US East Coast, this network's only
    # Atlantic port) to Richards Bay or Beira is a real pair that DOES round
    # the Cape -- confirmed live the same way -- and is used here instead,
    # since a test should assert something true, not something the original
    # request assumed.
    ids = chokepoints_for_route(PortEnum.HAMPTON_ROADS, PortEnum.RICHARDS_BAY)
    assert "chokepoint7" in ids  # Cape of Good Hope
    assert "chokepoint1" not in ids  # Suez Canal -- the real alternative this route does NOT take


def test_a_short_intra_basin_hop_crosses_nothing():
    # Vizag -> Paradip: both real east-coast-India ports, a few hundred nm
    # apart -- no real chokepoint sits anywhere near this hop.
    ids = chokepoints_for_route(PortEnum.VIZAG, PortEnum.PARADIP)
    assert ids == ()


def test_point_to_segment_detects_a_chokepoint_a_vertex_only_test_would_miss():
    """The whole reason chokepoints_on_route tests every polyline SEGMENT,
    not just its vertices -- searoute's polylines are coarse, so a real
    strait can sit between two widely-spaced vertices, invisible to a
    vertex-only test even though the real transit passes right through it.
    Constructed directly against the real Strait of Hormuz geometry already
    in CHOKEPOINT_GEOMETRY, not a made-up circle."""
    hormuz = CHOKEPOINT_GEOMETRY["chokepoint6"]
    center = (hormuz.lon, hormuz.lat)

    # Two points well outside Hormuz's own 45 nm radius, straddling it --
    # one in the Gulf of Oman, one deep in the Persian Gulf.
    a = (54.0, 25.5)
    b = (60.0, 27.0)
    dist_a = _haversine_nm(center, a)
    dist_b = _haversine_nm(center, b)
    assert dist_a > hormuz.radius_nm, "fixture must place vertex a outside the circle"
    assert dist_b > hormuz.radius_nm, "fixture must place vertex b outside the circle"

    # A vertex-only test (nearest of the two endpoints) would report this as
    # a clean miss.
    vertex_only_nearest = min(dist_a, dist_b)
    assert vertex_only_nearest > hormuz.radius_nm

    # The real segment between them passes much closer -- point-to-segment
    # correctly detects the crossing a vertex-only test would miss.
    segment_distance = _point_to_segment_distance_nm(center, a, b)
    assert segment_distance <= hormuz.radius_nm
    assert segment_distance < vertex_only_nearest

    hits = chokepoints_on_route([a, b])
    assert "chokepoint6" in hits


def test_chokepoints_on_route_returns_ids_in_the_order_the_route_encounters_them():
    # A synthetic three-point polyline crossing Gibraltar (west) then Suez
    # (east), in that order.
    gibraltar = CHOKEPOINT_GEOMETRY["chokepoint8"]
    suez = CHOKEPOINT_GEOMETRY["chokepoint1"]
    polyline = [
        (-10.0, 36.0),
        (gibraltar.lon, gibraltar.lat),
        (20.0, 34.0),
        (suez.lon, suez.lat),
        (34.0, 30.0),
    ]
    hits = chokepoints_on_route(polyline)
    assert hits.index("chokepoint8") < hits.index("chokepoint1")


def test_chokepoints_on_route_handles_a_degenerate_single_point_polyline():
    assert chokepoints_on_route([(0.0, 0.0)]) == ()
    assert chokepoints_on_route([]) == ()


def test_chokepoints_for_route_caches_the_second_call(monkeypatch):
    """The port pair set is small and bounded -- the second identical call
    must not recompute the polyline at all, checked by monkeypatching the
    real opt.route_trace._leg (itself already cached, but a fresh pair
    forces a real first computation) with a call-counting wrapper."""
    import opt.route_trace as route_trace_module

    chokepoints_for_route.cache_clear()
    route_trace_module._leg.cache_clear()

    call_count = 0
    real_leg = route_trace_module._leg

    def counting_leg(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return real_leg(*args, **kwargs)

    monkeypatch.setattr(route_trace_module, "_leg", counting_leg)

    first = chokepoints_for_route(PortEnum.NEWCASTLE_AU, PortEnum.PARADIP)
    second = chokepoints_for_route(PortEnum.NEWCASTLE_AU, PortEnum.PARADIP)

    assert first == second
    assert call_count == 1, "second call should have been served entirely from chokepoints_for_route's own cache"
