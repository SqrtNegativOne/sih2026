"""Tests for the sea-distance matrix and its runtime lookup.

The point of this module is that a missing distance must be *loud*. The behaviour it
replaces -- returning 10,000 nm from the scheduler and 5,000 nm from the repositioner
for the same unknown leg -- produced schedules that looked fine and were priced
against a fabricated number.
"""
from __future__ import annotations

from pathlib import Path
from typing import Final

import polars as pl
import pytest

from data_builders.build_geography import (
    PORT_COORDS,
    audit_legacy_table,
    compare_with_legacy,
)
from opt.geography import (
    DISTANCES_PATH,
    DistanceMatrixMissingError,
    UnknownPortPairError,
    clear_cache,
    distance_nm,
    known_ports,
)
from opt.network import PortEnum

#: Published sailing distances, used to check the graph and the coordinates together.
#: Tolerance is wide because published figures vary with routing convention, but a
#: swapped lat/lon or a wrong port lands nowhere near these.
#:
#: These are NOT taken from the legacy RouteEnum table. That table's African legs are
#: about twice their own great-circle distance -- Richards Bay to Paradip was entered
#: as 8,200 nm against a great circle of 4,073 nm, which no routing can produce. Using
#: it as a reference would have enshrined the error the rewrite exists to remove.
REFERENCE_NM: Final[dict[tuple[str, str], float]] = {
    ("Singapore", "Paradip"): 1550.0,
    ("Newcastle_AU", "Paradip"): 5600.0,
    ("Richards_Bay", "Paradip"): 4700.0,
    ("Balikpapan", "Paradip"): 2650.0,
    ("Beira", "Vizag"): 4150.0,
}


@pytest.fixture(scope="module")
def matrix() -> pl.DataFrame:
    if not DISTANCES_PATH.exists():
        pytest.skip(f"{DISTANCES_PATH} not built; run data_builders.build_geography")
    return pl.read_parquet(DISTANCES_PATH)


# ---------------------------------------------------------------------------
# Coverage: the defect being fixed
# ---------------------------------------------------------------------------


def test_every_portenum_member_has_coordinates() -> None:
    """A port the optimizer can reference must have a position, or it cannot be routed."""
    missing = {p.value.id for p in PortEnum} - set(PORT_COORDS)
    assert not missing, f"PortEnum members absent from PORT_COORDS: {sorted(missing)}"


def test_matrix_covers_every_pair(matrix: pl.DataFrame) -> None:
    """All 105 pairs, not the 47 the hand-entered table had."""
    n = len(PORT_COORDS)
    expected = n * (n - 1) // 2 + n  # unordered pairs plus self-pairs
    assert matrix.height == expected


def test_every_portenum_pair_resolves(matrix: pl.DataFrame) -> None:
    ports = [p.value.id for p in PortEnum]
    for a in ports:
        for b in ports:
            d = distance_nm(a, b)
            assert d >= 0.0


def test_unknown_pair_raises_rather_than_guessing(matrix: pl.DataFrame) -> None:
    with pytest.raises(UnknownPortPairError):
        distance_nm("Paradip", "Atlantis")


def test_missing_matrix_raises_with_build_instructions(tmp_path: Path) -> None:
    clear_cache()
    with pytest.raises(DistanceMatrixMissingError, match="build_geography"):
        distance_nm("Paradip", "Vizag", path=tmp_path / "nope.parquet")
    clear_cache()


# ---------------------------------------------------------------------------
# Correctness of the distances themselves
# ---------------------------------------------------------------------------


def test_distances_are_symmetric(matrix: pl.DataFrame) -> None:
    for a in list(PORT_COORDS)[:6]:
        for b in list(PORT_COORDS)[:6]:
            assert distance_nm(a, b) == distance_nm(b, a)


def test_self_distance_is_zero(matrix: pl.DataFrame) -> None:
    for port in PORT_COORDS:
        assert distance_nm(port, port) == 0.0


@pytest.mark.parametrize(("pair", "expected"), sorted(REFERENCE_NM.items()))
def test_matches_published_sailing_distances(
    matrix: pl.DataFrame, pair: tuple[str, str], expected: float
) -> None:
    """Catches swapped coordinates and mis-resolved ports."""
    actual = distance_nm(*pair)
    tol = max(0.35 * expected, 60.0)
    assert abs(actual - expected) <= tol, (
        f"{pair[0]} -> {pair[1]}: computed {actual:.0f} nm vs published ~{expected:.0f} nm"
    )


def test_sea_routes_exceed_great_circle(matrix: pl.DataFrame) -> None:
    """A sea route must never be shorter than the straight line through the earth.

    This is what a naive haversine distance gets wrong: Indonesia to India looks short
    in a straight line but has to round the Malay peninsula or transit Malacca.
    """
    import math

    for a_id, b_id in [
        ("Balikpapan", "Paradip"),
        ("Richards_Bay", "Haldia"),
        ("Hampton_Roads", "Vizag"),
    ]:
        a, b = PORT_COORDS[a_id], PORT_COORDS[b_id]
        lat1, lon1, lat2, lon2 = map(math.radians, (a.lat, a.lon, b.lat, b.lon))
        hav = (
            math.sin((lat2 - lat1) / 2) ** 2
            + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
        )
        great_circle = 2 * 3440.065 * math.asin(math.sqrt(hav))
        assert distance_nm(a_id, b_id) >= great_circle * 0.99


def test_triangle_inequality_via_singapore(matrix: pl.DataFrame) -> None:
    """Indonesia -> India direct cannot exceed the route via Singapore."""
    direct = distance_nm("Balikpapan", "Paradip")
    via = distance_nm("Balikpapan", "Singapore") + distance_nm("Singapore", "Paradip")
    assert direct <= via * 1.02


# ---------------------------------------------------------------------------
# Agreement with the table being replaced
# ---------------------------------------------------------------------------


def test_no_distance_is_shorter_than_the_great_circle(matrix: pl.DataFrame) -> None:
    """The physical law the whole matrix has to obey.

    Also the check that catches graph snapping: searoute returned 0 nm for several
    East Coast India pairs before the geodesic fallback was added.
    """
    violations = matrix.filter(
        (pl.col("distance_nm") < pl.col("great_circle_nm") * 0.99)
        & (pl.col("method") != "self")
    )
    assert violations.is_empty(), f"impossible distances:\n{violations}"


def test_ocean_legs_use_the_routing_graph(matrix: pl.DataFrame) -> None:
    """The geodesic fallback is for coastal hops only, never for an ocean crossing."""
    long_fallbacks = matrix.filter(
        (pl.col("method") == "geodesic_fallback") & (pl.col("distance_nm") > 500.0)
    )
    assert long_fallbacks.is_empty(), (
        f"geodesic fallback used on ocean-scale legs, so the routing graph is not "
        f"being consulted where it matters:\n{long_fallbacks}"
    )


def test_legacy_table_errors_are_detected(matrix: pl.DataFrame) -> None:
    """The audit must catch the hand-entered legs that geometry rules out.

    Documents the defect this module fixes: the Beira and Richards Bay legs were
    entered at roughly twice their great-circle distance, so every voyage from either
    origin was priced with about double the true fuel burn and transit time.
    """
    comparison = compare_with_legacy(matrix)
    assert comparison.height >= 40, "legacy comparison found too few overlapping legs"

    bad_pairs = {
        tuple(sorted((row["a"], row["b"])))
        for row in audit_legacy_table(comparison).iter_rows(named=True)
    }
    for expected in [("Beira", "Vizag"), ("Beira", "Paradip"), ("Richards_Bay", "Vizag")]:
        assert tuple(sorted(expected)) in bad_pairs, (
            f"{expected} should be flagged: the legacy distance is ~2x its great circle"
        )


def test_deep_sea_legs_reproduce_the_legacy_table(matrix: pl.DataFrame) -> None:
    """Where the legacy figures are geometrically sane, we should match them.

    Restricted to ocean legs the audit did not flag: agreement there is what confirms
    the coordinates and the routing graph are both right, and separates genuine
    correction from wholesale disagreement.
    """
    comparison = compare_with_legacy(matrix)
    bad = {
        tuple(sorted((r["a"], r["b"])))
        for r in audit_legacy_table(comparison).iter_rows(named=True)
    }
    sane_ocean = comparison.filter(
        (pl.col("legacy_nm") >= 1000.0)
        & ~pl.struct("a", "b").map_elements(
            lambda s: tuple(sorted((s["a"], s["b"]))) in bad, return_dtype=pl.Boolean
        )
    )
    assert sane_ocean.height >= 10
    within = sane_ocean.filter(pl.col("pct_diff") <= 25.0).height
    share = within / sane_ocean.height
    assert share >= 0.85, (
        f"only {100 * share:.0f}% of geometrically-sane ocean legs reproduce within "
        f"25%:\n{sane_ocean.sort('pct_diff', descending=True).head(8)}"
    )


def test_known_ports_matches_registry(matrix: pl.DataFrame) -> None:
    assert known_ports() == set(PORT_COORDS)
