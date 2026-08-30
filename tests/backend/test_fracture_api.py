"""3.4 -- GET /chokepoints and POST /fracture against the real FastAPI app
and real engines (no mocking of the domain layer, same philosophy as
test_fragility_api.py / test_reality_api.py)."""
from __future__ import annotations

from fastapi.testclient import TestClient

import opt.fracture as fracture_module
from backend.main import app

client = TestClient(app)

# Hampton Roads -> Vizag is a real Suez/Bab el-Mandeb routing (confirmed live
# in opt.chokepoints's own test suite); Vizag -> Paradip is a short intra-
# basin hop that crosses no monitored chokepoint.
_SUEZ_ROUTE = {"origin_port": "HAMPTON_ROADS", "dest_port": "VIZAG"}
_NON_CROSSING_ROUTE = {"origin_port": "VIZAG", "dest_port": "PARADIP"}


class TestChokepointsEndpoint:
    def test_returns_the_full_reference_table(self) -> None:
        r = client.get("/chokepoints")
        assert r.status_code == 200
        body = r.json()
        assert len(body) == 28
        row = next(c for c in body if c["id"] == "chokepoint6")
        assert row["name"] == "Strait of Hormuz"
        assert isinstance(row["lat"], float)
        assert isinstance(row["lon"], float)
        assert row["radius_nm"] > 0

    def test_every_row_has_the_documented_shape(self) -> None:
        body = client.get("/chokepoints").json()
        for row in body:
            assert set(row.keys()) == {"id", "name", "lat", "lon", "radius_nm"}


class TestFractureEndpointHappyPath:
    def test_a_suez_crossing_route_returns_its_real_chokepoints(self) -> None:
        r = client.post("/fracture", json=_SUEZ_ROUTE)
        assert r.status_code == 200
        body = r.json()
        ids = [c["chokepoint_id"] for c in body["chokepoints"]]
        assert "chokepoint1" in ids  # Suez Canal
        assert "chokepoint4" in ids  # Bab el-Mandeb
        for c in body["chokepoints"]:
            assert c["band"] in ("calm", "watch", "elevated", "critical")
            assert 0.0 <= c["index"] <= 100.0
            assert isinstance(c["inputs_available"], list)

    def test_the_route_enters_its_real_listed_areas(self) -> None:
        r = client.post("/fracture", json=_SUEZ_ROUTE)
        body = r.json()
        assert "gulf_of_aden" in body["jwc_listed_areas"]

    def test_a_non_crossing_route_returns_an_empty_chokepoint_list(self) -> None:
        r = client.post("/fracture", json=_NON_CROSSING_ROUTE)
        assert r.status_code == 200
        body = r.json()
        assert body["chokepoints"] == []
        assert body["jwc_listed_areas"] == []

    def test_no_hull_value_means_no_premium_line(self) -> None:
        r = client.post("/fracture", json=_SUEZ_ROUTE)
        body = r.json()
        assert body["war_risk_premium"] is None

    def test_a_real_hull_value_produces_a_labelled_premium(self) -> None:
        r = client.post("/fracture", json={**_SUEZ_ROUTE, "hull_value_usd": 45_000_000})
        assert r.status_code == 200
        body = r.json()
        premium = body["war_risk_premium"]
        assert premium is not None
        assert premium["hull_value_usd"] == 45_000_000
        assert premium["premium_usd"] > 0
        assert premium["provenance"] == "ESTIMATED"
        assert "NOT A MARKET QUOTE" in premium["basis"]

    def test_a_hull_value_on_a_non_crossing_route_still_owes_nothing(self) -> None:
        r = client.post("/fracture", json={**_NON_CROSSING_ROUTE, "hull_value_usd": 45_000_000})
        assert r.status_code == 200
        assert r.json()["war_risk_premium"] is None

    def test_as_of_is_echoed_and_defaults_to_the_latest_real_market_date(self) -> None:
        r = client.post("/fracture", json=_SUEZ_ROUTE)
        assert r.status_code == 200
        assert r.json()["as_of"]  # a real ISO date string, not null

    def test_ports_are_echoed_as_symbolic_codes(self) -> None:
        r = client.post("/fracture", json=_SUEZ_ROUTE)
        body = r.json()
        assert body["origin_port"] == "HAMPTON_ROADS"
        assert body["dest_port"] == "VIZAG"


class TestFractureEndpointErrors:
    def test_unknown_origin_port_returns_422(self) -> None:
        r = client.post("/fracture", json={"origin_port": "NOT_A_REAL_PORT", "dest_port": "VIZAG"})
        assert r.status_code == 422

    def test_unknown_dest_port_returns_422(self) -> None:
        r = client.post("/fracture", json={"origin_port": "VIZAG", "dest_port": "NOT_A_REAL_PORT"})
        assert r.status_code == 422

    def test_a_negative_hull_value_is_rejected_at_the_schema(self) -> None:
        r = client.post("/fracture", json={**_SUEZ_ROUTE, "hull_value_usd": -1})
        assert r.status_code == 422


class TestFractureEndpointMissingDataFiles:
    """The acceptance case named in the brief: missing signal data must
    still produce a real 200 with inputs_available reflecting the gap --
    never a 500."""

    def test_missing_transit_conflict_and_advisory_files_still_return_200(
        self, tmp_path, monkeypatch
    ) -> None:
        # opt.fracture imports CHOKEPOINT_DIR from opt.risk via `from ... import`,
        # which binds a separate name into opt.fracture's own module
        # namespace -- patching opt.risk.CHOKEPOINT_DIR would not affect it,
        # so the module actually read from (opt.fracture) is patched directly.
        monkeypatch.setattr(fracture_module, "GDELT_WEEKLY_PATH", tmp_path / "no_gdelt.csv")
        monkeypatch.setattr(fracture_module, "PANAMA_ADVISORIES_PATH", tmp_path / "no_advisories.csv")
        monkeypatch.setattr(fracture_module, "CHOKEPOINT_DIR", tmp_path / "no_portwatch")

        r = client.post("/fracture", json=_SUEZ_ROUTE)
        assert r.status_code == 200
        body = r.json()
        assert len(body["chokepoints"]) > 0
        for c in body["chokepoints"]:
            assert c["transit_z"] is None
            assert c["conflict_z"] is None
            # jwc_listed is a real, always-available fact -- the gap is in
            # the other two inputs, and inputs_available says so plainly.
            assert "transit_z" not in c["inputs_available"]
            assert "conflict_z" not in c["inputs_available"]
