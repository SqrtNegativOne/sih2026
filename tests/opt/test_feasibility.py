"""Real-data tests for the structural / contingent infeasibility split --
opt.feasibility + opt.quote.quote_envelope."""
from __future__ import annotations

from datetime import timedelta

from ml.live_forecast import latest_available_date
from opt.feasibility import check_structural
from opt.network import PortEnum
from opt.quote import quote_envelope


def _laycan():
    today = latest_available_date()
    return today, today + timedelta(days=5), today + timedelta(days=18)


def test_check_structural_flags_absurd_tonnage():
    today, start, end = _laycan()
    problems = check_structural(
        cargo_volume_dwt=1e9,
        origin_port=PortEnum.NEWCASTLE_AU,
        dest_port=PortEnum.PARADIP,
        laycan_end=end,
        as_of=today,
        contract_term_days=30,
    )
    assert any(p.field == "cargo_volume_dwt" for p in problems)


def test_check_structural_flags_closed_laycan():
    today, _, _ = _laycan()
    problems = check_structural(
        cargo_volume_dwt=70_000,
        origin_port=PortEnum.NEWCASTLE_AU,
        dest_port=PortEnum.PARADIP,
        laycan_end=today - timedelta(days=1),
        as_of=today,
        contract_term_days=30,
    )
    assert any(p.field == "laycan" for p in problems)


def test_check_structural_passes_a_normal_request():
    today, start, end = _laycan()
    assert check_structural(
        cargo_volume_dwt=70_000,
        origin_port=PortEnum.NEWCASTLE_AU,
        dest_port=PortEnum.PARADIP,
        laycan_end=end,
        as_of=today,
        contract_term_days=30,
    ) == ()


def test_quote_envelope_feasible_on_a_real_route():
    _, start, end = _laycan()
    env = quote_envelope(
        cargo_volume_dwt=70_000,
        origin_port=PortEnum.NEWCASTLE_AU,
        dest_port=PortEnum.PARADIP,
        laycan_start=start,
        laycan_end=end,
    )
    assert env.status == "feasible"
    assert env.quote is not None
    assert env.quote.fleet_mix is not None and env.quote.fleet_mix.configurations


def test_quote_envelope_structural_for_absurd_tonnage():
    _, start, end = _laycan()
    env = quote_envelope(
        cargo_volume_dwt=1e9,
        origin_port=PortEnum.NEWCASTLE_AU,
        dest_port=PortEnum.PARADIP,
        laycan_start=start,
        laycan_end=end,
    )
    assert env.status == "structural_infeasible"
    assert env.quote is None
    assert env.structural_problems


def test_quote_envelope_contingent_relaxes_a_blocked_destination():
    """Beira's berth limits rule out every class. The envelope must not present
    a silent answer: it reports the blockers, relaxes, and returns the relaxed
    solve."""
    _, start, end = _laycan()
    env = quote_envelope(
        cargo_volume_dwt=70_000,
        origin_port=PortEnum.RICHARDS_BAY,
        dest_port=PortEnum.BEIRA,
        laycan_start=start,
        laycan_end=end,
    )
    assert env.status == "contingent_infeasible"
    assert env.original_blockers
    assert env.relaxations_applied
    assert env.quote is not None
    assert env.quote.fleet_mix is not None and env.quote.fleet_mix.configurations
