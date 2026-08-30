"""Tests for data_builders.harvest_sentinel1 -- no network.

The STAC-parsing test uses a real, trimmed sample of the actual Copernicus
Data Space Ecosystem STAC search response (fetched by hand against
https://stac.dataspace.copernicus.eu/v1/search for the real Paradip anchorage
box, retrieved 2026-08-29/30, trimmed to two real features -- see
tests/data_builders/fixtures/sentinel1_stac_search_sample.json), not a
hand-written fictional response shape.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from data_builders.harvest_sentinel1 import (
    ANCHORAGE_BEARING_DEG,
    ANCHORAGE_BOXES,
    ANCHORAGE_HALF_WIDTH_NM,
    ANCHORAGE_OFFSET_NM,
    ENV_PASSWORD,
    ENV_USER,
    MissingCredentialsError,
    SceneMetadata,
    _destination_point,
    _existing_file_size,
    _get_access_token,
    _get_credentials,
    _parse_feature,
    fetch_scene,
    search_scenes,
)
from opt.chokepoints import _haversine_nm as _haversine_nm_local

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "sentinel1_stac_search_sample.json"


def _fixture() -> dict:
    return json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Anchorage boxes -- offshore, not on the port, for all five real ports
# ---------------------------------------------------------------------------


class TestAnchorageBoxes:
    def test_all_five_ports_are_present(self):
        assert set(ANCHORAGE_BOXES) == {
            "PARADIP", "VISAKHAPATNAM", "NEWCASTLE_AU", "HAY_POINT_AU", "RICHARDS_BAY_ZA",
        }

    @pytest.mark.parametrize("port", ["PARADIP", "VISAKHAPATNAM", "NEWCASTLE_AU", "HAY_POINT_AU", "RICHARDS_BAY_ZA"])
    def test_box_does_not_contain_the_port_point(self, port: str):
        """Design: anchorage waters are OFFSHORE of the berths -- the box is
        centred ANCHORAGE_OFFSET_NM from the port coordinate and spans only
        ANCHORAGE_HALF_WIDTH_NM either side of that centre, so its near edge
        sits (offset - half_width) nm out and never reaches the port itself
        (offset > half_width by construction)."""
        assert ANCHORAGE_OFFSET_NM > ANCHORAGE_HALF_WIDTH_NM
        box = ANCHORAGE_BOXES[port]
        inside = box.lon_min <= box.port_lon <= box.lon_max and box.lat_min <= box.port_lat <= box.lat_max
        assert inside is False

    @pytest.mark.parametrize("port", ["PARADIP", "VISAKHAPATNAM", "NEWCASTLE_AU", "HAY_POINT_AU", "RICHARDS_BAY_ZA"])
    def test_box_centre_is_a_plausible_offshore_distance(self, port: str):
        """The centre should sit within a few nm of the documented
        ANCHORAGE_OFFSET_NM from the real port coordinate -- not on top of
        it, and not hundreds of miles away (a sign of a unit-conversion bug)."""
        box = ANCHORAGE_BOXES[port]
        d = _haversine_nm_local((box.port_lon, box.port_lat), (box.centre_lon, box.centre_lat))
        assert ANCHORAGE_OFFSET_NM - 0.5 <= d <= ANCHORAGE_OFFSET_NM + 0.5

    def test_box_centre_bearing_is_the_documented_due_east(self):
        """Paradip's real coastline runs roughly N-S with open water to the
        east -- a due-east offset should increase longitude and leave
        latitude close to the port's own latitude."""
        assert ANCHORAGE_BEARING_DEG == 90.0
        box = ANCHORAGE_BOXES["PARADIP"]
        assert box.centre_lon > box.port_lon
        assert abs(box.centre_lat - box.port_lat) < 0.2

    def test_every_box_has_a_real_positive_area(self):
        for box in ANCHORAGE_BOXES.values():
            assert box.lon_max > box.lon_min
            assert box.lat_max > box.lat_min


# ---------------------------------------------------------------------------
# STAC response parsing -- against the real, trimmed fixture
# ---------------------------------------------------------------------------


class TestParseFeature:
    def test_real_fixture_features_parse_into_scene_metadata(self):
        data = _fixture()
        scenes = [s for f in data["features"] if (s := _parse_feature(f)) is not None]
        assert len(scenes) == 2

        first = scenes[0]
        assert first.scene_id == "S1D_IW_GRDH_1SDV_20260828T121932_20260828T122001_004326_007FA4_331A_COG"
        assert first.product_uuid == "9c037de8-5540-45d7-b461-c147c8c6170f"
        assert first.orbit_direction == "ascending"
        assert first.polarisations == ("VV", "VH")
        assert first.product_size_bytes == 1243028258
        assert first.download_url == (
            "https://download.dataspace.copernicus.eu/odata/v1/Products"
            "(9c037de8-5540-45d7-b461-c147c8c6170f)/$value"
        )
        assert first.footprint_bbox == (85.472878, 19.784695, 88.252861, 21.984388)
        assert first.acquisition_datetime.year == 2026
        assert first.acquisition_datetime.month == 8
        assert first.acquisition_datetime.day == 28

    def test_a_feature_missing_the_product_asset_is_skipped_not_crashed_on(self):
        feature = {
            "id": "no-product-asset",
            "properties": {"datetime": "2026-08-28T12:00:00Z", "_private": {"product_uuid": "abc"}},
            "assets": {},
            "bbox": [0, 0, 1, 1],
        }
        assert _parse_feature(feature) is None

    def test_a_feature_missing_a_datetime_is_skipped_not_crashed_on(self):
        feature = {
            "id": "no-datetime",
            "properties": {"_private": {"product_uuid": "abc"}},
            "assets": {"Product": {"href": "https://example.test/x", "file:size": 100}},
            "bbox": [0, 0, 1, 1],
        }
        assert _parse_feature(feature) is None


class TestSearchScenes:
    def test_unknown_port_raises_a_clear_keyerror(self):
        with pytest.raises(KeyError, match="Unknown port"):
            search_scenes("NOT_A_REAL_PORT", __import__("datetime").date(2026, 1, 1), __import__("datetime").date(2026, 1, 2))

    def test_search_uses_the_real_stac_response_shape(self, monkeypatch: pytest.MonkeyPatch):
        """Mocks requests.post to return the real fixture body -- confirms
        search_scenes's own parsing/dedup/pagination-termination logic
        against real response shapes, no live network in the test."""
        import datetime as dt

        from data_builders import harvest_sentinel1 as mod

        calls = []

        class _FakeResponse:
            def __init__(self, payload):
                self._payload = payload

            def raise_for_status(self):
                return None

            def json(self):
                return self._payload

        def fake_post(url, json, timeout):
            calls.append(json)
            return _FakeResponse({"features": _fixture()["features"], "links": []})

        monkeypatch.setattr(mod.requests, "post", fake_post)

        scenes = search_scenes("PARADIP", dt.date(2026, 6, 1), dt.date(2026, 8, 29))
        assert len(scenes) == 2
        assert len(calls) == 1  # no "next" link in the fake response -- exactly one page
        assert calls[0]["collections"] == ["sentinel-1-grd"]
        assert calls[0]["bbox"] == list(ANCHORAGE_BOXES["PARADIP"].bbox)

    def test_search_follows_the_real_next_link_pagination_shape(self, monkeypatch: pytest.MonkeyPatch):
        """A port with more than one page of results must be paginated via
        the real STAC token-based 'next' link (POST + body), not silently
        truncated to the first page -- exercised here since no real port in
        this five-port set happened to exceed one page in the live 90-day
        run, but a longer window or a busier port could."""
        import datetime as dt

        from data_builders import harvest_sentinel1 as mod

        fixture_features = _fixture()["features"]
        page1 = {
            "features": [fixture_features[0]],
            "links": [{"rel": "next", "method": "POST", "href": "...", "body": {"token": "page2"}}],
        }
        page2 = {"features": [fixture_features[1]], "links": []}
        responses = iter([page1, page2])

        class _FakeResponse:
            def __init__(self, payload):
                self._payload = payload

            def raise_for_status(self):
                return None

            def json(self):
                return self._payload

        calls = []

        def fake_post(url, json, timeout):
            calls.append(json)
            return _FakeResponse(next(responses))

        monkeypatch.setattr(mod.requests, "post", fake_post)

        scenes = search_scenes("PARADIP", dt.date(2026, 6, 1), dt.date(2026, 8, 29))
        assert len(scenes) == 2  # one feature per page, both real, both collected
        assert len(calls) == 2
        assert calls[1] == {"token": "page2"}  # the second call's body IS the real "next" link's body


# ---------------------------------------------------------------------------
# Credentials -- a clear, named, actionable error, not a bare KeyError/401
# ---------------------------------------------------------------------------


class TestCredentials:
    def test_missing_both_env_vars_raises_missing_credentials_error(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv(ENV_USER, raising=False)
        monkeypatch.delenv(ENV_PASSWORD, raising=False)
        with pytest.raises(MissingCredentialsError, match=f"{ENV_USER} and {ENV_PASSWORD}"):
            _get_credentials()

    def test_missing_just_the_password_still_raises_the_named_error(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv(ENV_USER, "someone@example.test")
        monkeypatch.delenv(ENV_PASSWORD, raising=False)
        with pytest.raises(MissingCredentialsError):
            _get_credentials()

    def test_the_error_names_the_real_registration_url(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv(ENV_USER, raising=False)
        monkeypatch.delenv(ENV_PASSWORD, raising=False)
        with pytest.raises(MissingCredentialsError, match="dataspace.copernicus.eu"):
            _get_credentials()

    def test_get_access_token_raises_missing_credentials_before_any_network_call(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """_get_access_token must fail fast on missing credentials rather
        than attempting a request and surfacing a bare 401."""
        import datetime as dt  # noqa: F401 -- keeps this test self-contained if reordered

        from data_builders import harvest_sentinel1 as mod

        monkeypatch.delenv(ENV_USER, raising=False)
        monkeypatch.delenv(ENV_PASSWORD, raising=False)

        def _fail_if_called(*a, **k):
            raise AssertionError("requests.post must not be called when credentials are missing")

        monkeypatch.setattr(mod.requests, "post", _fail_if_called)
        with pytest.raises(MissingCredentialsError):
            _get_access_token()


class TestFetchScene:
    def test_already_fully_downloaded_file_is_skipped_without_any_network_call(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        import datetime as dt

        from data_builders import harvest_sentinel1 as mod

        scene = SceneMetadata(
            scene_id="already-there",
            product_uuid="uuid-1",
            acquisition_datetime=dt.datetime(2026, 8, 28, tzinfo=dt.UTC),
            orbit_direction="ascending",
            polarisations=("VV", "VH"),
            footprint_bbox=(0.0, 0.0, 1.0, 1.0),
            product_size_bytes=10,
            download_url="https://example.test/scene.zip",
        )
        out_path = tmp_path / f"{scene.scene_id}.zip"
        out_path.write_bytes(b"0123456789")  # exactly 10 bytes, matching product_size_bytes

        def _fail_if_called(*a, **k):
            raise AssertionError("no network call should happen for an already-complete file")

        monkeypatch.setattr(mod.requests, "get", _fail_if_called)
        monkeypatch.setattr(mod, "_get_access_token", _fail_if_called)

        result = fetch_scene(scene, out_dir=tmp_path)
        assert result == out_path
        assert _existing_file_size(out_path) == 10


class TestDestinationPoint:
    def test_due_east_at_the_equator_moves_longitude_only(self):
        lat2, lon2 = _destination_point(0.0, 0.0, 90.0, 60.0)  # 60 nm = 1 deg at the equator
        assert abs(lat2 - 0.0) < 0.01
        assert abs(lon2 - 1.0) < 0.02

    def test_zero_distance_returns_the_same_point(self):
        lat2, lon2 = _destination_point(20.0, 86.0, 90.0, 0.0)
        assert abs(lat2 - 20.0) < 1e-9
        assert abs(lon2 - 86.0) < 1e-9
