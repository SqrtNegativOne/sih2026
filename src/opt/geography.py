"""Runtime sea-distance lookup for the optimizer.

Replaces the two silent fallbacks that used to answer "how far apart are these
ports?" with a made-up constant -- ``10_000.0`` in ``opt.voyage`` and ``5_000.0`` in
``opt.repositioning``. Roughly 55% of port pairs hit one of those, and because the two
engines disagreed the scheduler and the repositioner priced the same leg differently.

A missing pair is a data-coverage bug, not a routing outcome, so it raises. Build the
backing matrix with ``python -m data_builders.build_geography``.
"""
from __future__ import annotations

import functools
from pathlib import Path
from typing import Final

import polars as pl

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
DISTANCES_PATH: Final[Path] = REPO_ROOT / "src" / "data" / "port_distances.parquet"


class UnknownPortPairError(KeyError):
    """Raised when no sea distance is known for a pair of ports.

    Deliberately fatal. The previous behaviour -- substituting a constant -- produced
    schedules and costs that looked reasonable and were not.
    """


class DistanceMatrixMissingError(RuntimeError):
    """Raised when the distance matrix has not been built yet."""


@functools.lru_cache(maxsize=1)
def _load(path_str: str) -> dict[tuple[str, str], float]:
    """Load and index the distance matrix, keyed by ordered-normalised port pair."""
    path = Path(path_str)
    if not path.exists():
        raise DistanceMatrixMissingError(
            f"{path} not found. Build it with:\n"
            f"    python -m data_builders.build_geography"
        )
    df = pl.read_parquet(path)
    table: dict[tuple[str, str], float] = {}
    for row in df.iter_rows(named=True):
        key = _key(row["origin"], row["destination"])
        table[key] = float(row["distance_nm"])
    return table


def _key(a: str, b: str) -> tuple[str, str]:
    """Order-independent key so A->B and B->A resolve to one entry."""
    return (a, b) if a <= b else (b, a)


def distance_nm(port_a: str, port_b: str, path: Path | None = None) -> float:
    """Sea distance in nautical miles between two ports, by ``Port.id``.

    Raises
    ------
    UnknownPortPairError
        When the pair is absent from the matrix. Add the port to
        ``data_builders.build_geography.PORT_COORDS`` and rebuild rather than
        catching this.
    """
    table = _load(str(path or DISTANCES_PATH))
    key = _key(port_a, port_b)
    if key not in table:
        raise UnknownPortPairError(
            f"No sea distance for {port_a!r} <-> {port_b!r}. Add both ports to "
            f"data_builders.build_geography.PORT_COORDS and rebuild the matrix."
        )
    return table[key]


def known_ports(path: Path | None = None) -> set[str]:
    """Every port id present in the distance matrix."""
    table = _load(str(path or DISTANCES_PATH))
    return {p for pair in table for p in pair}


def clear_cache() -> None:
    """Drop the cached matrix. Call after rebuilding within a live process."""
    _load.cache_clear()
