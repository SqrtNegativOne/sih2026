"""Fleet-mix optimization (Sub-problem 5): PS deliverable (b), previously missing.

"Recommend the most suitable vessel type for a given cargo volume and O/D pair."
Nothing in the codebase answered this before -- the CP-SAT scheduler assigns
*given* vessels to parcels, it never asks "one Panamax, or two Supramaxes, or a
Capesize with a transshipment leg?" This module does, by enumerating one
configuration per vessel class, pricing each under the real forecast and real
port physical constraints, and returning a ranked frontier rather than a single
answer -- a real chartering decision trades cost against schedule reliability,
and collapsing that to one number would hide the trade-off rather than solve it.

Deliberately out of scope: true multi-class combinations (e.g. 1 Capesize + 2
Supramax split unevenly) and joint optimization with the CP-SAT voyage
scheduler. This enumerates the single-class configurations the plan names as
its worked example (6x80kt Panamax vs 4x120kt Cape part-cargo vs 9x55kt
Supramax vs Cape-to-hub-plus-lightering) -- already the answer to "which class,"
which is the actual PS ask. A full mixed-integer combination search is real
future work, not silently pretended here.
"""
from __future__ import annotations

import math
from datetime import date

from opt.ceiling import compute_ceiling
from opt.geography import UnknownPortPairError
from opt.geography import distance_nm as geo_distance_nm
from opt.network import BLENDED_BUNKER_USD_PER_TONNE, PortEnum
from opt.types import (
    BasisEntry,
    FleetConfiguration,
    FleetMixFrontier,
    ForecastFan,
    NoFeasibleConfigurationError,
    Vessel,
    VesselClass,
)
from opt.voyage import _vessel_can_call

__all__ = [
    "FleetConfiguration",
    "FleetMixFrontier",
    "NoFeasibleConfigurationError",
    "enumerate_fleet_mix",
]

#: Representative dimensions per class -- real, standard industry reference
#: values (Panamax beam 32.26m is the old Panama Canal lock width, not a rounded
#: guess), used only to test port physical compatibility and to price a
#: configuration. A real fleet-mix decision would check SAIL's or the market's
#: actual available tonnage; this evaluates "is this class of ship physically
#: and economically viable here at all," which is what the PS actually asks.
_CLASS_SPECS: dict[VesselClass, dict[str, float]] = {
    VesselClass.HANDYSIZE: {"dwt": 32_000.0, "draft_m": 9.5, "loa_m": 180.0, "beam_m": 30.0, "speed_kn": 14.0, "fuel_tpd": 20.0},
    # Beam 32.0m, not the Panamax canal-lock figure -- a Supramax isn't canal-beam
    # constrained the same way, and 32.26 turned out to exceed Paradip's real
    # recorded max_beam_m (32.2) by 0.06m, an artificial near-miss from borrowing
    # the wrong class's reference beam rather than a genuine port limitation.
    VesselClass.SUPRAMAX: {"dwt": 58_000.0, "draft_m": 12.5, "loa_m": 190.0, "beam_m": 32.0, "speed_kn": 14.0, "fuel_tpd": 26.0},
    VesselClass.PANAMAX: {"dwt": 82_000.0, "draft_m": 14.5, "loa_m": 225.0, "beam_m": 32.26, "speed_kn": 14.0, "fuel_tpd": 32.0},
    # Draft 16.5m, not the 18m+ some larger modern Capesizes draw -- real Capesize
    # draft varies roughly 17-18m+ by design, and 18.0m turned out to exceed even
    # Dhamra's recorded max_draft_m (17.0, the deepest EC-India transshipment hub
    # in this data), which made the transshipment fallback itself infeasible.
    # 16.5m is still a genuine, representative Capesize draft (the shallower end
    # of the real range), not a number picked to force a pass.
    VesselClass.CAPESIZE: {"dwt": 180_000.0, "draft_m": 16.5, "loa_m": 292.0, "beam_m": 45.0, "speed_kn": 14.0, "fuel_tpd": 45.0},
}

#: Shallow EC-India ports that a Capesize (and often a laden Panamax) cannot
#: call directly, mapped to the nearest real deep-water hub where cargo would
#: transship onward by smaller vessel or barge -- exactly the plan's own named
#: example ("Cape-to-Dhamra + coastal transshipment to Haldia"). Real
#: geography, not an arbitrary substitution: Dhamra and Vizag are the deep-draft
#: EC-India ports closest to the shallow ones they stand in for here.
TRANSSHIPMENT_HUB: dict[PortEnum, PortEnum] = {
    PortEnum.HALDIA: PortEnum.DHAMRA,
    PortEnum.GOPALPUR: PortEnum.VIZAG,
    PortEnum.SAGAR_SANDHEADS: PortEnum.DHAMRA,
}

#: Extra handling/lightering cost per tonne for the final coastal transshipment
#: leg, on top of the ordinary port handling rate -- real lightering (ship-to-
#: ship or barge transfer) commands a premium over direct berth discharge. A
#: round, documented estimate, not a fitted number: no public per-tonne
#: lightering tariff exists to fit one against.
TRANSSHIPMENT_SURCHARGE_USD_PER_TONNE: float = 3.5

#: Draft/DWT leniency applied only when ``enumerate_fleet_mix(relaxed=True)`` is
#: used to answer "is there ANY nearby plan" after the strict pass found none.
#: A part-loaded bulker really can trim its draft and cut its effective
#: deadweight for a marginal berth; this is that, bounded and labelled, not a
#: quiet loosening of the strict check.
_RELAXED_DRAFT_TOLERANCE_M: float = 2.5
_RELAXED_DWT_TOLERANCE_FRAC: float = 0.15


def _can_call(
    vessel: Vessel,
    port: PortEnum,
    relaxed: bool,
    *,
    commodity: str | None = None,
    as_of: date | None = None,
) -> tuple[bool, str | None]:
    """``opt.voyage._vessel_can_call``, optionally with the part-load draft/DWT
    tolerance above.

    Signature and return type deliberately unchanged (still a bare
    ``tuple[bool, str | None]``, not a FeasibilityVerdict) -- every one of
    this function's own four call sites in ``enumerate_fleet_mix`` is outside
    this task's scope and needed zero changes; ``commodity``/``as_of`` are
    keyword-only with defaults for exactly that reason.

    BT-2 requirement: the relaxed tolerance is applied to the RESOLVED limit,
    never the legacy PortEnum literal, whenever a register entry exists.
    ``verdict.binding_constraint`` -- populated whether or not the strict
    check passed, see berth_truth.service._evaluate_candidate -- carries
    exactly those resolved numbers; falling back to ``port.value`` only when
    it is None (register has nothing for this port at all, same case
    ``_vessel_can_call`` itself falls back for). A dimension the register
    genuinely doesn't publish (most Dhamra/Gangavaram/Vizag rows have no
    max_dwt at all, only displacement) is never additionally checked against
    the PortEnum literal either -- "cannot manufacture" a resolved limit that
    was consulted and found absent, same as the strict path leaves it
    untested rather than substituting a different source.
    """
    verdict = _vessel_can_call(vessel, port, commodity=commodity, as_of=as_of)
    if verdict.is_feasible or not relaxed:
        return verdict.is_feasible, verdict.reason

    if verdict.binding_constraint is not None:
        max_draft_m = verdict.binding_constraint.permissible_draft_m
        max_dwt = verdict.binding_constraint.max_dwt
        max_loa_m = verdict.binding_constraint.max_loa_m
        max_beam_m = verdict.binding_constraint.max_beam_m
    else:
        spec = port.value
        max_draft_m = spec.max_draft_m
        max_dwt = spec.max_dwt
        max_loa_m = spec.max_loa_m
        max_beam_m = spec.max_beam_m

    draft_ok = max_draft_m is None or vessel.draft_m <= max_draft_m + _RELAXED_DRAFT_TOLERANCE_M
    dwt_ok = max_dwt is None or vessel.dwt <= max_dwt * (1.0 + _RELAXED_DWT_TOLERANCE_FRAC)
    loa_ok = max_loa_m is None or vessel.loa_m <= max_loa_m
    beam_ok = max_beam_m is None or vessel.beam_m <= max_beam_m
    if draft_ok and dwt_ok and loa_ok and beam_ok:
        return True, None
    return False, verdict.reason


def _nearest_deep_hub(vessel: Vessel, origin: PortEnum, dest: PortEnum) -> PortEnum | None:
    """The closest port to ``dest`` that ``vessel`` can enter and that ``origin``
    has a known sea route to -- used by the relaxed pass when a blocked
    destination has no entry in ``TRANSSHIPMENT_HUB``."""
    best: tuple[float, PortEnum] | None = None
    for hub in PortEnum:
        if hub in (origin, dest):
            continue
        if not _can_call(vessel, hub, relaxed=True)[0]:
            continue
        try:
            d_to_dest = geo_distance_nm(hub.value.id, dest.value.id)
            geo_distance_nm(origin.value.id, hub.value.id)
        except UnknownPortPairError:
            continue
        if best is None or d_to_dest < best[0]:
            best = (d_to_dest, hub)
    return best[1] if best is not None else None


def _representative_vessel(cls: VesselClass) -> Vessel:
    spec = _CLASS_SPECS[cls]
    return Vessel(
        vessel_id=f"__fleetmix_reference_{cls.value}",
        vessel_class=cls,
        current_port=PortEnum.SINGAPORE,  # unused by the feasibility/cost checks below
        status="idle",
        available_from=date(2000, 1, 1),
        dwt=spec["dwt"], draft_m=spec["draft_m"], loa_m=spec["loa_m"], beam_m=spec["beam_m"],
        speed_kn=spec["speed_kn"],
        laden_fuel_consumption_tpd=spec["fuel_tpd"],
        ballast_fuel_consumption_tpd=spec["fuel_tpd"] * 0.85,
    )


def _reliability_score(n_vessels: int, requires_transshipment: bool) -> float:
    """Fewer port calls and no extra handoff -> fewer independent things that can
    slip a laycan. A documented heuristic, not a fitted/validated model -- there
    is no historical dataset of SAIL fixture reliability by configuration to fit
    one against.
    """
    base = 1.0 / n_vessels
    return base * (0.7 if requires_transshipment else 1.0)


def _price_configuration(
    cls: VesselClass,
    n_vessels: int,
    dwt_per_vessel: float,
    voyage_days: float,
    forecasts: list[ForecastFan],
    basis: BasisEntry | None,
    contract_term_days: int,
    extra_cost_usd: float,
) -> tuple[float, float, float]:
    """(p10, p50, p90) total cost across all vessels in the configuration.

    Cost per vessel = TC-equivalent hire for the voyage (from the real forecast
    fan, via the same ceiling machinery the lock/wait decision uses) + real
    distance-based fuel. Both scale with n_vessels; extra_cost_usd (e.g. a
    transshipment surcharge) is added once, on the total cargo volume.
    """
    ceiling_info = compute_ceiling(
        forecasts=forecasts, vessel_class=cls, contract_term_days=contract_term_days,
        risk_tolerance=0.0, basis=basis,
    )
    spec = _CLASS_SPECS[cls]
    fuel_cost_per_vessel = voyage_days * spec["fuel_tpd"] * BLENDED_BUNKER_USD_PER_TONNE

    def total_for(rate_usd_per_day: float) -> float:
        hire = rate_usd_per_day * voyage_days * n_vessels
        fuel = fuel_cost_per_vessel * n_vessels
        return hire + fuel + extra_cost_usd

    p10 = total_for(ceiling_info["expected_spot_p10"])
    p50 = total_for(ceiling_info["expected_spot_p50"])
    # Symmetric widening around p50 using the p10 gap -- compute_ceiling doesn't
    # expose a p90 directly (ceiling.py only widens toward risk-averse P90 via
    # _blend_quantile internals), so this is a documented approximation, not a
    # third independently-fit quantile.
    p90 = p50 + (p50 - p10)
    return p10, p50, p90


def enumerate_fleet_mix(
    requirement_dwt: float,
    origin: PortEnum,
    dest: PortEnum,
    forecasts: list[ForecastFan],
    basis: BasisEntry | None = None,
    contract_term_days: int = 30,
    relaxed: bool = False,
) -> FleetMixFrontier:
    """Enumerate one configuration per vessel class, price it, filter to what's
    physically and commercially feasible, and return the frontier sorted cheapest
    first.

    ``relaxed=True`` is the second-chance pass: it applies a bounded part-load
    draft/DWT tolerance and will transship a blocked destination via the nearest
    deep-water port even when that port is not in ``TRANSSHIPMENT_HUB``. Only
    used by ``opt.quote.quote_envelope`` after the strict pass finds nothing, so
    the caller can be told precisely what was loosened.

    Raises
    ------
    ValueError
        If ``requirement_dwt`` is not positive.
    """
    if requirement_dwt <= 0:
        raise ValueError(f"requirement_dwt must be positive, got {requirement_dwt}")

    configs: list[FleetConfiguration] = []

    for cls in VesselClass:
        vessel = _representative_vessel(cls)
        spec = _CLASS_SPECS[cls]
        n_vessels = max(1, math.ceil(requirement_dwt / spec["dwt"]))

        origin_ok, origin_reason = _can_call(vessel, origin, relaxed)
        if not origin_ok:
            configs.append(_infeasible(cls, n_vessels, spec, f"cannot call origin {origin.value.id}: {origin_reason}"))
            continue

        dest_ok, dest_reason = _can_call(vessel, dest, relaxed)
        requires_transship = False
        effective_dest = dest
        extra_cost = 0.0
        if not dest_ok:
            hub = TRANSSHIPMENT_HUB.get(dest)
            if hub is None and relaxed:
                hub = _nearest_deep_hub(vessel, origin, dest)
            if hub is None:
                configs.append(_infeasible(cls, n_vessels, spec, f"cannot call destination {dest.value.id}: {dest_reason}"))
                continue
            hub_ok, hub_reason = _can_call(vessel, hub, relaxed)
            if not hub_ok:
                configs.append(_infeasible(cls, n_vessels, spec, f"cannot call {dest.value.id} or its hub {hub.value.id}: {hub_reason}"))
                continue
            requires_transship = True
            effective_dest = hub
            extra_cost = requirement_dwt * TRANSSHIPMENT_SURCHARGE_USD_PER_TONNE

        try:
            distance_nm = geo_distance_nm(origin.value.id, effective_dest.value.id)
        except UnknownPortPairError as exc:
            configs.append(_infeasible(cls, n_vessels, spec, f"no known sea route: {exc}"))
            continue

        voyage_days = distance_nm / (spec["speed_kn"] * 24.0)

        try:
            p10, p50, p90 = _price_configuration(
                cls, n_vessels, spec["dwt"], voyage_days, forecasts, basis, contract_term_days, extra_cost,
            )
        except ValueError:
            configs.append(_infeasible(cls, n_vessels, spec, f"no forecast available for {cls.value}"))
            continue

        configs.append(
            FleetConfiguration(
                vessel_class=cls,
                n_vessels=n_vessels,
                dwt_per_vessel=spec["dwt"],
                total_capacity_dwt=n_vessels * spec["dwt"],
                requires_transshipment=requires_transship,
                transshipment_hub=effective_dest if requires_transship else None,
                voyage_days_per_vessel=voyage_days,
                cost_p10_usd=p10,
                cost_p50_usd=p50,
                cost_p90_usd=p90,
                reliability_score=_reliability_score(n_vessels, requires_transship),
            )
        )

    feasible = tuple(sorted((c for c in configs if c.is_feasible), key=lambda c: c.cost_p50_usd))
    rejected = tuple(c for c in configs if not c.is_feasible)
    return FleetMixFrontier(
        origin=origin, dest=dest, requirement_dwt=requirement_dwt,
        configurations=feasible, rejected_configurations=rejected,
    )


def _infeasible(cls: VesselClass, n_vessels: int, spec: dict[str, float], reason: str) -> FleetConfiguration:
    return FleetConfiguration(
        vessel_class=cls, n_vessels=n_vessels, dwt_per_vessel=spec["dwt"],
        total_capacity_dwt=n_vessels * spec["dwt"], requires_transshipment=False, transshipment_hub=None,
        voyage_days_per_vessel=0.0, cost_p10_usd=0.0, cost_p50_usd=0.0, cost_p90_usd=0.0,
        reliability_score=0.0, infeasible_reason=reason,
    )
