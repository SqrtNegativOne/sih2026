"""Live end-to-end demo of the finished backend, through the real front door.

Runs ``opt.quote.quote()`` -- the single-cargo entry point a frontend (or a
teammate's script) actually calls -- on real cargo scenarios and renders
every feature's real output: rate forecast in both $/day and $/MT with
per-horizon confidence, the option-value-aware LOCK/WAIT call, vessel-type
optimization with real rejection reasons, port constraints and live
congestion at BOTH ends, voyage scheduling, idle-vessel repositioning,
risk flags, and the "why" behind each.

Every number printed is computed from real data on disk (real Baltic index
history, real trained XGBoost models, real IMF PortWatch satellite-derived
port traffic, real sourced port infrastructure specs). Nothing here is
hardcoded or illustrative.

Not shown: the spot/TC/COA portfolio mix (``opt.api.run_portfolio_analysis``).
That one deliberately requires real SAIL business facts -- plant burden cover
days, the real cost of a production stockout, real spot sourcing speed --
which no public market data can supply, so it is opt-in rather than part of
the automatic pipeline. See ``opt/portfolio.py``.

Run:  PYTHONPATH=src python run_quote_demo.py
"""
from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from ml.live_forecast import latest_available_date
from opt.network import PortEnum
from opt.quote import QuoteResult, quote
from opt.types import Vessel, VesselClass

WIDTH = 78


def rule(char: str = "=") -> str:
    return char * WIDTH


def money(x: float | None, dp: int = 0) -> str:
    if x is None:
        return "n/a"
    return f"${x:,.{dp}f}"


def section(title: str) -> None:
    print()
    print(title)
    print(rule("-"))


def print_why(explanation, indent: str = "  ") -> None:
    for factor in explanation.factors:
        print(f"{indent}. {factor}")
    print(f"{indent}  [method] {explanation.method}")


def render(r: QuoteResult) -> None:
    print()
    print(rule())
    print(
        f"  QUOTE  |  {r.cargo_volume_dwt:,.0f} MT {r.commodity}"
        f"  |  {r.origin_port.value.id} -> {r.dest_port.value.id}"
    )
    print(
        f"  laycan {r.laycan_start} to {r.laycan_end}"
        f"  |  {r.contract_term_days}-day charter"
        f"  |  market data as of {r.as_of}"
    )
    print(rule())

    # ---------------- 1. Rate forecast ----------------
    section("1. RATE FORECAST  (real XGBoost quantile models on real Baltic history)")
    print(f"  Vessel class derived from cargo size: {r.target_vessel_class.value}")
    print(
        f"  Today's market quote:  {money(r.today_quote_usd_per_day)}/day"
        f"   ({money(r.today_quote_usd_per_mt, 2)}/MT"
        f" over {r.assumed_transit_days:.1f} transit days)"
        if r.today_quote_usd_per_mt is not None
        else f"  Today's market quote:  {money(r.today_quote_usd_per_day)}/day"
    )
    print()
    print(f"  {'horizon':<10}{'p10':>12}{'p50':>12}{'p90':>12}{'$/MT':>10}{'direction':>18}")
    for h in r.rate_forecast:
        arrow = {"up": "rising", "down": "falling", "flat": "flat"}[h.direction]
        print(
            f"  {str(h.horizon_days) + 'd':<10}"
            f"{money(h.p10_usd_per_day):>12}"
            f"{money(h.p50_usd_per_day):>12}"
            f"{money(h.p90_usd_per_day):>12}"
            f"{money(h.p50_usd_per_mt, 2):>10}"
            f"{arrow + ' ' + format(h.confidence_pct, '.0f') + '% conf':>18}"
        )
    print()
    print("  p10/p50/p90 = pessimistic / middle / optimistic estimate. Confidence is")
    print("  P(the forecast distribution agrees with its own direction vs today's quote).")

    # ---------------- 2. Recommendation ----------------
    section("2. RECOMMENDATION  (option-value-aware, PS deliverable a)")
    if r.lock_action == "LOCK":
        print(f"  >>> LOCK  -- sign the {r.contract_term_days}-day charter now.")
        print(
            f"      Today's {money(r.today_quote_usd_per_day)}/day is at or below the"
            f" walk-away ceiling of {money(r.ceiling_usd_per_day)}/day."
        )
    else:
        print(f"  >>> WAIT  -- do not sign at {money(r.today_quote_usd_per_day)}/day.")
        print(f"      Walk-away ceiling is {money(r.ceiling_usd_per_day)}/day"
              f" ({money(r.ceiling_usd_per_mt, 2)}/MT).")
        if r.optimal_entry_window_start_day is not None:
            print(
                f"      Entry window: day {r.optimal_entry_window_start_day}"
                f"-{r.optimal_entry_window_end_day}"
                f" (forecast trough near"
                f" {money(r.full_recommendation.optimal_entry_window_p50_usd)}/day)."
            )
    print()
    print(f"  Expected saving vs the alternative: {money(r.expected_savings_usd_per_day)}/day"
          f"  ({money(r.expected_savings_usd_total)} over {r.contract_term_days} days)")
    print(f"  Locking beat staying spot in {r.prob_savings_positive * 100:.0f}%"
          f" of 2,000 simulated market futures")
    print()
    print_why(r.explanations.lock_wait)

    sr = r.full_recommendation.stopping_result
    if sr is not None:
        boundary = sr.exercise_boundary_usd_per_day
        picks = [1, 7, 14, 30, 60, min(90, len(boundary))]
        print()
        print("  Exercise boundary (lock if the market is at or below this, on that day):")
        cells = [f"d{d}={money(boundary[d - 1])}" for d in picks if d <= len(boundary)]
        print("    " + "  ".join(cells))
        print("    Read as: on day N, lock if the market is at or below that level. The")
        print("    curve's shape follows the forecast's own drift over the horizon, so it")
        print("    is not the textbook option curve. The final day equals the strike")
        print(f"    ({money(sr.strike_usd_per_day)}) by construction: there is no more waiting left to do.")

    # ---------------- 3. Vessel type optimization ----------------
    section("3. VESSEL TYPE OPTIMIZATION  (PS deliverable b)")
    if r.fleet_mix is None:
        print("  Not available for this route (no priceable configuration).")
    else:
        if r.fleet_mix.configurations:
            for i, c in enumerate(r.fleet_mix.configurations):
                tag = "RECOMMENDED" if i == 0 else "alternative"
                hub = (
                    f", via {c.transshipment_hub.value.id} transshipment"
                    if c.requires_transshipment and c.transshipment_hub
                    else ""
                )
                print(
                    f"  [{tag}] {c.vessel_class.value}: {c.n_vessels} x"
                    f" {c.dwt_per_vessel:,.0f} dwt{hub}"
                )
                print(
                    f"      cost {money(c.cost_p50_usd)} (p50), range"
                    f" {money(c.cost_p10_usd)}-{money(c.cost_p90_usd)}"
                    f" | {c.voyage_days_per_vessel:.1f} voyage days"
                    f" | reliability {c.reliability_score:.2f}"
                )
        else:
            print("  No feasible configuration for this cargo on this route.")
        for c in r.fleet_mix.rejected_configurations:
            print(f"  [ruled out] {c.vessel_class.value}: {c.infeasible_reason}")

    # ---------------- 4. Port constraints ----------------
    section("4. PORT CONSTRAINT CHECK  (both ends -- the PS asks for both)")
    for label, pc in (("LOAD ", r.origin_port_check), ("DISCH", r.dest_port_check)):
        limits = []
        if pc.max_dwt is not None:
            limits.append(f"max {pc.max_dwt:,.0f} dwt")
        if pc.max_draft_m is not None:
            limits.append(f"draft {pc.max_draft_m}m")
        if pc.max_loa_m is not None:
            limits.append(f"LOA {pc.max_loa_m}m")
        if pc.max_beam_m is not None:
            limits.append(f"beam {pc.max_beam_m}m")
        source = "live PortWatch data" if pc.wait_days_is_real_data else "static baseline, no live coverage"
        print(f"  {label} {pc.port.value.id}")
        print(f"        limits: {' | '.join(limits) if limits else 'none on record'}")
        print(
            f"        congestion: {pc.congestion_label}"
            f"  ({pc.expected_wait_days:.1f} expected wait days, {source})"
        )

    # ---------------- 5. Voyage schedule ----------------
    rec = r.full_recommendation
    section("5. VOYAGE SCHEDULE  (CP-SAT constraint solver)")
    if not rec.voyage_assignments:
        print("  No vessel assigned (none supplied, or none profitable for this cargo).")
    for a, ex in zip(rec.voyage_assignments, r.explanations.voyage_assignments, strict=True):
        print(
            f"  {a.vessel_id} -> parcel {a.parcel_id}, discharge {a.dest_port.value.id}"
        )
        print(
            f"      ballast {a.ballast_hours}h | wait {a.wait_hours}h |"
            f" operations start hr {a.start_operation_hours} | finish hr {a.finish_hours}"
        )
        print(f"      net profit {money(a.profit_usd)}")
        print_why(ex, indent="      ")
    for rej in rec.rejected_options:
        print(f"  [not assigned] {rej.vessel_id} -> {rej.parcel_id}: {rej.reason}")

    # ---------------- 6. Idle scenario management ----------------
    section("6. IDLE SCENARIO MANAGEMENT  (PS deliverable c)")
    if not rec.repositioning_actions:
        if not rec.voyage_assignments:
            print("  No vessels supplied with this quote, so nothing to reposition.")
        else:
            print("  Every supplied vessel is scheduled; nothing idle to reposition.")
    for act, ex in zip(rec.repositioning_actions, r.explanations.repositioning, strict=True):
        if act.is_staying:
            print(f"  {act.vessel_id}: STAY at {act.current_port.value.id}")
        else:
            print(
                f"  {act.vessel_id}: REPOSITION"
                f" {act.current_port.value.id} -> {act.recommended_port.value.id}"
            )
        flag = "real data" if act.probability_is_real_data else "no coverage, neutral default"
        print(
            f"      P(cargo within window) ="
            f" {act.cargo_probability_within_window * 100:.1f}%  [{flag}]"
        )
        print(
            f"      score {money(act.recommended_score_usd)}"
            f"  vs staying put {money(act.current_port_score_usd)}"
        )
        print_why(ex, indent="      ")

    # ---------------- 7. Risk ----------------
    section("7. RISK FLAGS / EARLY WARNING  (PS deliverable d)")
    if r.risk_assessment is None or not r.risk_assessment.alerts:
        print("  No active risk signals as of the last solve.")
    else:
        for alert in r.risk_assessment.alerts:
            print(f"  [{alert.severity.upper():<8}] {alert.category} @ {alert.subject}")
            print(f"      {alert.message}")
            print(
                f"      measured {alert.metric_value:.2f} against a"
                f" {alert.threshold:.2f} threshold"
            )
    print()
    print(f"  Re-solve cadence: {rec.review_trigger.schedule}")

    # ---------------- 8. Savings ----------------
    section("8. SAVINGS ESTIMATE  (2,000-path Monte Carlo over the forecast fan)")
    print(f"  expected (p50):  {money(rec.expected_savings_usd_per_day)}/day")
    print(f"  bad case (p10):  {money(rec.p10_savings_usd_per_day)}/day")
    print(f"  good case (p90): {money(rec.p90_savings_usd_per_day)}/day")
    print()
    print_why(r.explanations.savings)
    print()


def main() -> None:
    today = latest_available_date()
    laycan_start = today + timedelta(days=7)
    laycan_end = today + timedelta(days=21)

    print()
    print(rule())
    print("  SIH26006 -- FREIGHT CHARTER OPTIMIZER: LIVE OUTPUT")
    print(rule())
    print("  Every figure below is computed at run time from real data on disk:")
    print("    - real Baltic index history + real trained XGBoost quantile models")
    print("    - real IMF PortWatch satellite-derived port traffic (160 ports)")
    print("    - real sourced port infrastructure specs (15 ports)")
    print("    - real sea distances over the Eurostat marine network graph")
    print(f"  Latest real market data on disk: {today}")

    # ---------- Scenario A: a cargo lot, plus a fleet with one spare ship ----------
    fleet = [
        Vessel(
            vessel_id="SAIL_PANAMAX_1",
            vessel_class=VesselClass.PANAMAX,
            current_port=PortEnum.NEWCASTLE_AU,
            status="idle",
            available_from=today,
            dwt=70_000, draft_m=13.5, loa_m=220.0, beam_m=32.0, speed_kn=13.0,
            laden_fuel_consumption_tpd=32.0, ballast_fuel_consumption_tpd=27.0,
        ),
        Vessel(
            vessel_id="SAIL_SUPRAMAX_2",
            vessel_class=VesselClass.SUPRAMAX,
            current_port=PortEnum.SINGAPORE,
            status="idle",
            available_from=today,
            dwt=56_000, draft_m=12.0, loa_m=190.0, beam_m=32.0, speed_kn=13.5,
            laden_fuel_consumption_tpd=28.0, ballast_fuel_consumption_tpd=23.0,
        ),
    ]

    print()
    print("  SCENARIO A -- thermal coal, Australia to east-coast India,")
    print("               with two idle SAIL vessels available.")
    result_a = quote(
        cargo_volume_dwt=70_000,
        origin_port=PortEnum.NEWCASTLE_AU,
        dest_port=PortEnum.PARADIP,
        laycan_start=laycan_start,
        laycan_end=laycan_end,
        contract_term_days=30,
        commodity="Thermal Coal",
        vessels=fleet,
        revenue_usd=1_450_000.0,
    )
    render(result_a)

    # ---------- Scenario B: different origin, different size, no fleet ----------
    print()
    print(rule())
    print("  SCENARIO B -- same question, different cargo and origin, no fleet")
    print("               supplied (the pure 'what should I do about this cargo'")
    print("               case). Note the class, route, and call all change.")
    print(rule())
    result_b = quote(
        cargo_volume_dwt=50_000,
        origin_port=PortEnum.BALIKPAPAN,
        dest_port=PortEnum.VIZAG,
        laycan_start=today + timedelta(days=3),
        laycan_end=today + timedelta(days=10),
        contract_term_days=30,
        commodity="Thermal Coal",
    )
    render(result_b)

    print(rule())
    print("  Not shown: spot / period-TC / COA portfolio mix.")
    print("  It needs real SAIL business facts (plant burden cover days, the real")
    print("  cost of a stockout, real spot sourcing speed) that no public market")
    print("  data can supply -- so it is opt-in, not automatic. See opt/portfolio.py.")
    print(rule())
    print()


if __name__ == "__main__":
    main()
