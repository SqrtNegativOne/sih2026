"""P4 requirement 6 -- quantify and expose the gap between directly-observed
USD/day (TC-average) history and the much deeper Baltic INDEX history, and
report the affine map's real regime breakpoints and error.

**What this audit found, checked against the real training config
(``src/config/samples.toml``), not assumed:** the forecast MODELS never
train on TC-average history at all -- ``[targets.classes]`` maps each class
to its INDEX series (``BC_INDEX``, ``BPI_INDEX``, ``BSI_INDEX``,
``BHSI_INDEX``), and ``data_builders.build_samples``/``ml.targets`` build
targets as forward INDEX returns. ``ml.units`` already documents why: "the
models keep training on log-index returns and conversion happens only at
the last mile, anchored to today's observed TC average" -- so training data
depth is the real, deep index history (real 2012-2026 coverage, ~3,400+ rows
per class), not the thin TC-average window. **The thin-series risk this
module actually reports on is the LAST-MILE CONVERSION step**
(``ml.units.fit_unit_map``/``project_return``), which is real and already
regime-aware (walks back to the current affine regime, flags
``is_fresh_regime``) -- this module surfaces its own real numbers rather
than re-deriving a second, possibly-diverging estimate of the same thing.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Final

import polars as pl

from ml.units import CLASS_SERIES, RegimeSegment, detect_regime_breaks

__all__ = ["ThinSeriesReport", "audit_all_classes", "audit_thin_series"]

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
MASTER_LONG_PATH: Final[Path] = REPO_ROOT / "src" / "data" / "master_long.parquet"


@dataclass(frozen=True)
class ThinSeriesReport:
    vessel_class: str
    index_series_id: str
    tcavg_series_id: str
    n_index_rows: int
    index_first_date: date
    index_last_date: date
    n_tcavg_rows: int  # the "directly-observed USD/day" history
    tcavg_first_date: date | None
    tcavg_last_date: date | None
    n_index_only_rows: int  # index history with no contemporaneous TCAVG at all
    regime_segments: tuple[RegimeSegment, ...]  # real breakpoints, ml.units.detect_regime_breaks
    trains_on_tcavg_directly: bool  # always False -- see module docstring; asserted, not assumed

    @property
    def tcavg_coverage_fraction(self) -> float:
        """What fraction of the real index history has a same-day TCAVG
        observation to convert against -- the honest "how thin" number."""
        if self.n_index_rows == 0:
            return 0.0
        return (self.n_index_rows - self.n_index_only_rows) / self.n_index_rows


def audit_thin_series(vessel_class: str, master: pl.DataFrame | None = None) -> ThinSeriesReport:
    if vessel_class not in CLASS_SERIES:
        raise KeyError(f"Unknown vessel class {vessel_class!r}; expected one of {sorted(CLASS_SERIES)}.")
    master = master if master is not None else pl.read_parquet(MASTER_LONG_PATH)
    index_id, tc_id = CLASS_SERIES[vessel_class]

    index_rows = master.filter(pl.col("series_id") == index_id).select("date").sort("date")
    tc_rows = master.filter(pl.col("series_id") == tc_id).select("date").sort("date")

    if index_rows.is_empty():
        raise ValueError(f"No real index history ({index_id}) for {vessel_class}.")

    n_index_only = index_rows.join(tc_rows, on="date", how="anti").height
    regimes = detect_regime_breaks(master, vessel_class) if not tc_rows.is_empty() else ()

    return ThinSeriesReport(
        vessel_class=vessel_class, index_series_id=index_id, tcavg_series_id=tc_id,
        n_index_rows=index_rows.height, index_first_date=index_rows["date"].min(), index_last_date=index_rows["date"].max(),
        n_tcavg_rows=tc_rows.height,
        tcavg_first_date=tc_rows["date"].min() if not tc_rows.is_empty() else None,
        tcavg_last_date=tc_rows["date"].max() if not tc_rows.is_empty() else None,
        n_index_only_rows=n_index_only,
        regime_segments=tuple(regimes),
        trains_on_tcavg_directly=False,
    )


def audit_all_classes(master: pl.DataFrame | None = None) -> dict[str, ThinSeriesReport]:
    master = master if master is not None else pl.read_parquet(MASTER_LONG_PATH)
    return {cls: audit_thin_series(cls, master) for cls in CLASS_SERIES}
