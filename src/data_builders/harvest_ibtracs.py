"""Download NOAA's IBTrACS tropical-cyclone best-track archive.

Why this exists
----------------
The optimizer has no cyclone-risk signal at all today -- ``opt.types.WeatherSeverity``
carries a ``CYCLONE`` label, but nothing populates it from real climatology. This
harvester pulls the raw fixes; ``data_builders.build_cyclone_climatology`` (a separate,
downstream module -- read its own docstring) reduces them to a per-basin,
per-ISO-week strike table the optimizer can actually look up offline.

Source
------
NOAA NCEI International Best Track Archive for Climate Stewardship (IBTrACS), v04r01,
the "ALL" list CSV -- every basin, one file, ~6,000+ storms, 1842-present, 3-hourly
fixes:
    https://www.ncei.noaa.gov/data/international-best-track-archive-for-climate-stewardship-ibtracs/v04r01/access/csv/ibtracs.ALL.list.v04r01.csv
No API key, no quota. Data are public domain per WMO/NOAA data policy; NOAA's own
documentation asks users to cite:

    Knapp, K. R., M. C. Kruk, D. H. Levinson, H. J. Diamond, and C. J. Neumann, 2010:
    The International Best Track Archive for Climate Stewardship (IBTrACS): Unifying
    tropical cyclone data. Bulletin of the American Meteorological Society, 91, 363-376.
    https://doi.org/10.1175/2009BAMS2755.1

    Knapp, K. R., H. J. Diamond, J. P. Kossin, M. C. Kruk, C. J. Schreck, 2018:
    International Best Track Archive for Climate Stewardship (IBTrACS) Project,
    Version 4. NOAA National Centers for Environmental Information.
    https://doi.org/10.25921/82ty-9e16

The file is large (hundreds of MB): downloaded in streamed chunks straight to disk,
never buffered whole in memory, matching this repo's other harvesters
(``harvest_portwatch.py``, the reference pattern for this module's structure --
module docstring explaining why, ``Final`` constants, a between-request delay,
checkpointing, runnable as ``python -m data_builders.<name>``). There is only one
request here, not thousands, so the checkpoint is simpler: skip re-downloading a
file that already exists and is non-empty, unless ``--force`` is passed.

Runnable as a script:
    python -m data_builders.harvest_ibtracs [--force]
"""
from __future__ import annotations

import argparse
import csv
import logging
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

LOGGER: Final[logging.Logger] = logging.getLogger(__name__)

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
RAW_DATA: Final[Path] = REPO_ROOT / "raw_data" / "ibtracs"

SOURCE_URL: Final[str] = (
    "https://www.ncei.noaa.gov/data/international-best-track-archive-for-climate-"
    "stewardship-ibtracs/v04r01/access/csv/ibtracs.ALL.list.v04r01.csv"
)
CSV_PATH: Final[Path] = RAW_DATA / "ibtracs.ALL.list.v04r01.csv"
PULL_NOTES_PATH: Final[Path] = RAW_DATA / "PULL_NOTES.md"

#: Streamed in chunks this size rather than read() in one call -- the file is
#: hundreds of MB and this repo's network policy requires never buffering a
#: harvested file whole in memory.
_CHUNK_BYTES: Final[int] = 1 << 20  # 1 MiB

_CITATION: Final[str] = (
    "Knapp, K. R., M. C. Kruk, D. H. Levinson, H. J. Diamond, and C. J. Neumann, 2010: "
    "The International Best Track Archive for Climate Stewardship (IBTrACS): Unifying "
    "tropical cyclone data. Bulletin of the American Meteorological Society, 91, 363-376. "
    "https://doi.org/10.1175/2009BAMS2755.1\n\n"
    "Knapp, K. R., H. J. Diamond, J. P. Kossin, M. C. Kruk, C. J. Schreck, 2018: "
    "International Best Track Archive for Climate Stewardship (IBTrACS) Project, "
    "Version 4. NOAA National Centers for Environmental Information. "
    "https://doi.org/10.25921/82ty-9e16"
)


def _setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def _already_pulled(path: Path) -> bool:
    """A checkpoint that also rejects a zero-byte file -- a killed download
    that left an empty placeholder must not be trusted as "done"."""
    return path.exists() and path.stat().st_size > 0


def download(force: bool = False, url: str = SOURCE_URL, out_path: Path = CSV_PATH) -> Path:
    """Stream the IBTrACS ALL-list CSV to disk.

    Skips the download when ``out_path`` already exists and is non-empty,
    unless ``force`` is True. Writes to a ``.part`` sibling first and renames
    on success, so an interrupted download never leaves a truncated file that
    ``_already_pulled`` would wrongly trust on the next run.
    """
    if not force and _already_pulled(out_path):
        LOGGER.info(f"{out_path} already present ({out_path.stat().st_size:,} bytes) -- skipping download")
        return out_path

    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_suffix(out_path.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})

    LOGGER.info(f"downloading {url} ...")
    with urllib.request.urlopen(req, timeout=120) as resp, tmp_path.open("wb") as fh:
        total = 0
        while chunk := resp.read(_CHUNK_BYTES):
            fh.write(chunk)
            total += len(chunk)
            if total % (_CHUNK_BYTES * 20) < _CHUNK_BYTES:
                LOGGER.info(f"  ... {total / 1_048_576:,.0f} MiB so far")

    tmp_path.replace(out_path)
    LOGGER.info(f"downloaded {out_path} ({out_path.stat().st_size:,} bytes)")
    return out_path


def _count_data_rows(path: Path) -> int:
    """Row count excluding the header and IBTrACS's own units row (the row
    immediately after the header, e.g. "Year","BASIN",...,"deg","deg",... --
    not a real fix, and build_cyclone_climatology skips it the same way)."""
    with path.open(encoding="utf-8", newline="") as fh:
        reader = csv.reader(fh)
        next(reader, None)  # header
        next(reader, None)  # units row
        return sum(1 for _ in reader)


def write_pull_notes(
    csv_path: Path = CSV_PATH,
    notes_path: Path = PULL_NOTES_PATH,
    retrieved_at: datetime | None = None,
) -> None:
    """Record source URL, retrieval date, file size, row count, and the
    citation NOAA asks for -- same precedent as raw_data/portwatch/PULL_NOTES.md."""
    retrieved_at = retrieved_at or datetime.now(UTC)
    size_bytes = csv_path.stat().st_size
    n_rows = _count_data_rows(csv_path)

    notes_path.parent.mkdir(parents=True, exist_ok=True)
    notes_path.write_text(
        "# IBTrACS Best-Track Archive -- Pull Notes\n\n"
        f"**Pull date:** {retrieved_at.date().isoformat()}\n"
        "**Source:** NOAA NCEI International Best Track Archive for Climate Stewardship "
        "(IBTrACS), v04r01, \"ALL\" list\n"
        f"**URL:** {SOURCE_URL}\n"
        f"**File:** `{csv_path.name}`\n"
        f"**File size:** {size_bytes:,} bytes ({size_bytes / 1_048_576:,.1f} MiB)\n"
        f"**Data rows:** {n_rows:,} (excludes header and the units row directly "
        "beneath it)\n\n"
        "## Licence\n\n"
        "Public domain per WMO/NOAA data policy -- no restrictions on use. NOAA's own "
        "documentation asks users to cite:\n\n"
        f"{_CITATION}\n\n"
        "## Downstream use\n\n"
        "Reduced by `data_builders.build_cyclone_climatology` into "
        "`src/data/cyclone_climatology.parquet` (per-basin, per-ISO-week strike rates). "
        "See that module's docstring for the basin definitions and the 1980-onward "
        "satellite-era cutoff.\n",
        encoding="utf-8",
    )
    LOGGER.info(f"wrote {notes_path}")


def main() -> None:
    _setup_logging()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="re-download even if the file already exists")
    args = parser.parse_args()

    path = download(force=args.force)
    write_pull_notes(path)
    LOGGER.info("harvest complete")


if __name__ == "__main__":
    main()
