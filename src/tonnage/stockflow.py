"""Basin x class free-tonnage stock-flow reconstruction.

The state we want -- how much capacity of each class is sitting free (ballasting or
waiting) in each basin on each day -- is never directly observed in free public data.
Signal Ocean sells exactly this number because they track live AIS positions and
classify "empty and heading toward a load region." PortWatch gives none of that: it
gives daily counts and tonnage of dry-bulk *calls*, i.e. flows in and out of ports,
never a stock.

What is real and derivable: every completed discharge eventually frees up capacity
in the basin it happened in, after some port turnaround time, and every completed
load removes capacity from the basin it happened in. That is a genuine flow-
conservation identity, computable per basin from each basin's own port activity --
it needs no assumption about *where* a freed ship chooses to ballast to next, because
it never has to know. What it cannot do on its own is anchor an absolute level: flow
imbalances only give a *relative* trajectory, and small per-day errors accumulate
without bound over a 7-year integration window. That is what the UNCTAD world-fleet
total is for -- not a per-day observation (there isn't one), but a periodic mass-
balance constraint the cumulative trajectory gets pinned to, distributed across
basins by each basin's own share of real activity. The docstring of
``docs/plan.md`` calls this a "constrained Kalman filter"; a textbook Kalman filter
needs a real per-step observation of the state to correct against, and none exists
here, so this module implements the honest version of that idea -- an anchored
flow-conservation integrator -- rather than dressing up a plain accumulator with
Kalman-filter vocabulary it doesn't earn. See ``docs/plan.md`` Part 2 and Part 7
(risk: reconstruction correlates poorly with Signal -- mitigation is to keep the
tightness index as a feature even if the structural forecast doesn't hold up).

**Read this before trusting an absolute number out of here.** ``stock_dwt`` is
anchored to each class's *total registered world fleet* (``UNCTAD_FLEET_DWT_BY_CLASS``),
not to the free/ballasting fraction of it -- most of the real world fleet is laden
or under period charter at any moment, not idle. Comparison against real Signal
Ocean ballaster counts (``tonnage.validate``) found the implied headcount running
0.35x to 26x the real number depending on class and basin, with no single
correction factor that fixes it -- see ``tonnage.validate.SignalValidationSummary``
for the full finding. Use ``stock_dwt`` as a *relative, within-(basin, class) over
time* signal (which is what ``tonnage.supplycurve``'s tightness ratio actually
consumes it as); do not report it as a calibrated absolute ballaster count.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import numpy as np
import polars as pl

from opt.types import VesselClass
from tonnage.basins import Basin, load_port_index, port_csv_path
from tonnage.classmix import (
    CLASS_MIDPOINT_DWT,
    NoActivityError,
    class_weights,
    mean_parcel_size,
)

#: Representative port turnaround (load OR discharge) per class, in whole days.
#: Larger ships take longer to work cargo -- a standard operational assumption, not
#: fitted, since PortWatch carries no per-call duration to fit it against.
CLASS_TURNAROUND_DAYS: Final[dict[VesselClass, int]] = {
    VesselClass.HANDYSIZE: 2,
    VesselClass.SUPRAMAX: 2,
    VesselClass.PANAMAX: 3,
    VesselClass.CAPESIZE: 4,
}

#: How many trailing days of net flow feed the stock estimate, i.e. the assumed
#: typical time a freed ship stays "available" before it fixes its next cargo and
#: drops out of the free pool. This is not a smoothing knob picked for nice-looking
#: output -- it exists because the 128-port harvest is deliberately export-heavy
#: (curated for major *load* ports plus a smaller set of import hubs, not a closed
#: system), so real total export tonnage runs 43% above real total import tonnage
#: across the whole pull. An *unbounded* cumulative net-flow integral drifts by
#: billions of tonnes over the 7-year window as a result -- confirmed empirically,
#: not assumed -- which a periodic anchor correction cannot recover from once the
#: raw trajectory has run that far from anything physical. Bounding the window to a
#: realistic re-fixing horizon keeps drift bounded to at most a few weeks of the
#: same real leakage, which the UNCTAD anchor correction can then absorb sensibly.
RESIDENCE_WINDOW_DAYS: Final[int] = 21

#: World dry-bulk fleet DWT by class -- the mass-balance anchor. Not one single
#: published table; derived from two real, cited public figures two years apart:
#:   - UNCTAD Review of Maritime Transport: total world dry-bulk fleet 974,000,000
#:     dwt as of 1 Jan 2023 (unctad.org/system/files/official-document/rmt2024ch2_en.pdf).
#:   - Vessel counts by segment as of Sept 2025 (Clarksons-sourced, via Breakwave
#:     Advisors: breakwaveadvisors.com/insights/2025/11/5/dry-bulk-fleet-composition-
#:     in-the-first-nine-months-of-2025) -- Capesize/VLOC 1,916; Panamax/Kamsarmax
#:     3,472 (Post-Panamax's 1,117 folded in here -- closer in size to Panamax than
#:     to any other class); Supramax/Ultramax 4,312; Handysize 3,202.
#: Multiplying counts through CLASS_MIDPOINT_DWT gives ~1,074M dwt total, about 10%
#: above the 2023 anchor -- consistent with ~2 years of the fleet's real ~2-3%/yr
#: growth, not a contradiction between the two sources. Held constant across the
#: whole 2019-2026 harvest window rather than interpolated: a deliberate
#: simplification (documented here, not hidden) smaller in effect than several
#: others already in this pipeline (the classmix kernel bandwidth, the turnaround-
#: day assumptions above).
UNCTAD_FLEET_DWT_BY_CLASS: Final[dict[VesselClass, float]] = {
    VesselClass.CAPESIZE: 1_916 * CLASS_MIDPOINT_DWT[VesselClass.CAPESIZE],
    VesselClass.PANAMAX: (3_472 + 1_117) * CLASS_MIDPOINT_DWT[VesselClass.PANAMAX],
    VesselClass.SUPRAMAX: 4_312 * CLASS_MIDPOINT_DWT[VesselClass.SUPRAMAX],
    VesselClass.HANDYSIZE: 3_202 * CLASS_MIDPOINT_DWT[VesselClass.HANDYSIZE],
}


@dataclass(frozen=True)
class StockflowResult:
    """Reconstructed daily free-tonnage stock, long format.

    ``frame`` columns: date, basin, vessel_class, stock_dwt, raw_stock_dwt (pre-
    anchor trajectory, for diagnostics), was_clipped (non-negativity bound bit).
    """

    frame: pl.DataFrame
    n_ports_used: int
    n_ports_skipped: int
    clipped_fraction: float


def _class_attributed_flows() -> pl.DataFrame:
    """Per (date, basin, vessel_class): class-attributed inflow/outflow tonnage.

    One row per port-day-class. Each port's class weights are computed once from
    its *own* full-history mean parcel size (tonnage.classmix) and held static --
    re-estimating per day would only worsen the identifiability problem classmix
    already documents, and a port's structural size profile is not something that
    changes day to day.
    """
    frames: list[pl.DataFrame] = []
    for port in load_port_index():
        path = port_csv_path(port.label)
        if not path.exists():
            continue
        try:
            estimate = mean_parcel_size(port.label, path)
        except NoActivityError:
            continue
        weights = class_weights(estimate.mean_parcel_t)

        daily = pl.read_csv(
            path,
            columns=["date", "import_dry_bulk", "export_dry_bulk"],
            schema_overrides={"date": pl.Date},
        )
        for cls, w in weights.items():
            if w <= 0:
                continue
            frames.append(
                daily.select(
                    pl.col("date"),
                    pl.lit(port.basin.value).alias("basin"),
                    pl.lit(cls.value).alias("vessel_class"),
                    (pl.col("export_dry_bulk") * w).alias("outflow_t"),
                    (pl.col("import_dry_bulk") * w).alias("inflow_t"),
                )
            )
    if not frames:
        return pl.DataFrame(
            schema={
                "date": pl.Date, "basin": pl.Utf8, "vessel_class": pl.Utf8,
                "outflow_t": pl.Float64, "inflow_t": pl.Float64,
            }
        )
    combined = pl.concat(frames)
    return (
        combined.group_by(["date", "basin", "vessel_class"])
        .agg(pl.col("outflow_t").sum(), pl.col("inflow_t").sum())
        .sort(["vessel_class", "basin", "date"])
    )


def _dense_daily_series(flows: pl.DataFrame, basin: Basin, cls: VesselClass) -> pl.DataFrame:
    """One row per calendar day over the full observed range, zero-filled gaps."""
    sub = flows.filter((pl.col("basin") == basin.value) & (pl.col("vessel_class") == cls.value))
    if sub.is_empty():
        return sub
    full_range = pl.date_range(sub["date"].min(), sub["date"].max(), interval="1d", eager=True)
    calendar = pl.DataFrame({"date": full_range})
    return (
        calendar.join(sub, on="date", how="left")
        .with_columns(pl.col("outflow_t").fill_null(0.0), pl.col("inflow_t").fill_null(0.0))
        .sort("date")
    )


def reconstruct(flows: pl.DataFrame | None = None) -> StockflowResult:
    """Reconstruct daily free-tonnage stock for every (basin, class) pair.

    Flow imbalances give the shape of the trajectory; the UNCTAD class total pins
    its level at every day, distributed across basins by each basin's own share of
    total observed activity for that class (not an equal split -- a basin that
    handles more real dry-bulk traffic gets more of the anchor).
    """
    if flows is None:
        flows = _class_attributed_flows()
    n_ports_used = sum(1 for p in load_port_index() if port_csv_path(p.label).exists())
    n_ports_skipped = sum(1 for p in load_port_index() if not port_csv_path(p.label).exists())

    per_class_frames: list[pl.DataFrame] = []
    total_clipped = 0
    total_days = 0

    for cls in VesselClass:
        basin_series: dict[Basin, pl.DataFrame] = {}
        basin_activity_share: dict[Basin, float] = {}
        total_activity = 0.0
        for basin in Basin:
            series = _dense_daily_series(flows, basin, cls)
            basin_series[basin] = series
            activity = 0.0 if series.is_empty() else float((series["outflow_t"] + series["inflow_t"]).sum())
            basin_activity_share[basin] = activity
            total_activity += activity
        if total_activity <= 0:
            continue
        for basin in Basin:
            basin_activity_share[basin] /= total_activity

        # Raw (uncentred) trajectories, one per basin, aligned on a shared calendar
        # so the cross-basin anchor correction can be applied day-by-day.
        all_dates = sorted(
            {d for s in basin_series.values() if not s.is_empty() for d in s["date"].to_list()}
        )
        raw_by_basin: dict[Basin, np.ndarray] = {}
        for basin in Basin:
            series = basin_series[basin]
            if series.is_empty():
                raw_by_basin[basin] = np.zeros(len(all_dates))
                continue
            outflow = series["outflow_t"].to_numpy()
            inflow = series["inflow_t"].to_numpy()
            lag = CLASS_TURNAROUND_DAYS[cls]
            lagged_inflow = np.concatenate([np.zeros(lag), inflow])[: len(inflow)]
            net = lagged_inflow - outflow
            csum = np.cumsum(net)
            shifted = np.concatenate([np.zeros(RESIDENCE_WINDOW_DAYS), csum])[: len(csum)]
            raw_stock = csum - shifted  # bounded trailing RESIDENCE_WINDOW_DAYS sum
            # Reindex onto the shared calendar (missing days -- basin had no
            # activity for this class at all on those dates -- hold the last value).
            aligned = np.zeros(len(all_dates))
            last = 0.0
            local_map = dict(zip(series["date"].to_list(), raw_stock))
            for i, d in enumerate(all_dates):
                if d in local_map:
                    last = local_map[d]
                aligned[i] = last
            raw_by_basin[basin] = aligned

        total_raw = np.sum([raw_by_basin[b] for b in Basin], axis=0)
        anchor = UNCTAD_FLEET_DWT_BY_CLASS[cls]
        correction = anchor - total_raw  # same for every basin at a given t, split by share below

        for basin in Basin:
            corrected = raw_by_basin[basin] + correction * basin_activity_share[basin]
            clipped = corrected < 0
            total_clipped += int(clipped.sum())
            total_days += len(corrected)
            stock = np.clip(corrected, 0.0, None)
            per_class_frames.append(
                pl.DataFrame(
                    {
                        "date": all_dates,
                        "basin": [basin.value] * len(all_dates),
                        "vessel_class": [cls.value] * len(all_dates),
                        "stock_dwt": stock,
                        "raw_stock_dwt": raw_by_basin[basin],
                        "was_clipped": clipped,
                    }
                )
            )

    frame = pl.concat(per_class_frames).sort(["vessel_class", "basin", "date"]) if per_class_frames else pl.DataFrame()
    clipped_fraction = (total_clipped / total_days) if total_days else 0.0
    return StockflowResult(
        frame=frame,
        n_ports_used=n_ports_used,
        n_ports_skipped=n_ports_skipped,
        clipped_fraction=clipped_fraction,
    )
