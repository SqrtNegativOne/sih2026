"""Empirical wait and handling distributions from ``fact_port_call`` -- P2 §3.

Before this module: ``opt.congestion._real_wait_days`` returns one scalar
(``static_baseline * portwatch_call_count_ratio``) -- not a queue, no
percentiles, no sample size. This module computes genuine distributions
where P1's ``fact_port_call`` has enough real evidence, and is explicit,
never silent, everywhere it does not.

Real data exercised against this module: P1's Paradip backfill (43 real
days, 1,224 real rows, 238 real vessel names) is the only source in this
repo with real arrival/ready/berth timestamps at that volume -- every other
port's ``fact_port_call`` history (Dhamra/Gangavaram's 3 archived HTML
snapshots) is far too thin to compute a percentile from, which is exactly
the sufficiency gate this module exists to enforce rather than paper over.

Critical scoping rule (verified, not assumed): a port with thin wait history
is NOT an unsupported M4 port. Berth *constraints* (geometry, draft) and
*wait* evidence are independent axes in this data model -- Gangavaram has a
rich, PUBLISHED berth register and almost no fact_port_call history; Paradip
has zero berth register rows and excellent fact_port_call history. Each
axis degrades on its own; neither one drags the other down.
"""
from __future__ import annotations

import statistics
from collections.abc import Sequence
from datetime import datetime
from enum import Enum
from typing import Final

from pydantic import BaseModel, ConfigDict

from berth_truth.fact_port_call import (
    FactPortCall,
    FactPortCallStore,
    classify_cargo,
    distinct_calls,
)
from opt.network import PortEnum

__all__ = [
    "MINIMUM_SAMPLE_SIZE",
    "EffectiveHandlingRate",
    "HandlingDistribution",
    "WaitDistribution",
    "WaitInterval",
    "compute_handling_distribution",
    "compute_wait_distribution",
    "effective_handling_rate_tph",
]

#: Below this many real observations, no percentile is reported -- a
#: percentile computed from a handful of points is not a distribution, it is
#: those points. 20 is a conventional, defensible floor for a P90 estimate
#: (below it, the tail estimate is dominated by one or two observations);
#: chosen and stated explicitly rather than left as an unexplained constant.
MINIMUM_SAMPLE_SIZE: Final[int] = 20


class WaitInterval(str, Enum):
    """The four real intervals real port-call timestamps support -- kept
    separate because they answer different operational questions, verified
    against Paradip's real data: a vessel can sit at anchorage waiting for
    cargo readiness (arrival->ready) for entirely different reasons than it
    waits for a berth to free up once ready (ready->berth)."""

    ARRIVAL_TO_READY = "ARRIVAL_TO_READY"
    READY_TO_BERTH = "READY_TO_BERTH"
    ARRIVAL_TO_BERTH = "ARRIVAL_TO_BERTH"
    BERTH_TO_SAIL = "BERTH_TO_SAIL"


_INTERVAL_FIELDS: dict[WaitInterval, tuple[str, str]] = {
    WaitInterval.ARRIVAL_TO_READY: ("arrival_ts", "ready_ts"),
    WaitInterval.READY_TO_BERTH: ("ready_ts", "berth_ts"),
    WaitInterval.ARRIVAL_TO_BERTH: ("arrival_ts", "berth_ts"),
    WaitInterval.BERTH_TO_SAIL: ("berth_ts", "sail_ts"),
}


class WaitDistribution(BaseModel):
    model_config = ConfigDict(frozen=True)

    interval: WaitInterval
    n: int
    p50_hours: float | None
    p75_hours: float | None
    p90_hours: float | None
    mean_hours: float | None
    source_level: str
    """Which segmentation level the sample was actually drawn from, after
    the fallback hierarchy -- e.g. 'port+vessel_class+commodity' or,
    coarsened, just 'port'. Discloses how specific the estimate really is."""
    is_sufficient: bool
    """P(wait > X) is a separate module-level function (``probability_wait_
    exceeds``), not a method here -- this summary model deliberately does
    not carry the raw sample values around (keeps it small and
    JSON-friendly), so a method on it could not compute a real answer."""


class HandlingDistribution(BaseModel):
    model_config = ConfigDict(frozen=True)

    n: int
    norm_tpd_median: float | None
    actual_tpd_median: float | None
    actual_over_norm_ratio: float | None
    """actual/norm, median -- >1.0 means real handling is outperforming the
    stated norm; <1.0 means underperforming. None if either side has no
    real data, or norm is degenerate (0)."""
    source_level: str
    is_sufficient: bool


def _interval_hours(row: FactPortCall, start_field: str, end_field: str) -> float | None:
    start: datetime | None = getattr(row, start_field)
    end: datetime | None = getattr(row, end_field)
    if start is None or end is None:
        return None
    delta_hours = (end - start).total_seconds() / 3600.0
    if delta_hours < 0:
        # A real data-quality signal, not a crash: e.g. a continuation row
        # inheriting a primary row's timestamps combined with a genuinely
        # out-of-order pair. Excluded from the sample rather than silently
        # included as a nonsensical negative wait.
        return None
    return delta_hours


def _percentile(sorted_values: Sequence[float], pct: float) -> float:
    """Linear-interpolation percentile (matches numpy's default 'linear'
    method) -- no external dependency needed for this."""
    if len(sorted_values) == 1:
        return sorted_values[0]
    k = (len(sorted_values) - 1) * (pct / 100.0)
    f, c = int(k), min(int(k) + 1, len(sorted_values) - 1)
    if f == c:
        return sorted_values[f]
    return sorted_values[f] + (sorted_values[c] - sorted_values[f]) * (k - f)


def compute_wait_distribution(
    port: PortEnum,
    interval: WaitInterval,
    *,
    vessel_class: str | None = None,
    commodity_class: str | None = None,
    store: FactPortCallStore | None = None,
    minimum_sample_size: int = MINIMUM_SAMPLE_SIZE,
) -> WaitDistribution:
    """Real percentiles where the sample clears ``minimum_sample_size``;
    an honestly insufficient (but still reported, with its real n)
    distribution otherwise. Segmentation fallback hierarchy, coarsest to
    finest requested: (port) -> (port, vessel_class) -> (port, vessel_class,
    commodity_class) -- tries the most specific level the caller asked for
    first, and only coarsens if that level is too thin, disclosing exactly
    which level the reported sample actually came from.

    F-11 fix: rows are collapsed to one per real call (``distinct_calls``)
    before any percentile is computed. These source documents re-list a
    vessel in every report filed while it's still in port, so a ship that
    waits longer is *reported on more often* -- verified live, the raw
    rows overstated Paradip's median arrival-to-berth wait by ~35% (73.9h
    vs. a real 54.8h once deduplicated). ``n`` below is therefore the real
    count of distinct calls, not of report-row sightings.
    """
    store = store or FactPortCallStore()
    start_field, end_field = _INTERVAL_FIELDS[interval]

    attempts: list[tuple[str, dict]] = []
    if vessel_class is not None and commodity_class is not None:
        attempts.append(("port+vessel_class+commodity", {"vessel_class": vessel_class, "commodity_class": commodity_class}))
    if vessel_class is not None:
        attempts.append(("port+vessel_class", {"vessel_class": vessel_class}))
    attempts.append(("port", {}))

    for source_level, filters in attempts:
        rows = distinct_calls(store.query(port=port, **filters))
        values = sorted(v for r in rows if (v := _interval_hours(r, start_field, end_field)) is not None)
        if len(values) >= minimum_sample_size:
            return WaitDistribution(
                interval=interval, n=len(values),
                p50_hours=_percentile(values, 50), p75_hours=_percentile(values, 75),
                p90_hours=_percentile(values, 90), mean_hours=statistics.mean(values),
                source_level=source_level, is_sufficient=True,
            )

    # Nothing cleared the gate -- report the coarsest (largest) real n found,
    # honestly marked insufficient. Never fabricate a percentile from it.
    rows = distinct_calls(store.query(port=port))
    values = [v for r in rows if (v := _interval_hours(r, start_field, end_field)) is not None]
    return WaitDistribution(
        interval=interval, n=len(values),
        p50_hours=None, p75_hours=None, p90_hours=None, mean_hours=None,
        source_level="port", is_sufficient=False,
    )


def probability_wait_exceeds(
    port: PortEnum, interval: WaitInterval, hours: float, *, store: FactPortCallStore | None = None,
    minimum_sample_size: int = MINIMUM_SAMPLE_SIZE,
) -> float | None:
    """P(wait > hours) computed directly from the real sample -- a separate
    function (not a WaitDistribution method) because it needs the raw
    values, which the frozen summary model deliberately does not carry
    around (keeps WaitDistribution small and JSON-friendly)."""
    store = store or FactPortCallStore()
    start_field, end_field = _INTERVAL_FIELDS[interval]
    rows = store.query(port=port)
    values = [v for r in rows if (v := _interval_hours(r, start_field, end_field)) is not None]
    if len(values) < minimum_sample_size:
        return None
    return sum(1 for v in values if v > hours) / len(values)


def compute_handling_distribution(
    port: PortEnum,
    *,
    commodity_class: str | None = None,
    store: FactPortCallStore | None = None,
    minimum_sample_size: int = MINIMUM_SAMPLE_SIZE,
) -> HandlingDistribution:
    """Real norm-vs-actual productivity from fact_port_call, same
    sufficiency gate as waits. Replaces opt.network.Port.handling_rate_tph
    (a literal) only where this gate passes -- see effective_handling_rate_tph
    below for where that substitution actually happens (P6; not reality.py --
    opt.voyage imports from reality.py, so putting the substitution there
    would be a circular import; this module has no such dependency).

    F-10 fix, three real, independently-confirmed corruptions removed:
    (1) rows are deduplicated to one per real call first (see
    ``distinct_calls`` -- same length-bias issue as F-11's wait
    percentiles, a longer-lingering call is reported, and counted, more
    times); (2) ``actual_tpd == 0`` rows (a vessel listed on a day it did
    no cargo work at all) are excluded from the actuals sample, matching
    the ``> 0`` floor ``norms`` already correctly used; (3) rows are
    restricted to cargo this module's own free-text classifier
    (``classify_cargo``) reads as dry bulk -- ``commodity_class`` itself is
    null on every real row today, so without this the sample silently mixes
    in liquid/gas cargo (crude oil, HSD, LPG...) at an entirely different
    berth type with entirely different throughput physics. Verified live:
    the uncorrected sample put Paradip's actual median at 6,720 t/day
    (280 t/h, vs. a published 1,200 t/h norm -- a 36% ratio implausible
    enough that this was the specific finding that surfaced all three
    bugs); corrected, real dry-bulk-only throughput is 15,621 t/day."""
    store = store or FactPortCallStore()
    filters = {"commodity_class": commodity_class} if commodity_class else {}
    rows = [
        r for r in distinct_calls(store.query(port=port, **filters))
        if classify_cargo(r.cargo_raw) == "dry_bulk"
    ]

    norms = [r.norm_tpd for r in rows if r.norm_tpd is not None and r.norm_tpd > 0]
    actuals = [r.actual_tpd for r in rows if r.actual_tpd is not None and r.actual_tpd > 0]
    n = max(len(norms), len(actuals))
    is_sufficient = len(actuals) >= minimum_sample_size

    ratio: float | None = None
    if norms and actuals:
        norm_med, actual_med = statistics.median(norms), statistics.median(actuals)
        ratio = actual_med / norm_med if norm_med else None

    return HandlingDistribution(
        n=n,
        norm_tpd_median=statistics.median(norms) if norms else None,
        actual_tpd_median=statistics.median(actuals) if actuals else None,
        actual_over_norm_ratio=ratio,
        source_level="port" if not commodity_class else "port+commodity",
        is_sufficient=is_sufficient,
    )


class EffectiveHandlingRate(BaseModel):
    """P6: the rate a caller (opt.voyage's CP-SAT port-time precompute,
    opt.landed_cost) should actually use -- empirical where the sufficiency
    gate passes, the original opt.network.Port.handling_rate_tph literal
    otherwise, with which one and why always disclosed. Never silently
    prefers the literal; never silently prefers the empirical figure either.

    **This is the port-side half of port-to-plant coupling, not the whole
    of it.** It uses real, observed port handling productivity to influence
    parcel timing, laytime exposure, and demurrage risk at the port -- it
    says nothing about the onward rail/rake/stockyard leg from port to a
    SAIL plant, because no rail, rake, or stockyard data exists anywhere in
    this repo. That data is not public, was never harvested, and is not
    fabricated here -- a caller needing the plant-side leg has to source it
    separately; this module does not claim to cover it."""

    model_config = ConfigDict(frozen=True)

    port: PortEnum
    commodity_class: str | None
    rate_tph: float | None
    """The rate to use. None only when neither an empirical estimate nor a
    static literal is available at all (should not happen for any of this
    repo's 15 ports today, since every PortEnum member defines a literal --
    but a port added later without one would surface as None here, not a
    silent 0)."""
    is_empirical: bool
    static_literal_tph: float | None
    handling: HandlingDistribution
    reason: str


def effective_handling_rate_tph(
    port: PortEnum,
    *,
    commodity_class: str | None = None,
    store: FactPortCallStore | None = None,
    minimum_sample_size: int = MINIMUM_SAMPLE_SIZE,
) -> EffectiveHandlingRate:
    """Real substitution decision: empirical actual_tpd_median (converted
    tonnes/day -> tonnes/hour on a 24h basis, the standard throughput-rate
    convention) when compute_handling_distribution's sufficiency gate
    passes, else the original static literal, unchanged. actual_tpd is a
    BERTH-side productivity figure directly reported by the source document
    (Paradip's own official section-A report, columns 9/10) -- distinct from,
    and not double-counting, the separate arrival->berth WAIT distribution
    this module also computes from different columns of the same table.
    """
    static_literal = port.value.handling_rate_tph
    handling = compute_handling_distribution(
        port, commodity_class=commodity_class, store=store, minimum_sample_size=minimum_sample_size,
    )

    if handling.is_sufficient and handling.actual_tpd_median is not None and handling.actual_tpd_median > 0:
        rate_tph = handling.actual_tpd_median / 24.0
        return EffectiveHandlingRate(
            port=port, commodity_class=commodity_class, rate_tph=rate_tph, is_empirical=True,
            static_literal_tph=static_literal, handling=handling,
            reason=(
                f"empirical: actual_tpd_median={handling.actual_tpd_median:.0f} (n={handling.n}, "
                f"{handling.source_level}) -> {rate_tph:.1f} tph, vs static literal {static_literal}"
            ),
        )

    reason = (
        f"static literal ({static_literal}): empirical sample insufficient "
        f"(n={handling.n} < {minimum_sample_size})" if not handling.is_sufficient
        else f"static literal ({static_literal}): no positive actual_tpd_median in the real sample"
    )
    return EffectiveHandlingRate(
        port=port, commodity_class=commodity_class, rate_tph=static_literal, is_empirical=False,
        static_literal_tph=static_literal, handling=handling, reason=reason,
    )
