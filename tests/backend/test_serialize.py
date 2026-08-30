"""Tests for backend.serialize's generic PortEnum -> code-string JSON walker,
in isolation from any real quote() call.

The two emissions tests below are the exception -- QuoteResult has too many
required fields (full_recommendation among them) to hand-construct honestly,
so they run a real ``opt.quote.quote()`` and check what
``quote_result_to_json`` actually emits for its ``emissions`` field, the same
"real data, no mocks" convention the rest of this test suite already uses
(e.g. tests/opt/test_route_trace.py)."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from backend.serialize import _is_port_dict, _replace_ports, quote_result_to_json
from ml.live_forecast import latest_available_date
from opt.network import PortEnum
from opt.quote import quote
from opt.types import Vessel, VesselClass


def _port_dict(port: PortEnum) -> dict:
    p = port.value
    return {
        "id": p.id, "max_loa_m": p.max_loa_m, "max_beam_m": p.max_beam_m,
        "max_draft_m": p.max_draft_m, "max_dwt": p.max_dwt,
        "handling_rate_tph": p.handling_rate_tph,
        "expected_wait_days": p.expected_wait_days, "bunker_price_usd": p.bunker_price_usd,
    }


def test_is_port_dict_recognizes_a_real_port_shape():
    assert _is_port_dict(_port_dict(PortEnum.PARADIP)) is True


def test_is_port_dict_rejects_an_unrelated_dict_with_extra_keys():
    d = _port_dict(PortEnum.PARADIP)
    d["extra_field"] = 1
    assert _is_port_dict(d) is False


def test_is_port_dict_rejects_a_dict_with_an_unknown_id():
    d = _port_dict(PortEnum.PARADIP)
    d["id"] = "Not_A_Real_Port"
    assert _is_port_dict(d) is False


def test_replace_ports_converts_a_top_level_port_dict():
    assert _replace_ports(_port_dict(PortEnum.NEWCASTLE_AU)) == "NEWCASTLE_AU"


def test_replace_ports_converts_deeply_nested_port_dicts():
    nested = {
        "a": [{"b": _port_dict(PortEnum.SINGAPORE)}, {"c": 1}],
        "d": {"e": {"f": _port_dict(PortEnum.HALDIA)}},
    }
    result = _replace_ports(nested)
    assert result["a"][0]["b"] == "SINGAPORE"
    assert result["a"][1]["c"] == 1
    assert result["d"]["e"]["f"] == "HALDIA"


def test_replace_ports_leaves_non_port_dicts_and_scalars_unchanged():
    obj = {"x": 1, "y": "hello", "z": [1, 2, {"w": True}], "n": None}
    assert _replace_ports(obj) == obj


def _laycan() -> tuple[date, date]:
    today = latest_available_date()
    return today + timedelta(days=5), today + timedelta(days=18)


def test_quote_result_to_json_emissions_is_null_without_a_vessel():
    start, end = _laycan()
    result = quote(
        cargo_volume_dwt=70_000,
        origin_port=PortEnum.NEWCASTLE_AU,
        dest_port=PortEnum.PARADIP,
        laycan_start=start,
        laycan_end=end,
    )
    assert result.emissions is None

    payload = quote_result_to_json(result)
    assert "emissions" in payload  # JSON null, not an omitted key
    assert payload["emissions"] is None


def test_quote_result_to_json_emissions_round_trips_with_a_vessel():
    start, end = _laycan()
    vessel = Vessel(
        vessel_id="V_TEST",
        vessel_class=VesselClass.PANAMAX,
        current_port=PortEnum.NEWCASTLE_AU,
        status="idle",
        available_from=date(2025, 1, 1),
        available_until=None,
        dwt=82_000.0,
        draft_m=12.0,
        loa_m=225.0,
        beam_m=32.0,
        speed_kn=14.0,
        laden_fuel_consumption_tpd=35.0,
        ballast_fuel_consumption_tpd=28.0,
    )
    result = quote(
        cargo_volume_dwt=70_000,
        origin_port=PortEnum.NEWCASTLE_AU,
        dest_port=PortEnum.PARADIP,
        laycan_start=start,
        laycan_end=end,
        vessels=[vessel],
    )

    payload = quote_result_to_json(result)
    emissions_json = payload["emissions"]

    if result.emissions is None:
        # rating_year outside 2023-2026 (e.g. a future `as_of`) -- still an
        # honest null, not a fabricated block.
        assert emissions_json is None
        return

    assert emissions_json is not None
    assert emissions_json["as_of"] == result.as_of.isoformat()
    assert emissions_json["rating_year"] == result.emissions.rating_year
    assert emissions_json["fleet_worst_rating"] == result.emissions.fleet_worst_rating
    assert len(emissions_json["projections"]) == 1

    projected = emissions_json["projections"][0]
    expected = result.emissions.projections[0]
    assert projected["vessel_id"] == expected.vessel_id
    assert projected["vessel_class"] == expected.vessel_class.value
    assert projected["rating"] == expected.rating
    assert projected["attained_cii"] == expected.attained_cii
    assert projected["required_cii"] == expected.required_cii
    assert projected["margin_pct"] == expected.margin_pct
    assert projected["provenance"] == "MODEL_DERIVED"
    # No PortEnum field on VesselCIIProjection -- confirms the generic
    # _replace_ports walk doesn't need (and doesn't get) special-cased here.
    assert "origin_port" not in projected and "dest_port" not in projected


def test_quote_result_to_json_emits_transit_buffer_null_safe():
    """2.4: opt.weather_window.transit_buffer() makes a real, cache-first
    HTTP call -- whether it succeeds here depends on this test run actually
    having network access, which this suite doesn't require. Either way,
    quote_result_to_json must emit SOME value (JSON null, never an omitted
    key) for transit_buffer, and a populated one must carry real origin/dest
    PortEnum codes flattened by the same generic _replace_ports walk that
    handles every other PortEnum field on QuoteResult."""
    start, end = _laycan()
    result = quote(
        cargo_volume_dwt=70_000,
        origin_port=PortEnum.NEWCASTLE_AU,
        dest_port=PortEnum.PARADIP,
        laycan_start=start,
        laycan_end=end,
    )
    payload = quote_result_to_json(result)
    assert "transit_buffer" in payload

    if result.transit_buffer is None:
        assert payload["transit_buffer"] is None
        return

    tb_json = payload["transit_buffer"]
    assert tb_json is not None
    assert tb_json["origin"] == "NEWCASTLE_AU"
    assert tb_json["dest"] == "PARADIP"
    assert tb_json["expected_delay_days"] == pytest.approx(result.transit_buffer.expected_delay_days)
    assert tb_json["forecast_covers_laycan"] == result.transit_buffer.forecast_covers_laycan
    assert tuple(tb_json["basins"]) == result.transit_buffer.basins


def test_quote_result_to_json_attaches_a_real_fracture_summary():
    """3.4: the route's Fracture Index is attached at serialization time
    (backend/serialize.py), not stored as a QuoteResult field -- confirm the
    real attach point actually fires for a real Suez-crossing quote."""
    start, end = _laycan()
    result = quote(
        cargo_volume_dwt=70_000,
        origin_port=PortEnum.HAMPTON_ROADS,
        dest_port=PortEnum.VIZAG,
        laycan_start=start,
        laycan_end=end,
    )
    payload = quote_result_to_json(result)
    assert "fracture" in payload
    fracture = payload["fracture"]
    assert fracture is not None
    assert len(fracture["chokepoints"]) > 0
    assert "gulf_of_aden" in fracture["jwc_listed_areas"]
    # No hull value in a plain quote's context -- the summary is never
    # asked to price a war-risk premium against an assumed vessel value.
    assert "war_risk_premium" not in fracture

    # No PortEnum field on a fracture chokepoint row -- confirms the generic
    # _replace_ports walk doesn't accidentally mangle it.
    row = fracture["chokepoints"][0]
    assert "chokepoint_id" in row
    assert "band" in row


def test_quote_result_to_json_fracture_summary_is_null_safe_on_a_route_with_no_chokepoints():
    start, end = _laycan()
    result = quote(
        cargo_volume_dwt=70_000,
        origin_port=PortEnum.VIZAG,
        dest_port=PortEnum.PARADIP,
        laycan_start=start,
        laycan_end=end,
    )
    payload = quote_result_to_json(result)
    fracture = payload["fracture"]
    assert fracture is not None
    assert fracture["chokepoints"] == []
    assert fracture["jwc_listed_areas"] == []
