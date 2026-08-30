"""Tests for opt.present -- $/MT conversion, per-horizon confidence%, and
congestion labels. Pure math is tested with hand-verified synthetic inputs
(matching opt.stopping/opt.congestion's own convention for their analogous
helpers); congestion_label's real-data path is tested against the real
IMF PortWatch data already on disk, same as opt.congestion's own tests."""
from __future__ import annotations

import math

import pytest

from opt.network import PortEnum
from opt.present import (
    congestion_label,
    estimate_transit_days,
    forecast_confidence,
    usd_per_day_to_usd_per_mt,
)
from opt.types import ForecastFan, VesselClass

# ---------------------------------------------------------------------------
# usd_per_day_to_usd_per_mt
# ---------------------------------------------------------------------------

def test_usd_per_mt_basic_arithmetic():
    # $20,000/day * 10 days / 100,000 dwt = $2.00/MT
    assert usd_per_day_to_usd_per_mt(20_000.0, 10.0, 100_000.0) == pytest.approx(2.0)


def test_usd_per_mt_none_when_transit_days_missing():
    assert usd_per_day_to_usd_per_mt(20_000.0, None, 100_000.0) is None


def test_usd_per_mt_none_when_transit_days_non_positive():
    assert usd_per_day_to_usd_per_mt(20_000.0, 0.0, 100_000.0) is None
    assert usd_per_day_to_usd_per_mt(20_000.0, -1.0, 100_000.0) is None


def test_usd_per_mt_none_when_cargo_non_positive():
    assert usd_per_day_to_usd_per_mt(20_000.0, 10.0, 0.0) is None


# ---------------------------------------------------------------------------
# estimate_transit_days -- real sea-distance matrix already on disk
# ---------------------------------------------------------------------------

def test_estimate_transit_days_real_route_is_positive_and_sane():
    days = estimate_transit_days(PortEnum.NEWCASTLE_AU, PortEnum.PARADIP)
    assert days is not None
    assert 5.0 < days < 40.0  # Australia -> east-coast India, a real multi-week haul


def test_estimate_transit_days_scales_inversely_with_speed():
    slow = estimate_transit_days(PortEnum.NEWCASTLE_AU, PortEnum.PARADIP, speed_kn=10.0)
    fast = estimate_transit_days(PortEnum.NEWCASTLE_AU, PortEnum.PARADIP, speed_kn=20.0)
    assert fast == pytest.approx(slow / 2.0)


def test_estimate_transit_days_none_at_non_positive_speed():
    assert estimate_transit_days(PortEnum.NEWCASTLE_AU, PortEnum.PARADIP, speed_kn=0.0) is None


# ---------------------------------------------------------------------------
# forecast_confidence
# ---------------------------------------------------------------------------

def _fan(p10, p50, p90, horizon=30):
    return ForecastFan(vessel_class=VesselClass.PANAMAX, horizon_days=horizon, p10=p10, p50=p50, p90=p90)


def test_flat_when_p50_equals_todays_quote():
    direction, confidence = forecast_confidence(20_000.0, _fan(18_000, 20_000, 22_000))
    assert direction == "flat"
    assert confidence == pytest.approx(50.0)


def test_down_direction_when_p50_below_quote():
    direction, confidence = forecast_confidence(20_000.0, _fan(16_000, 18_000, 20_500))
    assert direction == "down"
    assert 0.0 <= confidence <= 100.0


def test_up_direction_when_p50_above_quote():
    direction, confidence = forecast_confidence(16_000.0, _fan(16_500, 18_000, 20_000))
    assert direction == "up"
    assert 0.0 <= confidence <= 100.0


def test_confidence_approaches_100_far_outside_the_fan():
    # today's quote is far above even p90 -> near-certain "down" call.
    direction, confidence = forecast_confidence(100_000.0, _fan(16_000, 18_000, 20_000))
    assert direction == "down"
    assert confidence > 99.0


def test_confidence_near_50_at_the_edge_of_direction_flip():
    # quote just barely above p50 -> only weak "down" evidence.
    direction, confidence = forecast_confidence(18_001.0, _fan(16_000, 18_000, 20_000))
    assert direction == "down"
    assert 50.0 <= confidence < 55.0


def test_confidence_is_symmetric_for_mirrored_scenarios():
    # A quote the same log-distance below p50 as another is above it should
    # give the same confidence magnitude for the opposite direction.
    fan = _fan(16_000, 18_000, 20_000)
    _, conf_down = forecast_confidence(18_000 * math.exp(0.1), fan)
    _, conf_up = forecast_confidence(18_000 * math.exp(-0.1), fan)
    assert conf_down == pytest.approx(conf_up, rel=1e-6)


# ---------------------------------------------------------------------------
# congestion_label
# ---------------------------------------------------------------------------

def test_congestion_label_bucketing_with_a_patched_multiplier(monkeypatch):
    # Isolate the pure bucketing logic from real, drifting PortWatch data by
    # patching the one real-data call congestion_label makes.
    port = PortEnum.PARADIP
    baseline = port.value.expected_wait_days

    monkeypatch.setattr("opt.present.dynamic_wait_days", lambda p, as_of=None: (baseline * 0.5, True))
    assert congestion_label(port) == ("LOW", True)

    monkeypatch.setattr("opt.present.dynamic_wait_days", lambda p, as_of=None: (baseline * 1.0, True))
    assert congestion_label(port) == ("MODERATE", True)

    monkeypatch.setattr("opt.present.dynamic_wait_days", lambda p, as_of=None: (baseline * 2.0, True))
    assert congestion_label(port) == ("HIGH", True)


def test_congestion_label_flags_non_real_data(monkeypatch):
    port = PortEnum.PARADIP
    monkeypatch.setattr("opt.present.dynamic_wait_days", lambda p, as_of=None: (port.value.expected_wait_days, False))
    label, is_real = congestion_label(port)
    assert is_real is False
    assert label in ("LOW", "MODERATE", "HIGH")


def test_congestion_label_runs_clean_for_every_real_port():
    for port in PortEnum:
        label, is_real = congestion_label(port)
        assert label in ("LOW", "MODERATE", "HIGH")
        assert isinstance(is_real, bool)
