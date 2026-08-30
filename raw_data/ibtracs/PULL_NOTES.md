# IBTrACS Best-Track Archive -- Pull Notes

**Pull date:** 2026-08-29
**Source:** NOAA NCEI International Best Track Archive for Climate Stewardship (IBTrACS), v04r01, "ALL" list
**URL:** https://www.ncei.noaa.gov/data/international-best-track-archive-for-climate-stewardship-ibtracs/v04r01/access/csv/ibtracs.ALL.list.v04r01.csv
**File:** `ibtracs.ALL.list.v04r01.csv`
**File size:** 331,197,155 bytes (315.9 MiB)
**Data rows:** 726,241 (excludes header and the units row directly beneath it)

## Licence

Public domain per WMO/NOAA data policy -- no restrictions on use. NOAA's own documentation asks users to cite:

Knapp, K. R., M. C. Kruk, D. H. Levinson, H. J. Diamond, and C. J. Neumann, 2010: The International Best Track Archive for Climate Stewardship (IBTrACS): Unifying tropical cyclone data. Bulletin of the American Meteorological Society, 91, 363-376. https://doi.org/10.1175/2009BAMS2755.1

Knapp, K. R., H. J. Diamond, J. P. Kossin, M. C. Kruk, C. J. Schreck, 2018: International Best Track Archive for Climate Stewardship (IBTrACS) Project, Version 4. NOAA National Centers for Environmental Information. https://doi.org/10.25921/82ty-9e16

## Downstream use

Reduced by `data_builders.build_cyclone_climatology` into `src/data/cyclone_climatology.parquet` (per-basin, per-ISO-week strike rates). See that module's docstring for the basin definitions and the 1980-onward satellite-era cutoff.
