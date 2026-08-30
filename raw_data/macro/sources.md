# Sources for raw_data/macro/ -- P4 macro/commodity signal candidates

All three files below were fetched live during this session (2026-08-28) via
direct HTTPS download (`curl`), not transcribed or reconstructed -- each is
the exact byte content the publisher serves at the URL given.

## CMO-Historical-Data-Monthly.xlsx

- Publisher: The World Bank (Commodity Markets Outlook / "Pink Sheet").
- URL fetched: https://thedocs.worldbank.org/en/doc/74e8be41ceb20fa0da750cda2f6b9e4e-0050012026/related/CMO-Historical-Data-Monthly.xlsx
- File's own "Updated on" date: August 04, 2026 (covers monthly averages
  through 2026M07).
- License: World Bank Commodity Markets Outlook publications are licensed
  CC BY 3.0 IGO; the World Bank's general open-data terms (CC BY 4.0) apply
  to World Bank Group datasets more broadly -- both permit free use,
  redistribution, and derivative works (including commercial) with
  attribution. Verified via https://datacatalog.worldbank.org/public-licenses
  and World Bank Commodity Markets Outlook publication metadata (fetched via
  web search, 2026-08-28 -- the terms-of-use page itself returned a generic
  redirect on direct fetch, so this is corroborated from the World Bank's own
  published license metadata for these report series rather than a single
  page quote).
- Attribution used in this project: "World Bank Commodity Markets Outlook
  (Pink Sheet), https://www.worldbank.org/en/research/commodity-markets"
- Columns used (of ~89 real commodity series in the file): "Crude oil, Brent"
  ($/bbl), "Coal, Australian" ($/mt), "Iron ore, cfr spot" ($/dmtu). Monthly,
  1960M01-2026M07 (799 real rows); only 2012 onward is used, matching the
  training data's real start date.
- Publication lag: the file's own "Updated on August 04, 2026" note, for data
  through July 2026, means the July figure was not knowable until early
  August -- **each month's value is dated to the 1st of the FOLLOWING month**
  in this project's ingestion (`data_builders/build_macro.py`), a
  conservative, disclosed leakage guard, not the calendar month the price
  describes.

## fred_DEXINUS.csv

- Publisher: Federal Reserve Bank of St. Louis (FRED), series `DEXINUS`
  (Indian Rupees to One U.S. Dollar, daily).
- URL fetched: https://fred.stlouisfed.org/graph/fredgraph.csv?id=DEXINUS
- License: FRED data is free for public use; per FRED's published API terms
  (fred.stlouisfed.org/docs/api/terms_of_use.html) and corroborating
  third-party summaries (fetched via web search, 2026-08-28 -- the terms page
  itself 403'd automated fetching of the HTML page specifically, while the
  data-export endpoint above serves the real CSV directly, unauthenticated),
  the only material restriction is not redistributing raw FRED data as a
  competing data service -- not applicable here (used as one input feature
  inside a forecasting model, not republished as a data product).
- Attribution used in this project: "Board of Governors of the Federal
  Reserve System (US), Indian Rupees to U.S. Dollar Spot Exchange Rate
  [DEXINUS], retrieved from FRED, Federal Reserve Bank of St. Louis."
- Daily, 1973-01-02 to 2026-08-21 (13,994 real rows); only 2012 onward used.

## fred_INDPRO.csv

- Publisher: Board of Governors of the Federal Reserve System (US) via FRED,
  series `INDPRO` (Industrial Production: Total Index).
- URL fetched: https://fred.stlouisfed.org/graph/fredgraph.csv?id=INDPRO
- License: same as `fred_DEXINUS.csv` above.
- Attribution used in this project: "Board of Governors of the Federal
  Reserve System (US), Industrial Production: Total Index [INDPRO],
  retrieved from FRED, Federal Reserve Bank of St. Louis."
- **Disclosed limitation**: this is a US-only industrial production index,
  used as a proxy for "global industrial activity" for lack of a freely
  available true global aggregate reachable in this session -- not a global
  series itself. Reported as such everywhere it is used; never relabelled as
  "global."
- Monthly, 1919-01-01 to 2026-07-01 (1,291 real rows); only 2012 onward used.

## Category coverage vs the P4 candidate list

| Requested category | Series used | Coverage |
|---|---|---|
| Bunker/crude proxy | Crude oil, Brent | Direct |
| Coking coal | Coal, Australian | Direct (Newcastle_AU is a real port in this system's own network) |
| Iron ore | Iron ore, cfr spot | Direct |
| Steel activity | *(shares Iron ore, cfr spot)* | Proxy, disclosed -- no independent steel-activity series was sourced; iron ore price is the standard financial-market proxy for steel-sector demand given ~98% of mined iron ore becomes steel, but this is not an independent signal from the iron ore candidate above and is not tested as a separate feature |
| FX | USD/INR (DEXINUS) | Direct |
| Global industrial activity | US Industrial Production (INDPRO) | Single-country proxy, disclosed |
