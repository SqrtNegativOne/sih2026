"""P3 requirement 7 support: the M1 -> ML feature bridge used by ml.ablation_m1.

Real data throughout -- the real reconstruction, real samples_valid.parquet.
"""
from __future__ import annotations

import polars as pl
import pytest

from ml.baselines import load_split
from opt.types import VesselClass
from tonnage.mlfeatures import (
    M1_FEATURE_COLUMNS,
    build_m1_feature_frame,
    m1_feature_as_of,
)
from tonnage.stockflow import reconstruct


@pytest.fixture(scope="module")
def real_result():
    return reconstruct()


@pytest.fixture(scope="module")
def m1_frame(real_result):
    return build_m1_feature_frame(real_result)


def test_frame_has_the_expected_join_keys_and_feature_columns(m1_frame):
    assert set(m1_frame.columns) == {"date", "target_class", *M1_FEATURE_COLUMNS}


def test_target_class_values_match_opt_types_vesselclass(m1_frame):
    real_values = set(m1_frame["target_class"].unique().to_list())
    assert real_values <= {c.value for c in VesselClass}
    assert real_values  # real data actually produced at least one class


def test_no_column_name_collides_with_the_real_training_samples(m1_frame):
    train = load_split("train")
    collisions = set(M1_FEATURE_COLUMNS) & set(train.columns)
    assert collisions == set()


def test_left_join_onto_a_real_split_preserves_row_count(m1_frame):
    valid = load_split("valid")
    joined = valid.join(m1_frame, on=["date", "target_class"], how="left")
    assert joined.height == valid.height


def test_valid_split_has_full_m1_coverage(m1_frame):
    """valid spans 2023-01 to 2024-06 (ml.baselines/samples.toml), entirely
    inside the real PortWatch-derived reconstruction's coverage window -- a
    real, checkable fact, not an assumption. Coverage on `train` (which
    reaches back to 2012, well before PortWatch coverage begins) is real but
    partial, and is reported honestly in the ablation writeup rather than
    asserted here as if it should be 100%."""
    valid = load_split("valid")
    joined = valid.join(m1_frame, on=["date", "target_class"], how="left")
    assert joined["m1_tightness"].null_count() == 0
    assert joined["m1_stock_dwt_total"].null_count() == 0


def test_m1_feature_as_of_returns_none_for_a_date_with_no_coverage():
    from datetime import date

    empty = pl.DataFrame(schema={"date": pl.Date, "target_class": pl.Utf8, "m1_tightness": pl.Float64, "m1_stock_dwt_total": pl.Float64})
    assert m1_feature_as_of(empty, date(2024, 1, 1), VesselClass.CAPESIZE) is None


def test_m1_feature_as_of_returns_real_values_for_a_covered_date(m1_frame):
    any_row = m1_frame.row(0, named=True)
    cls = VesselClass(any_row["target_class"])
    result = m1_feature_as_of(m1_frame, any_row["date"], cls)
    assert result is not None
    assert result["m1_tightness"] == pytest.approx(any_row["m1_tightness"])
    assert result["m1_stock_dwt_total"] == pytest.approx(any_row["m1_stock_dwt_total"])


def test_stock_dwt_total_is_nonnegative(m1_frame):
    assert (m1_frame["m1_stock_dwt_total"] >= 0).all()
