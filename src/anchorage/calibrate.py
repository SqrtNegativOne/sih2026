"""4.3: compare real satellite vessel counts against the real PortWatch
daily port-call series this system already uses -- evidence, not a
replacement.

**This chunk's product is a spot check, never a live signal.** Sentinel-1's
real revisit cadence over these five anchorages (confirmed live in 4.1:
roughly one real scene every 4-11 days depending on the port) is far too
sparse to drive a live quote -- ``opt.congestion``/``opt.risk`` already read
PortWatch's real DAILY call counts for that, and nothing here changes that.
What a real satellite count CAN do is spot-check whether PortWatch's own
call-count series -- a proxy for "how busy is this anchorage" -- moves the
same direction as an independent, physically different observation (radar
backscatter, not AIS). Agreement is reassuring; disagreement, or too little
data to say either way, is real information too, and this module is built
to report the second outcome exactly as plainly as the first.

Provenance, stated once here because it must be carried through to the API
and UI, not just this docstring: the raw Sentinel-1 backscatter a scene
contains is OBSERVED (a real physical measurement). The vessel COUNT a
scene implies is MODEL_DERIVED (``anchorage.detect.AnchorageCensus.
provenance`` already says so) -- a computation over a real observation,
never itself an observation. PortWatch's own ``portcalls_dry_bulk`` column
is separately OBSERVED (real AIS-derived counts; see
``data_builders.provenance``'s own module docstring for the full
per-PortWatch-field breakdown). Comparing a MODEL_DERIVED count against an
OBSERVED one is exactly what a calibration/spot-check is for -- it does not
make the satellite count itself OBSERVED, and this module never labels it
that way.

**The minimum-n gate.** ``MIN_N_FOR_CORRELATION`` is a real, documented
threshold below which this module refuses to compute or report a
correlation coefficient at all -- reporting ``r`` on a handful of points is
not a real finding, it is noise dressed up as one (at n=4, one degree of
freedom after n-3, a Pearson r's own standard error is enormous; a small
handful of points can produce a near-perfect-looking r purely by chance).
10 is a disclosed, round convention -- not a claim of statistical
rigor, but a real floor comfortably above the point where a correlation
coefficient stops meaning anything at all. Below it, this module reports
``n`` and the plain descriptive figures (date range, mean absolute
difference) and says explicitly that a correlation was not computed and
why -- never silently omits the finding.

**Never tuned to the answer.** This module reads whatever real detections
``anchorage.detect``/``anchorage.store`` actually produced and reports
whatever correlation (or lack of one) results. If the real number is weak,
or ``n`` is too small to say anything at all, that is this chunk's actual,
intended, publishable output -- not a reason to go back and adjust 4.2's
CFAR parameters until it looks better. Doing that would be fitting the
detector to a single comparison set, which is a worse foundation for a real
future calibration than an honestly weak or absent one.
"""
from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Final

from scipy import stats

from anchorage.detect import AnchorageCensus
from anchorage.store import CENSUS_LOG, read_censuses
from tonnage.basins import port_csv_path

LOGGER: Final[logging.Logger] = logging.getLogger(__name__)

__all__ = [
    "ANCHORAGE_PORT_TO_TONNAGE_LABEL",
    "CALIBRATION_CSV_PATH",
    "CALIBRATION_DOC_PATH",
    "MIN_N_FOR_CORRELATION",
    "PortCalibrationResult",
    "calibrate_all_ports",
    "calibrate_port",
    "write_calibration_csv",
    "write_calibration_doc",
]

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
CALIBRATION_CSV_PATH: Final[Path] = REPO_ROOT / "src" / "data" / "anchorage_calibration.csv"
CALIBRATION_DOC_PATH: Final[Path] = REPO_ROOT / "docs" / "14_anchorage_calibration.md"

#: See module docstring's "The minimum-n gate" section.
MIN_N_FOR_CORRELATION: Final[int] = 10

#: The same five ports 4.1's harvester covers, mapped to the real PortWatch
#: file label their daily port-call CSV is named after
#: (``raw_data/portwatch/<label>_daily_portcalls.csv``). Four of these are
#: the exact same real labels ``opt.network.PORT_TO_TONNAGE_LABEL`` already
#: uses for the ports that exist in that enum; HAY_POINT_AU is not an
#: ``opt.network.PortEnum`` member (outside the core 16-port network, see
#: ``data_builders.harvest_sentinel1``'s own docstring), so its label is
#: confirmed here directly against the real harvested file
#: (``raw_data/portwatch/Hay_Point_AU_daily_portcalls.csv``, which exists --
#: harvested by the extended PortWatch pull) rather than derived from that
#: enum.
ANCHORAGE_PORT_TO_TONNAGE_LABEL: Final[dict[str, str]] = {
    "PARADIP": "Paradip",
    "VISAKHAPATNAM": "Visakhapatnam",
    "NEWCASTLE_AU": "Newcastle_AU",
    "HAY_POINT_AU": "Hay_Point_AU",
    "RICHARDS_BAY_ZA": "Richards_Bay_ZA",
}


@dataclass(frozen=True)
class PortCalibrationResult:
    """One port's real calibration comparison. ``spearman_r``/``pearson_r``
    are ``None`` whenever ``n < MIN_N_FOR_CORRELATION`` -- see
    ``finding`` for the plain-English reason, always populated."""

    port: str
    n: int
    date_range: tuple[date, date] | None
    spearman_r: float | None
    pearson_r: float | None
    mean_abs_diff: float | None
    finding: str


def _real_portwatch_counts_by_date(label: str, *, portwatch_csv_path: Path | None = None) -> dict[date, float]:
    """Real ``portcalls_dry_bulk`` (OBSERVED, per
    ``data_builders.provenance``) keyed by real calendar date, for the
    PortWatch file this port's label names. Empty dict, not an error, if no
    real file exists for this label. ``portwatch_csv_path`` overrides the
    real on-disk path -- test-injection only, same convention
    ``opt.landed_cost.compute_landed_cost``'s ``store``/``macro_long``
    parameters already use."""
    path = portwatch_csv_path if portwatch_csv_path is not None else port_csv_path(label)
    if not path.exists():
        return {}
    out: dict[date, float] = {}
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            out[date.fromisoformat(row["date"])] = float(row["portcalls_dry_bulk"])
    return out


def calibrate_port(
    port: str, censuses: list[AnchorageCensus], *, portwatch_csv_path: Path | None = None
) -> PortCalibrationResult:
    """Join every real ``AnchorageCensus`` for ``port`` (by its own real
    ``acquired_at`` date) against that SAME real date's PortWatch call
    count, and report the real comparison. Never raises for missing data --
    a port with zero censuses, or whose PortWatch file has no matching real
    date, still gets a real result with ``n=0`` and an honest ``finding``.

    ``portwatch_csv_path`` overrides the real on-disk PortWatch file --
    test-injection only.
    """
    label = ANCHORAGE_PORT_TO_TONNAGE_LABEL.get(port)
    if label is None:
        return PortCalibrationResult(
            port=port, n=0, date_range=None, spearman_r=None, pearson_r=None, mean_abs_diff=None,
            finding=f"No real PortWatch tonnage label known for {port!r} -- cannot join.",
        )

    portwatch_by_date = _real_portwatch_counts_by_date(label, portwatch_csv_path=portwatch_csv_path)
    port_censuses = [c for c in censuses if c.port == port]

    paired: list[tuple[float, float]] = []  # (satellite_count, portwatch_count)
    paired_dates: list[date] = []
    for census in port_censuses:
        d = census.acquired_at.date()
        if d in portwatch_by_date:
            paired.append((float(census.vessel_count), portwatch_by_date[d]))
            paired_dates.append(d)

    n = len(paired)
    if n == 0:
        reason = (
            "no real PortWatch daily port-call file found for this label"
            if not portwatch_by_date
            else "zero real AnchorageCensus records exist for this port yet (no Sentinel-1 scene "
            "has been processed -- see anchorage.detect and 4.1's own PULL_NOTES.md for why)"
            if not port_censuses
            else "no census's real acquired_at date matched a real PortWatch date for this port"
        )
        return PortCalibrationResult(
            port=port, n=0, date_range=None, spearman_r=None, pearson_r=None, mean_abs_diff=None,
            finding=f"n=0: {reason}. No correlation or difference can be reported.",
        )

    date_range = (min(paired_dates), max(paired_dates))
    sat_counts = [p[0] for p in paired]
    pw_counts = [p[1] for p in paired]
    mean_abs_diff = sum(abs(s - p) for s, p in paired) / n

    if n < MIN_N_FOR_CORRELATION:
        return PortCalibrationResult(
            port=port, n=n, date_range=date_range, spearman_r=None, pearson_r=None,
            mean_abs_diff=mean_abs_diff,
            finding=(
                f"n={n} real paired observation(s) -- below MIN_N_FOR_CORRELATION "
                f"({MIN_N_FOR_CORRELATION}), so no correlation is reported (see this module's own "
                "docstring for why a correlation on this few points is not a real finding). Mean "
                f"absolute difference over these {n} real pair(s): {mean_abs_diff:.2f} vessels."
            ),
        )

    # scipy.stats returns nan for a degenerate (zero-variance) series rather
    # than raising -- treated as "not computable," not silently reported as
    # a real 0.0 or 1.0 correlation.
    spearman_res = stats.spearmanr(sat_counts, pw_counts)
    pearson_res = stats.pearsonr(sat_counts, pw_counts)
    spearman_r = float(spearman_res.correlation) if spearman_res.correlation == spearman_res.correlation else None
    pearson_r = float(pearson_res.statistic) if pearson_res.statistic == pearson_res.statistic else None

    return PortCalibrationResult(
        port=port, n=n, date_range=date_range, spearman_r=spearman_r, pearson_r=pearson_r,
        mean_abs_diff=mean_abs_diff,
        finding=(
            f"n={n} real paired observations, {date_range[0]} to {date_range[1]}. "
            f"Spearman r={spearman_r!r}, Pearson r={pearson_r!r}, mean absolute difference "
            f"{mean_abs_diff:.2f} vessels."
        ),
    )


def calibrate_all_ports(*, census_path: Path = CENSUS_LOG) -> list[PortCalibrationResult]:
    """The real comparison for every one of the five anchorage ports, from
    whatever real censuses actually exist in ``census_path`` today."""
    censuses = list(read_censuses(path=census_path))
    return [calibrate_port(port, censuses) for port in ANCHORAGE_PORT_TO_TONNAGE_LABEL]


def write_calibration_csv(results: list[PortCalibrationResult], *, path: Path = CALIBRATION_CSV_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["port", "n", "date_from", "date_to", "spearman_r", "pearson_r", "mean_abs_diff", "finding"])
        for r in results:
            writer.writerow([
                r.port, r.n,
                r.date_range[0].isoformat() if r.date_range else "",
                r.date_range[1].isoformat() if r.date_range else "",
                r.spearman_r if r.spearman_r is not None else "",
                r.pearson_r if r.pearson_r is not None else "",
                f"{r.mean_abs_diff:.4f}" if r.mean_abs_diff is not None else "",
                r.finding,
            ])
    LOGGER.info(f"wrote {path}")


def write_calibration_doc(results: list[PortCalibrationResult], *, path: Path = CALIBRATION_DOC_PATH) -> None:
    """A short, human-readable summary -- the real numbers, not a
    restatement of the module docstring's methodology."""
    lines = [
        "# Anchorage satellite vs. PortWatch calibration (4.3)",
        "",
        f"Generated {datetime.now(UTC).date().isoformat()} by `anchorage.calibrate.calibrate_all_ports()`.",
        "",
        (
            "This is a spot-check comparison between real Sentinel-1-derived vessel counts "
            "(`anchorage.detect`, MODEL_DERIVED) and PortWatch's real daily dry-bulk call counts "
            "(OBSERVED). It is evidence, not a replacement for the live PortWatch-derived congestion "
            "signal `opt.congestion`/`opt.risk` already use -- Sentinel-1's real revisit cadence "
            "(roughly every 4-11 days per port, see `data_builders.harvest_sentinel1`'s own "
            "PULL_NOTES.md) is far too sparse to drive a live quote."
        ),
        "",
        (
            f"A correlation is reported only when a port has at least {MIN_N_FOR_CORRELATION} real "
            "paired observations (`MIN_N_FOR_CORRELATION`) -- see `anchorage.calibrate`'s own module "
            "docstring for why fewer than that is not a real finding."
        ),
        "",
        "## Results",
        "",
        "| Port | n | Date range | Spearman r | Pearson r | Mean abs. diff | Finding |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in results:
        date_range_str = f"{r.date_range[0]} to {r.date_range[1]}" if r.date_range else "n/a"
        spearman_str = f"{r.spearman_r:.3f}" if r.spearman_r is not None else "not reported"
        pearson_str = f"{r.pearson_r:.3f}" if r.pearson_r is not None else "not reported"
        mad_str = f"{r.mean_abs_diff:.2f}" if r.mean_abs_diff is not None else "n/a"
        lines.append(f"| {r.port} | {r.n} | {date_range_str} | {spearman_str} | {pearson_str} | {mad_str} | {r.finding} |")

    lines += [
        "",
        "## Reading this table",
        "",
        (
            "A port with `n=0` means no real Sentinel-1 scene has been processed into a real "
            "`AnchorageCensus` for it yet -- not a failed comparison, an absent one. This module never "
            "reports a correlation below `MIN_N_FOR_CORRELATION` real paired observations, regardless "
            "of how strong or weak the few available points might look."
        ),
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    LOGGER.info(f"wrote {path}")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    results = calibrate_all_ports()
    write_calibration_csv(results)
    write_calibration_doc(results)
    for r in results:
        LOGGER.info(f"{r.port}: {r.finding}")


if __name__ == "__main__":
    main()
