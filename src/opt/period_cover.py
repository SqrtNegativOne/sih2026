"""Is this book of cargo worth covering with chartered-in tonnage?

The season plan (``opt.voyage.schedule_voyages``) answers *how* a book of lots
gets carried: which ship takes which lot, when, and what each voyage earns net
of fuel, idle opex and demurrage. It does not answer whether owning that
programme is worth the tonnage it consumes, which is the question behind the
problem statement's "period versus spot" framing.

This module answers exactly one piece of that, and only the piece the data on
disk actually supports.

What it computes
----------------
The **break-even hire**: the highest daily rate at which chartering in the
tonnage to cover this plan still breaks even.

    ship_days      = n_vessels x plan span in days
    break_even     = total fleet profit / ship_days

That is arithmetic over the scheduler's own output and nothing else. Pay more
than the break-even per ship-day and the programme costs more in hire than the
cargo earns; pay less and it clears.

What it does NOT do, and why
----------------------------
**It does not quote a period charter rate, because this system has none.**

The only real $/day rate series on disk are the Baltic class TC *averages*
(``CAPESIZE_TCAVG`` / ``PANAMAX_TCAVG`` / ``SUPRAMAX_TCAVG`` /
``HANDYSIZE_TCAVG``, from the handybulk pull, mapped in ``ml.units``). Those
are **spot** indices -- the average of the spot voyage routes expressed in
dollars per day, i.e. what a ship earns trading spot today. A 3-, 6- or
12-month period rate is a forward, negotiated, broker-supplied number. It is
a different quantity, it moves differently from the spot average, and it is
not in ``master_long.parquet``. Deriving one from the spot average with an
assumed period premium would be inventing the single number the whole decision
turns on.

So the comparison offered here is the honest one the data supports: break-even
hire against the real spot TC average for the class. That answers "is this book
worth more per ship-day than simply trading these ships spot", which is a real
opportunity-cost test. Whether a *specific* period offer is worth taking is
answered by handing that offer's rate -- a number only the desk has -- to
``clears_period_offer``.

Provenance
----------
``spot_tc_average_usd_per_day`` is OBSERVED (a published index value, read at a
real date, never interpolated). ``break_even_hire_usd_per_day`` and
``ship_days`` are MODEL_DERIVED: computed from a CP-SAT solve over real port,
distance and vessel data. They do not inherit the OBSERVED provenance of the
inputs behind them.
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Final, Literal

import polars as pl
from pydantic import BaseModel, ConfigDict

from ml import units

LOGGER: Final[logging.Logger] = logging.getLogger(__name__)

#: Hours per day. Named because the scheduler reports every timing in hours
#: from its own epoch and every rate in this module is per day.
HOURS_PER_DAY: Final[float] = 24.0

PeriodVerdict = Literal["cover_beats_spot", "spot_beats_cover", "no_benchmark"]


class NoRealTcAverageError(LookupError):
    """No published class TC average exists at the requested date.

    Raised rather than falling back to the nearest observation: a rate carried
    forward from an earlier day is a different number wearing today's date, and
    the break-even comparison is only meaningful against a rate that was really
    quoted on the day the plan is priced from.
    """


class PeriodCoverAssessment(BaseModel):
    """The break-even hire for one season plan, and the real spot benchmark."""

    model_config = ConfigDict(frozen=True)

    ship_days: float
    """Fleet time the plan consumes: vessels x plan span in days. The
    denominator of the break-even, and the quantity a period charter would be
    buying."""

    break_even_hire_usd_per_day: float
    """MODEL_DERIVED. Highest daily hire at which covering this plan with
    chartered-in tonnage still breaks even. Negative when the plan loses money,
    which is a real answer: no hire rate makes a loss-making book worth
    covering."""

    spot_tc_average_usd_per_day: float | None
    """OBSERVED, or None when no published value exists at ``as_of``. The
    Baltic class TC average -- a SPOT index, not a period quote."""

    spot_tc_series_id: str
    """Which real series the benchmark came from, so the figure is traceable."""

    spot_tc_as_of: date | None
    """The date the benchmark was actually observed. Equals the requested date
    or is None; this module never carries a rate forward to a date it was not
    quoted on."""

    verdict: PeriodVerdict
    """``cover_beats_spot`` when the book earns more per ship-day than trading
    the ships spot; ``spot_beats_cover`` when it does not; ``no_benchmark``
    when there is no real spot rate to compare against, which is a gap in the
    data, not a neutral result."""

    margin_over_spot_usd_per_day: float | None
    """break_even - spot benchmark. None without a benchmark. Positive means
    there is room between what the book earns and what the ships would earn
    spot -- that room is what a period charter has to fit inside."""

    def clears_period_offer(self, offered_usd_per_day: float) -> bool:
        """Does a real period offer clear this plan's break-even?

        The rate is supplied by the caller because it is a broker number this
        system does not hold. Nothing here estimates it.
        """
        return offered_usd_per_day < self.break_even_hire_usd_per_day


def _default_master_path() -> Path:
    return Path(__file__).resolve().parents[2] / "src" / "data" / "master_long.parquet"


def real_spot_tc_average(
    vessel_class: str,
    as_of: date,
    master_path: Path | None = None,
) -> tuple[float, str]:
    """The published class TC average on exactly ``as_of``.

    Returns ``(usd_per_day, series_id)``. Raises ``NoRealTcAverageError`` when
    the date has no observation -- weekends and holidays genuinely have none,
    and that is reported rather than filled.
    """
    try:
        series_id = units.CLASS_SERIES[vessel_class][1]
    except KeyError as exc:
        raise NoRealTcAverageError(
            f"No TC-average series is mapped for vessel class {vessel_class!r}. "
            f"Known classes: {sorted(units.CLASS_SERIES)}."
        ) from exc

    path = master_path or _default_master_path()
    if not path.exists():
        raise NoRealTcAverageError(
            f"The market history ({path}) is not on disk, so no real spot benchmark exists."
        )

    master = pl.read_parquet(path)
    row = master.filter((pl.col("series_id") == series_id) & (pl.col("date") == as_of))
    if row.is_empty():
        raise NoRealTcAverageError(
            f"No {series_id} observation on {as_of.isoformat()}. The index is not published "
            f"every calendar day; pick a date the market was open."
        )
    return float(row["value"][0]), series_id


def assess_period_cover(
    *,
    total_profit_usd: float,
    n_vessels: int,
    span_hours: float,
    vessel_class: str,
    as_of: date,
    master_path: Path | None = None,
) -> PeriodCoverAssessment:
    """Break-even hire for a solved season plan, against the real spot average.

    ``span_hours`` is the plan's own horizon -- the latest ``finish_hours`` in
    the solve -- not a calendar quarter. A period charter covering this book has
    to cover the time the book actually occupies, and padding that to a round
    term would understate the break-even.

    A plan with no vessels or no duration has no break-even to compute; that is
    a caller error rather than a market finding, so it raises.
    """
    if n_vessels <= 0:
        raise ValueError("A season plan with no vessels consumes no ship-days.")
    if span_hours <= 0:
        raise ValueError("A season plan with no duration consumes no ship-days.")

    ship_days = n_vessels * (span_hours / HOURS_PER_DAY)
    break_even = total_profit_usd / ship_days

    try:
        spot, series_id = real_spot_tc_average(vessel_class, as_of, master_path)
    except NoRealTcAverageError as exc:
        LOGGER.info("No spot benchmark for %s on %s: %s", vessel_class, as_of, exc)
        return PeriodCoverAssessment(
            ship_days=ship_days,
            break_even_hire_usd_per_day=break_even,
            spot_tc_average_usd_per_day=None,
            spot_tc_series_id=units.CLASS_SERIES.get(vessel_class, ("", ""))[1],
            spot_tc_as_of=None,
            verdict="no_benchmark",
            margin_over_spot_usd_per_day=None,
        )

    return PeriodCoverAssessment(
        ship_days=ship_days,
        break_even_hire_usd_per_day=break_even,
        spot_tc_average_usd_per_day=spot,
        spot_tc_series_id=series_id,
        spot_tc_as_of=as_of,
        verdict="cover_beats_spot" if break_even > spot else "spot_beats_cover",
        margin_over_spot_usd_per_day=break_even - spot,
    )
