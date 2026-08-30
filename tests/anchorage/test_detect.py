"""Tests for anchorage.detect -- no real imagery, entirely synthetic arrays.

A real downloaded Sentinel-1 scene is a real ~1.2 GB file, too large to ship
in this test suite -- every test here builds a synthetic backscatter array
(or a synthetic-but-real-shaped annotation XML / geolocation grid) by hand
and checks the detector's real, documented behaviour against it. ``load_scene``
itself is real (``rasterio``, added once a real Copernicus account existed
to exercise it against) -- its own real-file read path is exercised by the
error-path and georeferencing-math tests below, not by shipping a real scene.
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest
from scipy import ndimage

from anchorage.detect import (
    DEFAULT_BACKGROUND_PX,
    DEFAULT_GUARD_PX,
    DEFAULT_PFA,
    HIGH_CLUTTER_CV,
    LOW_CLUTTER_CV,
    CFARParams,
    Detection,
    GeoTIFFBackendUnavailableError,
    _confidence_for_clutter,
    _min_area_px,
    apply_land_mask,
    apply_size_gate,
    cfar_detect,
    count_vessels,
    load_scene,
    merge_nearby_detections,
)
from data_builders.harvest_sentinel1 import SceneMetadata


def _flat_background(shape: tuple[int, int] = (200, 200), *, level: float = 10.0, seed: int = 0) -> np.ndarray:
    """A synthetic 'calm sea' background -- low-variance noise around
    `level`, no negative values (real SAR backscatter intensity is
    non-negative)."""
    rng = np.random.RandomState(seed)
    arr = rng.normal(loc=level, scale=level * 0.05, size=shape)
    return np.clip(arr, 0.01, None)


def _add_blob(image: np.ndarray, centre: tuple[int, int], *, half_size: int, intensity: float) -> np.ndarray:
    r, c = centre
    out = image.copy()
    out[r - half_size : r + half_size + 1, c - half_size : c + half_size + 1] = intensity
    return out


def _scene_metadata(scene_id: str = "TEST_SCENE") -> SceneMetadata:
    return SceneMetadata(
        scene_id=scene_id,
        product_uuid="00000000-0000-0000-0000-000000000000",
        acquisition_datetime=datetime(2026, 8, 28, 12, 0, 0, tzinfo=UTC),
        orbit_direction="ascending",
        polarisations=("VV", "VH"),
        footprint_bbox=(0.0, 0.0, 1.0, 1.0),
        product_size_bytes=1_000_000,
        download_url="https://example.test/scene.zip",
    )


# ---------------------------------------------------------------------------
# CFAR: N known bright blobs on a black background -> exactly N detections
# ---------------------------------------------------------------------------


class TestCfarDetectBlobCount:
    def test_black_background_with_no_blobs_detects_nothing(self):
        image = np.zeros((100, 100))
        assert cfar_detect(image, guard_px=2, background_px=10, pfa=1e-6) == []

    @pytest.mark.parametrize("n_blobs", [1, 3, 5])
    def test_n_known_bright_blobs_are_detected_as_exactly_n(self, n_blobs: int):
        image = np.zeros((300, 300)) + 1.0  # a small positive floor, real backscatter is non-negative
        centres = []
        # Space blobs far enough apart that they can never merge/overlap
        # within the CFAR background window itself.
        grid = [(60, 60), (60, 220), (220, 60), (220, 220), (140, 140)]
        for i in range(n_blobs):
            centres.append(grid[i])
            image = _add_blob(image, grid[i], half_size=2, intensity=500.0)

        detections = cfar_detect(image, guard_px=2, background_px=10, pfa=1e-6)
        assert len(detections) == n_blobs

        found_centres = {(round(d.centroid_row), round(d.centroid_col)) for d in detections}
        assert found_centres == set(centres)

    def test_defaults_match_the_documented_constants(self):
        image = np.zeros((300, 300)) + 1.0
        image = _add_blob(image, (150, 150), half_size=2, intensity=500.0)
        explicit = cfar_detect(image, guard_px=DEFAULT_GUARD_PX, background_px=DEFAULT_BACKGROUND_PX, pfa=DEFAULT_PFA)
        default = cfar_detect(image)
        assert explicit == default

    def test_invalid_guard_background_relationship_raises(self):
        image = np.zeros((50, 50))
        with pytest.raises(ValueError, match="background_px"):
            cfar_detect(image, guard_px=10, background_px=5, pfa=1e-6)

    def test_invalid_pfa_raises(self):
        image = np.zeros((50, 50))
        with pytest.raises(ValueError, match="pfa"):
            cfar_detect(image, guard_px=2, background_px=10, pfa=1.5)

    def test_non_2d_image_raises(self):
        with pytest.raises(ValueError, match="2-D"):
            cfar_detect(np.zeros((10, 10, 3)), guard_px=2, background_px=10, pfa=1e-6)

    def test_a_degenerate_zero_weight_blob_is_dropped_not_reported_as_nan(self, monkeypatch):
        """Real regression: a labeled blob whose CFAR-mask footprint sums to
        zero weight (observed live against two real Sentinel-1 scenes, both
        near a crop's own edge where the box-filter's zero-padding pulls the
        local background estimate to ~0) makes ndimage.center_of_mass divide
        0/0, returning NaN. That NaN then serialized as JSON null and broke
        Detection's float validation on every later read of the persisted
        census -- a real 500 on GET /anchorage/{port}/census. This drops any
        blob whose centroid can't be computed instead of reporting one, by
        mocking scipy's own return (a real non-negative backscatter array
        can't be coerced to trigger the exact float edge case on demand, but
        the code path under test is identical either way)."""
        import scipy.ndimage as ndimage_module

        image = np.zeros((300, 300)) + 1.0
        image = _add_blob(image, (60, 60), half_size=2, intensity=500.0)
        image = _add_blob(image, (220, 220), half_size=2, intensity=500.0)

        real_center_of_mass = ndimage_module.center_of_mass

        def fake_center_of_mass(input_arr, labels, index):
            real = real_center_of_mass(input_arr, labels, index)
            # Corrupt the first result the same way a real zero-normalizer
            # division does -- NaN, not an exception.
            corrupted = [(float("nan"), float("nan"))] + list(real[1:])
            return corrupted

        monkeypatch.setattr("anchorage.detect.ndimage.center_of_mass", fake_center_of_mass)

        detections = cfar_detect(image, guard_px=2, background_px=10, pfa=1e-6)
        assert len(detections) == 1  # the corrupted blob was dropped, not reported with a NaN centroid
        assert all(np.isfinite(d.centroid_row) and np.isfinite(d.centroid_col) for d in detections)


# ---------------------------------------------------------------------------
# Confidence degrades with simulated background clutter
# ---------------------------------------------------------------------------


class TestConfidenceDegradesWithClutter:
    def test_low_cv_is_high_confidence(self):
        assert _confidence_for_clutter(LOW_CLUTTER_CV - 0.01) == "high"

    def test_mid_cv_is_medium_confidence(self):
        midpoint = (LOW_CLUTTER_CV + HIGH_CLUTTER_CV) / 2
        assert _confidence_for_clutter(midpoint) == "medium"

    def test_high_cv_is_low_confidence(self):
        assert _confidence_for_clutter(HIGH_CLUTTER_CV + 0.01) == "low"

    def test_raising_simulated_background_clutter_reduces_confidence_end_to_end(self):
        """A calm background vs. the SAME underlying random field, spatially
        smoothed and scaled up, to simulate rough-sea texture -- confidence
        must degrade from the calm run to the rough run, via the real
        count_vessels() pipeline, not just the isolated threshold function.

        The clutter is built as SPATIALLY-CORRELATED texture
        (``scipy.ndimage.gaussian_filter`` on a random field, correlation
        length comparable to the CFAR background window), not plain
        per-pixel i.i.d. noise scaled up -- a real, physically-motivated
        choice, found necessary while writing this test: CFAR's own
        background box-filter averages over ~400 reference cells, which
        suppresses UNCORRELATED per-pixel variance by roughly
        sqrt(400) ~= 20x (confirmed directly: even an 8x i.i.d. noise scale-
        up barely moved the resulting coefficient of variation). Real rough
        sea state is not uncorrelated pixel noise -- it is real spatial
        texture (wind streaks, wave patterns) at a scale the CFAR window
        does not smooth away, which is exactly why it is a genuine
        detection risk in real SAR imagery and exactly why this synthetic
        test needs to look like that, not like plain noise, to be honest
        about what the confidence proxy actually responds to."""
        rng = np.random.RandomState(7)
        field = rng.normal(loc=0.0, scale=1.0, size=(200, 200))
        texture = ndimage.gaussian_filter(field, sigma=10.0)
        texture = texture / texture.std()

        calm = np.clip(50.0 + 3.0 * texture, 0.01, None)
        calm = _add_blob(calm, (100, 100), half_size=2, intensity=300.0)

        rough = np.clip(50.0 + 30.0 * texture, 0.01, None)
        rough = _add_blob(rough, (100, 100), half_size=2, intensity=300.0)

        calm_census = count_vessels(
            Path("unused"), "TEST_PORT", scene_metadata=_scene_metadata("calm"), image=calm
        )
        rough_census = count_vessels(
            Path("unused"), "TEST_PORT", scene_metadata=_scene_metadata("rough"), image=rough
        )

        assert rough_census.mean_sea_state_proxy > calm_census.mean_sea_state_proxy
        confidence_order = {"high": 0, "medium": 1, "low": 2}
        assert confidence_order[rough_census.confidence] > confidence_order[calm_census.confidence]


# ---------------------------------------------------------------------------
# Size gate: computed FROM pixel spacing, not a hardcoded pixel count
# ---------------------------------------------------------------------------


class TestSizeGate:
    def test_threshold_is_computed_from_pixel_spacing_not_hardcoded(self):
        """3m x 3m spacing, 30 m^2 minimum vessel -> ceil(30/9) = 4 px. A
        different spacing must give a different threshold -- this IS the
        'computed from pixel spacing' property the task asks for."""
        assert _min_area_px((3.0, 3.0), 30.0) == 4
        assert _min_area_px((1.0, 1.0), 30.0) == 30
        assert _min_area_px((10.0, 10.0), 30.0) == 1  # see apply_size_gate's own disclosed note

    def test_a_single_hot_pixel_is_rejected_at_a_finer_spacing(self):
        """At 3m spacing (finer than Sentinel-1's real 10m -- see
        apply_size_gate's own docstring for why the real default spacing
        cannot make this distinction), a lone 1-pixel blob is smaller than
        a plausible vessel and must be rejected."""
        hot_pixel = Detection(centroid_row=10.0, centroid_col=10.0, area_px=1, peak_intensity=999.0)
        result = apply_size_gate([hot_pixel], pixel_spacing_m=(3.0, 3.0), min_vessel_area_m2=30.0)
        assert result == []

    def test_a_ship_sized_blob_is_accepted_at_the_same_finer_spacing(self):
        """4 px at 3m spacing = 36 m^2 >= the 30 m^2 minimum -- accepted."""
        ship = Detection(centroid_row=10.0, centroid_col=10.0, area_px=4, peak_intensity=999.0)
        result = apply_size_gate([ship], pixel_spacing_m=(3.0, 3.0), min_vessel_area_m2=30.0)
        assert result == [ship]

    def test_zero_pixel_spacing_raises_rather_than_dividing_silently(self):
        with pytest.raises(ValueError, match="positive"):
            _min_area_px((0.0, 3.0), 30.0)


# ---------------------------------------------------------------------------
# Merge: two adjacent blobs within the merge radius count as one
# ---------------------------------------------------------------------------


class TestMergeNearbyDetections:
    def test_two_adjacent_blobs_within_merge_radius_become_one(self):
        a = Detection(centroid_row=100.0, centroid_col=100.0, area_px=4, peak_intensity=500.0)
        b = Detection(centroid_row=101.0, centroid_col=101.0, area_px=4, peak_intensity=400.0)  # ~1.4 px away
        merged = merge_nearby_detections([a, b], pixel_spacing_m=(10.0, 10.0), merge_radius_m=20.0)
        assert len(merged) == 1
        assert merged[0].area_px == 8
        assert merged[0].peak_intensity == 500.0  # the max of the two

    def test_two_distant_blobs_beyond_merge_radius_stay_separate(self):
        a = Detection(centroid_row=0.0, centroid_col=0.0, area_px=4, peak_intensity=500.0)
        b = Detection(centroid_row=100.0, centroid_col=100.0, area_px=4, peak_intensity=400.0)
        merged = merge_nearby_detections([a, b], pixel_spacing_m=(10.0, 10.0), merge_radius_m=20.0)
        assert len(merged) == 2

    def test_empty_input_returns_empty(self):
        assert merge_nearby_detections([], pixel_spacing_m=(10.0, 10.0), merge_radius_m=20.0) == []

    def test_a_chain_of_three_within_radius_merges_to_one(self):
        chain = [
            Detection(centroid_row=0.0, centroid_col=0.0, area_px=1, peak_intensity=100.0),
            Detection(centroid_row=1.0, centroid_col=0.0, area_px=1, peak_intensity=200.0),
            Detection(centroid_row=2.0, centroid_col=0.0, area_px=1, peak_intensity=150.0),
        ]
        merged = merge_nearby_detections(chain, pixel_spacing_m=(10.0, 10.0), merge_radius_m=20.0)
        assert len(merged) == 1
        assert merged[0].area_px == 3
        assert merged[0].peak_intensity == 200.0


# ---------------------------------------------------------------------------
# Land mask: a land-masked region contributes no detections
# ---------------------------------------------------------------------------


class TestLandMask:
    def test_no_water_bbox_keeps_every_detection(self):
        dets = [Detection(centroid_row=5.0, centroid_col=5.0, area_px=1, peak_intensity=1.0)]
        assert apply_land_mask(dets, water_bbox_px=None) == dets

    def test_a_detection_on_land_outside_the_water_bbox_is_dropped(self):
        on_land = Detection(centroid_row=5.0, centroid_col=5.0, area_px=4, peak_intensity=500.0)
        in_water = Detection(centroid_row=50.0, centroid_col=50.0, area_px=4, peak_intensity=500.0)
        result = apply_land_mask([on_land, in_water], water_bbox_px=(20, 80, 20, 80))
        assert result == [in_water]

    def test_a_fully_land_masked_region_contributes_no_detections(self):
        """The named acceptance case: every real detection sits outside the
        water rectangle -- the land-masked region contributes nothing."""
        all_on_land = [
            Detection(centroid_row=1.0, centroid_col=1.0, area_px=4, peak_intensity=500.0),
            Detection(centroid_row=2.0, centroid_col=99.0, area_px=4, peak_intensity=500.0),
        ]
        result = apply_land_mask(all_on_land, water_bbox_px=(50, 60, 50, 60))
        assert result == []

    def test_boundary_pixels_of_the_water_bbox_are_inclusive(self):
        on_edge = Detection(centroid_row=20.0, centroid_col=20.0, area_px=1, peak_intensity=1.0)
        assert apply_land_mask([on_edge], water_bbox_px=(20, 80, 20, 80)) == [on_edge]


# ---------------------------------------------------------------------------
# load_scene: real now (rasterio added once a real Copernicus account
# existed to exercise it against -- see pyproject.toml's own comment on
# that dependency). No real Sentinel-1 scene ships in this test suite (a
# real one is a real ~1.2 GB download), so these tests exercise the real
# error paths against synthetic inputs rather than the full real read.
# ---------------------------------------------------------------------------


class TestLoadSceneRealErrorPaths:
    def test_a_path_that_is_neither_a_zip_nor_a_directory_raises_clearly(self):
        with pytest.raises(GeoTIFFBackendUnavailableError, match="neither a .zip nor a directory"):
            load_scene(Path("/does/not/matter.tiff"), (0.0, 0.0, 1.0, 1.0))

    def test_a_zip_with_no_matching_polarisation_band_raises_clearly(self, tmp_path: Path):
        import zipfile

        empty_zip = tmp_path / "empty.zip"
        with zipfile.ZipFile(empty_zip, "w"):
            pass
        with pytest.raises(GeoTIFFBackendUnavailableError, match="measurement/annotation pair"):
            load_scene(empty_zip, (0.0, 0.0, 1.0, 1.0))

    def test_geolocation_grid_parsing_against_a_real_shaped_fixture(self):
        """A real Sentinel-1 annotation XML shape (trimmed to a handful of
        geolocationGridPoint entries, the real element/field names) parses
        into real (line, pixel, lat, lon) tuples."""
        xml = b"""<?xml version="1.0"?>
        <product>
          <geolocationGrid>
            <geolocationGridPointList count="2">
              <geolocationGridPoint>
                <line>0</line><pixel>0</pixel>
                <latitude>19.784727</latitude><longitude>85.836426</longitude>
              </geolocationGridPoint>
              <geolocationGridPoint>
                <line>0</line><pixel>1287</pixel>
                <latitude>19.807154</latitude><longitude>85.956939</longitude>
              </geolocationGridPoint>
            </geolocationGridPointList>
          </geolocationGrid>
        </product>"""
        from anchorage.detect import _parse_geolocation_grid

        points = _parse_geolocation_grid(xml)
        assert points == [
            (0, 0, 19.784727, 85.836426),
            (0, 1287, 19.807154, 85.956939),
        ]

    def test_geolocation_grid_parsing_rejects_a_non_s1_xml(self):
        from anchorage.detect import _parse_geolocation_grid

        with pytest.raises(ValueError, match="geolocationGridPoint"):
            _parse_geolocation_grid(b"<not_a_real_product/>")


# ---------------------------------------------------------------------------
# count_vessels: the full pipeline, end to end on synthetic data
# ---------------------------------------------------------------------------


class TestCountVessels:
    def test_full_pipeline_on_a_synthetic_scene_with_known_vessels(self):
        image = np.zeros((300, 300)) + 1.0
        for centre in [(60, 60), (220, 220)]:
            image = _add_blob(image, centre, half_size=2, intensity=500.0)

        census = count_vessels(
            Path("unused"), "PARADIP", scene_metadata=_scene_metadata("real-shaped-id"), image=image
        )
        assert census.vessel_count == 2
        assert len(census.detections) == 2
        assert census.port == "PARADIP"
        assert census.scene_id == "real-shaped-id"
        assert census.provenance == "MODEL_DERIVED"
        assert census.confidence in ("high", "medium", "low")

    def test_requires_either_image_or_scene_metadata_plus_a_way_to_load(self):
        with pytest.raises(ValueError, match="scene_metadata"):
            count_vessels(Path("unused"), "PARADIP")

    def test_requires_scene_metadata_even_when_image_is_supplied(self):
        with pytest.raises(ValueError, match="scene_metadata"):
            count_vessels(Path("unused"), "PARADIP", image=np.zeros((10, 10)))

    def test_land_masked_water_bbox_reduces_the_count(self):
        image = np.zeros((300, 300)) + 1.0
        image = _add_blob(image, (10, 10), half_size=2, intensity=500.0)  # will be "on land"
        image = _add_blob(image, (200, 200), half_size=2, intensity=500.0)  # will be "in water"

        params = CFARParams(water_bbox_px=(100, 300, 100, 300))
        census = count_vessels(
            Path("unused"), "PARADIP", scene_metadata=_scene_metadata(), image=image, params=params
        )
        assert census.vessel_count == 1

    def test_custom_params_are_honoured(self):
        """A single-pixel target here, deliberately -- guard_px=1 gives a
        3x3 guard box, which only comfortably excludes a target no larger
        than that from its own background estimate. A 5x5 blob under these
        same params genuinely self-masks (a real, known CFAR failure mode
        when the guard band is smaller than the target -- confirmed
        directly: it produces zero detections), which would make this test
        assert the wrong thing about what "custom params are honoured"
        means; a target sized to fit the custom guard band isolates that
        the custom guard_px/background_px/pfa values actually reached
        cfar_detect, without also exercising self-masking."""
        image = np.zeros((300, 300)) + 1.0
        image[150, 150] = 500.0
        params = CFARParams(guard_px=1, background_px=5, pfa=1e-3)
        census = count_vessels(
            Path("unused"), "PARADIP", scene_metadata=_scene_metadata(), image=image, params=params
        )
        assert census.vessel_count == 1


# ---------------------------------------------------------------------------
# The real local georeferencing fit -- a synthetic-but-realistic grid
# ---------------------------------------------------------------------------


class TestLocalAffineFit:
    def _synthetic_grid(self):
        """A synthetic geolocation grid built from a KNOWN real affine
        relationship (row = 100*lat - 2000, col = 100*lon - 8600) plus a
        regular sample lattice -- lets the test assert the fitted
        coefficients recover the known relationship almost exactly, which a
        real (noisy) Sentinel-1 grid could not guarantee this precisely."""
        points = []
        for line_step in range(5):
            for pixel_step in range(5):
                lat = 20.0 + line_step * 0.05
                lon = 86.0 + pixel_step * 0.05
                line = round(100 * lat - 2000)
                pixel = round(100 * lon - 8600)
                points.append((line, pixel, lat, lon))
        return points

    def test_fit_recovers_a_known_linear_relationship(self):
        from anchorage.detect import _fit_local_affine

        points = self._synthetic_grid()
        bbox = (86.05, 20.05, 86.15, 20.15)
        fit = _fit_local_affine(points, bbox)

        row, col = fit.to_pixel(86.1, 20.1)
        assert row == pytest.approx(100 * 20.1 - 2000, abs=0.5)
        assert col == pytest.approx(100 * 86.1 - 8600, abs=0.5)

    def test_fit_derives_a_real_positive_pixel_spacing(self):
        from anchorage.detect import _fit_local_affine

        fit = _fit_local_affine(self._synthetic_grid(), (86.05, 20.05, 86.15, 20.15))
        assert fit.pixel_spacing_m[0] > 0
        assert fit.pixel_spacing_m[1] > 0


class TestRealGroundSpacing:
    def test_a_real_one_degree_latitude_step_is_about_111km_over_100_steps(self):
        from anchorage.detect import _real_ground_spacing_m

        # 100 line-steps spanning exactly 1 degree of latitude at the equator
        # -- 1 degree of latitude is a real ~111.2 km, so each of the 100
        # steps is ~1112 m.
        p_a = (0, 0, 0.0, 0.0)
        p_b = (100, 0, 1.0, 0.0)
        spacing = _real_ground_spacing_m(p_a, p_b, axis=0)
        assert spacing == pytest.approx(1_112.0, rel=0.01)

    def test_degenerate_same_index_falls_back_to_the_default_spacing(self):
        from anchorage.detect import DEFAULT_PIXEL_SPACING_M, _real_ground_spacing_m

        p_a = (0, 0, 20.0, 86.0)
        p_b = (0, 0, 20.001, 86.001)  # same line index (axis=0) -- degenerate
        assert _real_ground_spacing_m(p_a, p_b, axis=0) == DEFAULT_PIXEL_SPACING_M[0]
