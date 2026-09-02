"""Landed-cost breakdown -- P6 commercial upgrade.

``landed_cost = freight + wait/delay cost (P2 empirical, where sufficient) +
handling cost (caveat) + demurrage (caveat) + war-risk premium (caveat) +
commodity price (P4, if secured) + FX (P4, if secured)``, per the P6 spec
plus the war-risk line added alongside it. Every component
is a separate, named field with its own ``Provenance`` and a human-readable
reason when unavailable. **A component that cannot be computed renders
``None``, never a silent 0.0** -- folding an unavailable component into a
total would misrepresent "we don't know" as "this cost doesn't exist."
Because of that, this module deliberately does not expose a single
all-components-summed ``landed_cost_usd_per_mt`` field: see
``LandedCostBreakdown.partial_total_usd_per_mt`` and its
``components_included``/``components_missing`` fields instead, which make
the gap visible in the total itself, not just in a caveat string next to it.

**Commercial terms are never invented.** ``handling_rate_usd_per_mt``,
``demurrage_usd_per_day``, and ``laytime_allowance_days`` are request
parameters with no default -- omitted means unavailable, tagged
``Provenance.DECLARED`` when the caller supplies them (a real business fact
this module cannot verify, only carry through honestly), never a SAIL-
specific literal invented here.

**A real unit trap, checked directly against the source rather than assumed:**
the World Bank Pink Sheet documents TWO iron-ore units -- the CONTRACT series
in "US cents/dmtu" (dry metric ton unit, 1% Fe-unit) and the SPOT series
(what this harvest actually reads, column "Iron ore, cfr spot") in
"US dollar/dry ton", i.e. already ``usd_per_mt``. An earlier version of this
harvest (P4) mislabeled the spot series ``usd_per_dmtu``, which would have
made a naive $/MT conversion multiply by Fe content and overstate the price
roughly 60-fold; caught while building this module (the first consumer to
branch on the unit string) and fixed at the source
(``data_builders.build_macro.WORLDBANK_SERIES``), verified against the
Pink Sheet's own "Description" sheet. Both ``MACRO_IRON_ORE`` and
``MACRO_COAL_AUSTRALIAN`` are ``usd_per_mt`` today and need no conversion.
The real, remaining caveat -- not correctable from data in this repo -- is
that both series price a standard benchmark grade (62% Fe iron ore fines
post-2008; a specific Australian coal reference grade), so an off-benchmark
real cargo's true delivered price would differ; ``commodity_price_reason``
states the benchmark explicitly rather than attempting an unevidenced
grade adjustment.

Reuses real, already-built pieces rather than a parallel system:
``berth_truth.empirical.compute_wait_distribution`` (P2, the same function
``fragility.engine``'s ``_empirical_wait_context`` already calls) for wait
cost, and ``src/data/macro_long.parquet`` (P4's real World Bank/FRED harvest,
``data_builders.build_macro``) for commodity price and FX -- the same
on-disk artifact ``ml.features.macro.MacroFeature`` and
``ml.macro_features._load_macro_long`` already read, just a point-in-time
lookup instead of a full-frame join (a different, smaller shape for a
different consumer, not a second data source).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Final

import polars as pl

from berth_truth.empirical import (
    MINIMUM_SAMPLE_SIZE,
    WaitInterval,
    compute_wait_distribution,
)
from berth_truth.fact_port_call import FactPortCallStore
from data_builders.provenance import Provenance
from opt.network import PortEnum
from opt.types import VesselClass
from opt.war_risk import listed_areas_on_route, war_risk_premium_usd

__all__ = [
    "COMMODITY_SERIES",
    "LandedCostBreakdown",
    "LandedCostRequest",
    "compute_landed_cost",
]

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
MACRO_LONG_PATH: Final[Path] = REPO_ROOT / "src" / "data" / "macro_long.parquet"

#: The two commodities SAIL actually charters dry bulk for, mapped to their
#: real macro_long.parquet series_id (data_builders.build_macro / P4).
COMMODITY_SERIES: Final[dict[str, str]] = {
    "iron_ore": "MACRO_IRON_ORE",
    "coal": "MACRO_COAL_AUSTRALIAN",
}


def _load_macro_long() -> pl.DataFrame:
    return pl.read_parquet(MACRO_LONG_PATH)


@dataclass(frozen=True)
class _MacroPoint:
    value: float
    unit: str
    observation_date: date


def usd_inr_rate_as_of(as_of: date) -> tuple[float, date] | None:
    """Real USD/INR rate on or before ``as_of``, or ``None``.

    A public read of the same ``MACRO_USD_INR`` series this module already uses
    when converting a landed cost to rupees. Exposed because rupee display is a
    desk-wide preference rather than a landed-cost-only option, and the
    alternative -- letting the frontend carry its own rate -- would mean a
    hardcoded number standing in for a real observation.

    Returns ``None`` rather than a fallback when no real observation covers the
    date, so a caller shows dollars instead of inventing a conversion.
    """
    point = _macro_value_as_of("MACRO_USD_INR", as_of)
    return None if point is None else (float(point.value), point.observation_date)


def _macro_value_as_of(series_id: str, as_of: date, macro_long: pl.DataFrame | None = None) -> _MacroPoint | None:
    """Real value of ``series_id`` as of ``as_of``, forward-filled to the
    most recent real observation on or before that date -- the same
    semantics ``ml.features.macro.MacroFeature`` uses for the ML pipeline
    (``join_asof(strategy="backward")``), applied here as a single
    point-in-time read instead of a full-frame join. ``None`` when the
    series is unknown or ``as_of`` predates its first real observation --
    never a fabricated pre-history value."""
    frame = macro_long if macro_long is not None else _load_macro_long()
    rows = (
        frame.filter((pl.col("series_id") == series_id) & (pl.col("date") <= as_of))
        .sort("date")
        .tail(1)
    )
    if rows.is_empty():
        return None
    row = rows.to_dicts()[0]
    return _MacroPoint(value=float(row["value"]), unit=row["unit"], observation_date=row["date"])


@dataclass(frozen=True)
class LandedCostRequest:
    dest_port: PortEnum
    cargo_volume_mt: float

    # Freight -- required. Caller supplies the real quoted/forecast $/day
    # figure (e.g. opt.quote's QuoteResult) and the voyage length used to
    # convert it to $/MT; this module does not itself forecast a rate.
    freight_usd_per_day: float
    voyage_days: float

    # Wait/delay cost -- P2 empirical, gated on real sufficiency.
    vessel_class: VesselClass | None = None
    commodity_class: str | None = None
    opex_usd_per_day: float = 0.0
    as_of: date | None = None

    # Handling cost -- caveat: no default, user-declared only.
    handling_rate_usd_per_mt: float | None = None

    # Demurrage -- caveat: no default, user-declared only. Needs both a rate
    # and a laytime allowance to compare against real/expected port time.
    demurrage_usd_per_day: float | None = None
    laytime_allowance_days: float | None = None

    # War-risk premium -- its own line item, never folded into freight.
    # Needs BOTH an origin (to know the real route, and therefore which Joint
    # War Committee Listed Areas it enters) and a hull value (a real
    # commercial fact this module cannot infer). Omit either and the
    # component is unavailable -- never a zero line, never an assumed hull.
    # war_risk_rate_pct_per_7_days overrides opt.war_risk's own documented
    # placeholder rate; supply a real quoted rate here if you have one.
    origin_port: PortEnum | None = None
    hull_value_usd: float | None = None
    war_risk_rate_pct_per_7_days: float | None = None

    # Commodity price -- P4, if secured (it is, see module docstring).
    commodity: str | None = None  # one of COMMODITY_SERIES's keys

    # FX -- P4, if secured. Applied as a conversion of the total, never
    # summed as if it were itself a cost.
    convert_to_inr: bool = False


@dataclass(frozen=True)
class LandedCostBreakdown:
    dest_port: PortEnum
    cargo_volume_mt: float

    freight_usd_per_mt: float
    freight_provenance: Provenance = field(default=Provenance.MODEL_DERIVED)

    wait_cost_usd_per_mt: float | None = None
    wait_cost_provenance: Provenance | None = None
    wait_cost_reason: str = ""
    wait_p50_hours: float | None = None
    wait_sample_n: int = 0

    handling_cost_usd_per_mt: float | None = None
    handling_cost_provenance: Provenance | None = None
    handling_cost_reason: str = ""

    demurrage_cost_usd_per_mt: float | None = None
    demurrage_cost_provenance: Provenance | None = None
    demurrage_cost_reason: str = ""

    war_risk_usd_per_mt: float | None = None
    war_risk_provenance: Provenance | None = None
    war_risk_reason: str = ""
    war_risk_premium_usd: float | None = None
    war_risk_areas: tuple[str, ...] = ()
    war_risk_rate_pct_per_7_days: float | None = None
    war_risk_rate_is_caller_supplied: bool = False

    commodity_price_usd_per_mt: float | None = None
    commodity_price_provenance: Provenance | None = None
    commodity_price_reason: str = ""
    commodity_price_raw_value: float | None = None
    commodity_price_raw_unit: str | None = None
    commodity_price_as_of: date | None = None

    fx_inr_per_usd: float | None = None
    fx_provenance: Provenance | None = None
    fx_as_of: date | None = None
    fx_reason: str = ""

    components_included: tuple[str, ...] = ()
    components_missing: tuple[str, ...] = ()
    partial_total_usd_per_mt: float = 0.0
    """Sum of components_included ONLY -- named "partial", not "landed_cost",
    on purpose: with any real component missing (the common case), this is a
    known lower bound, not a complete total. Check components_missing before
    treating this as anything else."""
    partial_total_inr_per_mt: float | None = None


def compute_landed_cost(
    request: LandedCostRequest, *, store: FactPortCallStore | None = None, macro_long: pl.DataFrame | None = None
) -> LandedCostBreakdown:
    """``store``/``macro_long`` are test-injection only (default to the real
    fact_port_call log and the real macro_long.parquet) -- same convention
    ``berth_truth.empirical.compute_wait_distribution`` and
    ``opt.backhaul.observed_pairing_evidence`` already use."""
    as_of = request.as_of if request.as_of is not None else date.today()  # noqa: DTZ011 -- no as_of context available at this call boundary, matching opt.voyage._vessel_can_call's own documented same choice

    freight_usd_per_mt = (request.freight_usd_per_day * request.voyage_days) / request.cargo_volume_mt

    # -- Wait/delay cost: P2 empirical, real sufficiency gate. --------------
    wait_dist = compute_wait_distribution(
        request.dest_port, WaitInterval.ARRIVAL_TO_BERTH,
        vessel_class=request.vessel_class.value if request.vessel_class else None,
        commodity_class=request.commodity_class,
        store=store,
    )
    wait_cost_usd_per_mt: float | None = None
    wait_cost_provenance: Provenance | None = None
    if wait_dist.is_sufficient and wait_dist.p50_hours is not None:
        wait_days = wait_dist.p50_hours / 24.0
        wait_cost_usd_per_mt = (wait_days * request.opex_usd_per_day) / request.cargo_volume_mt
        wait_cost_provenance = Provenance.MODEL_DERIVED
        wait_cost_reason = f"P50 empirical wait ({wait_dist.p50_hours:.1f}h, n={wait_dist.n}, {wait_dist.source_level})"
    else:
        wait_cost_reason = (
            f"insufficient empirical sample at {request.dest_port.name} "
            f"(n={wait_dist.n} < minimum {MINIMUM_SAMPLE_SIZE}, {wait_dist.source_level})"
        )

    # -- Handling cost: caveat, user-declared only. --------------------------
    handling_cost_usd_per_mt: float | None = None
    handling_cost_provenance: Provenance | None = None
    if request.handling_rate_usd_per_mt is not None:
        handling_cost_usd_per_mt = request.handling_rate_usd_per_mt
        handling_cost_provenance = Provenance.DECLARED
        handling_cost_reason = "user-declared handling rate"
    else:
        handling_cost_reason = "no handling_rate_usd_per_mt supplied -- no repo-derived $/MT handling tariff exists (opt.network.Port.handling_rate_tph is a throughput rate, not a price)"

    # -- Demurrage: caveat, user-declared only, needs a real exposure. ------
    demurrage_cost_usd_per_mt: float | None = None
    demurrage_cost_provenance: Provenance | None = None
    if request.demurrage_usd_per_day is not None and request.laytime_allowance_days is not None:
        actual_port_days = (wait_dist.p50_hours / 24.0) if (wait_dist.is_sufficient and wait_dist.p50_hours is not None) else None
        if actual_port_days is not None:
            exposure_days = max(0.0, actual_port_days - request.laytime_allowance_days)
            demurrage_cost_usd_per_mt = (exposure_days * request.demurrage_usd_per_day) / request.cargo_volume_mt
            demurrage_cost_provenance = Provenance.DECLARED
            demurrage_cost_reason = (
                f"user-declared rate/allowance against P50 empirical port time "
                f"({actual_port_days:.2f}d vs {request.laytime_allowance_days:.2f}d allowance)"
            )
        else:
            demurrage_cost_reason = "rate and allowance supplied, but no sufficient empirical port-time estimate to compare against"
    elif request.demurrage_usd_per_day is not None or request.laytime_allowance_days is not None:
        demurrage_cost_reason = "both demurrage_usd_per_day and laytime_allowance_days are required -- only one was supplied"
    else:
        demurrage_cost_reason = "no demurrage_usd_per_day/laytime_allowance_days supplied -- these are contractual terms, never assumed"

    # -- War-risk premium: its own line, never folded into freight. ---------
    # Unavailable unless BOTH an origin port (to resolve the real route) and
    # a hull value are supplied. An unknown hull value yields None, not a
    # zero line and never an assumed vessel value -- opt.war_risk.
    # war_risk_premium_usd enforces that itself.
    war_risk_usd_per_mt: float | None = None
    war_risk_provenance: Provenance | None = None
    war_risk_premium_total: float | None = None
    war_risk_areas: tuple[str, ...] = ()
    war_risk_rate: float | None = None
    war_risk_rate_is_caller_supplied = False
    if request.origin_port is None:
        war_risk_reason = "no origin_port supplied -- the route, and therefore which war-risk Listed Areas it enters, cannot be resolved"
    elif request.hull_value_usd is None:
        war_risk_areas = listed_areas_on_route(request.origin_port, request.dest_port)
        war_risk_reason = (
            f"route enters {len(war_risk_areas)} war-risk Listed Area(s) "
            f"({', '.join(war_risk_areas) or 'none'}), but no hull_value_usd was supplied -- "
            "a vessel's insured hull value is a real commercial fact this module never assumes"
        )
    else:
        war_risk_areas = listed_areas_on_route(request.origin_port, request.dest_port)
        premium = war_risk_premium_usd(
            request.hull_value_usd,
            war_risk_areas,
            request.voyage_days,
            rate_pct_per_7_days=request.war_risk_rate_pct_per_7_days,
        )
        if premium is None:
            war_risk_reason = "route enters no Joint War Committee Listed Area -- no additional war-risk premium is owed"
        else:
            war_risk_usd_per_mt = premium.premium_usd / request.cargo_volume_mt
            war_risk_provenance = premium.provenance
            war_risk_premium_total = premium.premium_usd
            war_risk_rate = premium.rate_pct_per_7_days
            war_risk_rate_is_caller_supplied = premium.rate_is_caller_supplied
            war_risk_reason = premium.basis

    # -- Commodity price: P4 real data, if secured (it is). -----------------
    commodity_price_usd_per_mt: float | None = None
    commodity_price_provenance: Provenance | None = None
    commodity_price_raw_value: float | None = None
    commodity_price_raw_unit: str | None = None
    commodity_price_as_of: date | None = None
    if request.commodity is None:
        commodity_price_reason = "no commodity supplied"
    elif request.commodity not in COMMODITY_SERIES:
        commodity_price_reason = f"unknown commodity {request.commodity!r}; expected one of {sorted(COMMODITY_SERIES)}"
    else:
        series_id = COMMODITY_SERIES[request.commodity]
        point = _macro_value_as_of(series_id, as_of, macro_long)
        if point is None:
            commodity_price_reason = f"no real {series_id} observation on or before {as_of}"
        else:
            commodity_price_raw_value = point.value
            commodity_price_raw_unit = point.unit
            commodity_price_as_of = point.observation_date
            if point.unit == "usd_per_mt":
                commodity_price_usd_per_mt = point.value
                commodity_price_provenance = Provenance.OBSERVED
                commodity_price_reason = (
                    f"World Bank/FRED, real observation as of {point.observation_date} -- "
                    "standard benchmark grade (see module docstring); an off-benchmark "
                    "real cargo's true price would differ, not adjusted here."
                )
            else:
                commodity_price_reason = f"unrecognised unit {point.unit!r} for {series_id} -- not converted, raw value still available"

    # -- FX: P4 real data, applied as a conversion, not a summed cost. ------
    fx_rate: float | None = None
    fx_provenance: Provenance | None = None
    fx_as_of: date | None = None
    if request.convert_to_inr:
        fx_point = _macro_value_as_of("MACRO_USD_INR", as_of, macro_long)
        if fx_point is not None:
            fx_rate, fx_as_of = fx_point.value, fx_point.observation_date
            fx_provenance = Provenance.OBSERVED
            fx_reason = f"FRED DEXINUS, real observation as of {fx_point.observation_date}"
        else:
            fx_reason = f"no real MACRO_USD_INR observation on or before {as_of}"
    else:
        fx_reason = "convert_to_inr not requested"

    # -- Partial total: only real, available components, gap visible. -------
    parts: dict[str, float | None] = {
        "freight": freight_usd_per_mt,
        "wait_cost": wait_cost_usd_per_mt,
        "handling_cost": handling_cost_usd_per_mt,
        "demurrage_cost": demurrage_cost_usd_per_mt,
        "war_risk": war_risk_usd_per_mt,
        "commodity_price": commodity_price_usd_per_mt,
    }
    included = tuple(k for k, v in parts.items() if v is not None)
    missing = tuple(k for k, v in parts.items() if v is None)
    partial_total = sum(v for v in parts.values() if v is not None)
    partial_total_inr = partial_total * fx_rate if fx_rate is not None else None

    return LandedCostBreakdown(
        dest_port=request.dest_port,
        cargo_volume_mt=request.cargo_volume_mt,
        freight_usd_per_mt=freight_usd_per_mt,
        wait_cost_usd_per_mt=wait_cost_usd_per_mt,
        wait_cost_provenance=wait_cost_provenance,
        wait_cost_reason=wait_cost_reason,
        wait_p50_hours=wait_dist.p50_hours,
        wait_sample_n=wait_dist.n,
        handling_cost_usd_per_mt=handling_cost_usd_per_mt,
        handling_cost_provenance=handling_cost_provenance,
        handling_cost_reason=handling_cost_reason,
        demurrage_cost_usd_per_mt=demurrage_cost_usd_per_mt,
        demurrage_cost_provenance=demurrage_cost_provenance,
        demurrage_cost_reason=demurrage_cost_reason,
        war_risk_usd_per_mt=war_risk_usd_per_mt,
        war_risk_provenance=war_risk_provenance,
        war_risk_reason=war_risk_reason,
        war_risk_premium_usd=war_risk_premium_total,
        war_risk_areas=war_risk_areas,
        war_risk_rate_pct_per_7_days=war_risk_rate,
        war_risk_rate_is_caller_supplied=war_risk_rate_is_caller_supplied,
        commodity_price_usd_per_mt=commodity_price_usd_per_mt,
        commodity_price_provenance=commodity_price_provenance,
        commodity_price_reason=commodity_price_reason,
        commodity_price_raw_value=commodity_price_raw_value,
        commodity_price_raw_unit=commodity_price_raw_unit,
        commodity_price_as_of=commodity_price_as_of,
        fx_inr_per_usd=fx_rate,
        fx_provenance=fx_provenance,
        fx_as_of=fx_as_of,
        fx_reason=fx_reason,
        components_included=included,
        components_missing=missing,
        partial_total_usd_per_mt=partial_total,
        partial_total_inr_per_mt=partial_total_inr,
    )
