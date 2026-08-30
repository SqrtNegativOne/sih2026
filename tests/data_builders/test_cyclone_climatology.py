"""Tests for data_builders.build_cyclone_climatology.

No network calls -- these exercise the reduction logic against a small
synthetic IBTrACS-shaped CSV fixture (two storms, known coordinates), never
against a real download. See data_builders.harvest_ibtracs.main() for the
live pull.
"""
from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from data_builders.build_cyclone_climatology import (
    _OUTPUT_SCHEMA,
    BASIN_BOUNDS,
    basins_for_coords,
    basins_for_port,
    build_climatology,
)
from opt.network import PortEnum

# Storm A: three fixes inside the BAY_OF_BENGAL box, all in the same ISO
# week (2021-10-05 -> ISO week 40). WMO_WIND is coalesced ahead of
# USA_WIND, and is blank on the third fix to exercise that fallback.
# Max wind actually used for this storm in this bucket: max(40, 55, 70) = 70.
_STORM_A_ROWS = [
    ("2021277N19087", 2021, "2021-10-05 00:00:00", 18.5, 85.0, "40", "45"),
    ("2021277N19087", 2021, "2021-10-05 06:00:00", 18.7, 85.3, "55", ""),
    ("2021277N19087", 2021, "2021-10-05 12:00:00", 19.0, 85.6, "", "70"),
]

# Storm B: entirely outside every BASIN_BOUNDS box (mid-Pacific).
_STORM_B_ROWS = [
    ("2019210N15100", 2019, "2019-07-01 00:00:00", 0.0, -150.0, "50", "55"),
]

_HEADER = "SID,SEASON,ISO_TIME,LAT,LON,WMO_WIND,USA_WIND\n"
_UNITS_ROW = "Year,Year,ISO_TIME,deg,deg,kts,kts\n"


def _write_fixture(path: Path) -> None:
    lines = [_HEADER, _UNITS_ROW]
    for sid, season, ts, lat, lon, wmo, usa in [*_STORM_A_ROWS, *_STORM_B_ROWS]:
        lines.append(f"{sid},{season},{ts},{lat},{lon},{wmo},{usa}\n")
    path.write_text("".join(lines), encoding="utf-8")


@pytest.fixture
def climatology(tmp_path: Path) -> pl.DataFrame:
    csv_path = tmp_path / "ibtracs_fixture.csv"
    _write_fixture(csv_path)
    return build_climatology(csv_path)


def test_storm_inside_a_basin_box_is_counted_for_the_right_basin_and_week(climatology: pl.DataFrame) -> None:
    row = climatology.filter((pl.col("basin") == "BAY_OF_BENGAL") & (pl.col("iso_week") == 40))
    assert row.height == 1, "storm A should produce exactly one BAY_OF_BENGAL/week-40 row"
    r = row.row(0, named=True)
    assert r["storm_count"] == 1
    assert r["mean_max_wind_kn"] == pytest.approx(70.0)
    assert r["p90_max_wind_kn"] == pytest.approx(70.0)


def test_storm_outside_every_box_is_counted_for_none(climatology: pl.DataFrame) -> None:
    # Storm B's only fix is mid-Pacific -- it must not appear in ANY basin's
    # rows, under any ISO week.
    assert climatology.filter(pl.col("storm_count") > 0).height == climatology.filter(
        pl.col("basin") == "BAY_OF_BENGAL"
    ).height, "no basin besides BAY_OF_BENGAL should have any storm in this fixture"
    total_storms_referenced = climatology["storm_count"].sum()
    assert total_storms_referenced == 1, "storm B must not be counted anywhere"


def test_strike_rate_equals_storm_count_over_years_covered(climatology: pl.DataFrame) -> None:
    assert climatology.height > 0
    for r in climatology.iter_rows(named=True):
        assert r["years_covered"] > 0
        assert r["strike_rate"] == pytest.approx(r["storm_count"] / r["years_covered"])
    # Both storms' seasons (2019, 2021) count toward the denominator even
    # though storm B itself never lands in a basin box.
    assert climatology["years_covered"][0] == 2


def test_a_ports_coordinates_map_to_the_expected_basins() -> None:
    assert basins_for_port(PortEnum.PARADIP) == ["BAY_OF_BENGAL"]
    assert basins_for_port(PortEnum.BALIKPAPAN) == ["SE_ASIA"]
    # Richards Bay, South Africa sits south of MOZAMBIQUE_CHANNEL's lat_min
    # by design -- see BASIN_BOUNDS's own note for that box.
    assert basins_for_port(PortEnum.RICHARDS_BAY) == []
    # Kolkata has no PortEnum member / PORT_COORDS entry in this repo, but
    # its real coordinates should still resolve via basins_for_coords.
    assert basins_for_coords(22.5726, 88.3639) == ["BAY_OF_BENGAL"]


def test_basin_bounds_are_disjoint() -> None:
    """Sanity check on the boxes themselves -- a real overlap would mean a
    fix could double-count across two basins, silently inflating strike_rate."""
    names = list(BASIN_BOUNDS)
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            box_a, box_b = BASIN_BOUNDS[a], BASIN_BOUNDS[b]
            overlaps_lat = box_a.lat_min <= box_b.lat_max and box_b.lat_min <= box_a.lat_max
            overlaps_lon = box_a.lon_min <= box_b.lon_max and box_b.lon_min <= box_a.lon_max
            assert not (overlaps_lat and overlaps_lon), f"{a} and {b} overlap"


def test_output_schema_has_exactly_the_seven_documented_columns(climatology: pl.DataFrame) -> None:
    assert climatology.columns == _OUTPUT_SCHEMA == [
        "basin", "iso_week", "storm_count", "years_covered",
        "strike_rate", "mean_max_wind_kn", "p90_max_wind_kn",
    ]
    schema = climatology.schema
    assert schema["basin"] == pl.Utf8
    assert schema["iso_week"].is_integer()
    assert schema["storm_count"].is_integer()
    assert schema["years_covered"].is_integer()
    assert schema["strike_rate"].is_float()
    assert schema["mean_max_wind_kn"].is_float()
    assert schema["p90_max_wind_kn"].is_float()
