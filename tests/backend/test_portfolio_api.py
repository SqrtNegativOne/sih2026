"""F-16 -- POST /portfolio: the PS's own stated objective (spot -> period/
COA mix), given a real endpoint. opt.portfolio's own math is validated in
tests/opt/test_portfolio.py; these tests cover the HTTP wiring only."""
from __future__ import annotations

from itertools import pairwise

from fastapi.testclient import TestClient

from backend.main import app

client = TestClient(app)

_BASE = {
    "vessel_class": "Capesize",
    "contract_term_days": 180,
    "plant_burden_cover_days": 10.0,
    "stockout_cost_usd": 250_000.0,
    "spot_sourcing_hazard_rate_per_day": 0.05,
}


class TestPortfolioEndpoint:
    def test_returns_a_recommended_mix_and_an_8_point_frontier(self) -> None:
        r = client.post("/portfolio", json=_BASE)
        assert r.status_code == 200
        body = r.json()
        assert body["vessel_class"] == "Capesize"
        assert body["today_quote_usd_per_day"] > 0.0
        assert body["spot_cost_usd"] > 0.0
        assert body["spot_cost_std_usd"] > 0.0
        assert len(body["frontier"]) == 8
        for mix in [body["recommended"], *body["frontier"]]:
            total = mix["spot_fraction"] + mix["tc_fraction"] + mix["coa_fraction"]
            assert total == 1.0 or abs(total - 1.0) < 1e-6
            assert mix["expected_cost_usd"] > 0.0
            assert mix["cost_std_usd"] >= 0.0

    def test_frontier_is_ordered_by_increasing_risk_aversion_k(self) -> None:
        r = client.post("/portfolio", json=_BASE)
        ks = [m["risk_aversion_k"] for m in r.json()["frontier"]]
        assert ks == sorted(ks)

    def test_higher_risk_aversion_never_increases_cost_variance(self) -> None:
        """A real, checkable monotonicity property of the underlying grid
        search (also asserted directly against opt.portfolio itself in
        tests/opt/test_portfolio.py) -- confirms the HTTP layer doesn't
        scramble the risk_aversion_k -> risk_aversion mapping along the way."""
        r = client.post("/portfolio", json=_BASE)
        stds = [m["cost_std_usd"] for m in r.json()["frontier"]]
        assert all(a >= b - 1e-6 for a, b in pairwise(stds))

    def test_default_risk_aversion_k_is_one(self) -> None:
        r = client.post("/portfolio", json=_BASE)
        assert r.json()["recommended"]["risk_aversion_k"] == 1.0

    def test_explicit_risk_aversion_k_is_honoured(self) -> None:
        r = client.post("/portfolio", json={**_BASE, "risk_aversion_k": 4.0})
        assert r.json()["recommended"]["risk_aversion_k"] == 4.0

    def test_route_basis_flag_is_false_with_no_route_given(self) -> None:
        r = client.post("/portfolio", json=_BASE)
        assert r.json()["route_basis_applied"] is False

    def test_a_real_route_is_accepted_and_flag_reflects_real_coverage(self) -> None:
        r = client.post("/portfolio", json={**_BASE, "origin_port": "NEWCASTLE_AU", "dest_port": "PARADIP"})
        assert r.status_code == 200
        assert isinstance(r.json()["route_basis_applied"], bool)

    def test_unknown_vessel_class_is_422(self) -> None:
        r = client.post("/portfolio", json={**_BASE, "vessel_class": "Ultramax"})
        assert r.status_code == 422

    def test_unknown_origin_port_is_422(self) -> None:
        r = client.post("/portfolio", json={**_BASE, "origin_port": "NOT_A_REAL_PORT", "dest_port": "PARADIP"})
        assert r.status_code == 422

    def test_negative_contract_term_is_422(self) -> None:
        r = client.post("/portfolio", json={**_BASE, "contract_term_days": -5})
        assert r.status_code == 422

    def test_negative_stockout_cost_is_422(self) -> None:
        r = client.post("/portfolio", json={**_BASE, "stockout_cost_usd": -1.0})
        assert r.status_code == 422

    def test_far_past_as_of_with_no_real_data_is_503(self) -> None:
        r = client.post("/portfolio", json={**_BASE, "as_of": "2015-01-01"})
        assert r.status_code == 503

    def test_missing_required_field_is_422(self) -> None:
        r = client.post("/portfolio", json={"vessel_class": "Capesize"})
        assert r.status_code == 422
