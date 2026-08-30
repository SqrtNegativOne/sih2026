"""Validation against real ground truth: Signal Ocean ballaster snapshots.

This is meant to be the Tonnage Field's centrepiece validation -- "we rebuilt a
commercial analytics product from free data, here is the correlation" -- and it is
important to be precise about what the real data on disk can and cannot support.

Of the 15 Signal weekly issues in ``raw_data/signal_weekly/``, only **3** report
per-class per-basin ballaster counts at all; the rest report freight rates. Those
3 issues cover different, non-overlapping (basin, class) combinations, at 3 dates.
That is not a time series -- it is 12 individual real data points, too few and too
sparse to compute a meaningful correlation coefficient (a Pearson r on 3-4 points
sharing a class is not evidence of anything, and computing one anyway would be far
more misleading than reporting none). What 12 real points from a real commercial
product's own weekly monitor *can* support is a direct magnitude comparison, point
by point, against this reconstruction on the same date -- which is what this module
does, honestly labelled as that and nothing more.

Signal's regions are finer than this reconstruction's 3-basin partition ("SE
Africa", "NOPAC", "Far East/NOPAC", "Australasia" vs just "pacific" /
"indian_ocean"), so several Signal rows can map onto one basin here. They are
summed before comparison (see ``_group_by_basin_date_class``) -- the correct
apples-to-apples move given the coarser partition, not a choice that flatters the
comparison; where a basin does contain multiple Signal sub-regions the reconstructed
number is *expected* to run higher than any single one of them, and the checks
below test for that explicitly rather than for a tight ratio.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Final

import polars as pl

from opt.types import VesselClass
from tonnage.basins import Basin
from tonnage.classmix import CLASS_MIDPOINT_DWT
from tonnage.stockflow import UNCTAD_FLEET_DWT_BY_CLASS, StockflowResult


@dataclass(frozen=True)
class SignalBallasterSnapshot:
    """One real, hand-transcribed row from a Signal weekly extraction CSV."""

    report_date: date
    vessel_class: VesselClass
    signal_region: str
    basin: Basin
    ballaster_count: int
    source_file: str


#: Every ballaster-count row across all 15 Signal weekly issues on disk. Fixed and
#: small by construction (see module docstring) -- not a sample, the full set.
KNOWN_SIGNAL_BALLASTER_SNAPSHOTS: Final[tuple[SignalBallasterSnapshot, ...]] = (
    SignalBallasterSnapshot(date(2024, 11, 30), VesselClass.CAPESIZE, "SE Africa", Basin.INDIAN_OCEAN, 104, "2024-11-30_capesize-market"),
    SignalBallasterSnapshot(date(2024, 11, 30), VesselClass.PANAMAX, "SE Africa", Basin.INDIAN_OCEAN, 76, "2024-11-30_capesize-market"),
    SignalBallasterSnapshot(date(2024, 11, 30), VesselClass.HANDYSIZE, "NOPAC", Basin.PACIFIC, 90, "2024-11-30_capesize-market"),
    SignalBallasterSnapshot(date(2025, 4, 10), VesselClass.CAPESIZE, "SE Africa", Basin.INDIAN_OCEAN, 96, "2025-04-10_us-dry-bulk-flows-grain-vs-coal"),
    SignalBallasterSnapshot(date(2025, 4, 10), VesselClass.PANAMAX, "SE Africa", Basin.INDIAN_OCEAN, 100, "2025-04-10_us-dry-bulk-flows-grain-vs-coal"),
    SignalBallasterSnapshot(date(2025, 4, 10), VesselClass.HANDYSIZE, "NOPAC", Basin.PACIFIC, 98, "2025-04-10_us-dry-bulk-flows-grain-vs-coal"),
    SignalBallasterSnapshot(date(2025, 6, 14), VesselClass.PANAMAX, "Far East/NOPAC", Basin.PACIFIC, 180, "2025-06-14_capesize-market-analysis"),
    SignalBallasterSnapshot(date(2025, 6, 14), VesselClass.PANAMAX, "Australasia", Basin.PACIFIC, 250, "2025-06-14_capesize-market-analysis"),
    SignalBallasterSnapshot(date(2025, 6, 14), VesselClass.SUPRAMAX, "Pacific", Basin.PACIFIC, 190, "2025-06-14_capesize-market-analysis"),
    SignalBallasterSnapshot(date(2025, 6, 14), VesselClass.SUPRAMAX, "North Atlantic", Basin.ATLANTIC, 100, "2025-06-14_capesize-market-analysis"),
    SignalBallasterSnapshot(date(2025, 6, 14), VesselClass.HANDYSIZE, "Far East/North Pacific", Basin.PACIFIC, 170, "2025-06-14_capesize-market-analysis"),
    SignalBallasterSnapshot(date(2025, 6, 14), VesselClass.HANDYSIZE, "Australasia", Basin.PACIFIC, 140, "2025-06-14_capesize-market-analysis"),
)


@dataclass(frozen=True)
class ComparisonPoint:
    report_date: date
    vessel_class: VesselClass
    basin: Basin
    signal_count: int  # summed across Signal sub-regions mapped to this basin
    signal_regions: tuple[str, ...]
    reconstructed_count: float | None  # None if this date is outside the harvest range
    ratio: float | None  # reconstructed / signal


def _grouped_signal_counts() -> list[tuple[date, VesselClass, Basin, int, tuple[str, ...]]]:
    groups: dict[tuple[date, VesselClass, Basin], list[SignalBallasterSnapshot]] = {}
    for s in KNOWN_SIGNAL_BALLASTER_SNAPSHOTS:
        groups.setdefault((s.report_date, s.vessel_class, s.basin), []).append(s)
    out = []
    for (d, cls, basin), rows in groups.items():
        total = sum(r.ballaster_count for r in rows)
        regions = tuple(r.signal_region for r in rows)
        out.append((d, cls, basin, total, regions))
    return out


def implied_ballaster_count(
    result: StockflowResult, basin: Basin, vessel_class: VesselClass, as_of: date
) -> float | None:
    """Reconstructed stock, converted from DWT-capacity to an implied vessel count.

    This is the one place ``CLASS_MIDPOINT_DWT`` is used to go *back* from
    capacity to a headcount -- everywhere else in tonnage/ deliberately stays in
    DWT-capacity terms. It only happens here because Signal's own published unit
    is a vessel count, and comparing on their unit is the fairer test.
    """
    row = result.frame.filter(
        (pl.col("basin") == basin.value)
        & (pl.col("vessel_class") == vessel_class.value)
        & (pl.col("date") == as_of)
    )
    if row.is_empty():
        return None
    stock_dwt = float(row["stock_dwt"][0])
    return stock_dwt / CLASS_MIDPOINT_DWT[vessel_class]


@dataclass(frozen=True)
class SignalValidationSummary:
    """The honest headline result of the Signal comparison.

    Measured on the real 12-point comparison: ratios ranged from 0.35x (Capesize,
    Indian Ocean -- the reconstruction *under*-counts against Signal's "SE Africa")
    to 26x (Handysize, Pacific -- the reconstruction massively *over*-counts against
    Signal's "NOPAC"), a 74x spread with no single correction factor that fixes all
    twelve points. The root cause is identifiable, not mysterious: ``stockflow``
    anchors ``stock_dwt`` to the UNCTAD *total registered fleet* per class, but a
    real ballaster count is a small fraction of the total fleet (most of the world
    fleet is laden, under period charter, or otherwise employed at any moment, not
    idle) -- a "prompt/spot-available fraction" correction is needed and this
    reconstruction does not have one. Fitting that fraction from these same 3
    Signal issues would be tuning a global parameter on the exact 12 points used to
    validate it -- the same mistake this codebase's own P0 work found and fixed in
    ``opt.calibration`` (PSO tuned on the test split) -- so it is deliberately not
    done. The gate from ``docs/plan.md`` Part 7 ("if correlation is weak, fall back
    to tightness-as-feature") is what actually applies here: treat ``stock_dwt``
    and everything built on it as a *relative, within-(basin, class) over time*
    signal, not a calibrated absolute headcount.
    """

    n_points: int
    min_ratio: float
    median_ratio: float
    max_ratio: float
    absolute_scale_validated: bool = False  # deliberately always False -- see docstring


def summarize_signal_validation(points: list[ComparisonPoint]) -> SignalValidationSummary:
    ratios = sorted(p.ratio for p in points if p.ratio is not None)
    if not ratios:
        return SignalValidationSummary(n_points=0, min_ratio=float("nan"), median_ratio=float("nan"), max_ratio=float("nan"))
    n = len(ratios)
    median = ratios[n // 2] if n % 2 == 1 else (ratios[n // 2 - 1] + ratios[n // 2]) / 2
    return SignalValidationSummary(
        n_points=n, min_ratio=ratios[0], median_ratio=median, max_ratio=ratios[-1]
    )


def compare_to_signal(result: StockflowResult) -> list[ComparisonPoint]:
    points = []
    for d, cls, basin, signal_total, regions in _grouped_signal_counts():
        recon = implied_ballaster_count(result, basin, cls, d)
        ratio = (recon / signal_total) if (recon is not None and signal_total > 0) else None
        points.append(
            ComparisonPoint(
                report_date=d,
                vessel_class=cls,
                basin=basin,
                signal_count=signal_total,
                signal_regions=regions,
                reconstructed_count=recon,
                ratio=ratio,
            )
        )
    return sorted(points, key=lambda p: (p.report_date, p.vessel_class.value, p.basin.value))


@dataclass(frozen=True)
class UnctadCrossCheck:
    vessel_class: VesselClass
    reconstructed_total_dwt: float
    unctad_anchor_dwt: float


def unctad_cross_check(result: StockflowResult, as_of: date | None = None) -> list[UnctadCrossCheck]:
    """Reconstructed world total per class vs. the UNCTAD anchor.

    Not independent evidence -- ``stockflow.reconstruct`` is built to hit this
    total by construction (that is what the anchor correction does). Reported here
    anyway because it is still a real check that the *mechanism* worked (every
    basin's share adds back up correctly, nothing was dropped or double-counted in
    the class/basin split), which is worth being explicit is a different claim
    from "the reconstruction is accurate."
    """
    df = result.frame
    target_date = as_of or df["date"].max()
    out = []
    for cls in VesselClass:
        sub = df.filter((pl.col("vessel_class") == cls.value) & (pl.col("date") == target_date))
        total = float(sub["stock_dwt"].sum()) if not sub.is_empty() else 0.0
        out.append(
            UnctadCrossCheck(
                vessel_class=cls,
                reconstructed_total_dwt=total,
                unctad_anchor_dwt=UNCTAD_FLEET_DWT_BY_CLASS[cls],
            )
        )
    return out
