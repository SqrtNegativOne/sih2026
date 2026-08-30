"""P3 requirement 2: OBSERVED_DIMENSIONS -> INFERRED_VESSEL_CLASS.

Real data throughout: the actual `raw_data/berth_truth/fact_port_call.jsonl`
backfill (1,224 real Paradip rows), no synthetic vessel dimensions except where
a test deliberately probes an exact/boundary case against the real, cited
`opt.fleetmix._CLASS_SPECS` reference vectors.
"""
from __future__ import annotations

import pytest

from berth_truth.fact_port_call import FactPortCallStore
from opt.fleetmix import _CLASS_SPECS
from opt.types import VesselClass
from tonnage.vesselclass import (
    aggregate_inferred_class_mix,
    classified_to_frame,
    classify_fact_port_calls,
    classify_vessel,
    count_declared_upgrades,
    cross_consistency_check,
)


def _real_rows():
    store = FactPortCallStore()
    rows = list(store.read_all())
    if not rows:
        pytest.skip("no real fact_port_call data ingested -- run the P1 backfill first")
    return rows


class TestClassifyVessel:
    def test_exact_class_reference_dims_infer_that_class_with_max_confidence(self) -> None:
        for cls in VesselClass:
            spec = _CLASS_SPECS[cls]
            inference = classify_vessel(spec["loa_m"], spec["beam_m"], spec["draft_m"])
            assert inference.inferred_class == cls
            assert inference.confidence == pytest.approx(1.0)
            assert inference.distances[cls] == pytest.approx(0.0)

    def test_confidence_is_bounded_between_half_and_one(self) -> None:
        # A grid of real-world-plausible dimensions, not just the four exact references.
        for loa in (150.0, 190.0, 230.0, 300.0):
            for beam in (28.0, 32.0, 40.0):
                for draft in (8.0, 13.0, 16.0):
                    inference = classify_vessel(loa, beam, draft)
                    assert 0.5 <= inference.confidence <= 1.0

    def test_nonpositive_dimension_raises(self) -> None:
        with pytest.raises(ValueError, match="positive"):
            classify_vessel(180.0, 30.0, 0.0)
        with pytest.raises(ValueError, match="positive"):
            classify_vessel(-10.0, 30.0, 9.0)

    def test_runner_up_is_the_second_closest_class(self) -> None:
        inference = classify_vessel(180.0, 30.0, 9.5)  # exact Handysize reference
        assert inference.inferred_class == VesselClass.HANDYSIZE
        # Supramax (58k dwt) is dimensionally nearer to Handysize than Panamax/Capesize.
        assert inference.runner_up == VesselClass.SUPRAMAX

    def test_distances_are_reported_for_every_class(self) -> None:
        inference = classify_vessel(200.0, 31.0, 11.0)
        assert set(inference.distances) == set(VesselClass)
        assert all(d >= 0 for d in inference.distances.values())


class TestClassifyFactPortCalls:
    def test_real_backfill_classifies_the_large_majority_of_rows(self) -> None:
        rows = _real_rows()
        batch = classify_fact_port_calls(rows)
        assert batch.n_input_rows == len(rows)
        # Every row is accounted for exactly once across the three buckets.
        assert batch.n_missing_dimension + batch.n_nonpositive_dimension + len(batch.classified) == batch.n_input_rows
        # Measured live: 1,211/1,224 rows have all three dims (98.9%); of those,
        # 249 have a non-positive (zero) draft. Assert the real, wide margin
        # rather than the exact live counts, so a future re-ingestion (more
        # real rows) does not spuriously break this test.
        assert len(batch.classified) > 900

    def test_every_classified_row_has_a_confidence_and_a_plausibility_flag(self) -> None:
        rows = _real_rows()
        batch = classify_fact_port_calls(rows)
        for c in batch.classified:
            assert 0.5 <= c.inference.confidence <= 1.0
            assert isinstance(c.plausible_at_port, bool)

    def test_capesize_inferred_calls_at_paradip_are_flagged_implausible(self) -> None:
        """Paradip's declared max_loa_m (225.0) is below even the Panamax
        reference LOA (225.0, exactly at it) and far below Capesize's (292.0).
        A real Capesize call there is not physically plausible -- verified
        live: every one of the real rows this classifier infers as Capesize at
        Paradip is flagged implausible, even with the 5% tolerance."""
        rows = _real_rows()
        batch = classify_fact_port_calls(rows)
        capesize_paradip = [
            c for c in batch.classified
            if c.inference.inferred_class == VesselClass.CAPESIZE and c.port_label == "Paradip"
        ]
        if not capesize_paradip:
            pytest.skip("no Capesize-inferred Paradip rows in the current real backfill")
        assert all(not c.plausible_at_port for c in capesize_paradip)

    def test_declared_upgrade_count_is_real_not_hardcoded(self) -> None:
        rows = _real_rows()
        n = count_declared_upgrades(rows)
        assert n == sum(1 for r in rows if r.imo)
        assert n >= 0


class TestCrossConsistency:
    def test_aggregate_excludes_implausible_rows(self) -> None:
        rows = _real_rows()
        batch = classify_fact_port_calls(rows)
        agg = aggregate_inferred_class_mix(batch.classified, "Paradip")
        n_paradip_classified = sum(1 for c in batch.classified if c.port_label == "Paradip")
        assert agg.n_calls + agg.n_excluded_implausible == n_paradip_classified
        assert agg.n_excluded_implausible > 0  # real finding: some rows are excluded
        assert sum(agg.share_by_class.values()) == pytest.approx(1.0)

    def test_cross_consistency_is_a_real_comparison_against_classmix(self) -> None:
        rows = _real_rows()
        batch = classify_fact_port_calls(rows)
        result = cross_consistency_check(batch.classified, "Paradip")
        assert result.port_label == "Paradip"
        assert 0.0 <= result.total_variation_distance <= 1.0
        assert set(result.dimension_based_share) == set(VesselClass)
        assert set(result.tonnage_based_share) == set(VesselClass)
        # TVD is derived, not asserted independently -- recompute and compare.
        expected_tvd = sum(result.per_class_abs_delta.values()) / 2.0
        assert result.total_variation_distance == pytest.approx(expected_tvd)

    def test_unknown_port_label_raises(self) -> None:
        rows = _real_rows()
        batch = classify_fact_port_calls(rows)
        with pytest.raises(ValueError):
            aggregate_inferred_class_mix(batch.classified, "Nonexistent_Port_Label")


class TestClassifiedToFrame:
    def test_frame_has_one_row_per_classified_call_with_plausibility_column(self) -> None:
        rows = _real_rows()
        batch = classify_fact_port_calls(rows)
        frame = classified_to_frame(batch.classified)
        assert frame.height == len(batch.classified)
        assert "plausible_at_port" in frame.columns
        assert set(frame["inferred_class"].unique().to_list()) <= {c.value for c in VesselClass}

    def test_empty_input_returns_empty_typed_frame(self) -> None:
        frame = classified_to_frame([])
        assert frame.height == 0
        assert "plausible_at_port" in frame.columns
