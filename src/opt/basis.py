"""Route-basis mechanism -- P4 requirement 1/2: real evidence or an explicit
"unavailable," never a fabricated route premium.

The problem this fixes: ``opt.quote.quote()`` has passed ``basis={}`` to
``OptimizerInputs`` since it was written (see the removed comment at the old
``opt/quote.py:213`` -- "No real basis calibration table exists yet"), so
every route from every origin prices identically in $/day; only $/MT differs,
and only because transit time differs. Measured live before this module
existed: Newcastle_AU / Richards_Bay / Balikpapan / Hampton_Roads all
returned an identical $18,141.46/day ceiling for the same cargo/laycan.

**What real route-rate evidence exists** (investigated directly against
``raw_data/signal_weekly/*.extraction.csv`` and the ``SG_*`` series they
build into ``master_long.parquet`` via ``data_builders.build_master.
read_signal_dir`` -- not assumed):

- Every ``RouteFamily`` is anchored on EC-India as the destination
  (``opt.network.RouteFamily``/``ORIGIN_PORT_ROUTE_FAMILY``). Signal's real
  route-level assessments (the ``SG_*`` series) are overwhelmingly Baltic
  benchmark routes with OTHER destinations (Qingdao, Rotterdam, Skaw-
  Gibraltar, ...) -- not EC-India.
- Exactly one series is a real, directly-sourced, EC-India-destination rate:
  ``SG_SUPRAMAX_INDONESIA_ECI_USD_T`` -- three real observations (2024-11-30,
  2025-04-10, 2025-06-07), each explicitly captioned in its source article's
  own extraction as "Supramax Indonesia to East Coast India freight rate",
  all ~$9/tonne. This is real evidence for ``RouteFamily.INDONESIA_EC_INDIA``
  -- the strongest of any route family, by a wide margin (every other family
  has zero direct hits).
- It is still not used to compute an applied basis, for two independent,
  disclosed reasons: (1) it is denominated in USD/tonne, not USD/day --
  ``BasisEntry.basis_mean``/``basis_std`` are multiplicative adjustments on a
  $/day TC rate, and converting a voyage $/tonne rate to a $/day TCE
  requires a chain of further assumptions this module does not have real
  evidence for (cargo quantity, full voyage duration including the ballast
  leg to the load port, bunker cost) -- stacking that many assumptions on
  top of an already-thin n=3 sample crosses from "modelled" into "invented";
  (2) n=3 is below ``MIN_ROUTE_OBS`` (see its own docstring for why 5, not a
  different number).

**Given no route family currently clears the bar, every route resolves to
ROUTE_RATE_BASIS_UNAVAILABLE today** -- the honest branch, not a failure of
this module. The VALIDATED and MODELLED code paths are real and tested (see
tests/opt/test_basis.py), not dead scaffolding: either activates
automatically, with no code change, the moment real $/day-denominated
route-level evidence for a family reaches the relevant sample size -- a
future Signal weekly issue reporting an EC-India route in $/day terms, for
instance, would clear MODELLED (or VALIDATED) on the next call.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Final

import numpy as np
import polars as pl

from opt.network import RouteFamily
from opt.types import BasisEntry

__all__ = [
    "MIN_ROUTE_OBS",
    "ROUTE_FAMILY_TO_SIGNAL_SERIES",
    "RouteBasisResult",
    "RouteEvidence",
    "RouteRateObservation",
    "basis_table_to_entries",
    "build_route_basis_table",
]

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
MASTER_LONG_PATH: Final[Path] = REPO_ROOT / "src" / "data" / "master_long.parquet"

#: Minimum real observations before a route-level statistic is trusted enough
#: to APPLY (VALIDATED). Not invented for this module: reuses
#: ``tonnage.supplycurve.fit_supply_curve``'s own established bar (it raises
#: ``NoOverlapError`` below 5 real joined rows) -- the same codebase, the
#: same discipline, not a different threshold picked to make one route pass.
MIN_ROUTE_OBS: Final[int] = 5

#: Below MIN_ROUTE_OBS, at least one real $/day-denominated observation is
#: still enough to attempt a disclosed MODELLED estimate (with materially
#: wider uncertainty -- see ``build_route_basis_table``). Zero real
#: observations is always ROUTE_RATE_BASIS_UNAVAILABLE.
MIN_ROUTE_OBS_MODELLED: Final[int] = 1


class RouteEvidence(str, Enum):
    """Per the P4 API contract, verbatim."""

    OBSERVED = "OBSERVED"
    MODELLED = "MODELLED"
    ROUTE_RATE_BASIS_UNAVAILABLE = "ROUTE_RATE_BASIS_UNAVAILABLE"


#: RouteFamily -> the real series_id(s) that are genuinely this route family's
#: own rate (same origin region AND EC-India as the named destination in the
#: source -- never a same-origin, different-destination series treated as if it
#: were the same route; that would be exactly the kind of proxy substitution
#: the P4 prompt forbids). Investigated directly against every real row in
#: raw_data/signal_weekly/*.extraction.csv -- families absent here were checked
#: and found to have zero real direct hits, not merely unchecked.
#:
#: Two publishers now appear here, and the prefix says which:
#:
#: - ``SG_`` -- Signal weekly report extractions.
#: - ``HB_`` -- handybulk's daily indicative charter levels, harvested by
#:   ``data_builders.harvest_route_rates``. These are the first $/day-
#:   denominated route observations this project has ever had, which is what
#:   the module docstring above was waiting for; the ``_USD_T`` Signal series
#:   beside them still cannot be converted and still does not feed a basis.
#:
#: A publisher is never collapsed into another's namespace: the prefix is how
#: a reader of a basis result can tell whose assessment moved a number.
ROUTE_FAMILY_TO_SIGNAL_SERIES: Final[dict[RouteFamily, tuple[str, ...]]] = {
    RouteFamily.INDONESIA_EC_INDIA: (
        "SG_SUPRAMAX_INDONESIA_ECI_USD_T",
        "HB_SUPRAMAX_INDONESIA_ECI_USD_DAY",
        "HB_PANAMAX_INDONESIA_ECI_USD_DAY",
        "HB_HANDYSIZE_INDONESIA_ECI_USD_DAY",
        "HB_CAPESIZE_INDONESIA_ECI_USD_DAY",
    ),
    # Registered but empty until the source actually quotes an EC-India lane
    # for them. Listing the series ids a family WOULD use is not the same as
    # claiming it has data: `_real_observations` returns nothing for a series
    # that is not in master_long, so an unpublished lane resolves to
    # ROUTE_RATE_BASIS_UNAVAILABLE exactly as before.
    RouteFamily.AUSTRALIA_EC_INDIA: (
        "HB_SUPRAMAX_AUSTRALIA_ECI_USD_DAY",
        "HB_PANAMAX_AUSTRALIA_ECI_USD_DAY",
        "HB_HANDYSIZE_AUSTRALIA_ECI_USD_DAY",
        "HB_CAPESIZE_AUSTRALIA_ECI_USD_DAY",
    ),
    RouteFamily.SOUTH_AFRICA_EC_INDIA: (
        "HB_SUPRAMAX_SOUTH_AFRICA_ECI_USD_DAY",
        "HB_PANAMAX_SOUTH_AFRICA_ECI_USD_DAY",
        "HB_HANDYSIZE_SOUTH_AFRICA_ECI_USD_DAY",
        "HB_CAPESIZE_SOUTH_AFRICA_ECI_USD_DAY",
    ),
    RouteFamily.MOZAMBIQUE_EC_INDIA: (
        "HB_SUPRAMAX_MOZAMBIQUE_ECI_USD_DAY",
        "HB_PANAMAX_MOZAMBIQUE_ECI_USD_DAY",
        "HB_HANDYSIZE_MOZAMBIQUE_ECI_USD_DAY",
        "HB_CAPESIZE_MOZAMBIQUE_ECI_USD_DAY",
    ),
    RouteFamily.US_EC_INDIA: (
        "HB_SUPRAMAX_US_ECI_USD_DAY",
        "HB_PANAMAX_US_ECI_USD_DAY",
        "HB_HANDYSIZE_US_ECI_USD_DAY",
        "HB_CAPESIZE_US_ECI_USD_DAY",
    ),
    RouteFamily.SINGAPORE_EC_INDIA: (
        "HB_SUPRAMAX_SINGAPORE_ECI_USD_DAY",
        "HB_PANAMAX_SINGAPORE_ECI_USD_DAY",
        "HB_HANDYSIZE_SINGAPORE_ECI_USD_DAY",
        "HB_CAPESIZE_SINGAPORE_ECI_USD_DAY",
    ),
    RouteFamily.RUSSIA_EC_INDIA: (
        "HB_SUPRAMAX_RUSSIA_ECI_USD_DAY",
        "HB_PANAMAX_RUSSIA_ECI_USD_DAY",
        "HB_HANDYSIZE_RUSSIA_ECI_USD_DAY",
        "HB_CAPESIZE_RUSSIA_ECI_USD_DAY",
    ),
    # Coastal repositioning within EC-India. The source does not quote it and
    # is unlikely to; left empty rather than given ids that could never fill.
    RouteFamily.INTRA_EC_INDIA: (),
}

#: Series-id suffix marking a $/day (TC-equivalent) unit, as
#: data_builders.build_master.read_signal_dir constructs it -- the only unit
#: this module will convert into a BasisEntry adjustment without a further
#: physical-conversion assumption chain.
_USD_DAY_SUFFIX: Final[str] = "_USD_DAY"


@dataclass(frozen=True)
class RouteRateObservation:
    date: date
    series_id: str
    value: float
    unit: str  # "usd/day" or "usd/t", read straight from master_long


@dataclass(frozen=True)
class RouteBasisResult:
    route_family: RouteFamily
    evidence: RouteEvidence
    basis_entry: BasisEntry | None
    n_real_observations: int
    raw_observations: tuple[RouteRateObservation, ...]
    reason: str


def _real_observations(master: pl.DataFrame, series_ids: tuple[str, ...], as_of: date | None) -> list[RouteRateObservation]:
    if not series_ids:
        return []
    sub = master.filter(pl.col("series_id").is_in(list(series_ids)))
    if as_of is not None:
        sub = sub.filter(pl.col("date") <= as_of)
    return [
        RouteRateObservation(date=row["date"], series_id=row["series_id"], value=row["value"], unit=row["unit"])
        for row in sub.sort("date").iter_rows(named=True)
    ]


def _class_benchmark_series(master: pl.DataFrame, series_id: str) -> str | None:
    """The class TCAVG series_id to compare a $/day route observation against.

    Inferred from the vessel-class code the series carries after its publisher
    prefix -- Signal's own route codes (P, S, C, HS) and handybulk's spelled-out
    class names both resolve here, because both begin with the same letters.
    Stripping either prefix first is what makes that true: without it an
    ``HB_``-prefixed series falls through to None and silently never produces a
    basis, which looks exactly like "no evidence" from the outside.
    """
    prefix = series_id.removeprefix("SG_").removeprefix("HB_")

    # Spelled-out class names first, because Signal's short codes would
    # mis-claim them. "HANDYSIZE" begins "HA", not the "HS" Signal uses, so it
    # fell through every branch below and returned None -- and a None here is
    # indistinguishable from "no evidence" at the call site, so a whole class
    # of route observations would have been silently discarded rather than
    # producing a basis. Found by tracing an HB_ series through this function
    # by hand rather than by anything failing.
    for name, tcavg in (
        ("HANDYSIZE", "HANDYSIZE_TCAVG"),
        ("SUPRAMAX", "SUPRAMAX_TCAVG"),
        ("PANAMAX", "PANAMAX_TCAVG"),
        ("CAPESIZE", "CAPESIZE_TCAVG"),
    ):
        if prefix.startswith(f"{name}_"):
            # SUPRAMAX_USG is a Signal series for a different destination and
            # is excluded below by the same rule it always was.
            if prefix.startswith("SUPRAMAX_USG"):
                return None
            return tcavg

    # Signal's own single-letter route codes (P2A_82, S10TC, C5, HS3_38 ...).
    if prefix.startswith("HS"):
        return "HANDYSIZE_TCAVG"
    if prefix.startswith("P"):
        return "PANAMAX_TCAVG"
    if prefix.startswith("S") and not prefix.startswith("SUPRAMAX_USG"):
        return "SUPRAMAX_TCAVG"
    if prefix.startswith("C"):
        return "CAPESIZE_TCAVG"
    return None


def _fit_basis(
    master: pl.DataFrame, observations: list[RouteRateObservation]
) -> tuple[float, float, int] | None:
    """Real (basis_mean, basis_std, n) from route $/day observations vs. the
    contemporaneous class TCAVG, as-of joined (backward) -- the same
    as-of-join discipline ``tonnage.supplycurve`` uses throughout. None if no
    usable class benchmark exists for these observations' class(es)."""
    ratios: list[float] = []
    for obs in observations:
        if not obs.series_id.endswith(_USD_DAY_SUFFIX):
            continue
        tc_id = _class_benchmark_series(master, obs.series_id)
        if tc_id is None:
            continue
        tc = master.filter((pl.col("series_id") == tc_id) & (pl.col("date") <= obs.date)).sort("date")
        if tc.is_empty():
            continue
        tc_value = float(tc["value"][-1])
        if tc_value <= 0:
            continue
        ratios.append((obs.value - tc_value) / tc_value)
    if len(ratios) < MIN_ROUTE_OBS_MODELLED:
        return None
    arr = np.array(ratios)
    mean = float(arr.mean())
    std = float(arr.std(ddof=1)) if len(arr) > 1 else abs(mean)  # single point: no variance estimate, use |mean| as a conservative spread
    return mean, std, len(ratios)


def _resolve_one(master: pl.DataFrame, route_family: RouteFamily, as_of: date | None) -> RouteBasisResult:
    series_ids = ROUTE_FAMILY_TO_SIGNAL_SERIES.get(route_family, ())
    observations = _real_observations(master, series_ids, as_of)
    usd_day_observations = [o for o in observations if o.unit == "usd/day" or o.series_id.endswith(_USD_DAY_SUFFIX)]

    if not observations:
        return RouteBasisResult(
            route_family=route_family, evidence=RouteEvidence.ROUTE_RATE_BASIS_UNAVAILABLE, basis_entry=None,
            n_real_observations=0, raw_observations=(),
            reason=(
                f"No real route-level rate evidence found for {route_family.value} in the Signal "
                "weekly extractions (checked directly against raw_data/signal_weekly/*.extraction.csv "
                "-- see opt.basis.ROUTE_FAMILY_TO_SIGNAL_SERIES). Falling back to the class benchmark."
            ),
        )

    fit = _fit_basis(master, usd_day_observations)
    if fit is None:
        non_day_units = sorted({o.unit for o in observations if not o.series_id.endswith(_USD_DAY_SUFFIX)})
        return RouteBasisResult(
            route_family=route_family, evidence=RouteEvidence.ROUTE_RATE_BASIS_UNAVAILABLE, basis_entry=None,
            n_real_observations=len(observations), raw_observations=tuple(observations),
            reason=(
                f"{len(observations)} real observation(s) exist for {route_family.value} "
                f"({', '.join(f'{o.series_id}={o.value}{o.unit}' for o in observations[:3])}"
                f"{'...' if len(observations) > 3 else ''}), but denominated in "
                f"{', '.join(non_day_units) or 'a non-USD/day unit'}, not USD/day. Converting a voyage "
                "rate in this unit to a $/day TC-equivalent requires cargo-quantity, full-voyage-"
                "duration, and bunker-cost assumptions this module does not have real evidence for -- "
                "not attempted, to avoid stacking assumptions on an already-thin sample. Falling back "
                "to the class benchmark."
            ),
        )

    mean, std, n = fit
    if n >= MIN_ROUTE_OBS:
        evidence = RouteEvidence.OBSERVED
        reason = (
            f"{n} real USD/day route observations for {route_family.value}, vs. the contemporaneous "
            f"class TCAVG benchmark: mean basis {mean:+.1%}, std {std:.1%}."
        )
    else:
        evidence = RouteEvidence.MODELLED
        reason = (
            f"Only {n} real USD/day route observation(s) for {route_family.value} -- below the "
            f"{MIN_ROUTE_OBS}-observation bar for a validated estimate (tonnage.supplycurve's own "
            f"precedent). Modelled estimate: basis {mean:+.1%}, std {std:.1%} -- clearly labelled "
            "MODELLED, not OBSERVED, and carries a small-sample-inflated uncertainty."
        )
    return RouteBasisResult(
        route_family=route_family, evidence=evidence,
        basis_entry=BasisEntry(route_family=route_family, basis_mean=mean, basis_std=std),
        n_real_observations=n, raw_observations=tuple(observations), reason=reason,
    )


def build_route_basis_table(master: pl.DataFrame | None = None, as_of: date | None = None) -> dict[RouteFamily, RouteBasisResult]:
    """The real, evidence-gated route-basis table -- one entry per
    ``RouteFamily``, built once from real data (``master_long.parquet``'s
    ``SG_*`` series), meant to be built once and cached by the caller (P4
    requirement 2 -- see ``opt.quote``'s module-level cache)."""
    master = master if master is not None else pl.read_parquet(MASTER_LONG_PATH)
    return {rf: _resolve_one(master, rf, as_of) for rf in RouteFamily}


def basis_table_to_entries(table: dict[RouteFamily, RouteBasisResult]) -> dict[RouteFamily, BasisEntry]:
    """The subset of the real table that has an APPLIED entry -- exactly what
    ``OptimizerInputs.basis`` (and ``opt.ceiling._apply_basis``) expects.
    Families resolved as ``ROUTE_RATE_BASIS_UNAVAILABLE`` are simply absent,
    which ``opt.ceiling``'s existing ``basis_table.get(route_family)``
    lookup already treats as "use the class benchmark unadjusted" -- no
    change needed there."""
    return {rf: r.basis_entry for rf, r in table.items() if r.basis_entry is not None}
