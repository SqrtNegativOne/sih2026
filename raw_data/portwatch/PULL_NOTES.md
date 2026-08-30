# PortWatch Daily Port Calls — Pull Notes

**Pull date:** 2026-08-22
**Source:** IMF PortWatch (ArcGIS Online hosted feature services)
**Org endpoint:** `https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services`

## Services used

| Service | Role |
|---|---|
| `PortWatch_ports_database` (FeatureServer, layer/table 0) | Port lookup: `portid`, `portname`, `country`, `ISO3`, `fullname`, lat/lon, LOCODE |
| `Daily_Ports_Data` (FeatureServer, **table 0**, not a spatial layer) | Daily port calls time series |

Also present but **not used**: `Daily_Chokepoints_Data` (chokepoints, not ports), `Daily_Trade_Data_WLD/_REG`,
`Monthly_TradeNow`, `portwatch_disruptions_database`, `portsfacts`, `Container_Metrics`.

Service discovery note: the FeatureServer root advertises `layers: []`; the daily data lives under
`tables[0]`. Query it at `/FeatureServer/0/query` regardless.

## Endpoint patterns

```
# Port lookup
GET .../PortWatch_ports_database/FeatureServer/0/query
    ?where=<urlencoded>&outFields=portid,portname,country,ISO3,fullname,lat,lon
    &returnGeometry=false&f=json

# Daily port calls (paginated, maxRecordCount=1000)
GET .../Daily_Ports_Data/FeatureServer/0/query
    ?where=portid='<portid>'&outFields=*&orderByFields=ObjectId
    &resultRecordCount=1000&resultOffset=<offset>&f=json
```

Pagination via `resultOffset` until `exceededTransferLimit` is absent/false.
Caveat: when a response fits in one page the field is **omitted entirely** from the JSON;
a strict property access throws. All ports needed 3 pages (2,783 rows each).

## Schema of daily table

`date` (DateOnly, ISO string), `year`, `month`, `day`, `portid`, `portname`, `country`, `ISO3`,
then three blocks per vessel type (`container`, `dry_bulk`, `general_cargo`, `roro`, `tanker`,
`cargo` aggregate) plus totals:

- `portcalls_<type>` + `portcalls`
- `import_<type>` + `import`
- `export_<type>` + `export`

### Deviations from requested header
- Requested `n_dry_bulk,capacity_dry_bulk,...`: this table exposes **counts**
  (`portcalls_dry_bulk`) and import/export tonnage proxies (`import_dry_bulk`, `export_dry_bulk`),
  but has **no capacity columns** and no `n_*` naming. Kept all available numeric columns as-is.

## Ports resolved

| File prefix | portid | Country | Rows | Date range |
|---|---|---|---|---|
| Paradip | port883 | India | 2783 | 2019-03-28 → 2026-08-14 |
| Visakhapatnam | port1367 | India | 2783 | full range* |
| Gopalpur | port2299 | India | 2783 | full range* |
| Dhamra | port290 | India | 2783 | full range* |
| Haldia | port442 | India | 2783 | full range* |
| Kolkata | port207 | India | 2783 | full range* |
| Newcastle_AU | port816 | Australia | 2783 | full range* |
| Hay_Point_AU | port458 | Australia | 2783 | full range* |
| Richards_Bay_ZA | port1099 | South Africa | 2783 | full range* |
| Beira_MZ | port137 | Mozambique | 2783 | full range* |
| Maputo_MZ | port702 | Mozambique | 2783 | full range* |
| Nacala_MZ | port784 | Mozambique | 2783 | full range* |
| Balikpapan_ID | port102 | Indonesia | 2783 | full range* |
| Samarinda_ID | port1137 | Indonesia | 2783 | full range* |

\* Global service range is **2019-01-01 → 2026-08-14**; every port returned exactly 2,783 daily
rows. Individual ports may have leading/trailing zero-activity days rather than missing days —
the service emits one row per port-day. Verify per-port first activity if you care about
true coverage start.

## Unresolved ports

| Requested | Status |
|---|---|
| **Gangavaram (India)** | NOT in PortWatch ports database. Searched `UPPER(portname)/fullname LIKE '%GANGA%'` (0 hits globally) and an Andhra-coast bounding box (lat 16.5–19.5, lon 83–85.5): only Visakhapatnam and Gopalpur exist there. Gangavaram's traffic may be folded into Visakhapatnam by PortWatch's port segmentation. |
| **Sandheads** | Not a separate entry; Sandheads is the pilot station seaward of the Kolkata/Haldia complex. Covered by `port207` (Kolkata / Syama Prasad Mookerjee Port) and `port442` (Haldia). |
| **Dalrymple Bay (AU)** | No separate entry found; adjacent to Hay Point (`port458`), which typically covers both terminals in AIS-based datasets. |

## Failures / quirks during pull

1. Initial script used `$pid` (read-only automatic variable in PowerShell = current process ID);
   renamed to `$portId`.
2. `exceededTransferLimit` absent on single-page responses → guarded with
   `PSObject.Properties['exceededTransferLimit']`.
3. No HTTP 403s encountered; no rate limiting observed with ~400 ms inter-request delay
   (14 ports × 3 pages).

## Files

- `<PORTNAME>_daily_portcalls.csv` — one per port, header:
  `date,portid,portname,country,ISO3,portcalls_container,...,export` (26 cols)
- `ports_index.csv` — portid lookup incl. resolution notes
- `pull_summary.csv` — machine-readable per-port pull stats
