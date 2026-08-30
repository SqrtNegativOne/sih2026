"""Render a real Sentinel-1 anchorage crop with its CFAR detections marked,
as a PNG a human can actually look at instead of a table of centroid pixels.

Nothing here changes what counts as a detection -- this is presentation only,
consuming the same ``Detection`` objects ``anchorage.detect.cfar_detect``
already produced and persisted via ``anchorage.store.record_census``. It
never re-runs the detector and never draws a marker that isn't backed by a
real, stored ``Detection``.

Raw Sentinel-1 GRD digital numbers span several orders of magnitude (a calm
sea is near-black, a vessel's corner-reflector return can be 100-1000x the
surrounding clutter), so a raw linear grayscale render is almost entirely
black with a few near-white pixels -- unusable. This applies a square-root
stretch (a cheap, standard SAR display convention; a log stretch was
considered but over-brightens the low-signal sea clutter this detector's own
CFAR threshold is specifically distinguishing from real returns) clipped to
the crop's own real 1st/99.5th percentile range, so contrast is always
scaled to what is actually in THIS crop rather than a fixed constant that
would be wrong for a different scene's dynamic range.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Final

import numpy as np

if TYPE_CHECKING:
    from anchorage.detect import Detection

__all__ = ["OVERLAY_DIR", "overlay_path", "render_detection_overlay"]

#: Same root ``anchorage.store.ANCHORAGE_DIR`` uses. Overlays are keyed by
#: (port, scene_id) rather than just port, so a caller can never be served a
#: stale image that no longer matches the CURRENT ``latest_census(port)`` --
#: if nobody has rendered an overlay for the latest scene yet, the lookup
#: honestly misses rather than returning an old scene's picture under the
#: new scene's numbers.
REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
OVERLAY_DIR: Final[Path] = REPO_ROOT / "raw_data" / "anchorage" / "overlays"


def overlay_path(port: str, scene_id: str, *, root: Path = OVERLAY_DIR) -> Path:
    """Where ``render_detection_overlay`` writes / a reader should look for
    the overlay PNG for this exact (port, scene_id)."""
    return root / port / f"{scene_id}.png"

#: Radius (pixels, in the OUTPUT image after any downscale) of the marker
#: ring drawn at each detection centroid. Cosmetic only.
_MARKER_RADIUS_PX: int = 6

#: Long-edge cap on the rendered PNG. An anchorage crop can run to ~1500px
#: on a side; downscaling to this keeps the file a reasonable size for a
#: frontend panel without needing full-resolution pixels a human can't
#: usefully see at panel scale anyway. Detection markers are repositioned by
#: the same scale factor, not redrawn at native resolution then shrunk, so
#: they stay aligned with the (downscaled) backscatter under them.
_MAX_OUTPUT_EDGE_PX: int = 900


def render_detection_overlay(
    image: np.ndarray,
    detections: list[Detection] | tuple[Detection, ...],
    *,
    port: str,
    scene_id: str,
    acquired_at: datetime,
    out_path: Path,
) -> Path:
    """Write a PNG of ``image`` (the same 2-D backscatter array
    ``anchorage.detect.load_scene`` returns) with a red ring at every real
    ``detections`` centroid, to ``out_path``. Returns ``out_path``.

    Raises ``OverlayBackendUnavailableError`` if Pillow is not installed --
    named and explicit, same convention as ``detect.load_scene``'s own
    ``GeoTIFFBackendUnavailableError`` for a missing ``rasterio``.
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:
        raise OverlayBackendUnavailableError(
            "Cannot render an overlay: Pillow is not installed. Run `uv add pillow` -- "
            "see pyproject.toml's own comment on this dependency."
        ) from exc

    arr = np.asarray(image, dtype=np.float64)
    if arr.ndim != 2:
        raise ValueError(f"image must be 2-D, got shape {arr.shape}")

    lo, hi = np.percentile(arr, [1.0, 99.5])
    stretched = np.sqrt(np.clip((arr - lo) / max(hi - lo, 1e-9), 0.0, 1.0))
    gray = (stretched * 255).astype(np.uint8)

    img = Image.fromarray(gray, mode="L").convert("RGB")
    scale = min(1.0, _MAX_OUTPUT_EDGE_PX / max(img.width, img.height))
    if scale < 1.0:
        img = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))), Image.LANCZOS)

    draw = ImageDraw.Draw(img)
    for d in detections:
        cx = d.centroid_col * scale
        cy = d.centroid_row * scale
        r = _MARKER_RADIUS_PX
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(255, 40, 40), width=2)

    caption = f"{port}  {scene_id}  {acquired_at.strftime('%Y-%m-%d %H:%MZ')}  {len(detections)} detection(s)"
    pad = 4
    try:
        font = ImageFont.load_default()
    except Exception:  # noqa: BLE001 -- a missing default font must not break the render
        font = None
    text_bbox = draw.textbbox((0, 0), caption, font=font)
    bar_h = (text_bbox[3] - text_bbox[1]) + 2 * pad
    draw.rectangle([0, img.height - bar_h, img.width, img.height], fill=(0, 0, 0))
    draw.text((pad, img.height - bar_h + pad), caption, fill=(255, 255, 255), font=font)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, format="PNG", optimize=True)
    return out_path


class OverlayBackendUnavailableError(RuntimeError):
    """Pillow is not installed, so a detection overlay cannot be rendered."""
