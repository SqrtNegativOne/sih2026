"""The three evaluators, cheapest first. Each is a thin wrapper around an
existing, already-tested function -- no equation from opt.voyage,
opt.fleetmix, opt.ceiling or opt.quote is reimplemented here, only called and
translated into a DecisionSignature.

Tier 1 (closed form, no solver)
    opt.ceiling.select_vessel_class_for_cargo -- the cargo/class boundary.
    opt.voyage._vessel_can_call -- the same register-backed check BT-2 wired
    into the real CP-SAT/fleet-mix pipeline, called here standalone.

Tier 2 (fleet-mix only)
    opt.fleetmix.enumerate_fleet_mix -- feasibility and the cheapest
    configuration, no CP-SAT voyage scheduling, no LSMC.

Tier 3 (full quote)
    opt.quote.quote_envelope -- the complete decision, per the explicit
    instruction to call this rather than opt.quote.quote: a relaxation
    quote_envelope applies internally (widened laycan, transshipment) that
    rescues an otherwise-infeasible solve is itself observed as an
    envelope_status change (feasible -> contingent_infeasible, or the
    reverse), not re-detected by a second, separate search.
"""
from __future__ import annotations

from datetime import date

from fragility.models import DecisionSignature
from opt.ceiling import select_vessel_class_for_cargo
from opt.fleetmix import enumerate_fleet_mix
from opt.network import PortEnum
from opt.quote import InsufficientMarketDataError, quote_envelope
from opt.types import ForecastFan, Vessel, VesselClass
from opt.voyage import _vessel_can_call

__all__ = [
    "config_id",
    "tier1_cargo_class_signature",
    "tier1_draft_signature",
    "tier2_fleet_mix_signature",
    "tier3_full_quote_signature",
]


def config_id(vessel_class: VesselClass, n_vessels: int, requires_transshipment: bool) -> str:
    """Stable id for 'which fleet-mix configuration is chosen' -- built from
    fields FleetConfiguration already has, not a new identity opt.fleetmix
    computes."""
    return f"{vessel_class.value}:{n_vessels}:{'T' if requires_transshipment else 'D'}"


def tier1_cargo_class_signature(cargo_volume_dwt: float) -> DecisionSignature:
    """The exact class boundary -- select_vessel_class_for_cargo is a pure,
    deterministic step function of tonnage, so this needs no caching of its
    own beyond the generic memoiser engine.py already applies."""
    target_class = select_vessel_class_for_cargo(cargo_volume_dwt)
    return DecisionSignature(target_vessel_class=target_class.value)


def tier1_draft_signature(
    *,
    vessel: Vessel,
    port: PortEnum,
    commodity: str | None,
    as_of: date,
) -> DecisionSignature:
    """Whether this one vessel can call this one port, right now, at the
    register's (or PortEnum's) resolved limits. Reported through
    feasibility_set -- {vessel.vessel_class.value} if it can, empty if it
    can't -- reusing the same field Tier 2/3 populate more fully, rather
    than adding a Tier-1-only field to DecisionSignature.
    """
    verdict = _vessel_can_call(vessel, port, commodity=commodity, as_of=as_of)
    feasible = frozenset({vessel.vessel_class.value}) if verdict.is_feasible else frozenset()
    return DecisionSignature(feasibility_set=feasible)


def tier2_fleet_mix_signature(
    *,
    cargo_volume_dwt: float,
    origin_port: PortEnum,
    dest_port: PortEnum,
    forecasts: list[ForecastFan],
    contract_term_days: int,
) -> DecisionSignature:
    target_class = select_vessel_class_for_cargo(cargo_volume_dwt)
    frontier = enumerate_fleet_mix(
        cargo_volume_dwt, origin_port, dest_port, forecasts, contract_term_days=contract_term_days
    )
    feasible = frozenset(c.vessel_class.value for c in frontier.configurations)
    chosen = frontier.configurations[0] if frontier.configurations else None
    chosen_id = (
        config_id(chosen.vessel_class, chosen.n_vessels, chosen.requires_transshipment)
        if chosen is not None
        else None
    )
    return DecisionSignature(
        target_vessel_class=target_class.value,
        chosen_config_id=chosen_id,
        feasibility_set=feasible,
    )


def tier3_full_quote_signature(
    *,
    cargo_volume_dwt: float,
    origin_port: PortEnum,
    dest_port: PortEnum,
    laycan_start: date,
    laycan_end: date,
    contract_term_days: int,
    commodity: str,
    as_of: date,
    risk_tolerance: float,
) -> DecisionSignature:
    try:
        envelope = quote_envelope(
            cargo_volume_dwt=cargo_volume_dwt,
            origin_port=origin_port,
            dest_port=dest_port,
            laycan_start=laycan_start,
            laycan_end=laycan_end,
            contract_term_days=contract_term_days,
            commodity=commodity,
            as_of=as_of,
            risk_tolerance=risk_tolerance,
        )
    except InsufficientMarketDataError:
        # A perturbation moved as_of/class combination outside real market
        # data coverage -- a real, reportable outcome (this variable's
        # search boundary is "where the data runs out"), not a crash.
        return DecisionSignature(envelope_status="no_market_data")

    if envelope.quote is None:
        return DecisionSignature(envelope_status=envelope.status)

    q = envelope.quote
    feasible = frozenset(c.vessel_class.value for c in q.fleet_mix.configurations) if q.fleet_mix else frozenset()
    chosen = q.fleet_mix.configurations[0] if q.fleet_mix and q.fleet_mix.configurations else None
    chosen_id = (
        config_id(chosen.vessel_class, chosen.n_vessels, chosen.requires_transshipment)
        if chosen is not None
        else None
    )
    return DecisionSignature(
        envelope_status=envelope.status,
        lock_action=q.lock_action,
        target_vessel_class=q.target_vessel_class.value,
        chosen_config_id=chosen_id,
        feasibility_set=feasible,
    )
