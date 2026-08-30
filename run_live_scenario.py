"""Run the optimizer on realistic end-user inputs only, with a real ML forecast.

The gap this closes: ``run_blackbox_scenario.py`` (the existing example script)
hand-types its ForecastFan objects with made-up numbers -- it never touches the
trained models. A real SAIL planner would never supply p10/p50/p90 forecasts
themselves; that is the one thing the *system* is supposed to produce. This script
draws the line where it actually belongs:

    USER PROVIDES                      SYSTEM COMPUTES
    --------------------------------   --------------------------------------
    Which ships you have, where they   The rate forecast (via the real trained
    are, when they're free             XGBoost models on real Baltic index
                                        history, converted to USD/day via the
    What cargo needs to move, how      real, dated index<->USD/day map)
    much, laycan window
                                        The LOCK/WAIT recommendation
    Today's live market quote (what a
    broker would actually tell you)    The vessel-to-cargo voyage schedule

Run two different, hand-distinct scenarios back to back so the recommendation
and schedule can be seen to genuinely change with the input, not print the same
answer regardless of what's fed in.
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import polars as pl

from ml.live_forecast import default_master_path, forecast_all_classes
from opt import (
    CargoParcel,
    OptimizerInputs,
    Vessel,
    VesselClass,
    format_recommendation_text,
    run_optimizer,
)
from opt.network import PortEnum, RouteFamily

REPO_ROOT = Path(__file__).resolve().parent
MASTER_PATH = default_master_path()

# ---------------------------------------------------------------------------
# USER SIDE: everything below this line is exactly what a SAIL planner would
# actually type in -- ships they have, cargo that needs moving, today's quote.
# No forecast numbers, no model internals.
# ---------------------------------------------------------------------------

def scenario_a(today: date, fans, quotes) -> OptimizerInputs:
    vessels = [
        Vessel(
            vessel_id="SAIL_Panamax_1",
            vessel_class=VesselClass.PANAMAX,
            # Vessel starts at the cargo's own load port -- it just discharged its
            # previous cargo there and is ready for its next fixture. Starting it in
            # Richards Bay (South Africa) while the cargo is Australia->India made the
            # ballast leg absurd (half the planet) and nothing was ever profitable.
            current_port=PortEnum.NEWCASTLE_AU,
            status="idle",
            available_from=today,
            # Paradip's real limits are 75,000 dwt / 225m LOA / 32.2m beam (opt.network) --
            # sized to comfortably clear those, not to sit right on the edge.
            dwt=70_000, draft_m=13.5, loa_m=220.0, beam_m=32.0, speed_kn=13.0,
            laden_fuel_consumption_tpd=32.0, ballast_fuel_consumption_tpd=27.0,
        ),
    ]
    parcels = [
        CargoParcel(
            parcel_id="Coal_Newcastle_to_Paradip",
            origin_port=PortEnum.NEWCASTLE_AU,
            dest_port=PortEnum.PARADIP,
            commodity="Thermal Coal",
            volume_dwt=70_000,
            laycan_start=today + timedelta(days=5),
            laycan_end=today + timedelta(days=18),
            route_family=RouteFamily.AUSTRALIA_EC_INDIA,
            revenue_usd=1_450_000.0,
        ),
    ]
    return OptimizerInputs(
        vessels=vessels, parcels=parcels, tc_quotes=quotes,
        planning_horizon_days=90, contract_term_days=90,
        forecasts=fans[VesselClass.PANAMAX], basis={},
    )


def scenario_b(today: date, fans, quotes) -> OptimizerInputs:
    vessels = [
        Vessel(
            vessel_id="SAIL_Supramax_1",
            vessel_class=VesselClass.SUPRAMAX,
            current_port=PortEnum.SINGAPORE,
            status="idle",
            available_from=today,
            dwt=56_000, draft_m=12.0, loa_m=190.0, beam_m=32.0, speed_kn=13.5,
            laden_fuel_consumption_tpd=28.0, ballast_fuel_consumption_tpd=23.0,
        ),
    ]
    parcels = [
        CargoParcel(
            # Haldia is real but shallow -- max_dwt 40,000, correctly too small for a
            # Supramax (that's opt.voyage's own LOA/beam/draft/DWT check working as
            # intended, not a bug). Vizag is the deep-water EC-India port a Supramax
            # actually clears (max_dwt 180,000) and it's on Balikpapan's real route list.
            parcel_id="Coal_Balikpapan_to_Vizag_urgent",
            origin_port=PortEnum.BALIKPAPAN,
            dest_port=PortEnum.VIZAG,
            commodity="Thermal Coal",
            volume_dwt=50_000,
            laycan_start=today + timedelta(days=2),
            laycan_end=today + timedelta(days=7),
            route_family=RouteFamily.INDONESIA_EC_INDIA,
            revenue_usd=780_000.0,
        ),
    ]
    return OptimizerInputs(
        vessels=vessels, parcels=parcels, tc_quotes=quotes,
        planning_horizon_days=30, contract_term_days=30,
        forecasts=fans[VesselClass.SUPRAMAX], basis={},
    )


def explain_simply(rec) -> str:
    lines = [f"DECISION: {rec.lock_action}"]
    if rec.lock_action == "LOCK":
        lines.append(
            f"  -> Sign the charter now at today's quote (${rec.tc_quote_usd_per_day:,.0f}/day). "
            f"The forecast doesn't expect a better price before you'd need the ship."
        )
    else:
        lines.append(
            f"  -> Don't sign yet. Today's quote (${rec.tc_quote_usd_per_day:,.0f}/day) is above the "
            f"walk-away ceiling (${rec.ceiling_usd_per_day:,.0f}/day) the forecast supports."
        )
        if rec.optimal_entry_window_start_day is not None:
            lines.append(
                f"  -> Best time to check back: day {rec.optimal_entry_window_start_day}-"
                f"{rec.optimal_entry_window_end_day} (rate expected to dip near "
                f"${rec.optimal_entry_window_p50_usd:,.0f}/day)."
            )
    lines.append(f"  -> Estimated saving from this decision vs. always locking today: "
                  f"${rec.expected_savings_usd_per_day:,.0f}/day (p10 case: ${rec.p10_savings_usd_per_day:,.0f}/day).")

    lines.append("\nVOYAGE SCHEDULE:")
    if not rec.voyage_assignments:
        lines.append("  -> No vessel could be profitably assigned to this cargo.")
    for a in rec.voyage_assignments:
        lines.append(
            f"  -> {a.vessel_id} carries {a.parcel_id} to {a.dest_port.value.id}, "
            f"profit ${a.profit_usd:,.0f}."
        )
    for r in rec.rejected_options:
        lines.append(f"  -> {r.vessel_id} was NOT assigned to {r.parcel_id}: {r.reason}")

    if rec.repositioning_actions:
        lines.append("\nIDLE VESSEL GUIDANCE:")
        for r in rec.repositioning_actions:
            if r.is_staying:
                lines.append(f"  -> {r.vessel_id}: stay at {r.current_port.value.id} (best local market).")
            else:
                lines.append(f"  -> {r.vessel_id}: reposition from {r.current_port.value.id} to {r.recommended_port.value.id}.")

    return "\n".join(lines)


def main() -> None:
    master = pl.read_parquet(MASTER_PATH)
    today = master.filter(pl.col("series_id") == "BC_INDEX")["date"].max()
    print(f"Using the latest real market data on disk as 'today': {today}\n")

    print("Computing real forecasts from the trained models (a few seconds)...")
    fans, quotes = forecast_all_classes(today)
    print("Done.\n")
    print("Today's real observed market quotes (from the actual Baltic TC-average series):")
    for cls, q in quotes.items():
        print(f"  {cls.value:10s} ${q:,.0f}/day")

    for label, builder in (("SCENARIO A — Panamax, Australia -> Paradip", scenario_a),
                            ("SCENARIO B — Supramax, Indonesia -> Haldia (urgent)", scenario_b)):
        print("\n" + "=" * 78)
        print(label)
        print("=" * 78)
        inputs = builder(today, fans, quotes)
        v = inputs.vessels[0]
        p = inputs.parcels[0]
        print(f"USER INPUT: 1 {v.vessel_class.value} ({v.vessel_id}) idle at {v.current_port.value.id}, "
              f"today free.")
        print(f"USER INPUT: {p.volume_dwt:,.0f} dwt {p.commodity} from {p.origin_port.value.id} to "
              f"{p.dest_port.value.id}, laycan {p.laycan_start} to {p.laycan_end}.")
        print(f"USER INPUT: contract term {inputs.contract_term_days} days.\n")

        rec = run_optimizer(inputs)
        print(format_recommendation_text(rec))
        print()
        print(explain_simply(rec))


if __name__ == "__main__":
    main()
