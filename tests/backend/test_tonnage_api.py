"""P3 -- the three new Tonnage Field endpoints, against the real FastAPI app
and real data on disk (no mocking, same philosophy as test_reality_api.py).

Module-scoped: the first request in this file pays the real reconstruction
(+ once, real ablation) cost; every test after that hits the warm cache.
"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

import tonnage.field as field_mod
from backend.main import app

client = TestClient(app)


@pytest.fixture(autouse=True, scope="module")
def _reset_tonnage_caches():
    field_mod.clear_snapshot_cache()
    field_mod.clear_ablation_cache()
    yield
    field_mod.clear_snapshot_cache()
    field_mod.clear_ablation_cache()


class TestTonnageField:
    def test_real_payload_has_index_type_and_computed_at(self) -> None:
        r = client.get("/tonnage-field")
        assert r.status_code == 200
        body = r.json()
        assert body["index_type"] in ("ABSOLUTE", "RELATIVE")
        assert body["computed_at"]
        assert body["as_of"]
        assert body["stale"] is False
        assert isinstance(body["calibration_factor_applied"], bool)
        assert body["calibration_factor_applied"] is False

    def test_tightness_by_class_covers_every_class_with_a_real_fit(self) -> None:
        r = client.get("/tonnage-field")
        body = r.json()
        classes = {row["vessel_class"] for row in body["tightness_by_class"]}
        assert classes  # at least one real class present
        for row in body["tightness_by_class"]:
            assert row["tightness"] >= 0

    def test_tightness_by_basin_class_is_present_and_real(self) -> None:
        r = client.get("/tonnage-field")
        body = r.json()
        assert len(body["tightness_by_basin_class"]) > 0
        for row in body["tightness_by_basin_class"]:
            assert row["basin"] in ("pacific", "atlantic", "indian_ocean")

    def test_evidence_quality_carries_the_real_signal_validation_summary(self) -> None:
        r = client.get("/tonnage-field")
        eq = r.json()["evidence_quality"]
        assert eq["n_ports_used"] > 0
        sv = eq["signal_validation"]
        assert sv["absolute_scale_validated"] is False
        assert sv["n_points"] > 0
        assert sv["min_ratio"] <= sv["median_ratio"] <= sv["max_ratio"]

    def test_explicit_as_of_resolves_to_a_real_on_or_before_date(self) -> None:
        r = client.get("/tonnage-field", params={"as_of": "2024-06-01"})
        assert r.status_code == 200
        assert r.json()["as_of"] <= "2024-06-01"

    def test_warm_call_is_fast(self) -> None:
        client.get("/tonnage-field")  # ensure warm
        t0 = time.perf_counter()
        r = client.get("/tonnage-field")
        elapsed = time.perf_counter() - t0
        assert r.status_code == 200
        assert elapsed < 0.5  # the P3 acceptance criterion, verbatim


class TestTonnageFieldForward:
    def test_real_payload_has_intervals_labelled_relative_or_absolute(self) -> None:
        r = client.get("/tonnage-field/forward", params={"horizon": 30})
        assert r.status_code == 200
        body = r.json()
        assert body["index_type"] in ("ABSOLUTE", "RELATIVE")
        assert len(body["projections"]) > 0
        for row in body["projections"][:20]:
            assert row["p10"] <= row["p50"] <= row["p90"]

    def test_default_horizon_is_ninety_days(self) -> None:
        r = client.get("/tonnage-field/forward")
        body = r.json()
        by_class = {row["vessel_class"] for row in body["projections"]}
        for cls in by_class:
            max_h = max(row["horizon_days"] for row in body["projections"] if row["vessel_class"] == cls)
            assert max_h == 90

    def test_invalid_horizon_is_rejected(self) -> None:
        assert client.get("/tonnage-field/forward", params={"horizon": 0}).status_code == 422
        assert client.get("/tonnage-field/forward", params={"horizon": 400}).status_code == 422

    def test_warm_call_is_fast(self) -> None:
        client.get("/tonnage-field/forward", params={"horizon": 30})
        t0 = time.perf_counter()
        r = client.get("/tonnage-field/forward", params={"horizon": 30})
        elapsed = time.perf_counter() - t0
        assert r.status_code == 200
        assert elapsed < 0.5


class TestTonnageFieldValidation:
    """First test in this class pays the real ~45s ablation cost; the rest
    reuse the module-scoped cache (autouse fixture only clears once per
    module, so the ablation snapshot survives across tests in this file)."""

    def test_real_payload_has_iv_kalman_and_ablation(self) -> None:
        r = client.get("/tonnage-field/validation")
        assert r.status_code == 200
        body = r.json()
        assert body["iv_verdict"]["status"] in ("NOT_ATTEMPTED", "REJECTED_ALTERNATIVE_SHIPPED", "IMPLEMENTED_VALIDATED")
        assert body["kalman_verdict"]["status"] in ("NOT_ATTEMPTED", "REJECTED_ALTERNATIVE_SHIPPED", "IMPLEMENTED_VALIDATED")
        assert len(body["sign_diagnoses"]) > 0
        for d in body["sign_diagnoses"]:
            assert d["vessel_class"]
            assert d["explanation"]

    def test_ablation_table_is_present_and_the_decision_is_recorded(self) -> None:
        r = client.get("/tonnage-field/validation")
        ablation = r.json()["ablation"]
        assert isinstance(ablation["adopt_b"], bool)
        assert ablation["reasoning"]
        assert len(ablation["rows"]) > 0
        assert ablation["m1_valid_coverage"] == pytest.approx(1.0)

    def test_warm_call_is_fast(self) -> None:
        client.get("/tonnage-field/validation")  # ensure warm
        t0 = time.perf_counter()
        r = client.get("/tonnage-field/validation")
        elapsed = time.perf_counter() - t0
        assert r.status_code == 200
        assert elapsed < 0.5
