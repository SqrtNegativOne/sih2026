"""Tests against the real P1 harvest -- no synthetic port index.

If ``raw_data/portwatch/port_index_extended.csv`` hasn't been built, these skip
rather than fail, so the suite still runs on a machine that hasn't pulled raw data.
"""
import pytest

from tonnage.basins import (
    Basin,
    PortIndexMissingError,
    basin_representative_port,
    basin_transit_hours,
    load_port_index,
    port_by_label,
    ports_in_basin,
)

try:
    load_port_index()
    _HAS_DATA = True
except PortIndexMissingError:
    _HAS_DATA = False

pytestmark = pytest.mark.skipif(not _HAS_DATA, reason="P1 port harvest not present on disk")


def test_real_ports_load_and_are_nonempty():
    ports = load_port_index()
    assert len(ports) >= 100


def test_known_ports_carry_expected_basin_and_role():
    # Port Hedland is the world's largest iron-ore export port (Pilbara, WA) -- real,
    # unambiguous ground truth, not a value this session picked.
    hedland = port_by_label("Port_Hedland_AU")
    assert hedland.basin == Basin.PACIFIC
    assert hedland.role == "iron_ore"

    rotterdam = port_by_label("Rotterdam_NL")
    assert rotterdam.basin == Basin.ATLANTIC

    chennai = port_by_label("Chennai_IN")
    assert chennai.basin == Basin.INDIAN_OCEAN


def test_original_14_ports_are_merged_in_with_real_csvs_on_disk():
    # These predate the P1 candidate harvest and were deliberately excluded from
    # _port_candidates.py -- confirm the merge actually happened and that the real
    # daily-portcalls CSV exists for each one, not just the index row.
    import tonnage.basins as basins_mod

    for label in (
        "Paradip", "Visakhapatnam", "Gopalpur", "Dhamra", "Haldia", "Kolkata",
        "Newcastle_AU", "Hay_Point_AU", "Balikpapan_ID", "Samarinda_ID",
        "Richards_Bay_ZA", "Beira_MZ", "Nacala_MZ", "Maputo_MZ",
    ):
        info = port_by_label(label)
        assert info.lat != 0.0 or info.lon != 0.0
        csv_path = basins_mod.port_csv_path(label)
        assert csv_path.exists(), f"{label} has an index entry but no CSV at {csv_path}"


def test_all_seven_ps_named_destination_ports_are_covered_or_documented_absent():
    # Gangavaram has no PortWatch entry at all (confirmed absent from the database
    # itself, documented in PULL_NOTES.md and build_geography.py) -- the other six
    # named EC-India ports must resolve.
    for label in ("Paradip", "Visakhapatnam", "Gopalpur", "Dhamra", "Haldia", "Kolkata"):
        assert port_by_label(label).basin == Basin.INDIAN_OCEAN


def test_every_basin_has_ports():
    for basin in Basin:
        assert len(ports_in_basin(basin)) >= 5, f"{basin} is too sparse to be usable"


def test_basin_representative_port_is_a_real_member_of_that_basin():
    for basin in Basin:
        rep = basin_representative_port(basin)
        assert rep.basin == basin
        assert rep in ports_in_basin(basin)


def test_cross_basin_transit_exceeds_intra_basin_transit():
    # A ballaster repositioning within one basin should never take longer, on this
    # representative-port measure, than crossing to a different ocean basin.
    intra = basin_transit_hours(Basin.PACIFIC, Basin.PACIFIC, speed_kn=13.0)
    cross = basin_transit_hours(Basin.PACIFIC, Basin.ATLANTIC, speed_kn=13.0)
    assert 0 <= intra < cross


def test_basin_transit_is_symmetric():
    ab = basin_transit_hours(Basin.PACIFIC, Basin.INDIAN_OCEAN, speed_kn=13.0)
    ba = basin_transit_hours(Basin.INDIAN_OCEAN, Basin.PACIFIC, speed_kn=13.0)
    assert ab == pytest.approx(ba)


def test_faster_speed_means_shorter_transit():
    slow = basin_transit_hours(Basin.ATLANTIC, Basin.PACIFIC, speed_kn=10.0)
    fast = basin_transit_hours(Basin.ATLANTIC, Basin.PACIFIC, speed_kn=15.0)
    assert fast < slow


def test_zero_or_negative_speed_rejected():
    with pytest.raises(ValueError):
        basin_transit_hours(Basin.ATLANTIC, Basin.PACIFIC, speed_kn=0.0)


def test_unknown_label_raises():
    with pytest.raises(KeyError):
        port_by_label("Not_A_Real_Port_Label")
