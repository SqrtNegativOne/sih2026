"""Tests for anchorage.render -- real Pillow rendering, no real imagery.

Every image here is a small synthetic array (real Sentinel-1 crops run to a
few thousand pixels a side and ~1 GB source scenes, both too large for this
suite); the point under test is the rendering/downscale/marker-placement
logic, not any particular scene's own pixel content.
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

from anchorage.detect import Detection
from anchorage.render import _MAX_OUTPUT_EDGE_PX, overlay_path, render_detection_overlay


def _detection(row: float, col: float, *, area_px: int = 6, peak: float = 500.0) -> Detection:
    return Detection(centroid_row=row, centroid_col=col, area_px=area_px, peak_intensity=peak)


class TestRenderDetectionOverlay:
    def test_writes_a_real_png_at_out_path(self, tmp_path: Path) -> None:
        image = np.random.RandomState(0).normal(loc=10.0, scale=1.0, size=(120, 150))
        out = tmp_path / "PARADIP" / "scene.png"
        result = render_detection_overlay(
            np.clip(image, 0.01, None),
            [_detection(60, 75)],
            port="PARADIP",
            scene_id="TEST_SCENE",
            acquired_at=datetime(2026, 8, 28, 12, 0, tzinfo=UTC),
            out_path=out,
        )
        assert result == out
        assert out.exists()
        assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"  # real PNG magic bytes

    def test_no_detections_still_renders_a_plain_image(self, tmp_path: Path) -> None:
        image = np.clip(np.random.RandomState(1).normal(10.0, 1.0, (80, 80)), 0.01, None)
        out = tmp_path / "empty.png"
        render_detection_overlay(
            image, [], port="RICHARDS_BAY_ZA", scene_id="S", acquired_at=datetime(2026, 1, 1, tzinfo=UTC), out_path=out
        )
        assert out.exists()

    def test_output_is_downscaled_to_the_documented_cap(self, tmp_path: Path) -> None:
        from PIL import Image

        image = np.clip(np.random.RandomState(2).normal(10.0, 1.0, (1500, 2000)), 0.01, None)
        out = tmp_path / "big.png"
        render_detection_overlay(
            image, [], port="NEWCASTLE_AU", scene_id="S", acquired_at=datetime(2026, 1, 1, tzinfo=UTC), out_path=out
        )
        with Image.open(out) as img:
            assert max(img.width, img.height) <= _MAX_OUTPUT_EDGE_PX

    def test_a_small_image_is_not_upscaled(self, tmp_path: Path) -> None:
        from PIL import Image

        image = np.clip(np.random.RandomState(3).normal(10.0, 1.0, (50, 60)), 0.01, None)
        out = tmp_path / "small.png"
        render_detection_overlay(
            image, [], port="HAY_POINT_AU", scene_id="S", acquired_at=datetime(2026, 1, 1, tzinfo=UTC), out_path=out
        )
        with Image.open(out) as img:
            assert (img.width, img.height) == (60, 50)

    def test_non_2d_image_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="2-D"):
            render_detection_overlay(
                np.zeros((10, 10, 3)),
                [],
                port="PARADIP",
                scene_id="S",
                acquired_at=datetime(2026, 1, 1, tzinfo=UTC),
                out_path=tmp_path / "x.png",
            )

    def test_creates_missing_parent_directories(self, tmp_path: Path) -> None:
        out = tmp_path / "a" / "b" / "c.png"
        render_detection_overlay(
            np.clip(np.random.RandomState(4).normal(10.0, 1.0, (40, 40)), 0.01, None),
            [],
            port="VISAKHAPATNAM",
            scene_id="S",
            acquired_at=datetime(2026, 1, 1, tzinfo=UTC),
            out_path=out,
        )
        assert out.exists()


class TestOverlayPath:
    def test_keys_by_port_and_scene_id(self, tmp_path: Path) -> None:
        p = overlay_path("PARADIP", "SCENE_A", root=tmp_path)
        assert p == tmp_path / "PARADIP" / "SCENE_A.png"

    def test_different_scene_id_is_a_different_path(self, tmp_path: Path) -> None:
        a = overlay_path("PARADIP", "SCENE_A", root=tmp_path)
        b = overlay_path("PARADIP", "SCENE_B", root=tmp_path)
        assert a != b
