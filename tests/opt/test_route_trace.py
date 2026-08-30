"""Real-data tests for the route-exploration trace -- opt.route_trace."""
from __future__ import annotations

from datetime import timedelta

from ml.live_forecast import latest_available_date
from opt.network import PortEnum
from opt.quote import quote


def _laycan():
    today = latest_available_date()
    return (today + timedelta(days=5)), (today + timedelta(days=18))


def test_route_exploration_has_a_chosen_fleet_mix_route_with_a_real_polyline():
    start, end = _laycan()
    result = quote(
        cargo_volume_dwt=70_000,
        origin_port=PortEnum.NEWCASTLE_AU,
        dest_port=PortEnum.PARADIP,
        laycan_start=start,
        laycan_end=end,
    )
    routes = result.route_exploration
    assert routes, "quote() should populate route_exploration"

    fleet_routes = [r for r in routes if r.kind == "fleet_mix"]
    assert fleet_routes
    chosen = [r for r in fleet_routes if r.status == "chosen"]
    assert len(chosen) == 1, "exactly one fleet-mix routing is the chosen one"

    for r in routes:
        assert r.legs
        for leg in r.legs:
            assert len(leg.polyline) >= 2
            for lon, lat in leg.polyline:
                assert -180.0 <= lon <= 180.0
                assert -90.0 <= lat <= 90.0


def test_route_exploration_records_rejected_configurations_with_reasons():
    start, end = _laycan()
    # 200,000 dwt -> Capesize is the natural class but cannot enter Paradip
    # (draft), so it should appear as a rejected fleet-mix route with a reason.
    result = quote(
        cargo_volume_dwt=200_000,
        origin_port=PortEnum.NEWCASTLE_AU,
        dest_port=PortEnum.PARADIP,
        laycan_start=start,
        laycan_end=end,
    )
    rejected = [r for r in result.route_exploration if r.status == "rejected"]
    assert rejected
    assert all(r.reason for r in rejected)
