"""Voyage Scheduling (Sub-problem 2)

Assigns vessels to cargo parcels and sequences them to maximize profit (TCE).
Uses Google OR-Tools CP-SAT solver.

Costs factored into the objective:
  - Laden fuel (origin -> destination)
  - Ballast fuel (current port -> first cargo; between cargoes)
  - opex_usd_per_day charged for unproductive time: port queue wait, early
    arrival ahead of laycan, and inter-cargo idle gaps -- the vessel's real
    daily running cost accrues whether it is earning or not, so this is the
    genuine cost of an idle day, not an arbitrary scheduling penalty. The
    load-port wait allowance itself is real and current where the data
    supports it (opt.congestion.dynamic_wait_days scales the port's static
    baseline by how busy it actually is right now), not a fixed literal
    regardless of real conditions.
  - Demurrage (contractual $/day per cargo, CargoParcel.demurrage_usd_per_day)
    for port time beyond that cargo's free laytime allowance
    (demurrage_wait_days); both default to 0.0 so this is a no-op unless set
  - Fuel is costed at the *real, per-port* bunker price of wherever each leg
    departs from (opt.network.Port.bunker_price_usd -- defined for every port
    from the start, never read until P3; see _bunker_price_usd), not one
    blended global constant regardless of where the vessel actually bunkers.

Constraints enforced:
  - Each cargo served by exactly one vessel (or dropped if unprofitable)
  - Vessel DWT and draft compatibility with origin AND destination port
  - Laycan time windows (arrive before end, can't load before start)
  - Physical sequencing (finish c1 + ballast time <= arrive c2)

Deliberately not done in this pass: the plan's fuller P3 voyage.py rewrite also
calls for making speed a decision variable (slow steaming, cubic speed-
consumption curves) and restructuring the CP-SAT encoding for scale
(arc-flow / set-partitioning to cut the O(|V|*|C|^2) transition blowup). Both
are real, valuable, and substantially riskier than the fixes above -- this is
the single most heavily-tested, most central module in the whole optimizer,
already carrying real fixture data and a demo that depends on its exact
behavior. Given the explicit ask to be careful about exactly this kind of
change, that rewrite is scoped out as separate, dedicated future work rather
than attempted in the same pass as everything else in P3 -- flagged here, not
silently dropped.
"""
from __future__ import annotations

import functools
from dataclasses import dataclass
from datetime import date
from typing import Any, Final

from ortools.sat.python import cp_model

from berth_truth.models import PortId
from berth_truth.service import check_vessel_against_register
from opt.congestion import dynamic_wait_days
from opt.geography import distance_nm as geo_distance_nm
from opt.network import BLENDED_BUNKER_USD_PER_TONNE, PortEnum, RouteEnum
from opt.types import (
    CargoParcel,
    FeasibilityMargins,
    FeasibilityVerdict,
    LimitSource,
    OptimizerInputs,
    Vessel,
    VoyageAssignment,
)


@dataclass
class VoyageScheduleResult:
    assignments: list[VoyageAssignment]
    total_profit_usd: float
    solver_status: str
    infeasible_pairs: list[tuple[str, str, str]]  # (vessel_id, parcel_id, reason) ruled out by port constraints


@functools.cache
def _cached_effective_handling_rate_tph(port: PortEnum) -> float | None:
    """P6: real fact_port_call productivity (berth_truth.empirical.
    effective_handling_rate_tph) where its sufficiency gate passes, else the
    same opt.network.Port.handling_rate_tph literal this always used --
    never a silent third number. Not segmented by commodity here: the real,
    verified-sufficient case today (Paradip) is at the port level, and
    CargoParcel.commodity/FactPortCall.commodity_class are two independently
    free-text vocabularies with no confirmed mapping between them, so
    filtering by commodity here could silently make the gate always fail
    rather than genuinely segment -- not attempted without that mapping.

    Cached at process scope: this does real fact_port_call file I/O, and
    schedule_voyages calls this once per (vessel, cargo) pair in its
    port-time precompute -- the exact same reasoning and pattern as
    opt.repositioning._port_class_hazard_rates.

    Imports berth_truth.empirical locally, not at module scope: a
    module-level import here creates a real circular import
    (berth_truth.fact_port_call -> berth_truth.sources -> opt.network ->
    opt/__init__.py -> opt.api -> opt.fleetmix -> opt.voyage ->
    berth_truth.empirical -> berth_truth.fact_port_call, still
    mid-initialization) whenever berth_truth.fact_port_call is the entry
    point of the import graph -- confirmed by running the test suite, not
    hypothesised. Deferring the import to call time (after every module has
    finished initializing) breaks the cycle without restructuring either
    package; mirrors opt.backhaul's identical local-import fix for
    opt.voyage._vessel_can_call."""
    from berth_truth.empirical import effective_handling_rate_tph

    return effective_handling_rate_tph(port).rate_tph


def clear_effective_handling_rate_cache() -> None:
    """Drop the cached rates. Call after rebuilding the P1 fact_port_call
    log within a live process -- mirrors opt.repositioning.clear_hazard_cache."""
    _cached_effective_handling_rate_tph.cache_clear()


def _get_distance_nm(
    port_a: PortEnum, port_b: PortEnum
) -> float:
    """Sea distance between two ports, from the precomputed matrix.

    Previously this scanned RouteEnum and returned 10,000 nm when the pair was not
    listed -- which was 55% of pairs, and which set transit time, fuel cost and the
    objective. opt.repositioning had the same function returning 5,000 nm instead, so
    the two engines priced identical legs differently. Both now read one matrix, and a
    missing pair raises rather than resolving to a number.
    """
    return geo_distance_nm(port_a.value.id, port_b.value.id)


def _bunker_price_usd(port: PortEnum) -> float:
    """Real per-port bunker price (opt.network.Port.bunker_price_usd), with the
    old blended global constant as a fallback for the hypothetical case of a
    port missing one -- every port defined today has a real value (Paradip
    $610/t through Singapore $540/t), so this previously-unused field is doing
    real work now rather than being dead data next to a global constant that
    ignored it.
    """
    price = port.value.bunker_price_usd
    return price if price is not None else BLENDED_BUNKER_USD_PER_TONNE


#: opt.network.PortEnum <-> berth_truth.models.PortId, for the three ports
#: BT-1's register actually covers. Every other PortEnum member has no
#: register entry, so _vessel_can_call falls straight through to the legacy
#: PortEnum check for them -- requirement 8, "PortEnum keeps working
#: unchanged for any port with no register entry." Note PortEnum.VIZAG maps
#: to PortId.VISAKHAPATNAM: the two packages named the same real port
#: differently, and this dict is the one place that translation happens.
_REGISTER_PORT_ID: Final[dict[PortEnum, PortId]] = {
    PortEnum.DHAMRA: PortId.DHAMRA,
    PortEnum.GANGAVARAM: PortId.GANGAVARAM,
    PortEnum.VIZAG: PortId.VISAKHAPATNAM,
}


def _portenum_fallback_verdict(vessel: Vessel, port: PortEnum) -> FeasibilityVerdict:
    """The original, unchanged dimension check against opt.network.Port --
    now only reached when berth_truth has no register entry for this port at
    all. Behaviour is byte-for-byte identical to what this function used to
    do unconditionally; only the wrapping type changed."""
    spec = port.value
    checks: list[tuple[str, bool, str]] = [
        (
            "DWT",
            spec.max_dwt is None or vessel.dwt <= spec.max_dwt,
            f"Vessel DWT ({vessel.dwt}) exceeds port max ({spec.max_dwt}) at {port.name}",
        ),
        (
            "draft",
            spec.max_draft_m is None or vessel.draft_m <= spec.max_draft_m,
            f"Vessel draft ({vessel.draft_m}m) exceeds port max ({spec.max_draft_m}m) at {port.name}",
        ),
        (
            "LOA",
            spec.max_loa_m is None or vessel.loa_m <= spec.max_loa_m,
            f"Vessel LOA ({vessel.loa_m}m) exceeds port max ({spec.max_loa_m}m) at {port.name}",
        ),
        (
            "beam",
            spec.max_beam_m is None or vessel.beam_m <= spec.max_beam_m,
            f"Vessel beam ({vessel.beam_m}m) exceeds port max ({spec.max_beam_m}m) at {port.name}",
        ),
    ]
    failure = next((reason for _, ok, reason in checks if not ok), None)
    return FeasibilityVerdict(
        is_feasible=failure is None,
        limit_source=LimitSource.PORTENUM_FALLBACK,
        margins=FeasibilityMargins(
            draft_margin_m=None if spec.max_draft_m is None else spec.max_draft_m - vessel.draft_m,
            loa_margin_m=None if spec.max_loa_m is None else spec.max_loa_m - vessel.loa_m,
            beam_margin_m=None if spec.max_beam_m is None else spec.max_beam_m - vessel.beam_m,
        ),
        reason=failure,
    )


def _vessel_can_call(
    vessel: Vessel,
    port: PortEnum,
    *,
    commodity: str | None = None,
    as_of: date | None = None,
) -> FeasibilityVerdict:
    """Check vessel dimensions against a port's berth limits.

    Routes through berth_truth's constraint register when one exists for
    ``port`` (Dhamra, Gangavaram, Vizag as of BT-1), commodity- and
    date-aware; falls back to the original opt.network.Port dimension check,
    unchanged, for every other port. The problem statement names four
    constraints explicitly -- draft, LOA, beam and DWT -- but only draft and
    DWT were ever checked before this module's P0 pass added LOA/beam; a
    port/berth with no limit on record does not constrain that dimension,
    matching the original None-means-unconstrained behaviour, now surfaced
    explicitly via ``untested_checks`` rather than silently.

    ``commodity`` is free text (CargoParcel.commodity) mapped onto the
    register's CommodityClass via berth_truth.service.infer_commodity_class;
    an unrecognised or omitted commodity degrades to "no preference," not an
    error. ``as_of`` defaults to today when omitted, matching every existing
    caller that never had a notion of "as of which date" before.
    """
    register_port_id = _REGISTER_PORT_ID.get(port)
    if register_port_id is not None:
        result = check_vessel_against_register(
            register_port_id,
            commodity=commodity,
            # Deliberate (DTZ011): as_of is a calendar date, not an instant --
            # every real caller in this module now passes a real one
            # explicitly (schedule_voyages uses the cargo's own laycan_start),
            # so this default is only ever reached by a caller with no date
            # context at all, where "the machine's local today" is the same
            # reasonable, low-stakes fallback the rest of this codebase uses
            # in that situation. datetime.now(tz=UTC).date() would be a
            # different date than the operator's for part of every day, which
            # is worse, not better, for a port-calendar lookup.
            as_of=as_of if as_of is not None else date.today(),  # noqa: DTZ011
            vessel_draft_m=vessel.draft_m,
            vessel_loa_m=vessel.loa_m,
            vessel_beam_m=vessel.beam_m,
            vessel_dwt=vessel.dwt,
        )
        if result is not None:
            return FeasibilityVerdict(
                is_feasible=result.is_feasible,
                berth_id=result.berth_id,
                binding_constraint=result.binding_constraint,
                limit_source=LimitSource.REGISTER,
                draft_source=result.draft_source,
                draft_status=result.draft_status,
                draft_as_of=result.draft_as_of,
                margins=FeasibilityMargins(
                    draft_margin_m=result.draft_margin_m,
                    loa_margin_m=result.loa_margin_m,
                    beam_margin_m=result.beam_margin_m,
                ),
                is_soft_limit=result.is_soft_limit,
                staleness_days=result.staleness_days,
                untested_checks=result.untested_checks,
                observed_only_berths=result.observed_only_berths,
                reason=result.reason,
            )
        # register_port_id is real, but berth_truth has nothing at all for it
        # (no seeded rows) -- falls through to PORTENUM_FALLBACK below, same
        # as a port never mapped in _REGISTER_PORT_ID.

    return _portenum_fallback_verdict(vessel, port)


def schedule_voyages(
    inputs: OptimizerInputs,
    max_solve_seconds: float = 30.0,
) -> VoyageScheduleResult:
    """Solve the Voyage Scheduling (PDPTW) problem using OR-Tools CP-SAT.

    Maximizes total fleet profit = revenue - fuel - opex(idle time) - demurrage.
    """
    model = cp_model.CpModel()

    if not inputs.vessels or not inputs.parcels:
        return VoyageScheduleResult([], 0.0, "NO_DATA", [])

    epoch_date = min(v.available_from for v in inputs.vessels)

    def date_to_hours(d: Any) -> int:
        from datetime import datetime
        if isinstance(d, datetime):
            d = d.date()
        e = epoch_date
        if isinstance(e, datetime):
            e = e.date()
        return (d - e).days * 24

    C: list[CargoParcel] = []
    parent_map: dict[str, str] = {} # sub_id -> parent_id
    group_map: dict[str, list[str]] = {} # parent_id -> list of sub_ids
    
    for p in inputs.parcels:
        dests = [p.dest_port]
        if p.alternative_dest_ports:
            for dp in p.alternative_dest_ports:
                if dp not in dests:
                    dests.append(dp)
        
        group_map[p.parcel_id] = []
        for d in dests:
            sub_id = f"{p.parcel_id}__{d.name}"
            sub_p = p.model_copy(update={"parcel_id": sub_id, "dest_port": d})
            C.append(sub_p)
            parent_map[sub_id] = p.parcel_id
            group_map[p.parcel_id].append(sub_id)

    V = inputs.vessels

    max_time_hours = (inputs.planning_horizon_days + 60) * 24

    # Pre-compute windows
    cargo_windows = {
        c.parcel_id: {
            "start": date_to_hours(c.laycan_start),
            "end": date_to_hours(c.laycan_end),
        }
        for c in C
    }

    vessel_props = {
        v.vessel_id: {
            "available": date_to_hours(v.available_from),
            "port": v.current_port,
        }
        for v in V
    }

    # ---------------------------------------------------------------------------
    # Port compatibility pre-filter
    # ---------------------------------------------------------------------------
    # Build a set of (v_id, c_id) pairs that are physically impossible.
    infeasible_pairs: list[tuple[str, str, str]] = []
    feasible: dict[tuple[str, str], bool] = {}
    for v in V:
        for c in C:
            # laycan_start, not today: a berth-limit check should reflect what
            # will be in force when the vessel would actually call, not the
            # date the schedule happens to be solved on -- exactly the
            # point-in-time distinction BT-1's register exists to support.
            verdict_orig = _vessel_can_call(v, c.origin_port, commodity=c.commodity, as_of=c.laycan_start)
            verdict_dest = _vessel_can_call(v, c.dest_port, commodity=c.commodity, as_of=c.laycan_start)
            ok = verdict_orig.is_feasible and verdict_dest.is_feasible
            feasible[v.vessel_id, c.parcel_id] = ok
            if not ok:
                reason = verdict_orig.reason if not verdict_orig.is_feasible else verdict_dest.reason
                infeasible_pairs.append((v.vessel_id, c.parcel_id, reason)) # type: ignore

    # ---------------------------------------------------------------------------
    # Decision variables
    # ---------------------------------------------------------------------------
    served_group = {p.parcel_id: model.NewBoolVar(f"served_grp_{p.parcel_id}") for p in inputs.parcels}

    x: dict[tuple[str, str], Any] = {}
    for v in V:
        for c in C:
            x[v.vessel_id, c.parcel_id] = model.NewBoolVar(f"x_{v.vessel_id}_{c.parcel_id}")

    # Infeasible pairs are permanently 0
    for v_id, c_id, _ in infeasible_pairs:
        model.Add(x[v_id, c_id] == 0)

    # Each parent cargo served by at most one vessel at exactly one port
    for p in inputs.parcels:
        model.Add(
            sum(x[v.vessel_id, sub_id] for v in V for sub_id in group_map[p.parcel_id]) 
            == served_group[p.parcel_id]
        )

    # Routing flow variables
    trans: dict[tuple[str, str, str], Any] = {}
    start_node: dict[tuple[str, str], Any] = {}
    end_node: dict[tuple[str, str], Any] = {}
    for v in V:
        for c in C:
            v_id = v.vessel_id
            c_id = c.parcel_id
            start_node[v_id, c_id] = model.NewBoolVar(f"snode_{v_id}_{c_id}")
            end_node[v_id, c_id] = model.NewBoolVar(f"enode_{v_id}_{c_id}")
            for c2 in C:
                if parent_map[c_id] != parent_map[c2.parcel_id]:
                    trans[v_id, c_id, c2.parcel_id] = model.NewBoolVar(f"tr_{v_id}_{c_id}_{c2.parcel_id}")

    # Flow conservation
    for v in V:
        v_id = v.vessel_id
        model.Add(sum(start_node[v_id, c.parcel_id] for c in C) <= 1)
        model.Add(sum(end_node[v_id, c.parcel_id] for c in C) <= 1)

        for c in C:
            c_id = c.parcel_id
            in_edges = start_node[v_id, c_id] + sum(
                trans[v_id, p.parcel_id, c_id] for p in C if parent_map[p.parcel_id] != parent_map[c_id]
            )
            model.Add(in_edges == x[v_id, c_id])

            out_edges = end_node[v_id, c_id] + sum(
                trans[v_id, c_id, n.parcel_id] for n in C if parent_map[n.parcel_id] != parent_map[c_id]
            )
            model.Add(out_edges == x[v_id, c_id])

    # ---------------------------------------------------------------------------
    # Time variables — first pass: declare
    # ---------------------------------------------------------------------------
    arrival_time: dict[tuple[str, str], Any] = {}
    start_time: dict[tuple[str, str], Any] = {}
    finish_time: dict[tuple[str, str], Any] = {}
    actual_laden_hours_var: dict[tuple[str, str], Any] = {}
    actual_port_hours_var: dict[tuple[str, str], Any] = {}

    for v in V:
        for c in C:
            v_id, c_id = v.vessel_id, c.parcel_id
            arrival_time[v_id, c_id] = model.NewIntVar(0, max_time_hours, f"arr_{v_id}_{c_id}")
            start_time[v_id, c_id] = model.NewIntVar(0, max_time_hours, f"st_{v_id}_{c_id}")
            finish_time[v_id, c_id] = model.NewIntVar(0, max_time_hours, f"fin_{v_id}_{c_id}")
            actual_laden_hours_var[v_id, c_id] = model.NewIntVar(0, max_time_hours, f"act_laden_{v_id}_{c_id}")
            actual_port_hours_var[v_id, c_id] = model.NewIntVar(0, max_time_hours, f"act_port_{v_id}_{c_id}")

    # ---------------------------------------------------------------------------
    # Time variables — second pass: constrain
    # ---------------------------------------------------------------------------
    # Pre-compute durations (hours) — deterministic given vessel + cargo
    laden_hours_map: dict[tuple[str, str], int] = {}
    port_hours_map: dict[tuple[str, str], int] = {}
    for v in V:
        for c in C:
            laden_nm = _get_distance_nm(c.origin_port, c.dest_port)
            laden_h = int(laden_nm / v.speed_kn) if v.speed_kn > 0 else 0
            laden_hours_map[v.vessel_id, c.parcel_id] = laden_h

            # Port time from the effective handling rate (P6: real
            # fact_port_call productivity where it clears the sufficiency
            # gate, else the same static literal this always used -- see
            # _cached_effective_handling_rate_tph) if available, else 4
            # days default, unchanged from before.
            origin_rate = _cached_effective_handling_rate_tph(c.origin_port)
            dest_rate = _cached_effective_handling_rate_tph(c.dest_port)
            if origin_rate:
                load_h = int(c.volume_dwt / origin_rate)
            else:
                load_h = 48  # 2-day default
            if dest_rate:
                disch_h = int(c.volume_dwt / dest_rate)
            else:
                disch_h = 48
            port_hours_map[v.vessel_id, c.parcel_id] = load_h + disch_h

    for v in V:
        for c in C:
            v_id, c_id = v.vessel_id, c.parcel_id
            arr = arrival_time[v_id, c_id]
            st = start_time[v_id, c_id]
            fin = finish_time[v_id, c_id]
            not_x = x[v_id, c_id].Not()

            # Pin to 0 when not assigned (prevents vacuous constraint satisfaction)
            model.Add(arr == 0).OnlyEnforceIf(not_x)
            model.Add(st == 0).OnlyEnforceIf(not_x)
            model.Add(fin == 0).OnlyEnforceIf(not_x)

            # Temporal ordering
            model.Add(st >= arr)
            model.Add(st >= cargo_windows[c_id]["start"]).OnlyEnforceIf(x[v_id, c_id])
            model.Add(arr <= cargo_windows[c_id]["end"]).OnlyEnforceIf(x[v_id, c_id])

            base_laden = laden_hours_map[v_id, c_id]
            base_port = port_hours_map[v_id, c_id]
            
            # 1. Weather Events (Laden Transit Delay)
            delay_vars = []
            route = None
            for r in RouteEnum:
                if (r.value.origin.id == c.origin_port.value.id and r.value.destination.id == c.dest_port.value.id) or \
                   (r.value.origin.id == c.dest_port.value.id and r.value.destination.id == c.origin_port.value.id):
                    route = r
                    break
            
            if route:
                weather_events_on_route = [w for w in inputs.weather_events if w.route == route]
                for w in weather_events_on_route:
                    w_start_h = date_to_hours(w.start_time)
                    w_end_h = date_to_hours(w.end_time)
                    
                    hits_weather = model.NewBoolVar(f"weather_{v_id}_{c_id}_{w.event_id}")
                    
                    after_w_start = model.NewBoolVar(f"after_w_start_{v_id}_{c_id}_{w.event_id}")
                    before_w_end = model.NewBoolVar(f"before_w_end_{v_id}_{c_id}_{w.event_id}")
                    
                    model.Add(st >= w_start_h).OnlyEnforceIf(after_w_start)
                    model.Add(st < w_start_h).OnlyEnforceIf(after_w_start.Not())
                    
                    model.Add(st <= w_end_h).OnlyEnforceIf(before_w_end)
                    model.Add(st > w_end_h).OnlyEnforceIf(before_w_end.Not())
                    
                    model.AddBoolAnd([after_w_start, before_w_end]).OnlyEnforceIf(hits_weather)
                    model.AddBoolOr([after_w_start.Not(), before_w_end.Not()]).OnlyEnforceIf(hits_weather.Not())
                    
                    delay_var = model.NewIntVar(0, max_time_hours, f"delay_{v_id}_{c_id}_{w.event_id}")
                    model.Add(delay_var == w.delay_hours).OnlyEnforceIf(hits_weather)
                    model.Add(delay_var == 0).OnlyEnforceIf(hits_weather.Not())
                    delay_vars.append(delay_var)
            
            actual_laden_var = actual_laden_hours_var[v_id, c_id]
            model.Add(actual_laden_var == base_laden + sum(delay_vars)).OnlyEnforceIf(x[v_id, c_id])
            model.Add(actual_laden_var == 0).OnlyEnforceIf(not_x)

            # 2. Port Logistics (Congestion Wait & Handling Rate)
            port_events_at_origin = [e for e in inputs.port_events if e.port == c.origin_port]
            extra_port_vars = []

            for e in port_events_at_origin:
                e_start_h = date_to_hours(e.start_time)
                e_end_h = date_to_hours(e.end_time)
                
                is_congested = model.NewBoolVar(f"congest_{v_id}_{c_id}_{e.status_id}")
                
                after_e_start = model.NewBoolVar(f"after_e_start_{v_id}_{c_id}_{e.status_id}")
                before_e_end = model.NewBoolVar(f"before_e_end_{v_id}_{c_id}_{e.status_id}")
                
                model.Add(arr >= e_start_h).OnlyEnforceIf(after_e_start)
                model.Add(arr < e_start_h).OnlyEnforceIf(after_e_start.Not())
                
                model.Add(arr <= e_end_h).OnlyEnforceIf(before_e_end)
                model.Add(arr > e_end_h).OnlyEnforceIf(before_e_end.Not())
                
                model.AddBoolAnd([after_e_start, before_e_end]).OnlyEnforceIf(is_congested)
                model.AddBoolOr([after_e_start.Not(), before_e_end.Not()]).OnlyEnforceIf(is_congested.Not())
                
                model.Add(st - arr >= e.additional_wait_hours).OnlyEnforceIf(is_congested)
                
                if e.handling_rate_multiplier > 0 and e.handling_rate_multiplier < 1.0:
                    extra_hours = int(base_port / e.handling_rate_multiplier) - base_port
                    if extra_hours > 0:
                        extra_p_var = model.NewIntVar(0, max_time_hours, f"extrap_{v_id}_{c_id}_{e.status_id}")
                        model.Add(extra_p_var == extra_hours).OnlyEnforceIf(is_congested)
                        model.Add(extra_p_var == 0).OnlyEnforceIf(is_congested.Not())
                        extra_port_vars.append(extra_p_var)
            
            actual_port_var = actual_port_hours_var[v_id, c_id]
            model.Add(actual_port_var == base_port + sum(extra_port_vars)).OnlyEnforceIf(x[v_id, c_id])
            model.Add(actual_port_var == 0).OnlyEnforceIf(not_x)

            model.Add(fin == st + actual_laden_var + actual_port_var).OnlyEnforceIf(x[v_id, c_id])

            # Ballast from vessel home to first cargo
            ballast_nm = _get_distance_nm(v.current_port, c.origin_port)
            ballast_h = int(ballast_nm / v.speed_kn) if v.speed_kn > 0 else 0
            model.Add(arr >= vessel_props[v_id]["available"] + ballast_h).OnlyEnforceIf(
                start_node[v_id, c_id]
            )

            # Ballast between cargoes
            for c2 in C:
                if parent_map[c_id] == parent_map[c2.parcel_id]:
                    continue
                b_nm = _get_distance_nm(c.dest_port, c2.origin_port)
                b_h = int(b_nm / v.speed_kn) if v.speed_kn > 0 else 0
                model.Add(
                    arrival_time[v_id, c2.parcel_id] >= fin + b_h
                ).OnlyEnforceIf(trans[v_id, c_id, c2.parcel_id])

    # ---------------------------------------------------------------------------
    # Objective: Maximize Profit
    # ---------------------------------------------------------------------------
    # Penalty rates (convert $/day -> $/hour, integer-safe scaling)
    # We work in integer cents to avoid float precision issues.
    SCALE = 100  # 1 unit = $0.01
    opex_per_hour = int(inputs.opex_usd_per_day / 24 * SCALE)

    profit_terms: list[Any] = []

    # Per-leg profit components, so a solved VoyageAssignment can report its own
    # real profit_usd instead of the placeholder 0.0 this used to hardcode --
    # (var, sign) pairs read back with solver.Value() after solving. Keyed by the
    # same (v_id, c_id) used to build each term below; transitions are keyed by
    # (v_id, from_c_id, to_c_id) and attributed to the leg they enable.
    own_leg_vars: dict[tuple[str, str], list[tuple[Any, int]]] = {}
    transition_vars: dict[tuple[str, str, str], list[tuple[Any, int]]] = {}

    for v in V:
        v_id = v.vessel_id
        for c in C:
            c_id = c.parcel_id

            # --- Revenue ---
            revenue_scaled = int(c.revenue_usd * SCALE)
            contrib = model.NewIntVar(-500_000_000, 500_000_000, f"contrib_{v_id}_{c_id}")
            model.Add(contrib == revenue_scaled).OnlyEnforceIf(x[v_id, c_id])
            model.Add(contrib == 0).OnlyEnforceIf(x[v_id, c_id].Not())
            profit_terms.append(contrib)
            own_leg_vars.setdefault((v_id, c_id), []).append((contrib, 1))

            # --- Laden fuel cost ---
            # Bunkered at the port the leg departs from -- c.origin_port, where
            # the vessel loads and starts the laden run. Real, distinct
            # per-port prices (opt.network.Port.bunker_price_usd) were already
            # defined for every port and never read; this is that fix.
            laden_nm = _get_distance_nm(c.origin_port, c.dest_port)
            laden_days = laden_nm / (v.speed_kn * 24.0) if v.speed_kn > 0 else 0
            laden_bunker_per_tonne = _bunker_price_usd(c.origin_port)
            laden_fuel_cost_scaled = int(laden_days * v.laden_fuel_consumption_tpd * laden_bunker_per_tonne * SCALE)

            laden_cost_var = model.NewIntVar(0, 500_000_000, f"laden_cost_{v_id}_{c_id}")
            model.Add(laden_cost_var == laden_fuel_cost_scaled).OnlyEnforceIf(x[v_id, c_id])
            model.Add(laden_cost_var == 0).OnlyEnforceIf(x[v_id, c_id].Not())
            profit_terms.append(-laden_cost_var)
            own_leg_vars[(v_id, c_id)].append((laden_cost_var, -1))

            # --- Ballast fuel from vessel home to first cargo ---
            # Bunkered at v.current_port -- where the vessel is when this leg starts.
            b_nm_start = _get_distance_nm(v.current_port, c.origin_port)
            b_days_start = b_nm_start / (v.speed_kn * 24.0) if v.speed_kn > 0 else 0
            start_bunker_per_tonne = _bunker_price_usd(v.current_port)
            b_fuel_start_scaled = int(b_days_start * v.ballast_fuel_consumption_tpd * start_bunker_per_tonne * SCALE)

            start_cost_var = model.NewIntVar(0, 500_000_000, f"start_cost_{v_id}_{c_id}")
            model.Add(start_cost_var == b_fuel_start_scaled).OnlyEnforceIf(
                start_node[v_id, c_id]
            )
            model.Add(start_cost_var == 0).OnlyEnforceIf(start_node[v_id, c_id].Not())
            profit_terms.append(-start_cost_var)
            own_leg_vars[(v_id, c_id)].append((start_cost_var, -1))

            # --- Port queue wait penalty at this cargo's load port ---
            # Real, current congestion where the data supports it (opt.congestion
            # scales the port's static baseline by how busy it real is right
            # now), falling back to that same static baseline unchanged
            # otherwise -- see opt.congestion.dynamic_wait_days.
            origin_wait_days, _ = dynamic_wait_days(c.origin_port)
            wait_h_expected = int(origin_wait_days * 24)
            wait_penalty_scaled = wait_h_expected * opex_per_hour

            wait_cost_var = model.NewIntVar(0, 100_000_000, f"wait_cost_{v_id}_{c_id}")
            model.Add(wait_cost_var == wait_penalty_scaled).OnlyEnforceIf(x[v_id, c_id])
            model.Add(wait_cost_var == 0).OnlyEnforceIf(x[v_id, c_id].Not())
            profit_terms.append(-wait_cost_var)
            own_leg_vars[(v_id, c_id)].append((wait_cost_var, -1))

            # --- Early arrival wait penalty (arriving before laycan) ---
            early_arr_gap = model.NewIntVar(0, max_time_hours, f"early_arr_{v_id}_{c_id}")
            model.Add(early_arr_gap == st - arr).OnlyEnforceIf(x[v_id, c_id])
            model.Add(early_arr_gap == 0).OnlyEnforceIf(x[v_id, c_id].Not())
            early_arr_cost = model.NewIntVar(0, 500_000_000, f"early_arr_cost_{v_id}_{c_id}")
            model.Add(early_arr_cost == early_arr_gap * opex_per_hour).OnlyEnforceIf(x[v_id, c_id])
            model.Add(early_arr_cost == 0).OnlyEnforceIf(x[v_id, c_id].Not())
            profit_terms.append(-early_arr_cost)
            own_leg_vars[(v_id, c_id)].append((early_arr_cost, -1))

            # --- Demurrage: contractual per-cargo rate for port time beyond the
            # free laytime allowance. c.demurrage_usd_per_day/demurrage_wait_days
            # default to 0.0, so this term is a no-op unless a charter party sets
            # them -- see CargoParcel for what this simplifies away (real laytime
            # has separate load/discharge allowances and rules about whether
            # waiting-for-berth counts; this model only sees total port time).
            #
            # excess_hours is left unconstrained above its lower bound
            # (>= actual_port_var - allowed_hours, >= 0); since it appears only in
            # this negative profit term and nowhere else, maximizing profit pushes
            # it down to exactly max(0, actual_port_var - allowed_hours) on its
            # own -- the standard epigraph trick for max(0, x) in a maximization.
            demurrage_per_hour = int(c.demurrage_usd_per_day / 24 * SCALE)
            allowed_port_hours = int(c.demurrage_wait_days * 24)
            excess_port_hours = model.NewIntVar(0, max_time_hours, f"demurrage_excess_{v_id}_{c_id}")
            model.Add(excess_port_hours >= actual_port_var - allowed_port_hours).OnlyEnforceIf(x[v_id, c_id])
            model.Add(excess_port_hours == 0).OnlyEnforceIf(x[v_id, c_id].Not())
            demurrage_cost = model.NewIntVar(0, 500_000_000, f"demurrage_cost_{v_id}_{c_id}")
            model.Add(demurrage_cost == excess_port_hours * demurrage_per_hour).OnlyEnforceIf(x[v_id, c_id])
            model.Add(demurrage_cost == 0).OnlyEnforceIf(x[v_id, c_id].Not())
            profit_terms.append(-demurrage_cost)
            own_leg_vars[(v_id, c_id)].append((demurrage_cost, -1))

            # --- Ballast fuel + inter-cargo idle gap for transitions ---
            for c2 in C:
                if parent_map[c_id] == parent_map[c2.parcel_id]:
                    continue
                # Bunkered at c.dest_port -- where the vessel is, having just
                # discharged, when this transition leg starts.
                b_nm = _get_distance_nm(c.dest_port, c2.origin_port)
                b_days = b_nm / (v.speed_kn * 24.0) if v.speed_kn > 0 else 0
                transition_bunker_per_tonne = _bunker_price_usd(c.dest_port)
                b_fuel_scaled = int(b_days * v.ballast_fuel_consumption_tpd * transition_bunker_per_tonne * SCALE)
                b_hours_int = int(b_days * 24)

                trans_cost_var = model.NewIntVar(0, 500_000_000, f"tr_cost_{v_id}_{c_id}_{c2.parcel_id}")
                model.Add(trans_cost_var == b_fuel_scaled).OnlyEnforceIf(
                    trans[v_id, c_id, c2.parcel_id]
                )
                model.Add(trans_cost_var == 0).OnlyEnforceIf(trans[v_id, c_id, c2.parcel_id].Not())
                profit_terms.append(-trans_cost_var)
                transition_vars.setdefault((v_id, c_id, c2.parcel_id), []).append((trans_cost_var, -1))

                # Inter-cargo idle gap: time vessel sits doing nothing between
                # discharging c1 and departing for c2 = arrival(c2) - finish(c1) - ballast_hours
                # We create a slack variable for this and penalise it.
                idle_gap = model.NewIntVar(0, max_time_hours, f"idle_{v_id}_{c_id}_{c2.parcel_id}")
                # idle_gap = arrival_time[c2] - finish_time[c1] - b_hours (clamped >= 0)
                # When trans=1: arrival_time[c2] >= finish[c1] + b_hours (from constraint above),
                # so idle_gap >= 0 always.
                model.Add(
                    idle_gap == arrival_time[v_id, c2.parcel_id] - finish_time[v_id, c_id] - b_hours_int
                ).OnlyEnforceIf(trans[v_id, c_id, c2.parcel_id])
                model.Add(idle_gap == 0).OnlyEnforceIf(trans[v_id, c_id, c2.parcel_id].Not())

                idle_cost_var = model.NewIntVar(0, 500_000_000, f"idle_cost_{v_id}_{c_id}_{c2.parcel_id}")
                model.Add(idle_cost_var == idle_gap * opex_per_hour).OnlyEnforceIf(
                    trans[v_id, c_id, c2.parcel_id]
                )
                model.Add(idle_cost_var == 0).OnlyEnforceIf(trans[v_id, c_id, c2.parcel_id].Not())
                profit_terms.append(-idle_cost_var)
                transition_vars[(v_id, c_id, c2.parcel_id)].append((idle_cost_var, -1))

    model.Maximize(sum(profit_terms))

    # ---------------------------------------------------------------------------
    # Solve
    # ---------------------------------------------------------------------------
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = max_solve_seconds
    status = solver.Solve(model)
    status_name = solver.StatusName(status)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return VoyageScheduleResult([], 0.0, status_name, infeasible_pairs)

    # Reconstruct schedule
    assignments: list[VoyageAssignment] = []
    for v in V:
        v_id = v.vessel_id
        curr_c_id: str | None = next(
            (c.parcel_id for c in C if solver.Value(start_node[v_id, c.parcel_id])), None
        )
        prev_fin = 0
        prev_c_id: str | None = None
        while curr_c_id is not None:
            arr = solver.Value(arrival_time[v_id, curr_c_id])
            st = solver.Value(start_time[v_id, curr_c_id])
            fin = solver.Value(finish_time[v_id, curr_c_id])

            # Ballast hours to get here
            if not assignments or assignments[-1].vessel_id != v_id:
                # First cargo for this vessel
                b_nm = _get_distance_nm(v.current_port, next(c.origin_port for c in C if c.parcel_id == curr_c_id))
                b_h = int(b_nm / v.speed_kn) if v.speed_kn > 0 else 0
                inter_gap_h = 0
            else:
                prev_fin = assignments[-1].finish_hours
                b_nm = _get_distance_nm(
                    assignments[-1].dest_port,
                    next(c.origin_port for c in C if c.parcel_id == curr_c_id)
                )
                b_h = int(b_nm / v.speed_kn) if v.speed_kn > 0 else 0
                inter_gap_h = arr - prev_fin - b_h

            # Real per-leg profit: this leg's own revenue/fuel/wait/demurrage terms,
            # plus the transition cost that got the vessel here (if any) -- both read
            # back from the solved CP-SAT variables that built the objective, not
            # recomputed independently, so this can never drift from what the solver
            # actually optimized.
            leg_scaled = sum(sign * solver.Value(var) for var, sign in own_leg_vars.get((v_id, curr_c_id), []))
            if prev_c_id is not None:
                leg_scaled += sum(
                    sign * solver.Value(var)
                    for var, sign in transition_vars.get((v_id, prev_c_id, curr_c_id), [])
                )

            assignments.append(VoyageAssignment(
                vessel_id=v_id,
                parcel_id=parent_map[curr_c_id],
                dest_port=next(c.dest_port for c in C if c.parcel_id == curr_c_id),
                arrival_hours=arr,
                wait_hours=max(0, st - arr),
                start_operation_hours=st,
                finish_hours=fin,
                ballast_hours=b_h,
                inter_cargo_gap_hours=max(0, inter_gap_h),
                profit_usd=leg_scaled / SCALE
            ))

            prev_c_id = curr_c_id
            curr_c_id = next(
                (c2.parcel_id for c2 in C if parent_map[c2.parcel_id] != parent_map[curr_c_id] and solver.Value(trans[v_id, curr_c_id, c2.parcel_id])),
                None
            )

    return VoyageScheduleResult(
        assignments=assignments,
        total_profit_usd=float(solver.ObjectiveValue()) / SCALE,
        solver_status=status_name,
        infeasible_pairs=infeasible_pairs,
    )
