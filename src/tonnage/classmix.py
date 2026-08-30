"""Vessel-class attribution from PortWatch dry-bulk port calls.

PortWatch gives per-port daily *counts* of dry-bulk calls and the *tonnage* imported
and exported -- never a vessel identity, size, or class. The only observable this
supports is the mean parcel size per call: ``(import_dry_bulk + export_dry_bulk) /
portcalls_dry_bulk``. That is one real number per port (or per port-day). It is
enough to place a port confidently on the DWT axis -- Port Hedland (iron ore,
Pilbara) comes out around 168,000 t/call from real data, squarely Capesize; Kwinana
(grain) comes out around 28,000 t/call, squarely Handysize -- but it is **not**
enough to *decompose* a genuinely mixed port into exact class shares. Recovering
four class fractions from one aggregate ratio is a one-equation-four-unknowns
problem: not identified, no matter how the equation is fit.

So this module does not claim a deconvolution it cannot support. It computes a
smooth, monotonic **soft class-weight** from the single real observable -- a
Gaussian kernel in log-DWT space centred on each class's representative size --
which correctly saturates near 100% for a port that is genuinely single-class
(verified against Port Hedland/Kwinana on real data in the test suite) and blends
proportionally for a port that sits between two classes (Newcastle, Rotterdam).
That blend should be read as "how much this port's traffic *resembles* each size
class," not as a recovered true mixture -- the difference matters and is the reason
this module is named ``classmix``, not ``deconvolve``.
"""
from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import polars as pl

from opt.types import VesselClass
from tonnage.basins import load_port_index, port_csv_path

#: Representative DWT per class. Matches the values already used for vessel
#: fixtures elsewhere in the repo (e.g. Supramax 55,000 in run_blackbox_scenario.py)
#: rather than introducing a second, slightly different convention.
CLASS_MIDPOINT_DWT: Final[dict[VesselClass, float]] = {
    VesselClass.HANDYSIZE: 32_000.0,
    VesselClass.SUPRAMAX: 55_000.0,
    VesselClass.PANAMAX: 76_000.0,
    VesselClass.CAPESIZE: 175_000.0,
}

#: Conventional DWT band edges, for reference/reporting only -- the weight function
#: itself works in continuous log-DWT space and does not hard-threshold on these.
CLASS_BAND_DWT: Final[dict[VesselClass, tuple[float, float]]] = {
    VesselClass.HANDYSIZE: (10_000.0, 39_999.0),
    VesselClass.SUPRAMAX: (40_000.0, 64_999.0),
    VesselClass.PANAMAX: (65_000.0, 99_999.0),
    VesselClass.CAPESIZE: (100_000.0, 220_000.0),
}

#: Kernel bandwidth in natural-log DWT units. Chosen as roughly half the mean
#: adjacent-class log-gap (gaps are 0.54 / 0.32 / 0.84 for Handy-Supra / Supra-
#: Panamax / Panamax-Cape) so neighbouring classes are distinguishable but a port
#: sitting between two class midpoints still gets meaningful weight on both.
DEFAULT_SIGMA_LOG: Final[float] = 0.35


class NoActivityError(ValueError):
    """Raised when a port has zero recorded dry-bulk calls -- there is nothing to weight."""


@dataclass(frozen=True)
class ParcelSizeEstimate:
    label: str
    mean_parcel_t: float
    stdev_daily_t: float  # spread across active days, a rough confidence signal
    n_calls: int
    n_active_days: int


def mean_parcel_size(label: str, path: Path | None = None) -> ParcelSizeEstimate:
    """Real mean dry-bulk parcel size at a port, from its actual PortWatch CSV.

    The point estimate is the aggregate ratio (total tonnage / total calls) over
    the whole pull, not a mean-of-daily-ratios -- the latter is undefined on
    zero-call days and noisy on low-call days, and would silently overweight them.
    """
    csv_path = path or port_csv_path(label)
    total_calls = 0
    total_tonnes = 0.0
    active_day_ratios: list[float] = []
    n_active_days = 0
    with csv_path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            calls = int(row["portcalls_dry_bulk"])
            if calls <= 0:
                continue
            tonnes = float(row["import_dry_bulk"]) + float(row["export_dry_bulk"])
            total_calls += calls
            total_tonnes += tonnes
            active_day_ratios.append(tonnes / calls)
            n_active_days += 1
    if total_calls == 0:
        raise NoActivityError(f"{label} has zero recorded dry-bulk calls in {csv_path}.")
    stdev = _stdev(active_day_ratios) if len(active_day_ratios) > 1 else 0.0
    return ParcelSizeEstimate(
        label=label,
        mean_parcel_t=total_tonnes / total_calls,
        stdev_daily_t=stdev,
        n_calls=total_calls,
        n_active_days=n_active_days,
    )


def _stdev(values: list[float]) -> float:
    n = len(values)
    mean = sum(values) / n
    return math.sqrt(sum((v - mean) ** 2 for v in values) / (n - 1))


def class_weights(
    mean_parcel_t: float, sigma_log: float = DEFAULT_SIGMA_LOG
) -> dict[VesselClass, float]:
    """Soft class-mix weights from a mean parcel size, via a log-DWT Gaussian kernel.

    Weights sum to 1. See the module docstring for what this is and is not claiming.
    """
    if mean_parcel_t <= 0:
        raise ValueError(f"mean_parcel_t must be positive, got {mean_parcel_t}")
    log_m = math.log(mean_parcel_t)
    raw = {
        cls: math.exp(-0.5 * ((log_m - math.log(mid)) / sigma_log) ** 2)
        for cls, mid in CLASS_MIDPOINT_DWT.items()
    }
    total = sum(raw.values())
    if total <= 0:
        # mean_parcel_t is astronomically far in log-space from every class midpoint
        # (essentially unreachable in practice) -- fall back to nearest class only
        # rather than dividing by zero.
        nearest = min(CLASS_MIDPOINT_DWT, key=lambda c: abs(log_m - math.log(CLASS_MIDPOINT_DWT[c])))
        return {cls: (1.0 if cls == nearest else 0.0) for cls in CLASS_MIDPOINT_DWT}
    return {cls: w / total for cls, w in raw.items()}


def port_class_weights(
    label: str, sigma_log: float = DEFAULT_SIGMA_LOG, path: Path | None = None
) -> dict[VesselClass, float]:
    """Convenience: real port CSV in, class-mix weights out."""
    estimate = mean_parcel_size(label, path)
    return class_weights(estimate.mean_parcel_t, sigma_log)


def build_fleet_class_mix(sigma_log: float = DEFAULT_SIGMA_LOG) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Class-mix weights for every port that has real dry-bulk activity on disk.

    Returns ``(mix, skipped)``. Ports resolved in the index but never actually
    pulled (index/harvest ran ahead of the CSV pull for 15 of the 129 P1
    candidates) or with zero dry-bulk calls land in ``skipped`` with a reason,
    rather than silently disappearing or being zero-filled into ``mix``.
    """
    rows: list[dict] = []
    skipped: list[dict] = []
    for port in load_port_index():
        csv_path = port_csv_path(port.label)
        if not csv_path.exists():
            skipped.append({"label": port.label, "reason": "no CSV pulled"})
            continue
        try:
            estimate = mean_parcel_size(port.label, csv_path)
        except NoActivityError:
            skipped.append({"label": port.label, "reason": "zero dry-bulk calls"})
            continue
        weights = class_weights(estimate.mean_parcel_t, sigma_log)
        rows.append(
            {
                "label": port.label,
                "portid": port.portid,
                "basin": port.basin.value,
                "role": port.role,
                "mean_parcel_t": estimate.mean_parcel_t,
                "stdev_daily_t": estimate.stdev_daily_t,
                "n_calls": estimate.n_calls,
                "n_active_days": estimate.n_active_days,
                **{f"weight_{cls.name}": w for cls, w in weights.items()},
            }
        )
    mix = pl.DataFrame(rows).sort("label") if rows else pl.DataFrame(rows)
    skipped_df = pl.DataFrame(skipped)
    return mix, skipped_df
