"""POST /season-plan -- scheduling a book of cargo lots across a fleet.

The problem statement asks for *multiple* voyages, and `opt.voyage.
schedule_voyages` has always solved exactly that: a CP-SAT pickup-and-delivery
model over `inputs.parcels` (plural). Nothing exposed it -- `opt.quote.run_quote`
hardcodes `parcels=[parcel]` -- so every caller had only ever seen the one-lot
case of a many-lot solver.

The scheduler's own math is exercised in tests/opt/; these cover the HTTP
wiring, the validation, and the two "unassigned" explanations, which are the
part a caller reads when a lot does not get a ship.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.main import app

client = TestClient(app)


def _vessel(vessel_id: str, port: str) -> dict:
    return {
        "vessel_id": vessel_id,
        "vessel_class": "Supramax",
        "current_port": port,
        "available_from": "2026-08-20",
        "dwt": 58_000,
        "draft_m": 12.8,
        "loa_m": 190,
        "beam_m": 32.2,
        "speed_kn": 13,
        "laden_fuel_consumption_tpd": 30,
        "ballast_fuel_consumption_tpd": 26,
    }


def _parcel(parcel_id: str, origin: str, dest: str, start: str, end: str, revenue: float) -> dict:
    return {
        "parcel_id": parcel_id,
        "origin_port": origin,
        "dest_port": dest,
        "commodity": "Thermal Coal",
        "volume_dwt": 55_000,
        "laycan_start": start,
        "laycan_end": end,
        "revenue_usd": revenue,
    }


_BASE = {
    "as_of": "2026-08-20",
    "contract_term_days": 90,
    "vessels": [_vessel("SAIL_1", "NEWCASTLE_AU")],
    "parcels": [
        _parcel("Q3-01", "NEWCASTLE_AU", "PARADIP", "2026-09-03", "2026-09-10", 2_200_000)
    ],
}


class TestSeasonPlanShape:
    def test_single_lot_schedules(self) -> None:
        r = client.post("/season-plan", json=_BASE)
        assert r.status_code == 200
        body = r.json()
        assert body["n_parcels"] == 1
        assert body["n_vessels"] == 1
        assert body["solver_status"] in {"OPTIMAL", "FEASIBLE"}
        assert body["n_assigned"] == len(body["assignments"])

    def test_assignment_references_the_callers_own_parcel_id(self) -> None:
        """The caller supplies the ids, so an assignment has to come back
        under the id that was sent -- otherwise a plan cannot be joined back
        to the book it was built from."""
        r = client.post("/season-plan", json=_BASE)
        ids = {a["parcel_id"] for a in r.json()["assignments"]}
        assert ids <= {"Q3-01"}

    def test_multiple_lots_are_solved_together(self) -> None:
        """The whole point of the endpoint: two lots, one fleet, one solve.
        Scheduling them jointly is a different problem from pricing them
        separately, because one vessel cannot serve two overlapping laycans."""
        payload = {
            **_BASE,
            "vessels": [_vessel("SAIL_1", "NEWCASTLE_AU"), _vessel("SAIL_2", "RICHARDS_BAY")],
            "parcels": [
                _parcel("Q3-01", "NEWCASTLE_AU", "PARADIP", "2026-09-03", "2026-09-10", 2_200_000),
                _parcel("Q3-02", "RICHARDS_BAY", "VIZAG", "2026-09-15", "2026-09-22", 2_000_000),
            ],
        }
        body = client.post("/season-plan", json=payload).json()
        assert body["n_parcels"] == 2
        assert body["n_vessels"] == 2
        # Every assignment must name a real supplied vessel and a real lot.
        for a in body["assignments"]:
            assert a["vessel_id"] in {"SAIL_1", "SAIL_2"}
            assert a["parcel_id"] in {"Q3-01", "Q3-02"}

    def test_every_lot_is_either_assigned_or_explained(self) -> None:
        """No lot may simply vanish from the answer -- the caller has to be
        able to account for every one it sent."""
        payload = {
            **_BASE,
            "parcels": [
                _parcel("Q3-01", "NEWCASTLE_AU", "PARADIP", "2026-09-03", "2026-09-10", 2_200_000),
                _parcel("Q3-09", "NEWCASTLE_AU", "PARADIP", "2026-09-03", "2026-09-10", 0.0),
            ],
        }
        body = client.post("/season-plan", json=payload).json()
        accounted = {a["parcel_id"] for a in body["assignments"]} | {
            u["parcel_id"] for u in body["unassigned"]
        }
        assert accounted == {"Q3-01", "Q3-09"}


class TestSeasonPlanExplanations:
    def test_zero_revenue_lot_says_why_it_was_not_assigned(self) -> None:
        """A lot with no revenue is unassigned BY CONSTRUCTION, not by any
        port or laycan constraint -- assigning a ship to it could never raise
        fleet profit. Saying 'no feasible pairing' there would be wrong."""
        payload = {**_BASE, "parcels": [_parcel("NOREV", "NEWCASTLE_AU", "PARADIP", "2026-09-03", "2026-09-10", 0.0)]}
        body = client.post("/season-plan", json=payload).json()
        reasons = {u["parcel_id"]: u["reason"] for u in body["unassigned"]}
        assert "NOREV" in reasons
        assert "revenue" in reasons["NOREV"].lower()

    def test_port_constraint_rejections_are_reported(self) -> None:
        """A 58,000 dwt vessel cannot call Haldia (40,000 dwt max). That is a
        real constraint from the port register, and it must surface as a named
        pair rather than as a silently missing assignment."""
        payload = {
            **_BASE,
            "parcels": [_parcel("HAL", "NEWCASTLE_AU", "HALDIA", "2026-10-01", "2026-10-08", 1_900_000)],
        }
        body = client.post("/season-plan", json=payload).json()
        assert body["n_assigned"] == 0
        pairs = body["infeasible_pairs"]
        assert pairs, "a DWT-infeasible pairing must be reported, not dropped"
        assert any("HALDIA" in p["reason"] or "DWT" in p["reason"] for p in pairs)


class TestSeasonPlanValidation:
    def test_duplicate_parcel_ids_rejected(self) -> None:
        """Duplicate ids would make the returned assignments unjoinable."""
        payload = {
            **_BASE,
            "parcels": [
                _parcel("DUP", "NEWCASTLE_AU", "PARADIP", "2026-09-03", "2026-09-10", 1_000_000),
                _parcel("DUP", "RICHARDS_BAY", "VIZAG", "2026-09-15", "2026-09-22", 1_000_000),
            ],
        }
        assert client.post("/season-plan", json=payload).status_code == 422

    def test_backwards_laycan_rejected_naming_the_index(self) -> None:
        payload = {
            **_BASE,
            "parcels": [_parcel("BAD", "NEWCASTLE_AU", "PARADIP", "2026-09-10", "2026-09-03", 1_000_000)],
        }
        r = client.post("/season-plan", json=payload)
        assert r.status_code == 422
        assert "parcels[0]" in r.json()["detail"]

    def test_unknown_port_rejected(self) -> None:
        payload = {
            **_BASE,
            "parcels": [_parcel("X", "NOT_A_PORT", "PARADIP", "2026-09-03", "2026-09-10", 1_000_000)],
        }
        assert client.post("/season-plan", json=payload).status_code == 422

    def test_at_least_one_vessel_and_one_parcel_required(self) -> None:
        assert client.post("/season-plan", json={**_BASE, "vessels": []}).status_code == 422
        assert client.post("/season-plan", json={**_BASE, "parcels": []}).status_code == 422


class TestSeasonPlanPeriodCover:
    """The break-even hire the plan implies, per vessel class.

    `opt.period_cover` carries the reasoning and its own tests; these cover
    only what the endpoint promises about the field.
    """

    def test_a_solved_plan_reports_a_break_even_against_the_real_spot_index(self) -> None:
        body = client.post("/season-plan", json=_BASE).json()
        cover = body["period_cover"]
        assert len(cover) == 1
        row = cover[0]
        assert row["vessel_class"] == "Supramax"
        assert row["n_vessels"] == 1
        assert row["spot_tc_series_id"] == "SUPRAMAX_TCAVG"
        # Real published index value, read at the pricing date -- never
        # carried forward from another day.
        assert row["spot_tc_as_of"] == "2026-08-20"
        assert row["spot_tc_average_usd_per_day"] > 0
        assert row["verdict"] in {"cover_beats_spot", "spot_beats_cover"}

    def test_break_even_is_the_plans_own_profit_over_its_own_ship_days(self) -> None:
        body = client.post("/season-plan", json=_BASE).json()
        row = body["period_cover"][0]
        assert row["break_even_hire_usd_per_day"] == pytest.approx(
            row["profit_usd"] / row["ship_days"]
        )

    def test_idle_vessels_are_counted_in_ship_days(self) -> None:
        """Chartering two ships and using one still costs two ships' hire, so
        the break-even must be lower than for the same book on one ship."""
        one = client.post("/season-plan", json=_BASE).json()["period_cover"][0]
        two = client.post(
            "/season-plan",
            json={**_BASE, "vessels": [_vessel("SAIL_1", "NEWCASTLE_AU"), _vessel("SAIL_2", "NEWCASTLE_AU")]},
        ).json()["period_cover"][0]
        assert two["n_vessels"] == 2
        assert two["ship_days"] > one["ship_days"]
        assert two["break_even_hire_usd_per_day"] < one["break_even_hire_usd_per_day"]

    def test_a_plan_with_no_assignments_reports_no_break_even(self) -> None:
        """Nothing was carried, so there are no earnings to break even on.
        A break-even of zero would read as a market finding rather than as
        "there is no plan"."""
        payload = {
            **_BASE,
            "parcels": [_parcel("NOREV", "NEWCASTLE_AU", "PARADIP", "2026-09-03", "2026-09-10", 0.0)],
        }
        body = client.post("/season-plan", json=payload).json()
        assert body["n_assigned"] == 0
        assert body["period_cover"] == []

    def test_a_mixed_fleet_is_split_by_class(self) -> None:
        """One break-even across a Capesize and a Supramax would be compared
        against one class's spot average -- arithmetically fine, commercially
        meaningless, since the two earn very different money per day."""
        cape = {**_vessel("CAPE_1", "NEWCASTLE_AU"), "vessel_class": "Capesize", "dwt": 180_000, "draft_m": 18.2, "loa_m": 292, "beam_m": 45}
        body = client.post(
            "/season-plan",
            json={**_BASE, "vessels": [_vessel("SAIL_1", "NEWCASTLE_AU"), cape]},
        ).json()
        classes = {r["vessel_class"]: r for r in body["period_cover"]}
        assert set(classes) == {"Supramax", "Capesize"}
        # Each is benchmarked against its OWN published index.
        assert classes["Supramax"]["spot_tc_series_id"] == "SUPRAMAX_TCAVG"
        assert classes["Capesize"]["spot_tc_series_id"] == "CAPESIZE_TCAVG"

    def test_a_class_with_no_published_rate_that_day_reports_no_benchmark(self) -> None:
        """Reachable with real data, not a contrived date.

        The Supramax and Handysize TC averages have shorter histories than the
        Panamax and Capesize ones -- there are 91 real dates carrying a Panamax
        rate and no Supramax rate. On one of those, a mixed fleet must report a
        real benchmark for one class and `no_benchmark` for the other, rather
        than filling the gap by carrying a rate forward.
        """
        thin_day = "2025-12-22"
        pmax = {
            **_vessel("PMAX_1", "NEWCASTLE_AU"),
            "vessel_class": "Panamax",
            "dwt": 82_000,
            "draft_m": 14.4,
            "loa_m": 229,
            "beam_m": 32.3,
        }
        body = client.post(
            "/season-plan",
            json={
                **_BASE,
                "as_of": thin_day,
                "vessels": [_vessel("SAIL_1", "NEWCASTLE_AU"), pmax],
            },
        ).json()
        rows = {r["vessel_class"]: r for r in body["period_cover"]}
        assert rows["Panamax"]["spot_tc_average_usd_per_day"] is not None
        assert rows["Panamax"]["spot_tc_as_of"] == thin_day
        assert rows["Supramax"]["verdict"] == "no_benchmark"
        assert rows["Supramax"]["spot_tc_average_usd_per_day"] is None
        assert rows["Supramax"]["spot_tc_as_of"] is None
        # The break-even itself still stands -- it needs no market data.
        assert rows["Supramax"]["break_even_hire_usd_per_day"] is not None
