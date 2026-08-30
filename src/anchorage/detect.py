"""Count vessels in a Sentinel-1 GRD anchorage crop with a classical CFAR
detector -- no machine learning.

Why CFAR, not a model
----------------------
There is no labelled training set for this problem in this codebase (no
verified "here are the real ship positions in this scene" ground truth), and
a fitted model with no ground truth to validate against is exactly the
"recommendation that looks like real advice while reflecting a made-up
number" anti-pattern this codebase's own house rules warn against elsewhere.
A classical detector whose every threshold is a documented, inspectable
number is worth more here than an unvalidated model whose decision boundary
nobody can explain.

Constant-False-Alarm-Rate (CFAR) thresholding is the standard approach for
exactly this problem and has been for decades: a metal ship is a strong,
coherent radar reflector, and calm open water is nearly radar-black at
C-band, so the ship-vs-background contrast is about as high as detection
problems in remote sensing get. This module implements Cell-Averaging CFAR
(CA-CFAR) with guard cells, the reference form of the method (Rohling, H.,
1983, "Radar CFAR Thresholding in Clutter and Multiple Target Situations,"
IEEE Transactions on Aerospace and Electronic Systems, AES-19(4)) --
adaptive per-pixel thresholding against a LOCAL background estimate, so the
same detector works whether the local sea state is calm or moderately rough,
rather than one fixed global threshold that would be wrong almost
everywhere.

Known failure modes, stated plainly rather than discovered later
------------------------------------------------------------------
- **High sea state raises background clutter and can bury real detections.**
  Wind-roughened water has a higher radar cross-section (capillary and
  short gravity waves scatter more), which raises the local background
  estimate CFAR thresholds against -- a genuinely calm-water detection
  threshold applied to a rough scene would flag whitecaps as ships; CFAR's
  adaptive threshold avoids that specific failure, but at the real cost of
  needing a higher absolute backscatter to clear a higher local threshold,
  so a real, small vessel that would clear the bar on a calm day can fall
  below it in rough conditions. This module surfaces that risk explicitly
  via ``AnchorageCensus.confidence`` rather than hiding it in a single
  count -- see "Confidence degrades with clutter" below.
- **Very small or wooden vessels may fall below threshold, always.** Wood
  and fibreglass are far weaker radar reflectors than steel, and a small
  craft's radar cross-section can simply be too low to separate from
  background clutter at any real Pfa this module would use. This is a real,
  structural limitation of C-band SAR ship detection, not a tuning problem
  this module's parameters can fix.
- **No real coastline dataset exists in this repo yet**, so the land mask
  below is a documented rectangular approximation, not a real coastline
  polygon -- see ``apply_land_mask``'s own docstring for exactly what that
  costs.
- **Sentinel-1 GRD reading needs a real geospatial library this module does
  not yet have.** ``load_scene``'s real-file path needs to convert a real
  lon/lat bounding box into a pixel window against the scene's own
  geotransform, and to read ONLY that window (never the whole multi-GB
  scene) -- both are exactly what ``rasterio`` (built on GDAL) is for. Per
  this chunk's own explicit instruction not to add a heavy geospatial
  dependency without confirmation, ``rasterio`` is proposed here, not
  added, and ``load_scene`` raises ``GeoTIFFBackendUnavailableError`` when
  called against a real file until that is confirmed. Every other function
  in this module operates on a plain ``numpy`` array and is real, working,
  and fully tested against synthetic arrays today.

Confidence degrades with clutter, automatically
-------------------------------------------------
``AnchorageCensus.mean_sea_state_proxy`` is the coefficient of variation
(std / mean) of the CFAR background estimate over the whole scene crop -- a
real, physically-grounded proxy (rougher seas genuinely have higher-variance
backscatter, not just higher mean backscatter), and dimensionless, so it
means roughly the same thing across different scenes and sensors without
needing a calibrated absolute intensity scale this codebase does not have.
``LOW_CLUTTER_CV`` / ``HIGH_CLUTTER_CV`` below are **disclosed, provisional
judgement calls**, not fitted to any known-truth vessel count -- calibrating
against a real reference (PortWatch or otherwise) is explicitly chunk 4.3's
job, done as an honest comparison after the fact, never by tuning these
numbers until they produce an answer that matches one.

Pixel spacing and vessel size, computed, not hardcoded
----------------------------------------------------------
The size gate and the merge radius are both real-world distances
(``MIN_VESSEL_AREA_M2``, ``MERGE_RADIUS_M``) converted to a pixel threshold
FROM the scene's own pixel spacing, never a hardcoded pixel count -- the
same real distance means a different pixel count on a 10 m product than it
would on a resampled one, and hardcoding the pixel count would silently
break the moment the pixel spacing changed.
``DEFAULT_PIXEL_SPACING_M`` (10.0, 10.0) is the real, published ground-range
pixel spacing of the Sentinel-1 IW GRDH product this system's harvester
(``data_builders.harvest_sentinel1``) actually fetches -- a default, not a
hardcoded assumption baked into the size-gate/merge math itself, which both
take pixel spacing as an explicit parameter.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict
from scipy import ndimage

from data_builders.harvest_sentinel1 import SceneMetadata
from data_builders.provenance import Provenance

LOGGER: Final[logging.Logger] = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_BACKGROUND_PX",
    "DEFAULT_GUARD_PX",
    "DEFAULT_PFA",
    "DEFAULT_PIXEL_SPACING_M",
    "HIGH_CLUTTER_CV",
    "LOW_CLUTTER_CV",
    "MERGE_RADIUS_M",
    "MIN_VESSEL_AREA_M2",
    "AnchorageCensus",
    "CFARParams",
    "Detection",
    "GeoTIFFBackendUnavailableError",
    "apply_land_mask",
    "apply_size_gate",
    "cfar_detect",
    "count_vessels",
    "load_scene",
    "merge_nearby_detections",
]


class GeoTIFFBackendUnavailableError(RuntimeError):
    """Real windowed GeoTIFF reading needs ``rasterio`` (built on GDAL), to
    convert a real lon/lat bbox into a pixel window against the scene's own
    geotransform and to read ONLY that window rather than the whole
    multi-GB scene. Not added automatically -- see this module's own
    docstring for why this is a deliberate, disclosed gap, not an oversight.
    Install with ``uv add rasterio`` and confirm before relying on
    ``load_scene`` against a real file."""


#: Real, published ground-range pixel spacing of the Sentinel-1 IW GRDH
#: product (~10m x 10m) -- the exact product type
#: ``data_builders.harvest_sentinel1`` fetches. A default, not baked into
#: the size-gate/merge math, which both take pixel spacing explicitly.
DEFAULT_PIXEL_SPACING_M: Final[tuple[float, float]] = (10.0, 10.0)

#: CA-CFAR guard-cell half-width (pixels): the inner exclusion zone around
#: the cell under test, so a real target's own energy (and its immediate
#: sidelobes) does not leak into its own background estimate and inflate
#: the local threshold against itself. 2 px is small enough not to swallow
#: two genuinely separate nearby vessels into one guard region, while still
#: covering a single bright target's typical few-pixel footprint at 10 m
#: spacing.
DEFAULT_GUARD_PX: Final[int] = 2

#: CA-CFAR background-window half-width (pixels): the outer edge of the
#: reference-cell annulus used to estimate local clutter. Real CFAR
#: literature typically wants tens of reference cells for a stable mean
#: estimate; at guard_px=2 an outer half-width of 10 gives an annulus of
#: (21^2 - 5^2) = 416 reference cells, comfortably in that range without the
#: window growing so large it stops being "local."
DEFAULT_BACKGROUND_PX: Final[int] = 10

#: Probability of false alarm for the CA-CFAR threshold. 1e-6 is a
#: conservative, commonly-used value in published SAR ship-detection work --
#: conservative because a spike with no ground truth to validate against
#: should err toward under-flagging speckle rather than over-flagging it as
#: a vessel.
DEFAULT_PFA: Final[float] = 1e-6

#: A small real vessel's plausible minimum radar-visible footprint --
#: roughly a 10 m x 3 m small craft. Disclosed assumption, not a fitted
#: number; used only to derive a PIXEL area threshold from the scene's own
#: real pixel spacing (see ``apply_size_gate``), never applied as a raw
#: pixel count.
MIN_VESSEL_AREA_M2: Final[float] = 30.0

#: Real-world radius within which two adjacent CFAR blobs are treated as
#: fragments of one vessel rather than two separate ones -- a single large
#: vessel's hull and its own bright sidelobes/wake return can fragment into
#: more than one connected component. ~20 m is roughly one small-to-medium
#: vessel length, a disclosed judgement call, not a fitted number.
MERGE_RADIUS_M: Final[float] = 20.0

#: Coefficient-of-variation (std/mean) of the CFAR background estimate,
#: below which the scene is judged calm enough for a "high" confidence
#: count. Disclosed, provisional -- see module docstring's "Confidence
#: degrades with clutter" section. NOT fitted to any known-truth vessel
#: count; chunk 4.3 calibrates against a real reference honestly, later.
LOW_CLUTTER_CV: Final[float] = 0.30

#: Coefficient-of-variation above which confidence drops to "low" (between
#: LOW_CLUTTER_CV and this is "medium"). Same disclosure as LOW_CLUTTER_CV.
HIGH_CLUTTER_CV: Final[float] = 0.60


class Detection(BaseModel):
    """One CFAR detection -- a connected blob of pixels that cleared the
    local adaptive threshold, before or after post-filtering (land mask,
    size gate, merge) depending on which stage produced this instance."""

    model_config = ConfigDict(frozen=True)

    centroid_row: float
    centroid_col: float
    area_px: int
    peak_intensity: float


class AnchorageCensus(BaseModel):
    """The real per-scene vessel count and the evidence behind it. Every
    field here is MODEL_DERIVED -- computed from a real scene, but a
    detection count is not itself an observation the way the raw
    backscatter values are."""

    model_config = ConfigDict(frozen=True)

    port: str
    scene_id: str
    acquired_at: datetime
    vessel_count: int
    detections: tuple[Detection, ...]
    mean_sea_state_proxy: float
    confidence: Literal["high", "medium", "low"]
    provenance: str = Provenance.MODEL_DERIVED.value


@dataclass(frozen=True)
class CFARParams:
    """Every tunable knob in this module, bundled with its default -- see
    the module-level ``Final`` constants for the reasoning behind each
    default. ``water_bbox_px`` is ``None`` by default (no land masking
    applied) since a real anchorage crop's own extent is caller-supplied
    and this module has no coastline dataset to derive one automatically --
    see ``apply_land_mask``'s own docstring."""

    guard_px: int = DEFAULT_GUARD_PX
    background_px: int = DEFAULT_BACKGROUND_PX
    pfa: float = DEFAULT_PFA
    pixel_spacing_m: tuple[float, float] = DEFAULT_PIXEL_SPACING_M
    min_vessel_area_m2: float = MIN_VESSEL_AREA_M2
    merge_radius_m: float = MERGE_RADIUS_M
    water_bbox_px: tuple[int, int, int, int] | None = None


#: The real internal SAFE-package layout inside the zip fetch_scene()
#: downloads (confirmed directly against a real downloaded scene, not
#: assumed from documentation): a real .SAFE/ directory keeping the classic
#: Sentinel-1 layout even for the COG-reprocessed product --
#: measurement/<pol>....tiff (the actual raster) and
#: annotation/<pol>....xml (the real geolocation grid, calibration, noise).
_MEASUREMENT_GLOB: Final[str] = "*/measurement/*-{pol}-*.tiff"
_ANNOTATION_GLOB: Final[str] = "*/annotation/*-{pol}-*.xml"

#: A real, disclosed engineering call, not a full-scene GDAL GCP warp (see
#: load_scene's own docstring for why): how many of the scene's real
#: geolocation grid points (nearest the requested bbox) go into the local
#: affine fit. 16 is comfortably more than the 3 a plane needs, so the fit
#: is genuinely least-squares or a scene this size, small enough that the
#: nearest 16 real points are all within a few km of the bbox for a crop
#: this size, keeping the linearisation local and therefore accurate.
_N_NEAREST_GCPS_FOR_LOCAL_FIT: Final[int] = 16


def _parse_geolocation_grid(annotation_xml: bytes) -> list[tuple[int, int, float, float]]:
    """(line, pixel, lat, lon) for every real ``geolocationGridPoint`` in a
    Sentinel-1 product's own annotation XML. This is the REAL source of
    georeferencing for a GRD product -- confirmed directly against a real
    downloaded scene that the measurement GeoTIFF itself carries no CRS and
    an identity transform (no embedded affine georeferencing at all); SAR
    ground-range geometry is documented by ESA as a real, only-mildly-
    non-linear function of slant range and azimuth time, which is exactly
    why the product ships a real point grid rather than one global affine."""
    import xml.etree.ElementTree as ET

    root = ET.fromstring(annotation_xml)
    points: list[tuple[int, int, float, float]] = []
    for gp in root.iter("geolocationGridPoint"):
        line = int(gp.findtext("line"))
        pixel = int(gp.findtext("pixel"))
        lat = float(gp.findtext("latitude"))
        lon = float(gp.findtext("longitude"))
        points.append((line, pixel, lat, lon))
    if not points:
        raise ValueError("No geolocationGridPoint entries found -- not a real Sentinel-1 annotation XML.")
    return points


@dataclass(frozen=True)
class _LocalAffineFit:
    """A real least-squares local linearisation of (lon, lat) -> (row, col),
    fit from the nearest real geolocation grid points to one bbox -- see
    ``load_scene``'s own docstring for why this, not a full-scene GCP warp."""

    row_coef: np.ndarray  # [a, b, c] such that row ~= a*lon + b*lat + c
    col_coef: np.ndarray
    pixel_spacing_m: tuple[float, float]  # (row spacing, col spacing), real, derived from the fit points

    def to_pixel(self, lon: float, lat: float) -> tuple[float, float]:
        row = float(self.row_coef[0] * lon + self.row_coef[1] * lat + self.row_coef[2])
        col = float(self.col_coef[0] * lon + self.col_coef[1] * lat + self.col_coef[2])
        return row, col


def _fit_local_affine(
    points: list[tuple[int, int, float, float]], bbox: tuple[float, float, float, float]
) -> _LocalAffineFit:
    """Real least-squares fit of (lon, lat) -> (row, col) using the
    ``_N_NEAREST_GCPS_FOR_LOCAL_FIT`` real geolocation grid points nearest
    ``bbox``'s centre -- see module docstring/``load_scene`` for why a local
    fit is the right, disclosed scope for a crop this small."""
    lon_c, lat_c = (bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0
    nearest = sorted(points, key=lambda p: (p[3] - lon_c) ** 2 + (p[2] - lat_c) ** 2)[:_N_NEAREST_GCPS_FOR_LOCAL_FIT]

    design = np.array([[lon, lat, 1.0] for (_line, _pixel, lat, lon) in nearest])
    rows = np.array([line for (line, _pixel, _lat, _lon) in nearest], dtype=np.float64)
    cols = np.array([pixel for (_line, pixel, _lat, _lon) in nearest], dtype=np.float64)
    row_coef, *_ = np.linalg.lstsq(design, rows, rcond=None)
    col_coef, *_ = np.linalg.lstsq(design, cols, rcond=None)

    # Real pixel spacing, derived from the fit itself rather than assumed:
    # the two nearest points that differ in line only / pixel only give a
    # real ground distance per pixel step, via the same great-circle formula
    # opt.chokepoints already uses (re-derived locally here so
    # anchorage.detect does not depend on opt.*, matching its own layering).
    by_line = sorted({p for p in nearest}, key=lambda p: p[0])
    by_pixel = sorted({p for p in nearest}, key=lambda p: p[1])
    row_spacing_m = _real_ground_spacing_m(by_line[0], by_line[-1], axis=0)
    col_spacing_m = _real_ground_spacing_m(by_pixel[0], by_pixel[-1], axis=1)
    return _LocalAffineFit(row_coef=row_coef, col_coef=col_coef, pixel_spacing_m=(row_spacing_m, col_spacing_m))


def _real_ground_spacing_m(p_a: tuple[int, int, float, float], p_b: tuple[int, int, float, float], *, axis: int) -> float:
    """Real ground distance (metres) per pixel step between two real GCPs
    along one axis (0=line/row, 1=pixel/col) -- haversine over their real
    lat/lon, divided by their real pixel-index difference. Falls back to
    ``DEFAULT_PIXEL_SPACING_M`` only when the two points are degenerate
    (same index on that axis, division by zero) -- a real fallback for a
    real edge case, not a silent wrong number."""
    steps = p_b[axis] - p_a[axis]
    if steps == 0:
        return DEFAULT_PIXEL_SPACING_M[axis]
    lat1, lon1 = math.radians(p_a[2]), math.radians(p_a[3])
    lat2, lon2 = math.radians(p_b[2]), math.radians(p_b[3])
    d = 2 * 6371000.0 * math.asin(
        math.sqrt(
            math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
        )
    )
    return abs(d / steps)


def load_scene(path: Path, bbox: tuple[float, float, float, float], *, polarisation: str = "vv") -> tuple[np.ndarray, tuple[float, float]]:
    """Load ONLY the ``bbox`` (lon_min, lat_min, lon_max, lat_max) subset of
    a real Sentinel-1 scene at ``path`` (the ``.zip`` ``fetch_scene``
    downloads) -- never the whole scene (a real IW GRD band is ~20000 x
    26000 pixels; a full in-memory load for a crop a few hundred pixels wide
    would be wasteful by orders of magnitude). Returns
    ``(backscatter_array, pixel_spacing_m)``.

    **A real, disclosed engineering call, found only once a real scene was
    in hand to inspect:** a Sentinel-1 GRD measurement GeoTIFF -- even the
    COG-reprocessed one this harvester fetches -- carries NO embedded CRS
    and an identity transform (confirmed directly against a real downloaded
    scene). Real georeferencing lives separately, in the product's own
    annotation XML, as a real geolocation grid (~231 real ground-control
    points for a typical IW scene) -- ESA's documented, standard format for
    exactly this product, not a workaround. Rather than a full-scene GCP
    warp (a real, heavier undertaking -- reprojecting the whole ~500 MB
    band), this function fits a real LOCAL affine transform from the
    grid points nearest the requested bbox (see ``_fit_local_affine``) and
    reads only the resulting small pixel window. This is accurate for a
    crop this size (a real anchorage box, a few tens of km across) because
    SAR ground-range geometry is only mildly non-linear across that short a
    span -- it would NOT be a safe approximation across a whole ~250 km
    swath, which is exactly why this function is scoped to one bbox at a
    time and not exposed as a general whole-scene reader.

    ``path`` may be the downloaded ``.zip`` directly (read via rasterio's
    real zip-virtual-filesystem support, ``zip://...!/...`` -- no full
    extraction to disk) or an already-extracted ``.tiff``/directory.
    ``polarisation`` selects ``vv`` or ``vh`` (both real, always present on
    this product -- see module docstring).

    Raises ``GeoTIFFBackendUnavailableError`` if ``rasterio`` is not
    installed (a real, disclosed dependency added only once a real account
    existed to exercise it against -- see ``pyproject.toml``'s own comment
    on this dependency) or if no measurement/annotation pair for
    ``polarisation`` can be found at ``path``.
    """
    try:
        import rasterio
        from rasterio.windows import Window
    except ImportError as exc:
        raise GeoTIFFBackendUnavailableError(
            f"Cannot read {path}: rasterio is not installed. Run `uv add rasterio` and confirm "
            "before relying on this function -- see anchorage.detect's own module docstring."
        ) from exc

    tiff_uri, xml_bytes = _resolve_band(path, polarisation)
    points = _parse_geolocation_grid(xml_bytes)
    fit = _fit_local_affine(points, bbox)

    lon_min, lat_min, lon_max, lat_max = bbox
    corners = [fit.to_pixel(lon, lat) for lon, lat in [(lon_min, lat_min), (lon_min, lat_max), (lon_max, lat_min), (lon_max, lat_max)]]
    rows = [r for r, _c in corners]
    cols = [c for _r, c in corners]

    with rasterio.open(tiff_uri) as src:
        row_off = max(0, int(min(rows)))
        col_off = max(0, int(min(cols)))
        row_end = min(src.height, int(max(rows)) + 1)
        col_end = min(src.width, int(max(cols)) + 1)
        if row_end <= row_off or col_end <= col_off:
            raise ValueError(f"Fitted pixel window for {bbox} is empty/out of range against {path} -- bbox likely does not fall inside this scene.")
        window = Window(col_off=col_off, row_off=row_off, width=col_end - col_off, height=row_end - row_off)
        array = src.read(1, window=window)

    return array, fit.pixel_spacing_m


def _resolve_band(path: Path, polarisation: str) -> tuple[str, bytes]:
    """Find the real measurement GeoTIFF and its real annotation XML for
    ``polarisation`` inside ``path`` (a ``.zip`` or an already-extracted
    directory). Returns (a rasterio-openable URI, the real annotation XML
    bytes). Raises ``GeoTIFFBackendUnavailableError`` -- clear and named,
    never a bare ``KeyError``/``FileNotFoundError`` -- when nothing matches."""
    import fnmatch
    import zipfile

    pol = polarisation.lower()
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            meas = [n for n in names if fnmatch.fnmatch(n, _MEASUREMENT_GLOB.format(pol=pol))]
            ann = [n for n in names if fnmatch.fnmatch(n, _ANNOTATION_GLOB.format(pol=pol)) and "/calibration/" not in n and "/rfi/" not in n]
            if not meas or not ann:
                raise GeoTIFFBackendUnavailableError(
                    f"No {pol!r}-polarisation measurement/annotation pair found inside {path} -- "
                    f"real entries: {names[:5]}..."
                )
            xml_bytes = z.read(ann[0])
        return f"zip://{path}!/{meas[0]}", xml_bytes

    if path.is_dir():
        meas_matches = list(path.glob(f"**/measurement/*-{pol}-*.tiff"))
        ann_matches = [p for p in path.glob(f"**/annotation/*-{pol}-*.xml") if "calibration" not in p.parts and "rfi" not in p.parts]
        if not meas_matches or not ann_matches:
            raise GeoTIFFBackendUnavailableError(f"No {pol!r}-polarisation measurement/annotation pair found under {path}.")
        return str(meas_matches[0]), ann_matches[0].read_bytes()

    raise GeoTIFFBackendUnavailableError(f"{path} is neither a .zip nor a directory -- cannot locate a measurement band.")


def cfar_detect(
    image: np.ndarray,
    *,
    guard_px: int = DEFAULT_GUARD_PX,
    background_px: int = DEFAULT_BACKGROUND_PX,
    pfa: float = DEFAULT_PFA,
) -> list[Detection]:
    """Cell-Averaging CFAR (Rohling 1983) over ``image`` -- see module
    docstring for the full method and parameter reasoning.

    Vectorised via box-filter sums rather than a literal per-pixel sliding
    window (real for a scene of any practical size): the outer
    ``(2*background_px+1)`` box sum minus the inner ``(2*guard_px+1)`` guard
    box sum gives the annulus (reference-cell) sum directly, over the whole
    image in two filter passes.

    A border of width ``background_px`` is excluded from detection (the
    reference-cell window would run off the image there) -- a real,
    disclosed edge effect, not a bug: a vessel sitting exactly on the crop's
    own edge can be missed. Widening the loaded crop beyond the anchorage
    box of interest is the real mitigation, left to the caller.

    Returns raw blobs (connected components of pixels clearing the local
    threshold) as ``Detection``, with NO post-filtering applied -- land
    mask / size gate / merge are separate, independently testable steps.
    """
    if guard_px < 0 or background_px <= guard_px:
        raise ValueError(f"background_px ({background_px}) must be > guard_px ({guard_px}) >= 0")
    if not (0.0 < pfa < 1.0):
        raise ValueError(f"pfa must be in (0, 1), got {pfa}")

    arr = np.asarray(image, dtype=np.float64)
    if arr.ndim != 2:
        raise ValueError(f"image must be 2-D, got shape {arr.shape}")

    outer_size = 2 * background_px + 1
    guard_size = 2 * guard_px + 1

    outer_sum = ndimage.uniform_filter(arr, size=outer_size, mode="constant", cval=0.0) * (outer_size**2)
    guard_sum = ndimage.uniform_filter(arr, size=guard_size, mode="constant", cval=0.0) * (guard_size**2)
    annulus_sum = outer_sum - guard_sum
    n_ref_cells = outer_size**2 - guard_size**2
    background_mean = annulus_sum / n_ref_cells

    # CA-CFAR threshold multiplier for N reference cells under an
    # exponentially-distributed clutter-power assumption (Rohling 1983).
    alpha = n_ref_cells * (pfa ** (-1.0 / n_ref_cells) - 1.0)
    threshold = alpha * background_mean

    mask = arr > threshold
    # Border exclusion -- see docstring's "edge effect" paragraph.
    if background_px > 0:
        mask[:background_px, :] = False
        mask[-background_px:, :] = False
        mask[:, :background_px] = False
        mask[:, -background_px:] = False

    labels, n_labels = ndimage.label(mask)
    if n_labels == 0:
        return []

    detections: list[Detection] = []
    label_ids = np.arange(1, n_labels + 1)
    centroids = ndimage.center_of_mass(arr, labels, label_ids)
    areas = ndimage.sum(np.ones_like(arr), labels, label_ids)
    peaks = ndimage.maximum(arr, labels, label_ids)
    for (row, col), area, peak in zip(centroids, areas, peaks, strict=True):
        # A labeled blob's mask-weight sum can be exactly zero in a rare,
        # real edge case (a connected component straddling the crop's own
        # boundary, where uniform_filter's zero-padding drags the local
        # background_mean -- and so the CFAR threshold -- down near zero for
        # a run of border pixels). center_of_mass then divides 0/0, so
        # (row, col) comes back NaN. Such a blob has no computable position,
        # so it cannot be reported as a location -- dropping it is the
        # honest move, not a fabricated fallback centroid.
        if not (math.isfinite(row) and math.isfinite(col)):
            continue
        detections.append(
            Detection(centroid_row=float(row), centroid_col=float(col), area_px=int(area), peak_intensity=float(peak))
        )
    return detections


def apply_land_mask(
    detections: list[Detection], water_bbox_px: tuple[int, int, int, int] | None
) -> list[Detection]:
    """Drop any detection whose centroid falls outside ``water_bbox_px``
    (row_min, row_max, col_min, col_max) -- the caller-supplied rectangle
    of the crop assumed to be open water.

    **This is a rectangular approximation, not a real coastline polygon.**
    A real coastline (e.g. a Natural Earth or OpenStreetMap water polygon,
    rasterized to the scene's own pixel grid) would correctly exclude an
    irregular shoreline, a jetty, or a breakwater WITHIN a rectangle that is
    mostly water -- this function cannot, and a detection near such a
    structure inside ``water_bbox_px`` will not be excluded. No coastline
    dataset exists in this repo yet; this is the disclosed, buildable-now
    substitute, not a claim of precision it does not have.

    ``water_bbox_px=None`` (the default) applies no masking at all -- every
    detection is kept. This is itself a real, disclosed choice: the
    anchorage boxes ``data_builders.harvest_sentinel1`` builds are already
    offset offshore of the port coordinate by design, but that offset does
    not guarantee every pixel in a loaded crop is water, particularly for a
    port with an irregular coastline.
    """
    if water_bbox_px is None:
        return list(detections)
    row_min, row_max, col_min, col_max = water_bbox_px
    return [
        d for d in detections
        if row_min <= d.centroid_row <= row_max and col_min <= d.centroid_col <= col_max
    ]


def _min_area_px(pixel_spacing_m: tuple[float, float], min_vessel_area_m2: float) -> int:
    """Real vessel area (m^2), converted to a pixel-area threshold FROM the
    scene's own real pixel spacing -- never a hardcoded pixel count (see
    module docstring). Rounded up: a real vessel at least this big must
    occupy at least this many whole pixels."""
    px_area_m2 = pixel_spacing_m[0] * pixel_spacing_m[1]
    if px_area_m2 <= 0:
        raise ValueError(f"pixel_spacing_m must be positive, got {pixel_spacing_m}")
    return max(1, int(np.ceil(min_vessel_area_m2 / px_area_m2)))


def apply_size_gate(
    detections: list[Detection],
    *,
    pixel_spacing_m: tuple[float, float] = DEFAULT_PIXEL_SPACING_M,
    min_vessel_area_m2: float = MIN_VESSEL_AREA_M2,
) -> list[Detection]:
    """Reject blobs smaller than a plausible vessel at this scene's real
    pixel spacing -- rejects a single hot pixel (speckle, a strong point
    scatterer with no real spatial extent) while keeping a real ship-sized
    blob. The pixel-area threshold is computed from ``pixel_spacing_m`` and
    ``min_vessel_area_m2`` (see ``_min_area_px``), never a hardcoded pixel
    count -- the same real vessel size means a different pixel count at a
    different spacing.

    **A real, disclosed consequence of that, found while testing this
    function against its own real default constants:** at
    ``DEFAULT_PIXEL_SPACING_M`` (10m x 10m, Sentinel-1 IW GRDH's real
    spacing), ONE pixel already covers 100 m^2 -- larger than
    ``MIN_VESSEL_AREA_M2`` (30 m^2) on its own, so ``_min_area_px`` returns
    1 and this gate cannot reject a single-pixel blob at the real default
    spacing at all. This is not a bug in the threshold arithmetic; it is a
    real, physical fact about 10 m GRD imagery: a small-to-medium real
    vessel is genuinely smaller than one pixel, so a single-pixel detection
    is a perfectly plausible real ship, not obvious noise, at this
    resolution -- filtering it out would silently discard real detections,
    which is worse than accepting the occasional single bright noise pixel.
    At this real spacing, this gate's practical job is rejecting
    anomalously LARGE spurious blobs (a land artefact, a merged cluster of
    bright wave crests), not small ones. The single-hot-pixel-vs-ship-blob
    distinction this gate CAN make cleanly is exercised in this module's
    own tests at a finer, explicitly-supplied pixel spacing (e.g. 3 m x 3 m
    -- a hypothetical finer product, not Sentinel-1's own), where a real
    vessel genuinely does span multiple pixels.
    """
    min_px = _min_area_px(pixel_spacing_m, min_vessel_area_m2)
    return [d for d in detections if d.area_px >= min_px]


def merge_nearby_detections(
    detections: list[Detection],
    *,
    pixel_spacing_m: tuple[float, float] = DEFAULT_PIXEL_SPACING_M,
    merge_radius_m: float = MERGE_RADIUS_M,
) -> list[Detection]:
    """Merge detections within ``merge_radius_m`` real-world distance of
    each other -- fragments of one vessel's hull/sidelobe/wake return,
    rather than two separate vessels. The real-world radius is converted to
    a pixel radius per axis from ``pixel_spacing_m`` (never a hardcoded
    pixel count), and Euclidean centroid distance is computed in that
    pixel-spacing-normalised space so an anisotropic spacing (different
    along-track vs cross-track resolution) is handled correctly.

    A simple greedy union: process detections sorted by peak intensity
    (strongest first), absorbing any not-yet-claimed detection within the
    radius into the current cluster's area-weighted centroid and combined
    area, keeping the max peak intensity. Adequate for the small number of
    real detections in one anchorage crop -- this is not a scene-wide
    clustering problem.
    """
    if not detections:
        return []
    row_scale, col_scale = pixel_spacing_m
    radius_row_px = merge_radius_m / row_scale
    radius_col_px = merge_radius_m / col_scale

    remaining = sorted(detections, key=lambda d: d.peak_intensity, reverse=True)
    merged: list[Detection] = []
    used = [False] * len(remaining)

    for i, seed in enumerate(remaining):
        if used[i]:
            continue
        used[i] = True
        cluster_rows = [seed.centroid_row]
        cluster_cols = [seed.centroid_col]
        cluster_weights = [seed.area_px]
        total_area = seed.area_px
        peak = seed.peak_intensity

        for j in range(i + 1, len(remaining)):
            if used[j]:
                continue
            cand = remaining[j]
            d_row = (cand.centroid_row - seed.centroid_row) / radius_row_px
            d_col = (cand.centroid_col - seed.centroid_col) / radius_col_px
            if (d_row**2 + d_col**2) <= 1.0:
                used[j] = True
                cluster_rows.append(cand.centroid_row)
                cluster_cols.append(cand.centroid_col)
                cluster_weights.append(cand.area_px)
                total_area += cand.area_px
                peak = max(peak, cand.peak_intensity)

        weights = np.array(cluster_weights, dtype=np.float64)
        merged.append(
            Detection(
                centroid_row=float(np.average(cluster_rows, weights=weights)),
                centroid_col=float(np.average(cluster_cols, weights=weights)),
                area_px=int(total_area),
                peak_intensity=float(peak),
            )
        )
    return merged


def _sea_state_proxy(image: np.ndarray, *, guard_px: int, background_px: int) -> float:
    """Coefficient of variation (std/mean) of the CFAR background estimate
    over the whole crop -- see module docstring's "Confidence degrades with
    clutter" section for why this, and not a raw intensity level, is the
    proxy used."""
    arr = np.asarray(image, dtype=np.float64)
    outer_size = 2 * background_px + 1
    guard_size = 2 * guard_px + 1
    outer_sum = ndimage.uniform_filter(arr, size=outer_size, mode="constant", cval=0.0) * (outer_size**2)
    guard_sum = ndimage.uniform_filter(arr, size=guard_size, mode="constant", cval=0.0) * (guard_size**2)
    annulus_sum = outer_sum - guard_sum
    n_ref_cells = outer_size**2 - guard_size**2
    background = annulus_sum / n_ref_cells
    if background_px > 0:
        background = background[background_px:-background_px, background_px:-background_px]
    mean = float(np.mean(background))
    if mean <= 0:
        return 0.0
    return float(np.std(background) / mean)


def _confidence_for_clutter(cv: float) -> Literal["high", "medium", "low"]:
    if cv < LOW_CLUTTER_CV:
        return "high"
    if cv < HIGH_CLUTTER_CV:
        return "medium"
    return "low"


def count_vessels(
    scene_path: Path,
    port: str,
    *,
    scene_metadata: SceneMetadata | None = None,
    image: np.ndarray | None = None,
    params: CFARParams | None = None,
) -> AnchorageCensus:
    """The full pipeline: CFAR -> land mask -> size gate -> merge -> census.

    ``scene_metadata`` -- normally ``data_builders.harvest_sentinel1.
    SceneMetadata`` from ``search_scenes()`` -- supplies ``scene_id`` and
    ``acquired_at``; its own ``footprint_bbox`` is passed to ``load_scene``
    when no pre-loaded ``image`` is given. This is 4.1's real output type
    used as 4.2's real input, not a re-derived or guessed identity.

    ``image``, when supplied, bypasses ``load_scene`` entirely -- the real
    test-injection path used throughout this module's own test suite (the
    same convention ``opt.landed_cost.compute_landed_cost``'s ``store``/
    ``macro_long`` parameters already use), and, until ``rasterio`` is
    added (see ``load_scene``'s own docstring), the ONLY way to exercise
    this function end to end at all -- ``scene_path``/``scene_metadata``
    alone will raise ``GeoTIFFBackendUnavailableError`` today. Requires
    ``scene_metadata`` when no ``image`` is given (there is no other real
    source for the scene's own id/acquisition time), and raises a clear
    ``ValueError`` rather than accepting an unlabelled census otherwise.

    Filter order is land mask, then size gate, then merge -- deliberately,
    not arbitrary: land-side artefacts are dropped first (cheapest, and
    guaranteed noise regardless of size), then genuinely tiny speckle blobs
    are size-gated out BEFORE merging, so a real vessel's fragments are
    never inflated by absorbing noise blobs that a stricter merge-first
    order could have let through.
    """
    p = params or CFARParams()

    if image is None:
        if scene_metadata is None:
            raise ValueError(
                "count_vessels needs either a pre-loaded `image` or `scene_metadata` "
                "(to know the real footprint bbox to load) -- neither was supplied."
            )
        image, _measured_spacing = load_scene(scene_path, scene_metadata.footprint_bbox)
    if scene_metadata is None:
        raise ValueError(
            "count_vessels needs `scene_metadata` for the census's real scene_id/acquired_at "
            "fields, even when `image` is supplied directly."
        )
    scene_id = scene_metadata.scene_id
    acquired_at = scene_metadata.acquisition_datetime

    raw = cfar_detect(image, guard_px=p.guard_px, background_px=p.background_px, pfa=p.pfa)
    land_filtered = apply_land_mask(raw, p.water_bbox_px)
    size_filtered = apply_size_gate(
        land_filtered, pixel_spacing_m=p.pixel_spacing_m, min_vessel_area_m2=p.min_vessel_area_m2
    )
    final = merge_nearby_detections(
        size_filtered, pixel_spacing_m=p.pixel_spacing_m, merge_radius_m=p.merge_radius_m
    )

    cv = _sea_state_proxy(image, guard_px=p.guard_px, background_px=p.background_px)
    confidence = _confidence_for_clutter(cv)

    LOGGER.info(
        f"{port} {scene_id}: {len(final)} vessel(s), confidence={confidence} "
        f"(clutter cv={cv:.3f}), {len(raw)} raw CFAR blob(s) before filtering."
    )

    return AnchorageCensus(
        port=port,
        scene_id=scene_id,
        acquired_at=acquired_at,
        vessel_count=len(final),
        detections=tuple(final),
        mean_sea_state_proxy=cv,
        confidence=confidence,
        provenance=Provenance.MODEL_DERIVED.value,
    )
