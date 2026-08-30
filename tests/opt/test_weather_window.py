"""Tests for opt.weather_window -- no real network call in any test here;
requests.get is monkeypatched throughout. See fetch_marine_window's own
docstring for the live behaviour these mocks stand in for."""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import polars as pl
import pytest
import requests

from opt import weather_window
from opt.network import PortEnum
from opt.weather_window import (
    DAYS_LOST_PER_STORM,
    ROUGH_SEA_THRESHOLD_M,
    MarineWindow,
    TransitBuffer,
    fetch_marine_window,
    transit_buffer,
)


class _FakeResponse:
    def __init__(self, payload: object, ok: bool = True):
        self._payload = payload
        self._ok = ok

    def raise_for_status(self) -> None:
        if not self._ok:
            raise requests.HTTPError("mock 500")

    def json(self) -> object:
        return self._payload


def _payload_for_days(days: list[date], heights: dict[date, float]) -> dict:
    """A minimal, real-shaped Open-Meteo marine payload: one hourly reading
    per day at 12:00, wave_height carrying the height, the other two series
    null (exercising the same coalesce-to-max-of-available-series path a
    real payload with partial agency coverage would)."""
    return {
        "latitude": 0.0,
        "longitude": 0.0,
        "hourly": {
            "time": [f"{d.isoformat()}T12:00" for d in days],
            "wave_height": [heights[d] for d in days],
            "wind_wave_height": [None for _ in days],
            "swell_wave_height": [None for _ in days],
        },
    }


def _days(start: date, n: int) -> list[date]:
    return [start + timedelta(days=i) for i in range(n)]


def _write_climatology(path: Path, rows: list[tuple[str, int, float]]) -> None:
    pl.DataFrame(rows, schema=["basin", "iso_week", "strike_rate"], orient="row").write_parquet(path)


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------


def test_a_cached_response_is_reused_with_no_second_http_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    start = date(2026, 6, 1)
    days = _days(start, 7)
    payload = _payload_for_days(days, {d: 1.0 for d in days})
    call_count = 0

    def fake_get(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return _FakeResponse(payload)

    monkeypatch.setattr(weather_window.requests, "get", fake_get)

    first = fetch_marine_window(PortEnum.PARADIP, start, start + timedelta(days=6), cache_dir=tmp_path, as_of=start)
    second = fetch_marine_window(PortEnum.PARADIP, start, start + timedelta(days=6), cache_dir=tmp_path, as_of=start)

    assert call_count == 1, "second call should have been served entirely from the disk cache"
    assert first is not None and second is not None
    assert first == second


# ---------------------------------------------------------------------------
# Network failure
# ---------------------------------------------------------------------------


def test_network_failure_returns_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    def fake_get(*args, **kwargs):
        raise requests.ConnectionError("mock: network unreachable")

    monkeypatch.setattr(weather_window.requests, "get", fake_get)

    result = fetch_marine_window(
        PortEnum.PARADIP, date(2026, 6, 1), date(2026, 6, 7), cache_dir=tmp_path, as_of=date(2026, 6, 1)
    )
    assert result is None


def test_transit_buffer_degrades_gracefully_when_the_network_is_down(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    def fake_get(*args, **kwargs):
        raise requests.ConnectionError("mock: network unreachable")

    monkeypatch.setattr(weather_window.requests, "get", fake_get)

    result = transit_buffer(
        PortEnum.NEWCASTLE_AU,
        PortEnum.PARADIP,
        date(2026, 6, 1),
        date(2026, 6, 7),
        as_of=date(2026, 6, 1),
        climatology_path=tmp_path / "no_climatology_here.parquet",
        cache_dir=tmp_path,
    )
    assert isinstance(result, TransitBuffer)
    assert result.forecast_delay_days == 0.0
    assert result.forecast_covers_laycan is False
    # A missing climatology file degrades to 0.0 too -- a demo on airplane
    # mode with neither data source built must still get a complete quote.
    assert result.expected_delay_days == 0.0


# ---------------------------------------------------------------------------
# No double-counting
# ---------------------------------------------------------------------------


def test_a_fully_covered_laycan_does_not_also_add_the_full_climatology_delay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    start = date(2026, 6, 1)
    laycan_days = _days(start, 3)  # a short laycan, fully inside a 7-day forecast
    # One rough day (>= threshold) out of three -> forecast_delay_days == 1.0.
    heights = {laycan_days[0]: 1.0, laycan_days[1]: ROUGH_SEA_THRESHOLD_M + 0.5, laycan_days[2]: 1.0}
    payload = _payload_for_days(_days(start, 7), {**heights, **{d: 1.0 for d in _days(start, 7) if d not in heights}})

    monkeypatch.setattr(weather_window.requests, "get", lambda *a, **k: _FakeResponse(payload))

    climatology_path = tmp_path / "climatology.parquet"
    week = start.isocalendar().week
    # A real, nonzero climatology signal for this exact week -- if it leaked
    # into expected_delay_days despite full forecast coverage, this test
    # would catch it (0.5 * DAYS_LOST_PER_STORM = a large, obvious delta).
    _write_climatology(climatology_path, [("BAY_OF_BENGAL", week, 0.5)])

    result = transit_buffer(
        PortEnum.PARADIP,
        PortEnum.VIZAG,  # also BAY_OF_BENGAL -- basins is still just that one
        laycan_days[0],
        laycan_days[-1],
        as_of=start,
        climatology_path=climatology_path,
        cache_dir=tmp_path,
    )

    assert result.forecast_covers_laycan is True
    assert result.forecast_delay_days == pytest.approx(1.0)
    assert result.climatology_delay_days == pytest.approx(0.5 * DAYS_LOST_PER_STORM)
    # The laycan is fully covered by the forecast -> climatology contributes
    # nothing to the combined figure, even though climatology_delay_days
    # itself is reported (nonzero) for transparency.
    assert result.expected_delay_days == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Beyond the 7-day horizon
# ---------------------------------------------------------------------------


def test_laycan_entirely_beyond_the_horizon_falls_back_to_pure_climatology(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    as_of = date(2026, 6, 1)
    forecast_days = _days(as_of, 7)  # the real API's actual horizon
    payload = _payload_for_days(forecast_days, {d: 1.0 for d in forecast_days})
    monkeypatch.setattr(weather_window.requests, "get", lambda *a, **k: _FakeResponse(payload))

    far_laycan_start = as_of + timedelta(days=30)
    far_laycan_end = as_of + timedelta(days=33)

    direct = fetch_marine_window(
        PortEnum.PARADIP, far_laycan_start, far_laycan_end, cache_dir=tmp_path, as_of=as_of
    )
    assert direct is None, "no overlap between the mocked 7-day forecast and a laycan 30 days out"

    climatology_path = tmp_path / "climatology.parquet"
    week = far_laycan_start.isocalendar().week
    _write_climatology(climatology_path, [("BAY_OF_BENGAL", week, 0.4)])

    result = transit_buffer(
        PortEnum.PARADIP,
        PortEnum.VIZAG,
        far_laycan_start,
        far_laycan_end,
        as_of=as_of,
        climatology_path=climatology_path,
        cache_dir=tmp_path,
    )
    assert result.forecast_covers_laycan is False
    assert result.forecast_delay_days == 0.0
    assert result.climatology_delay_days == pytest.approx(0.4 * DAYS_LOST_PER_STORM)
    assert result.expected_delay_days == pytest.approx(result.climatology_delay_days)


# ---------------------------------------------------------------------------
# Malformed / partial JSON
# ---------------------------------------------------------------------------


def test_malformed_json_is_handled_without_raising(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # Valid JSON, but missing the "hourly" block entirely -- a real partial
    # response, not a network-level failure.
    monkeypatch.setattr(weather_window.requests, "get", lambda *a, **k: _FakeResponse({"latitude": 0.0}))

    result = fetch_marine_window(
        PortEnum.PARADIP, date(2026, 6, 1), date(2026, 6, 7), cache_dir=tmp_path, as_of=date(2026, 6, 1)
    )
    assert result is None


def test_json_decode_failure_is_handled_without_raising(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    class _BrokenResponse(_FakeResponse):
        def json(self) -> object:
            raise ValueError("mock: not valid JSON")

    monkeypatch.setattr(weather_window.requests, "get", lambda *a, **k: _BrokenResponse(None))

    result = fetch_marine_window(
        PortEnum.PARADIP, date(2026, 6, 1), date(2026, 6, 7), cache_dir=tmp_path, as_of=date(2026, 6, 1)
    )
    assert result is None


# ---------------------------------------------------------------------------
# A few more direct checks on MarineWindow's own shape
# ---------------------------------------------------------------------------


def test_marine_window_reports_real_provenance_and_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    start = date(2026, 6, 1)
    days = _days(start, 7)
    payload = _payload_for_days(days, {d: 1.0 for d in days})
    monkeypatch.setattr(weather_window.requests, "get", lambda *a, **k: _FakeResponse(payload))

    result = fetch_marine_window(PortEnum.PARADIP, start, start + timedelta(days=6), cache_dir=tmp_path, as_of=start)
    assert isinstance(result, MarineWindow)
    assert result.source == "open-meteo/dwd"
    assert result.provenance == "ESTIMATED"
    assert result.rough_days == 0
