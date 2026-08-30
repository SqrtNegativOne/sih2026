# Sentinel-1 SAR anchorage-scene spike -- pull notes

**Retrieval date:** 2026-08-29
**Source:** Copernicus Data Space Ecosystem STAC API, `https://stac.dataspace.copernicus.eu/v1/search`, collection `sentinel-1-grd`.
**Product type:** IW_GRDH_1S (Ground Range Detected, High resolution, Interferometric Wide swath).
**Polarisation:** VV+VH, dual-pol -- observed directly on every real scene found, not assumed.
**Download:** `https://download.dataspace.copernicus.eu/odata/v1/Products({uuid})/$value` with an OIDC Bearer token from `https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token`.

## Credentials

Set `COPERNICUS_USER` and `COPERNICUS_PASSWORD` (a free account at https://dataspace.copernicus.eu/) before calling fetch_scene(). Never hardcoded, never committed -- see MissingCredentialsError.

## Real 90-day scene counts per port

| Port | Scenes found | Approx. cadence |
|---|---|---|
| PARADIP | 13 | ~1 every 6.9 days |
| VISAKHAPATNAM | 16 | ~1 every 5.6 days |
| NEWCASTLE_AU | 25 | ~1 every 3.6 days |
| HAY_POINT_AU | 8 | ~1 every 11.2 days |
| RICHARDS_BAY_ZA | 16 | ~1 every 5.6 days |

**Real measured scene size:** 0.41 GB - 1.27 GB (mean 1.03 GB), across 78 real scenes.

## Verdict

**Registration/authentication verified end to end in this environment:** False
**A real authenticated download completed in this environment:** False

See this module's own docstring for the full reasoning. Search is free, open, and fast; every port in this five-port set gets at least one real scene roughly every 1-2 weeks, so per-port, per-fortnight ground truth is achievable on the real revisit cadence alone. Download requires a free CDSE account (self-service, no approval workflow, no paid tier detected) and was not completed end to end by this automated run -- see the module docstring's disclosed limitation.
