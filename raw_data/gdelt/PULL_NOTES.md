# GDELT Chokepoint Conflict-Intensity Weekly Series -- Pull Notes

**Retrieval date:** 2026-08-29
**Source:** GDELT 2.0 Event Database, daily export (`https://data.gdeltproject.org/events/{date}.export.CSV.zip`)
**Date range requested:** 2025-08-28 to 2026-08-28
**Output:** `raw_data/gdelt/chokepoint_conflict_weekly.csv` (1,484 rows)
**Filter:** CAMEO root event codes ['18', '19', '20'] (Assault, Fight, Unconventional Mass Violence), geolocated within each chokepoint's real circle (`opt.chokepoints.CHOKEPOINT_GEOMETRY`).

## Licence / citation

GDELT is free and open for any use; the GDELT Project asks that publications and products using it cite:

Leetaru, Kalev, and Schrodt, Philip A. "GDELT: Global Data on Events, Language, and Tone, 1979-2012." International Studies Association Annual Conference, San Francisco, 2013.

See https://www.gdeltproject.org/about.html#termsofuse for the full terms.

## Honesty note

GDELT counts media coverage, not events -- coverage volume is driven by news attention as much as by ground truth. Any consumer of this table must z-score each chokepoint against its OWN history and never compare raw event_count across chokepoints. See `data_builders.harvest_gdelt`'s own module docstring.
