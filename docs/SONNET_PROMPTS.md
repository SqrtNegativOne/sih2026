# SIH26006 — Seven Sonnet Execution Prompts (rev. 2)

Paste one at a time, in order. Each is self-contained.

## Verified repo facts (2026-08-28) — embedded so Sonnet need not rediscover them

| Fact | Value |
|---|---|
| Test baseline | `686 collected · 684 passed · 2 skipped` — the floor |
| Product port universe | **15** — `PortEnum` and `PORT_COORDS` agree exactly |
| The 15 | PARADIP, VIZAG, GANGAVARAM, GOPALPUR, DHAMRA, SAGAR_SANDHEADS, HALDIA, NEWCASTLE_AU, GLADSTONE_AU, RICHARDS_BAY, BEIRA, MUARA_PANTAI, BALIKPAPAN, HAMPTON_ROADS, SINGAPORE |
| **PS country gap** | PS names origins *"Australia, the US, Mozambique, **Russia** and Indonesia"* (`docs/00_og_problem_statement.md:3`). **The network has no Russian origin.** `docs/01_problem_statement.md:8` already flags this. `Vostochny_RU` exists in PortWatch. |
| PS Indian ports | Paradip, Vizag, Gangavaram, Gopalpur, Dhamra, Sagar-Sandheads, Haldia — all 7 present ✓ |
| Not PS countries | Richards Bay (ZA), Singapore (SG) — useful extras, **not substitutes** for a required country |
| Berth register today | 3 ports: Dhamra (20 rows/14 berths), Gangavaram (9), Visakhapatnam (29) |
| BT-0 archive today | **3 HTML captures total** |
| Transshipment hubs | `HALDIA→DHAMRA`, `GOPALPUR→VIZAG`, `SAGAR_SANDHEADS→DHAMRA` (`opt/fleetmix.py:76`) |
| **PortWatch gaps** | **GANGAVARAM, SAGAR_SANDHEADS, GLADSTONE_AU, HAMPTON_ROADS, SINGAPORE have no PortWatch file** |
| Existing proxy | `MUARA_PANTAI → Samarinda_ID` — **a proxy, not identity** |
| Tide | `tide_allowance_m`/`tide_rule` exist on `BerthConstraint`, **never read outside models.py/registry.py** |
| Wait model | `congestion._real_wait_days` = `static_baseline × portwatch_call_count_ratio`. One scalar. |
| Route basis | `BasisEntry` at `opt/types.py:50` **never instantiated**; `opt/quote.py:202` passes `basis={}` |
| Backend | 5 endpoints: `/health /meta /ports /quote /quote/stream`. No tonnage/impact/fragility/berth_truth import. |
| Frontend | **one page**: `voyage-desk-page.tsx`. Hash placeholder already deleted. |
| Disconnected | `src/tonnage/` stockflow/forward/supplycurve reach no decision; `src/impact/` imported by nothing (38 tests) |

## Priority order — resolve every trade-off with this

1. vessel-class + **route** freight forecasting · 2. LOCK/WAIT · 3. vessel type for cargo + route ·
4. port constraints / Berth Reality · 5. congestion + turnaround · 6. idle/repositioning ·
7. explainability + Decision Fragility · 8. macro/commodity · 9. regret/accountability ·
10. optional commercial differentiators.

**Never let M1, Almgren–Chriss, backhaul or emissions delay a direct PS requirement.**

## The seven prompts

| # | Prompt | Closes |
|---|---|---|
| **P1** | Port Network Audit + All-Port Berth Truth Foundation | PS country coverage, core vs extended network, source+adapter registry, provider framework, multi-port ingestion, backfill, `fact_port_call` |
| **P2** | Complete M4 Berth Reality Engine + Port Twin | Constraints network-wide, tide enforcement, queue/waits where evidence allows, handling, feasibility, optimizer wiring, API, UI |
| **P3** | M1 Tonnage Field — identification gate, validation, ablation | Scale honesty, inferred class, supply curve, forward tightness, **ablation**, API, UI |
| **P4** | Route-Aware Forecasting + Data Semantics | Route basis or honest unavailability, macro/commodity, PortWatch provenance, thin-series, point-in-time |
| **P5** | Decision Intelligence — DF + Regret Ledger + Replay | DF on real M4 inputs, `/fragility`, live ledger, separate historical replay, both UIs |
| **P6** | Commercial Upgrades (feasibility-gated) | Backhaul opportunity score, landed cost, operational penalty, `src/impact/` connect-or-mark-experimental |
| **P7** | Final Integration + Judge-Ready Audit | Ship-verification matrix, launcher, caching, closure report |

---
---

# PROMPT 1 — PORT NETWORK AUDIT + ALL-PORT BERTH TRUTH FOUNDATION

## Context

This repo is a dry-bulk freight chartering optimizer for SIH26006 (Ministry of Steel / SAIL).
Prior work delivered:

- **BT-0** — `src/berth_truth/archiver.py`: a working snapshot archiver for Adani's Dhamra and
  Gangavaram schedule pages. Hashes content (sha256), distinguishes new content from re-observation,
  parses to `ScheduleSnapshot`/`ScheduleRow`, scores `parse_confidence`, quarantines uncertain
  fields. **Only 3 HTML captures exist on disk.**
- **BT-1** — `registry.py`/`resolver.py`/`models.py`: an effective-dated, source-cited
  `BerthConstraint` register covering **3 ports**: Dhamra (20 rows/14 berths), Gangavaram (9),
  Visakhapatnam (29).
- **BT-2** — `opt/voyage.py::_vessel_can_call` and `opt/fleetmix.py::_can_call` return/consume a
  `FeasibilityVerdict` (berth id, binding constraint, limit source, draft status, margins,
  untested checks).

Test baseline: **686 collected, 684 passed, 2 skipped.** Never regress it.

**Important framing: "3 ports have a berth register" is NOT "3 ports have usable data."**
Substantially more public data exists than the register reflects — see §5.

## Objective

(a) Audit the port network against the problem statement itself and close any required-country gap;
(b) turn the two-port scraper into a network-wide operational data foundation with correct modelling
of non-berth locations and a normalised port-call fact table. **Verify sources, build adapters,
ingest, backfill, normalise — in this one prompt.** Not a research report.

## Inspect first

Adapt if paths changed — do not assume this list is current.

- `docs/00_og_problem_statement.md` and `docs/01_problem_statement.md` — **read these first**
- `src/opt/network.py` — `PortEnum`, `Port`, `expected_wait_days`, `handling_rate_tph`
- `src/data_builders/build_geography.py` — `PORT_COORDS`
- `src/opt/fleetmix.py` — `TRANSSHIPMENT_HUB` (~line 76)
- `src/opt/congestion.py` — `PORT_TO_TONNAGE_LABEL`, `dynamic_wait_days`, `_real_wait_days`
- `src/berth_truth/` — `archiver.py`, `store.py`, `models.py`, `registry.py`, `resolver.py`,
  `service.py`, `parsers/adani_schedule.py`
- `tests/berth_truth/` — all 124 tests; your regression guard
- `raw_data/berth_truth/`, `raw_data/portwatch/`

## Preserve

- The Adani path must keep working **byte-identically**. The 3 captures in
  `raw_data/berth_truth/raw_html/` are fixtures — assert identical sha256 and parse output.
- `BerthConstraint`, `LimitStatus`, `DraftSource`, `DraftStatus`, effective dating, supersession,
  exact-date resolution.
- `FeasibilityVerdict` and both BT-2 call sites.
- Quarantine-over-guess. Null-over-invention.

## Implementation requirements

### 1. PS coverage audit — do this before anything else

The PS opening states origins are *"Australia, the US, Mozambique, **Russia** and Indonesia"*
(`docs/00_og_problem_statement.md:3`). The detailed description later lists only *"Australia, the
US, Mozambique, and Indonesia"*. `docs/01_problem_statement.md:8` already flags the inconsistency.

**The current network has no Russian origin.** Define two sets:

- **`CORE_PS_NETWORK`** — representative loading ports covering *every* country the PS names, plus
  all seven Indian discharge locations (Paradip, Vizag, Gangavaram, Gopalpur, Dhamra,
  Sagar-Sandheads, Haldia — all already present ✓).
- **`EXTENDED_NETWORK`** — useful additional markets/hubs already in the repo (Richards Bay,
  Singapore). **Keep them. But an extended port must never be counted as covering a PS-required
  country** — Richards Bay is South Africa and does not cover Mozambique.

Investigate and add **only if your domain review confirms relevance to metallurgical/thermal coal
imports to East-Coast India**:

- **Vostochny (RU)** — closes the Russia gap. `Vostochny_RU_daily_portcalls.csv` already exists in
  `raw_data/portwatch/`. Consider also Nakhodka/Vanino if better suited.
- **Nacala-à-Velha (MZ)** — Mozambican metallurgical coal via the Nacala corridor; assess alongside
  (not necessarily instead of) Beira. `Nacala_MZ_daily_portcalls.csv` exists.
- **Hay Point / DBCT (AU)** — the principal Australian *coking* coal terminals, alongside Newcastle
  (predominantly thermal) and Gladstone. `Hay_Point_AU_daily_portcalls.csv` exists.

Adding a port means: `PortEnum` member, `PORT_COORDS` entry, real sourced constraints, and a
geography/route check that `opt/geography.py` can compute distances for it. **If you cannot source
real constraints for a candidate, do not add it** — report the gap instead. A missing country
honestly reported beats a fabricated port.

### 2. Canonical port registry with operational structure

Create `src/berth_truth/port_master.py`. Per port, a `PortProfile`:

- `port_enum_name`, `canonical_id`, `country`, `role` (`LOAD`/`DISCHARGE`/`HUB`/`LIGHTERAGE`),
  `network_tier` (`CORE_PS` / `EXTENDED`)
- **`operational_model`** — the critical field:
  - `BERTHED` — conventional alongside berths (Paradip, Vizag, Dhamra, Gangavaram, Newcastle…)
  - `MULTI_TERMINAL` — distinct terminals with genuinely different limits.
    **Hampton Roads must be this** — currently one flat constant covering Norfolk / Lamberts Point /
    Newport News coal piers.
  - `ANCHORAGE_TRANSFER` — worked at anchorage, not alongside (**Muara Pantai / Taboneo-style
    Indonesian anchorages**)
  - `LIGHTERAGE` — ship-to-ship / barge transfer at a roadstead (**Sagar / Sandheads** — a
    lightering point for Haldia, not a berth)
  - `HUB_TRANSSHIP` — transshipment/repositioning node only (**Singapore**)
- `parent_or_hub` (mirror `TRANSSHIPMENT_HUB`), `terminals: tuple[TerminalProfile, ...]`

**Do not force a berth structure onto a location that has none.** An `ANCHORAGE_TRANSFER` or
`LIGHTERAGE` location models transfer points and draft-at-anchorage. Getting this wrong is worse
than leaving the port `SUPPORTED_STATIC`.

### 3. Source + adapter registry

`src/berth_truth/sources.py` with `PortSource`:

`port_id, source_name, source_url, doc_format, cadence, adapter, coverage_level,
source_quality, publication_date_field, retrieved_at, notes`

- `coverage_level`: `A` machine-readable/API · `B` official HTML · `C` official PDF/periodic ·
  `D` static berth/nav documentation only · `E` no operational feed found
- **`source_quality`**: `OFFICIAL_PORT_AUTHORITY` · `OFFICIAL_TERMINAL_OPERATOR` ·
  `GOVERNMENT_OTHER` · `PUBLIC_AGGREGATOR`. Downstream models must be able to see that an
  aggregator feed is lower-confidence than a port-authority feed.
- Keep provenance: source name, URL, retrieved timestamp, publication date. **Do not build a
  licence-audit workstream** — record the URL and move on.

### 4. Provider framework

`src/berth_truth/providers/` with a `Provider` protocol:
`supports(source) -> bool` · `fetch(source) -> RawCapture` · `parse(capture) -> ParseResult`.

Adapters: `html_table.py` (**move** the Adani logic here — move, do not rewrite), `pdf_report.py`,
`json_api.py`, `csv_xlsx.py`, `static_doc.py`, `manual_declaration.py`.

`RawCapture` carries `source_url`, `retrieved_at`, `doc_published_date`, `content_sha256`,
`parser_version`, `raw_bytes_path`. Registry dispatch on `doc_format`; unknown format raises —
never a silent skip. Refactor `archiver.archive_port` through the registry; keep the
`python -m berth_truth` CLI surface identical.

### 5. Source verification per operational location — then ingest

Independent research indicates **substantially more data exists than the 3-port register suggests**.
Verify each of the following, then implement adapters for everything you confirm. Treat this as
leads to verify, not as facts:

**Verified by direct fetch and parse (use these directly):**

- **Paradip** — `https://www.paradipport.gov.in/uploads/YYYY/MM/dtrDDMM.pdf` (current) and
  `https://paradipport.gov.in/Writereaddata/Daily_Traffic/dtrDDMM.pdf` (archive). 7 pages,
  machine-readable via `pypdf`. Sections **A. WORKING VESSELS · B. VESSELS WAITING AT ANCHORAGE ·
  C. EXPECTED VESSEL · D. BERTHING MOVEMENTS**. Per vessel: name, **DRAFT, LOA, BEAM**, berth,
  cargo, shipper/receiver/stevedore, D/L flag, **ARVL / READY / BERTH as three distinct
  timestamps**, ETD/SLD, TOTAL, NORM, DAY'S actual, MQ/BQ-to-date, BALANCE, remarks with event
  codes (`DC` `DCOMP` `MF` `GD` `IS` `LC` `FS` `SLD`).
  ⚠️ **`extract_text()` returns columns interleaved out of order** — a naive line regex will
  mis-associate fields. Use **positional extraction (character x/y)** or validate every row with
  `total == handled + balance` and quarantine failures. A large quarantine bucket on pass one is
  success, not failure. Also carries **high-tide restriction notes** — capture them for P2.
- **Visakhapatnam** — `vpt.shipping.gov.in/admin_assets/uploads/…BERTHING PROGRAME - ETA.pdf`.
  "VESSELS WAITING & EXPECTED", grouped by commodity (`[A] IRON ORE`, `[E] CRUDE & POL`…). Carries
  vessel name, nationality, **position (`ROADS` = at anchorage)**, draft, arrival date, agent,
  tonnes, commodity, shipper. **No berth-time column** — derive waits by differencing consecutive
  captures, tagged `evidence_class=SNAPSHOT_TRANSITION`. Find the index page listing these.
- **Dhamra / Gangavaram** — `adaniports.com/…/vesselschedule`, live berth/anchorage/expected/sailed.
  Already working; keep as-is.
- **Newcastle** — Port Authority of NSW publishes official **daily vessel movements**
  (`portauthoritynsw.com.au/port-operations/newcastle-harbour/…`); Port of Newcastle publishes a
  shipping schedule (`pon.com.au/shipping/shipping-schedule/`). Likely Level B.

**Reported as available — verify and implement:**

- **Gopalpur** — live vessel schedule reportedly exists, plus historical trade notices and berth/
  infrastructure information
- **Haldia** — berth position, arrivals/departures, **changing draft forecasts**. Official source is
  `smportkolkata.shipping.gov.in` (Haldia Dock Complex).
- **Sandheads** — vessel-at-Sandheads information including anchoring time, vessel draft, LOA,
  cargo. Model as `LIGHTERAGE`.
- **Gladstone** — official shipping movements / QSHIPS plus detailed port procedures
- **Singapore** — MPA / OCEANS-X vessel arrival/departure data

**Weaker or fragmented — verify carefully, degrade honestly:**

- **Richards Bay** — excellent terminal constraints and handling data (Transnet/RBCT); a live
  line-up may require a non-port public source
- **Beira** — strong official operational/constraint data; an official *live schedule* is not
  confirmed
- **Muara Pantai** — clearly an anchorage/transshipment operation; public movements exist, official
  live feed weaker
- **Hampton Roads** — strong terminal constraints; public live coal-terminal schedule weaker
- **Balikpapan** — an official Kariangau terminal schedule exists, **but verify whether that
  terminal actually represents the bulk-coal operation our route intends.** If it does not, say so
  and do not substitute it.

**Rules for this section:**

- If after a serious search no live schedule exists, record
  **`LIVE_OPERATIONAL_FEED_NOT_FOUND`** and continue. **Do not fabricate a schedule. Do not stop the
  prompt because one port is unavailable.**
- Prefer official port authority / terminal operator. **This is an educational project — you may use
  a public non-official or aggregator feed when no official live feed exists**, but you must set
  `source_quality=PUBLIC_AGGREGATOR` so downstream models know it is lower-confidence.
- A port may legitimately end as `SUPPORTED_STATIC` (constraints only, no live feed).

### 6. `fact_port_call`

Append-only store. All fields nullable except identity/provenance:

```
port_id, terminal, berth_or_point, vessel_name, imo, loa_m, beam_m, arrival_draft_m,
vessel_class_inferred, cargo_raw, commodity_class, load_discharge, shipper, receiver, stevedore,
arrival_ts, ready_ts, berth_ts, sail_ts, eta_ts, etd_ts,
total_qty_t, handled_qty_t, balance_qty_t, norm_tpd, actual_tpd,
source_url, source_doc_date, source_quality, retrieved_at, content_sha256, parser_version,
extraction_confidence, evidence_class, quarantine_reason
```

- **Append-only**, keyed `(content_sha256, row_index)`. A corrected later document creates a NEW
  record; supersession is recorded, never applied destructively.
- **Null beats invention.** Absent field stays `None` — never 0, never a default.
- `evidence_class` separates `DIRECTLY_REPORTED` from `SNAPSHOT_TRANSITION` (derived by differencing
  captures). These are not the same quality of evidence — never conflate them.
- Note the field is `vessel_class_inferred`, not `vessel_class`. See P3 §2 for why.
- Project BT-0's `ScheduleRow` into `fact_port_call` so all sources land in one table.

### 7. Historical backfill

For sources with dated archives (Paradip certainly), walk backwards. Target ≥90 days for Paradip;
report how far back the site actually goes. **Rate-limit ≥1 request per 3 s per host**, honouring
the existing `MIN_FETCH_INTERVAL_SECONDS` precedent. Re-running must be idempotent.

### 8. PortWatch mapping — identity only, never proximity

I verified these selectable ports have **no PortWatch file**: `GANGAVARAM`, `SAGAR_SANDHEADS`,
`GLADSTONE_AU`, `HAMPTON_ROADS`, `SINGAPORE`. And `MUARA_PANTAI` is currently mapped to
`Samarinda_ID`.

**A nearby port is not a valid proxy.** Only map a PortWatch dataset to a product port when
**geographic/entity identity is established** — same port, same facility. Otherwise expose
**`PORTWATCH_UNAVAILABLE`** and let Berth Truth or another source cover the location.

Re-examine the existing `MUARA_PANTAI → Samarinda_ID` mapping under this rule. If it is a proxy
rather than an identity (an anchorage vs a river port), **either mark it explicitly as
`PROXY` with reduced confidence, or remove it** — do not leave it presented as equivalent truth.

## Data inputs

`raw_data/berth_truth/`, `raw_data/portwatch/` (161 CSVs), live sources above. New captures under
`raw_data/berth_truth/`.

## Integration points

`berth_truth.registry`/`resolver` continue serving BT-2 unchanged. `fact_port_call` is consumed by
**P2** (waits/handling/queue) and **P3** (class inference) — design the read API for both: query by
port, date range, vessel class, commodity. `PORT_TO_TONNAGE_LABEL` in `opt/congestion.py`.

## Edge cases / fallback

- Source unreachable → record failure, keep prior captures, never synthesise.
- Low parse confidence → quarantine with reason, retain raw bytes.
- `coverage_level=E` → everything downstream still works, reporting
  `LIVE_OPERATIONAL_FEED_NOT_FOUND` rather than an empty success.
- Non-berth location → `berth_or_point` holds the transfer point/anchorage; berth fields stay `None`.
- PortWatch identity unproven → `PORTWATCH_UNAVAILABLE`, not a nearby substitute.

## Tests

1. `test_ps_coverage.py` — every PS-named country has ≥1 `CORE_PS_NETWORK` load port, **or an
   explicit documented gap**; all 7 Indian discharge ports present; an `EXTENDED` port cannot
   satisfy a PS country requirement.
2. `test_port_master.py` — every port has a `PortProfile`; `operational_model` correct for Sandheads
   (`LIGHTERAGE`), Muara Pantai (`ANCHORAGE_TRANSFER`), Hampton Roads (`MULTI_TERMINAL`), Singapore
   (`HUB_TRANSSHIP`); hub relationships mirror `TRANSSHIPMENT_HUB`.
3. `test_sources.py` — every port maps to ≥1 `PortSource` with coverage level and `source_quality`;
   provenance mandatory.
4. `test_providers.py` — protocol conformance; dispatch; unknown format raises; **regression: the 3
   Adani captures produce identical sha256 and parse output**.
5. `test_paradip_parser.py` — commit a **real** PDF fixture; exact extraction for ≥3 named vessels
   incl. LOA/beam/draft and all three timestamps; interleaved/corrupt row quarantined not guessed;
   `total == handled + balance` fires.
6. `test_fact_port_call.py` — append-only (re-ingest → no duplicates); null-not-zero;
   `evidence_class` separates reported from derived; supersession preserved.
7. `test_portwatch_mapping.py` — **no mapping without established identity**; unproven →
   `PORTWATCH_UNAVAILABLE`.
8. Regression: **all 124 existing `berth_truth` tests pass unchanged**; full suite ≥684.

## Acceptance criteria

- PS country coverage audited; any gap either closed with a real sourced port or explicitly reported.
- Every port has a `PortProfile`, `network_tier`, `operational_model`, coverage status.
- Non-berth locations modelled as such — no invented berths.
- `python -m berth_truth --port dhamra` behaves exactly as before.
- **Operational data ingested for materially more than 3 ports**, or a per-port statement of what
  was searched and why nothing was found.
- Paradip history ≥90 days or a documented site limit.
- No PortWatch proxy without established identity.
- Harvester re-run is idempotent.

## Commands to run

```
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m ruff check src/ tests/
python -m berth_truth --port all          # paste real output
```

## Required final response

Files added · files modified · tests added · test count before/after with real output ·
**PS country coverage table (country → covering port → sourced?)** · **a table of every port →
network_tier → operational_model → coverage_level → source_quality → adapter → rows ingested** ·
live fetches performed · every `LIVE_OPERATIONAL_FEED_NOT_FOUND` with what you searched ·
quarantine rate with example reasons · PortWatch mappings kept/removed and why · limitations.

## Execution instruction

**Implement the work now. Do not return another plan. Inspect the current repo first, preserve
functioning work, and continue until this workstream is connected end-to-end. Do not stop after
adding classes, interfaces, TODOs or disconnected modules.** If a source does not exist, record
`LIVE_OPERATIONAL_FEED_NOT_FOUND` and continue with the remaining ports.

---
---

# PROMPT 2 — COMPLETE M4 BERTH REALITY ENGINE + PORT TWIN

## Context

P1 built the data foundation: PS-audited port network, operational models, provider adapters,
multi-port ingestion, backfill, append-only `fact_port_call`.

Before P1: berth constraints for 3 ports; `tide_allowance_m`/`tide_rule` present on
`BerthConstraint` but **never read outside `models.py`/`registry.py`** (tide is decorative); waiting
time = `static_baseline × portwatch_call_count_ratio` in `opt/congestion.py::_real_wait_days` — one
scalar, not a queue; `handling_rate_tph` a literal in `opt/network.py`; nothing reachable over HTTP
beyond `/health /meta /ports /quote /quote/stream`.

**Original Moat 4 is the Berth Reality Engine. BT-0/1/2 + P1 are its foundation, not the moat.**

## Objective

Build the M4 engine — geometry + draft + tide + queue + handling + uncertainty — wire it into the
optimizer, expose it over HTTP, build the Port Twin screen. **M4 is done when a judge can drill
PORT → TERMINAL → BERTH and see real, sourced, dated numbers** — not when `/port-twin` returns JSON.

## Inspect first

- `src/berth_truth/` — `port_master.py`, `sources.py`, `providers/`, `fact_port_call` (P1), plus
  `registry.py`, `resolver.py`, `service.py`, `models.py`
- `src/opt/types.py` — `FeasibilityVerdict`, `FeasibilityMargins`, `LimitSource`
- `src/opt/voyage.py::_vessel_can_call` · `src/opt/fleetmix.py::_can_call`
- `src/opt/congestion.py` — `dynamic_wait_days`, `_real_wait_days`, `_congestion_multiplier`
- `src/opt/network.py` — `expected_wait_days`, `handling_rate_tph`
- `backend/main.py`, `backend/serialize.py`
- `frontend/src/pages/voyage-desk-page.tsx`, `components/desk/`, `components/shell/icon-rail.tsx`,
  `lib/api.ts`, `lib/types.ts`

## Preserve

- `FeasibilityVerdict`'s contract and both call sites. **`src/fragility/` depends on
  `_vessel_can_call` returning this shape** — P5 extends it; do not break it now.
- `dynamic_wait_days`'s signature and its `is_real_data` flag. Add alongside; do not replace.
- Observed-only berths never clear a vessel (BT-2 invariant).
- The Voyage Desk page and its SSE `/quote/stream` flow.

## Implementation requirements

### 1. Constraint register across the network

Extend BT-1 beyond 3 ports using P1's sources. Per location: terminal, berth-or-point, LOA, beam,
DWT **where the source states DWT** (never compare a published *displacement* to vessel DWT — record
untested and say so), permissible draft, **channel depth vs designed depth vs permissible draft as
separate fields**, declared/operational draft with its own effective date, commodity compatibility,
and the **observed operational envelope** (max LOA/beam/draft seen in `fact_port_call`).

**Declared and observed stay permanently separate.** An observation may never raise a declared
limit. But **surface disagreement explicitly** — a vessel observed larger than the published limit
is a reportable finding.

Only a port-level maximum available → one row, `is_published_constraint_berth=False`,
`LimitStatus.ASSUMED`, real provenance. **Do not invent berth subdivisions.** For `LIGHTERAGE` /
`ANCHORAGE_TRANSFER` locations model draft-at-transfer-point, not berths.

### 2. Tide — real, with a hard authority separation

- `TideAuthority.PORT_RULE` — port-authority operational rule/notice. **Authoritative.** May gate
  feasibility. (Paradip's daily report carries high-tide restriction notes — capture them.)
- `TideAuthority.ADVISORY_MODEL` — FES, pyTMD, Open-Meteo, any scientific model. **Advisory only.
  May never satisfy or override a navigational check.** It may widen uncertainty or annotate
  sensitivity, nothing more.

**Enforce in code, not by comment** — the feasibility gate must reject an `ADVISORY_MODEL` input at
the type level. Test that an advisory value cannot clear a vessel.

The resolver must actually read tide: a berth with a `PORT_RULE` tidal restriction and no valid
window returns `CANNOT_VERIFY`/conditional — **never a silent pass**.

### 3. Empirical waits and handling — evidence-gated

`src/berth_truth/empirical.py`:

- Compute all four intervals separately — they answer different questions:
  `arrival→ready`, `ready→berth`, `arrival→berth`, `berth→sail`.
- Segment by `(port, terminal, berth, vessel_class, commodity, month)` with a documented fallback
  hierarchy up those levels when a cell is thin.
- `WaitDistribution(n, p50, p75, p90, mean, source_level, is_sufficient)`.
- **Minimum-sample rule is mandatory.** Below it (suggest n<20 — justify), `is_sufficient=False`,
  caller keeps the baseline flagged `is_real_data=False`. **Never emit a percentile from 3
  observations.**
- Handling: `norm_tpd` vs `actual_tpd` per berth/commodity → empirical productivity, same gate.
  Replaces `handling_rate_tph` literals **only where the gate passes**.

⚠️ **Critical scoping rule:** some ports have rich archives, others only live snapshots.
**A port with insufficient queue history is NOT an unsupported M4 port.** It can still have
excellent berth feasibility from constraints alone. Degrade *the wait component only* — keep
sample size visible, fall back to the flagged baseline, and never mark the whole port unsupported
because its queue history is thin.

### 4. The M4 engine

`src/berth_truth/reality.py` → `PortRealityReport`:

- **Three-state verdict**: `FEASIBLE` / `INFEASIBLE` / `CANNOT_VERIFY`. Never two.
- Qualifying berth(s)/transfer point, binding constraint, vessel requirement, available margin
- Official source + document date **for every constraint used**
- Tide impact: `NONE` / `CONDITIONAL` / `BLOCKING` + authority level
- Wait: full distribution (P50/P75/P90, n, sufficiency) plus `P(wait > X days)` **where sufficient**;
  otherwise the flagged baseline with `wait_evidence=BASELINE_ONLY`
- Handling rate + implied laytime, with its own sufficiency flag
- `untested_checks`, `stale_inputs`, `observed_envelope`, `declared_vs_observed_conflict`
- Overall confidence, and `source_quality` of the underlying feed (P1) surfaced

### 5. Wire into the optimizer

`_vessel_can_call` and `_can_call` consume `PortRealityReport`'s constraint resolution.
`quote()`/`quote_envelope()` gain access to the wait distribution so laytime/demurrage reflect P50
vs P90 rather than one scalar. **Do not change the CP-SAT encoding**; feed it better numbers.

### 6. API contract

```
GET /ports/{code}/reality?vessel_dwt&draft_m&loa_m&beam_m&commodity&as_of
    → PortRealityReport
GET /ports/{code}/berths   → register rows w/ provenance + effective dates
GET /ports/{code}/calls?from&to&limit&offset → fact_port_call rows, paginated
GET /ports/{code}/waits?vessel_class&commodity → WaitDistribution (or BASELINE_ONLY)
```

Explicit `NOT_AVAILABLE` payload (not an empty 200) for a Level-E port. Unknown port → 422. Cache
reality reports; do not re-read the register per request.

### 7. Port Twin frontend

**PORT → TERMINAL → BERTH** drill-down:

- Live line-up, berth constraints with source links and document dates, observed calls, current
  draft status, **wait rendered as a distribution when sufficient — and clearly as a baseline
  estimate when not**, productivity, feasibility verdict.
- **`CANNOT_VERIFY` renders visibly differently from `FEASIBLE`.** Never a soft pass.
- Every figure shows source and date. `PUBLIC_AGGREGATOR` sources visibly marked lower-confidence.
- A `LIGHTERAGE`/`ANCHORAGE_TRANSFER` port renders its own structure, not empty berth rows.
- A Level-E port renders honestly — "no public operational feed", not a blank success.
- Add to `icon-rail.tsx`. Decide by inspection whether new page or Voyage Desk tab; **no duplicate
  screens**. The repo has **no map library** — do not describe a grid as a map.

## Edge cases / fallback

- No `fact_port_call` history → baseline wait, `is_real_data=False`, visible; **port still supported**.
- `LimitStatus.NOT_PUBLISHED` → does not block, lands in `untested_checks`.
- `draft_status=STALE_OR_UNAVAILABLE` → the draft check **did not run**: never a silent pass, never
  a fail.
- Displacement instead of DWT → untested, stated in the reason.
- Advisory-only tide → annotates uncertainty; **cannot** produce `FEASIBLE`.

## Tests

1. `test_tide.py` — authority hierarchy enforced; **advisory provably cannot clear a vessel**;
   `PORT_RULE` restriction demonstrably changes a verdict.
2. `test_empirical.py` — hand-computed percentiles from a fixture; sample floor enforced; hierarchy
   fallback; **a port with thin history still produces a valid feasibility verdict** with
   `wait_evidence=BASELINE_ONLY`.
3. `test_reality.py` — all three verdicts reachable incl. `CANNOT_VERIFY`; observation never raises
   a declared limit; disagreement surfaced; every number carries a source.
4. `test_registry.py` — network-wide coverage; **the original 3 ports resolve byte-identically**.
5. `tests/backend/test_reality_api.py` — real payload; unknown port 422; Level-E explicit
   unavailable; cache behaviour.
6. Regression: all `opt` tests unchanged; full suite ≥684; `npm run build` passes.

## Acceptance criteria

- Every port resolves to something with honest provenance.
- Tide gates a real verdict; advisory provably cannot.
- Wait is a distribution where evidence allows, a flagged baseline where not — and **thin queue
  history never marks a port unsupported**.
- `CANNOT_VERIFY` reachable and tested.
- Optimizer consumes the new constraints and waits.
- Port Twin renders for a berthed port, a lighterage port, and a Level-E port.

## Commands to run

```
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m ruff check src/ tests/
cd frontend && npm run build
# start backend + frontend, exercise every endpoint, paste real output
```

## Required final response

Files added/modified · tests added · counts · **empirical P50/P90 vs the current static literal for
every port that cleared the gate, and which ports did not clear it** · every declared-vs-observed
disagreement in real data · every tide rule found with authority level · which ports return
`CANNOT_VERIFY` and why · screens built · limitations.

## Execution instruction

**Implement the work now. Do not return another plan. Inspect the current repo first, preserve
functioning work, and continue until SOURCE → BERTH TRUTH → M4 → VOYAGE/FLEETMIX/QUOTE → API → UI
is connected end-to-end. Do not stop after adding classes, interfaces, TODOs or disconnected
modules.**

---
---

# PROMPT 3 — M1 TONNAGE FIELD: IDENTIFICATION GATE, VALIDATION, ABLATION

## Context

**Original Moat 1** is the Tonnage Field in `src/tonnage/` (`basins`, `classmix`, `stockflow`,
`forward`, `supplycurve`, `validate`). 46 passing tests, **almost entirely disconnected**: only
`tonnage.basins` (CSV paths) and `tonnage.classmix.CLASS_MIDPOINT_DWT` (constants) are imported
outside `src/tonnage/`. `stockflow`, `forward`, `supplycurve`, `validate` feed only `src/impact/` —
imported by **nothing**. Its frontend was a string-hash placeholder, since **deleted**, so M1 has no
UI at all.

Known defects, documented candidly in `src/impact/__init__.py` and `tonnage/validate.py`:

1. **Absolute scale unvalidated** — 0.35× to 26× against 12 Signal ballaster points. Root cause:
   `stock_dwt` anchors to *total registered fleet*, not the free/ballasting fraction.
2. **Supply curve wrong-signed** — `r=-0.02` (Capesize), `r=-0.24` (Handysize).
3. **Class mix underidentified** — `classmix.py` honestly documents that mean parcel size is one
   equation for four unknowns.
4. **No IV, no Kalman** despite both being claimed in planning docs.

## Objective

Repair M1's physical meaning **or honestly reframe it**, validate it, test whether it improves the
freight forecast, and ship it end-to-end. **M1 must not become another isolated screen — and must
not delay the PS-critical work in P4.**

## Inspect first

- `src/tonnage/` — all six modules; read `validate.py`'s findings and `src/impact/__init__.py`'s
  docstring
- `src/ml/` — `live_forecast.py`, `model_xgb.py`, `quantiles.py`, `baselines.py`,
  **`frozen_test.py`** (the leakage guard — use it, do not build a second backtest system),
  `features/`
- `src/opt/ceiling.py`, `src/opt/quote.py`, `src/opt/stopping.py`
- `src/berth_truth/` — `fact_port_call` (P1)
- `backend/main.py`, `frontend/src/pages/`, `frontend/src/lib/api.ts`

## Preserve

`tonnage.basins` and `CLASS_MIDPOINT_DWT` — `opt/congestion`, `opt/risk`, `opt/repositioning` import
them. `ml/frozen_test.py` leakage guards.

## Implementation requirements

### 1. HARD IDENTIFICATION GATE — do this first, it governs everything else

Attempt to separate `stock_total_dwt` from `stock_available_dwt` (free/ballasting) using available
evidence. Then **make an explicit, documented determination**:

**Can absolute available/free tonnage be identified from the evidence actually available?**

Port-call history alone **does not** answer this. Observing vessels arrive at Paradip or Gangavaram
tells you nothing about the complete global fleet that is available, ballasting, committed, open, or
positioned in each supply basin. Do not assume otherwise.

- **If YES** — implement it, and validate the absolute scale honestly.
- **If NO** — **do not force an absolute DWT estimate, and do not choose a calibration factor to
  make 12 Signal points fit.** Reframe M1 as a **Physical Supply Pressure Index / Tonnage Tightness
  Index**: a *relative* index, explicitly labelled as relative, with no absolute-DWT claim. Then
  validate whether **changes in the index predict future freight pressure** — that is the real
  question, and a validated relative index is a legitimate result.

**An honest relative pressure index is better than a fake absolute free-tonnage number.** State
which branch you took and why, prominently, in your report.

Ballast state: do **not** rely on a single draught threshold as maritime truth. If vessel-level
features legally exist, treat it probabilistically; otherwise keep the aggregate estimator and state
the uncertainty.

### 2. Vessel class — INFERRED, not ground truth

`fact_port_call` gives **observed dimensions**, not vessel particulars. Model this honestly:

```
OBSERVED_DIMENSIONS (loa_m, beam_m, arrival_draft_m)
        ↓ inference, with confidence
INFERRED_VESSEL_CLASS
```

LOA + beam + draft **help** infer class but are **not exact** without DWT/IMO or an external vessel
record. Only treat a class as ground truth where an external vessel record supplies actual DWT/class.

- Build the classifier from dimensions; report a **confidence per inference**.
- Validate accuracy **separately for Capesize, Panamax, Supramax, Handysize** — a pooled number
  hides the failure that matters.
- Use it to calibrate/validate class mix at covered ports; **label extrapolation to uncovered basins
  explicitly** in the output.
- Where an IMO is available and an external vessel record can be joined, upgrade that record from
  `INFERRED` to `DECLARED` and say how many records that covers.

### 3. Supply curve — diagnose before fixing

Systematically test each candidate explanation for the wrong sign: scale error (may change after
task 1), confounding, sparse freight labels, regime effects, incorrect lag, aggregation level,
target transformation. **Report which explains it.**

**Do not force the sign.** If it does not validate after honest diagnosis, report a negative result
and reduce the claim. A validated negative result is a real contribution; a forced positive one is
fraud.

### 4. IV and Kalman — justify or delete

- **IV**: determine whether a defensible instrument genuinely exists (argue relevance *and*
  exclusion). If not, **remove the IV claim from all documentation.**
- **Kalman/state-space**: implement only if it demonstrably improves out-of-sample latent estimation.
  **Show the comparison.** Otherwise remove the claim.

### 5. Forward physical tightness

`forward.py` must answer: *is physical tonnage likely to tighten or loosen over the coming fixing
window?* — with intervals, not a point estimate. Not a historical heatmap. If task 1 took the
relative-index branch, this is a forward *tightness index*, labelled as such.

### 6. Validation

Time-based holdout; no leakage (use `frozen_test`); lead/lag analysis; **per-vessel-class metrics**;
basin diagnostics; regime/stability diagnostics; confidence intervals; negative results reported.
**Never claim "predicts freight N days ahead" unless a holdout test demonstrates it.**

### 7. The ablation — this decides how M1 ships

```
A: existing forecast model
B: existing forecast model + M1 physical-pressure features
```

Out-of-sample, **future** time-based holdout, per vessel class, leakage guard active.

- **If B measurably beats A** → wire the M1 signal into the production forecast/decision path
  (`ml/live_forecast.py` → `opt/ceiling.py` → `opt/quote.py`), and state by how much.
- **If it does not** → **do NOT force it into pricing to make the moat look connected.** Ship it as
  an honestly-labelled decision-support signal, surface the neutral/negative ablation in the UI, and
  say so plainly.

Both outcomes are acceptable. Silently wiring an unvalidated signal into pricing is not.

### 8. API contract

```
GET /tonnage-field?as_of            → tightness by basin × class, uncertainty, provenance,
                                      sample sufficiency, index_type (ABSOLUTE|RELATIVE), computed_at
GET /tonnage-field/forward?horizon  → forward tightness with intervals
GET /tonnage-field/validation       → holdout metrics per class + the ablation result
```

**Cache the reconstruction** — it takes ~7 s (33,396 rows × date/basin/class, 128 ports). A 7-second
recompute per UI action is unacceptable: compute at startup or on TTL, expose `computed_at`. Warm
responses < 500 ms.

### 9. Tonnage Field frontend

Current tightness, forward tightness with uncertainty bands, class and basin breakdown, evidence
quality, historical movement, **the ablation result stated honestly**, and — if task 1 took the
relative branch — **clear labelling that this is a relative index, not absolute free tonnage**.

Provenance per figure: `OBSERVED` / `ESTIMATED` / `MODEL_DERIVED` / `DECLARED` / `INFERRED`.
**No hash-generated or synthetic value anywhere** — add a guard test. `visx` is already present; do
not add a charting library without justification.

## Edge cases / fallback

- Basin/class cell with insufficient data → reported insufficient, not interpolated.
- Reconstruction failure → cached last-good with a visible staleness flag, never silent zeros.
- Relative-index branch → the UI must present M1 as a **relative pressure index**, never as absolute
  available tonnage.

## Tests

1. `test_identification_gate.py` — the absolute-vs-relative determination is explicit and recorded;
   **no arbitrary calibration factor is applied** (assert no unexplained constant scaling).
2. `test_classmix.py` — per-class inference accuracy against labelled data; **inference carries a
   confidence**; extrapolation flagged; `INFERRED` never presented as `DECLARED`.
3. `test_stockflow.py` — total vs available distinct where the gate passed; relative index correctly
   labelled where it did not.
4. `test_supplycurve.py` — diagnostics asserted against real output, **not a desired sign**.
5. `test_forward.py` — intervals present and correctly ordered.
6. `test_validate.py` — scale/index results asserted against real output; ablation recorded.
7. `tests/backend/test_tonnage_api.py` — real payload; `index_type` present; `computed_at`; cache;
   warm timing.
8. **Guard test: no FNV/hash-derived data source anywhere in `frontend/src/`.**
9. Regression: `opt/congestion`, `opt/risk`, `opt/repositioning` unaffected; full suite ≥684.

## Acceptance criteria

- The identification gate is decided explicitly and the model is labelled accordingly
  (`ABSOLUTE` or `RELATIVE`).
- **No arbitrary calibration factor introduced.**
- Class inference is per-class validated and labelled `INFERRED` unless an external record upgrades it.
- Sign problem explained with evidence, or a negative result reported.
- IV and Kalman implemented-and-validated **or explicitly removed as claims**.
- Ablation run, reported, acted on correctly in **either** direction.
- `/tonnage-field` < 500 ms warm; UI shows uncertainty; no placeholder.
- Documentation claims match what validation supports.

## Commands to run

```
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m ruff check src/ tests/
cd frontend && npm run build
# run the reconstruction and the ablation; paste real timings and metrics
```

## Required final response

Files added/modified · tests added · counts · **the identification-gate determination and its
reasoning** · old vs new scale/index results · **per-class inference accuracy** · the sign diagnosis
with evidence · IV verdict · Kalman verdict with comparison · **the full ablation table (A vs B,
per class, out-of-sample)** and what you did as a result · warm timing · **every claim you removed
from the docs** · limitations.

## Execution instruction

**Implement the work now. Do not return another plan. Inspect the current repo first, preserve
functioning work, and continue until DATA → PHYSICAL MODEL → VALIDATION → FORECAST/DECISION SUPPORT
→ API → UI → TEST is connected end-to-end. Do not stop after adding classes, interfaces, TODOs or
disconnected modules.** If the identification gate fails, ship the honest relative index — do not
stop, and do not manufacture an absolute number. If the ablation is negative, ship the honest
decision-support path — do not force the wiring.

---
---

# PROMPT 4 — ROUTE-AWARE FORECASTING + DATA SEMANTICS CORRECTIONS

## Context

**This is the highest-priority prompt in the set.** The PS headline ask is *"forecasting freight
rates by vessel class and route."* The route half does not exist.

`BasisEntry` is defined at `src/opt/types.py:50` and **instantiated nowhere in `src/`** — only in
tests. `src/opt/quote.py:202` passes `basis={}` with a comment acknowledging the gap. Measured
against the live models — four origins across Australia, South Africa, Indonesia and the US Gulf,
same cargo, destination and laycan:

```
         origin |  $/day ceiling | $/MT ceiling | transit d
   NEWCASTLE_AU |      18,141.46 |        4.395 |     18.17
   RICHARDS_BAY |      18,141.46 |        3.619 |     14.96
     BALIKPAPAN |      18,141.46 |        2.036 |      8.42
  HAMPTON_ROADS |      18,141.46 |        7.686 |     31.77
```

`$/day` identical to the cent. Only `$/MT` varies — and only because transit differs. On a dashboard
that reads as route-aware pricing while not being it.

Three further defects: **PortWatch semantics** (`portcalls_dry_bulk` is a real AIS-derived count,
but `import_dry_bulk`/`export_dry_bulk` are PortWatch's own *model estimates*, consumed as if
measured); **thin `$/day` history** (`CAPESIZE_TCAVG` 279 rows, `PANAMAX` 275,
`SUPRAMAX`/`HANDYSIZE` 185 each from 2025-11-19; deeper history is index points through a fitted
affine map that has **broken three times in twenty months**); **no point-in-time data** (PortWatch
revises history; the repo holds one snapshot).

## Objective

Make forecasting genuinely route-aware **or honestly labelled as class-only**, add macro signals that
measurably help, fix the three data-semantics defects.

## Inspect first

- `src/opt/types.py:50` (`BasisEntry`), `src/opt/quote.py` (~197-202, `basis={}`),
  `src/opt/calibration.py`, `src/opt/ceiling.py`
- `src/ml/` — `live_forecast.py`, `targets.py`, `units.py`, `features/`, `frozen_test.py`,
  `baselines.py`, `export.py`
- `src/data_builders/` — `build_master.py`, `harvest_portwatch.py`
- `raw_data/` — `portwatch/`, `investing_com/`, `signal_weekly/`, `baltic_routes.csv`,
  `handybulk_index_levels.csv`, `sources.md`

## Preserve

`ml/frozen_test.py` leakage guards and its repo-wide static check. The existing pipeline shape.
`ml/units.py` conversions.

## Implementation requirements

### 1. Route basis — evidence first, honesty second, never fabrication

Build a route-basis mechanism from **actual rate/basis evidence**. Candidate inputs:
`raw_data/baltic_routes.csv`, any route-level fixture/rate series you can source,
`opt/geography.py` distances.

⚠️ **Port-call volumes and route traffic do NOT tell you the freight-rate premium in dollars/day.**
Use them as **explanatory variables only where validation supports it** — never as a direct
substitute for rate evidence.

Where route-level rate observations are insufficient, return the labelled decomposition:

```
CLASS_BENCHMARK + MODELLED_ROUTE_ADJUSTMENT + UNCERTAINTY
```

with the modelled component **clearly identified as modelled**.

Where no defensible adjustment can be constructed for a route, return
**`ROUTE_RATE_BASIS_UNAVAILABLE`** and fall back to the class forecast — surfaced in API and UI.

**Do not fabricate different rates merely to make a test pass.** See the acceptance criteria: the
test rewards *either* validated route rates *or* an explicit class-only forecast with route basis
unavailable.

### 2. Instantiate `BasisEntry` in production

`quote()`/`quote_envelope()` must receive a real basis table, built once and cached — even if that
table's entries are largely `ROUTE_RATE_BASIS_UNAVAILABLE`. Wire through `opt/calibration.py` if
that is the natural home; inspect and decide.

### 3. `$/MT` labelling

**Until route basis is real, stop presenting `$/MT` as a route quote.** Either it becomes genuinely
route-aware, or rename the field and the UI label to reflect what it is: `class_rate ÷ transit_days`.

### 4. Macro / commodity signals

Research public sources for: coking coal, iron ore, steel activity, bunker/crude proxy, FX, global
industrial activity.

For **each** candidate run and report: economic rationale → lag analysis → leakage review →
ablation → out-of-sample test. **Keep only what measurably improves out-of-sample accuracy.** Do not
dump features into XGBoost because data exist. **Record every rejected feature and why** — the
rejections are as valuable as the acceptances.

### 5. PortWatch provenance semantics

Explicit provenance enum carried **through to API and UI**:
`OBSERVED · ESTIMATED · INFERRED · MODEL_DERIVED · DECLARED`

- `portcalls_dry_bulk` → `OBSERVED`
- `import_dry_bulk` / `export_dry_bulk` → `ESTIMATED` (PortWatch's own model output)
- Downstream derivations → `MODEL_DERIVED`

Audit **every** consumer (`tonnage/`, `opt/congestion.py`, `opt/risk.py`, `opt/repositioning.py`) —
no estimate may be presented as a measurement anywhere it reaches a user.

### 6. Thin-series audit

Quantify and expose the difference between directly-observed `$/day` history and affine-converted
index history. Report per class: rows of direct history, rows of converted history, the affine map's
breakpoints, and error where both exist. **Do not silently treat converted history as equally strong
ground truth** — mark it, and consider down-weighting it in training.

### 7. Point-in-time snapshots

Archive source snapshots forward (PortWatch and any revised series), keyed by retrieval date, so
future backtests are scored against what was knowable. For historical periods without vintage data,
**disclose the limitation** in backtest output. **Integrate with `ml/frozen_test.py` — do not create
a second backtest system.**

## API / frontend

Quote response gains `route_adjustment` (nullable), `route_evidence`
(`OBSERVED` / `MODELLED` / `ROUTE_RATE_BASIS_UNAVAILABLE`), and per-figure `provenance`. The Voyage
Desk rate table must show whether a rate is route-adjusted or class-only, and **must not imply
route-awareness that does not exist.** Surviving macro features appear as forecast context.

## Edge cases / fallback

- No route evidence → `ROUTE_RATE_BASIS_UNAVAILABLE`, surfaced, class forecast used.
- Macro source unavailable at inference → feature dropped, forecast still produced, flagged.
- Series with only converted history → usable but marked lower-confidence.

## Tests

1. `tests/opt/test_basis.py` — **the acceptance test must pass on EITHER branch**: (a) the four
   origins return genuinely validated different `$/day`, OR (b) the response explicitly carries
   `ROUTE_RATE_BASIS_UNAVAILABLE`/`MODELLED` with the class-only fallback visible. **Assert against
   real model output. A test that only passes by fabricating differences is a failing test.**
2. `BasisEntry` is instantiated in a production path (not only tests).
3. `tests/ml/test_macro_features.py` — each retained feature has a recorded ablation result; leakage
   guard passes.
4. `tests/data_builders/test_provenance.py` — every PortWatch field carries correct provenance; no
   `ESTIMATED` value surfaces labelled `OBSERVED`.
5. Thin-series audit numbers asserted against the real master table.
6. Point-in-time archive round-trips; `frozen_test` green.
7. Full suite ≥684; `npm run build` passes.

## Acceptance criteria

- Route forecasting is **either** genuinely validated **or** explicitly reported unavailable — in
  API and UI. No fabricated premiums.
- `$/MT` is genuinely route-aware or renamed to what it is.
- Every retained macro feature has a documented out-of-sample ablation result.
- Provenance labels reach the UI.
- Thin-series difference quantified and visible.
- Snapshot archiving runs and is tested.

## Commands to run

```
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m ruff check src/ tests/
cd frontend && npm run build
# re-run the four-origin comparison; paste the before/after table
```

## Required final response

Files added/modified · tests added · counts · **the four-origin `$/day` table before and after, and
which branch you landed on** · every macro signal tested with ablation result and kept/dropped ·
provenance audit findings · thin-series numbers per class · what remains genuinely unavailable.

## Execution instruction

**Implement the work now. Do not return another plan. Inspect the current repo first, preserve
functioning work, and continue until the forecast path is genuinely class-aware AND route-aware —
or honestly labelled class-only with route basis unavailable — end-to-end through API and UI. Do not
stop after adding classes, interfaces, TODOs or disconnected modules. Do not manufacture route
premiums to satisfy a test.**

---
---

# PROMPT 5 — DECISION INTELLIGENCE: DF + REGRET LEDGER + REPLAY

## Context

Two subsystems, both incomplete the same way — the model exists, nothing can see it.

**DF (`src/fragility/`)** answers "how far is this recommendation from changing?" by re-invoking the
real engines and bisecting to a flip point. 29 passing tests. A full 8-variable sweep on a real cargo
takes ~17 s and 57 solves against a 200 cap, and is **deterministic** (identical `model_dump()`
across runs — `opt.stopping.solve_lock_or_wait`'s `seed=0` default is never overridden). Variables:
cargo volume, origin/dest wait days, vessel draft, permissible draft, laycan width, risk tolerance,
contract term. Three cost tiers (closed-form → fleet-mix → full `quote_envelope`).

DF **correctly reports `UNAVAILABLE`** for both wait-day variables today, because — per its own
engine docstring — `opt/fleetmix.py` never reads `dynamic_wait_days` and no parameter on
`quote()`/`quote_envelope()` overrides it. There was no honest injection point. **After P2 there
may be one — verify, do not assume.**

**Regret**: `src/opt/stopping.py` implements Longstaff–Schwartz optimal stopping and drives the
production LOCK/WAIT decision. The *regret* half was never built. Known context: measured decision
value is **~$0.60/day pooled, $0.00/day for Supramax** (which LOCKed on every decision point),
against an oracle showing $56.90/day available.

Neither has an endpoint or a screen.

## Objective

Feed DF the real M4 inputs and ship it; build the live regret ledger; add a **separately labelled**
historical replay for demo purposes; expose all of it.

## Inspect first

- `src/fragility/` — `engine.py` (**read the per-variable tier table in its module docstring — it
  documents exactly why each variable sits where it does**), `search.py`, `tiers.py`, `models.py`
- `src/opt/stopping.py` — `solve_lock_or_wait`, the exercise boundary
- `src/opt/backtest.py`, `src/ml/frozen_test.py`
- `src/berth_truth/empirical.py` + `reality.py` (P2)
- `backend/main.py`, `frontend/src/pages/`, `components/shell/icon-rail.tsx`

## Preserve — DF's architecture is a key differentiator; do not materially change it

- `DecisionSignature`, tiered escalation, memoisation, **determinism**, evaluation caps, and the
  `UNAVAILABLE` discipline. The identical-sweep determinism test must keep passing.
- `_WAIT_DAYS_UNAVAILABLE_REASON` may only be removed **if P2 genuinely created an injection
  point** — verify by reading the code.
- `opt/stopping.py`'s LSMC logic. The ledger accounts around it; it does not change it.

## Implementation requirements

### DF

1. Add variables now backed by real evidence from P2: **empirical berth wait (P50 and P90
   separately)**, berth-specific draft limit, operational/declared draft status, tide sensitivity
   where a `PORT_RULE` exists, vessel-class and cargo boundaries.
2. Wire wait-day variables **only if** P2 created a real override path. If not, keep `UNAVAILABLE`
   and update the reason to reflect current code. **Do not weaken `UNAVAILABLE` to populate a
   screen.** Empirical waits only where evidence exists.
3. Target output shapes:
   - *"Recommendation = Panamax, but +7,500 t flips to Capesize."*
   - *"Permissible draft margin = 0.18 m — operationally fragile."*
   - *"Destination P90 berth wait above 4.2 days flips LOCK → WAIT."*
4. **Fragile / stable ranking** across variables so the UI leads with what matters for this cargo.
5. Keep solves bounded and cached — reuse the existing `_Memo` and per-tier caps.

### Live Regret Ledger — forward-only

6. `src/opt/ledger.py` — append-only. On each recommendation store: decision timestamp, **full input
   state** (enough to reproduce), forecasts and uncertainty, the recommendation, the alternative
   considered, the exercise boundary, model/data versions.
7. As real outcomes arrive: realised outcome, what waiting would have cost, whether LOCK/WAIT was
   correct, realised regret, cumulative performance, comparison against naive baselines
   (always-lock, always-wait, oracle where computable).
8. **Do not fabricate historical SAIL recommendations.** The live ledger starts empty and fills
   forward. An empty ledger renders as empty, honestly.
9. Entries are **never mutated**. Outcome scoring appends a linked record.

### Historical Replay — separate, and clearly labelled

10. Additionally provide **`HISTORICAL_MODEL_REPLAY`** using genuine backtesting over real historical
    data (reuse `opt/backtest.py` and `ml/frozen_test.py` — **do not build a second backtest
    system**). This is what makes the screen non-empty for a demo.
11. **Keep the two conceptually and visually separate**: `LIVE_DECISION_LEDGER` (real
    recommendations this system made, forward-only) vs `HISTORICAL_MODEL_REPLAY` (retrospective
    model simulation). Different data structures, different API fields, different UI sections, and
    the replay explicitly labelled **"retrospective model simulation — not decisions this system
    actually made."** Never merge their statistics into one number.

## API contract

```
POST /fragility          → QuoteRequest-shaped body + optional variable subset
                           → findings[] with flip_value | unavailable_reason, tier,
                             evaluations_used, fragile/stable ranking
GET  /ledger/live?from&to        → real recommendations + outcomes where known
GET  /ledger/live/performance    → cumulative regret, correctness, baseline comparison
POST /ledger/outcome             → record a realised outcome against an entry
GET  /ledger/replay?from&to      → historical backtest replay, explicitly labelled
```

`/fragility` must stay within a sane latency budget — reuse DF's caps; support a variable subset and
stream or paginate if a full sweep is too slow for a request cycle.

## Frontend

- **Fragility screen**: base recommendation, nearest flip points ordered by fragility, fragile/stable
  ranking, **unavailable variables shown with reasons — not hidden**. A variable with no evidence
  must be visibly distinct from one with no flip in range.
- **Ledger screen**: two clearly separated sections — Live Decision Ledger (empty state renders
  honestly, no seeded examples) and Historical Model Replay (labelled retrospective).
- Add both to the icon rail; reuse `components/desk/` primitives.

## Edge cases / fallback

- Insufficient empirical waits at a port → that DF variable stays `UNAVAILABLE` with a reason.
  **Never manufacture a flip point.**
- Cap reached before a flip → `flip_found=False` **plus the range actually searched** — never a
  fabricated bound.
- Ledger entry with no outcome → pending, excluded from performance stats.
- If measured edge remains ~zero → **report it.** An honest ledger showing no edge is a stronger
  artifact than a hidden one.

## Tests

1. Extend `tests/fragility/test_engine.py` — empirical-wait flip on a port with sufficient data;
   still `UNAVAILABLE` on a port without; **determinism test still passes**; caps respected;
   ranking ordering.
2. `tests/backend/test_fragility_api.py` — real payload; unavailable variables present with reasons;
   latency within budget.
3. `tests/opt/test_ledger.py` — append-only enforced (mutation fails); regret arithmetic on a
   fixture; baseline comparison; empty-state; outcome linking; **live and replay statistics never
   merge**.
4. `tests/backend/test_ledger_api.py` — live and replay are distinct endpoints/fields.
5. Regression: full suite ≥684; `npm run build` passes.

## Acceptance criteria

- DF determinism preserved; identical sweeps return identical flip values.
- A wait-driven flip appears **only** where empirical data supports it.
- Unavailable variables visible with reasons in API and UI.
- Live ledger append-only, starts empty, compares against real baselines.
- Historical replay present, genuinely backtested, and **unmistakably labelled retrospective**.
- Both screens render and are reachable.

## Commands to run

```
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m ruff check src/ tests/
cd frontend && npm run build
# run a real fragility sweep (paste timing + findings); exercise all endpoints
```

## Required final response

Files added/modified · tests added · counts · new DF variables and their evidence source · which
stayed `UNAVAILABLE` and the verified reason · sweep timing and determinism proof · ledger schema ·
**live vs replay separation as implemented** · current measured performance vs baselines (**report
honestly even if ~zero**) · limitations.

## Execution instruction

**Implement the work now. Do not return another plan. Inspect the current repo first, preserve
functioning work, and continue until REAL M4 INPUTS → FLIP SEARCH → API → UI → TEST and
RECOMMENDATION → PERSISTENCE → OUTCOME SCORING → API → UI → TEST are both connected end-to-end.
Do not stop after adding classes, interfaces, TODOs or disconnected modules.**

---
---

# PROMPT 6 — COMMERCIAL UPGRADES (FEASIBILITY-GATED)

## Context

Core moats are complete after P1–P5. This prompt adds commercial capabilities **only where the data
genuinely support them**, and resolves a long-standing disconnected subsystem.

`src/impact/` implements Almgren–Chriss market-impact execution scheduling (`elasticity.py`,
`execution.py`, `fixedpoint.py`) with **38 passing tests and zero consumers**. Its own
`__init__.py` docstring states it depends on `tonnage.stockflow`'s absolute scale and
`tonnage.supplycurve`'s rate~tightness slope — **both of which P3 investigated.**

P1's `fact_port_call` includes a `load_discharge` flag. East-coast India imports coking coal and
exports iron ore from the same berths in the same week.

⚠️ **Priority reminder: nothing in this prompt may delay or compromise P4 (route forecasting) or
P2 (berth reality). If a capability here does not validate, ship it informational or drop it.**

## Inspect first

- `src/impact/` — all three modules and `__init__.py`'s docstring
- **P3's final report** — specifically its identification-gate determination and supply-curve sign
  diagnosis. This governs the `src/impact/` decision below.
- `src/opt/repositioning.py` — the per-port Poisson hazard model; **reuse it, do not rebuild**
- `src/opt/voyage.py` — the CP-SAT encoding. **Do not rewrite it.**
- `src/opt/fleetmix.py`, `src/opt/quote.py`, `src/opt/network.py` (`handling_rate_tph`)
- `src/berth_truth/` — `fact_port_call`, `empirical.py`, `reality.py`

## Preserve

Everything. This prompt adds modules and connects existing ones; it rewrites nothing. Do not touch
the CP-SAT encoding or the LSMC decision logic.

## Implementation requirements

### 1. Return Leg — opportunity score first, dollars only if earned

Create `src/opt/backhaul.py` (separate module; do not modify the CP-SAT encoding).

⚠️ **Observed inbound coal + outbound ore at the same port does NOT by itself justify a monetary
backhaul credit.** A genuine backhaul also depends on: timing alignment, parcel compatibility,
vessel suitability, next destination, charter terms, hold-cleaning and cargo restrictions, and
actual freight economics.

Therefore, in order:

1. Build a **`BackhaulOpportunityScore`** — a bounded, explainable score combining observed
   load/discharge pairing frequency (from `fact_port_call`), timing overlap, vessel-class
   compatibility, and the existing Poisson hazard rates. State its inputs and its limits.
2. **Only** convert the score into a $/MT credit **if the data actually support that calculation** —
   meaning you can evidence the freight economics, not just the co-occurrence. Document the test you
   used to decide.
3. If it does not support a dollar figure: **keep it informational**, clearly labelled as an
   opportunity indicator, and do **not** let it move the recommendation.

### 2. Landed cost layer

The system optimises **freight**, not delivered cost, while the PS names commodity price trends.
Build `src/opt/landed_cost.py`:

```
landed_cost = freight
            + expected wait/delay cost   (P2 empirical distributions, where sufficient)
            + handling cost              (see caveat below)
            + expected demurrage         (see caveat below)
            + commodity price            (P4, if secured)
            + FX                         (P4, if secured)
```

⚠️ **A handling *rate* is not a handling *cost*, and a wait distribution is not demurrage.**
Converting either into money requires commercial terms — tariffs, laytime allowances, demurrage
rates — that this repo does not have.

- Every component **separately visible** with its own provenance and its own availability flag.
- Where commercial terms are unavailable, require **explicit user-entered assumptions** with clearly
  labelled defaults surfaced in the UI. **Never silently invent SAIL demurrage rates, commodity
  contract terms, or charter terms.**
- An unavailable component renders as `None` with the gap visible — never folded into a total that
  hides it.

### 3. Port operational penalty

Use P2's observed handling/discharge productivity to influence optimal parcel size, expected
laytime, demurrage exposure and vessel selection. Replace flat `handling_rate_tph` literals **only
where the empirical sufficiency gate passes**; elsewhere keep the literal, flagged.

**Scope honestly**: build only what port observations support. Rail/rake/stockyard data for the
plant-side leg does not exist publicly and is not in this repo — **do not fabricate it.** Label this
the port-side half of port-to-plant coupling.

### 4. `src/impact/` — decide from P3's evidence

**Read P3's identification-gate determination and supply-curve diagnosis first.**

- **If P3 concluded M1's absolute scale is not identifiable, or the rate~tightness elasticity does
  not validate** → the Almgren–Chriss inputs do not validate. **Mark `src/impact/` experimental in
  its docstring, remove it from the main moat claims and product documentation, and state this
  plainly in your report. Do not spend significant effort forcing it into the shipped product.**
  This is the expected outcome and it is the correct one.
- **Only if P3 validated both** → connect it: a real endpoint and a screen that genuinely plots the
  Almgren–Chriss execution frontier (optimal fixing schedule under market impact) from real
  `tonnage.supplycurve` output. **It must actually represent that model** — the old "Execution
  Frontier" screen was a risk-tolerance sweep over the ceiling rule wearing this moat's name.

Either outcome is acceptable. **Shipping a mathematically impressive but unsupported screen is not.**

## API / frontend

```
GET  /quote → extended with backhaul opportunity score (+ credit only if validated)
              and landed-cost breakdown, component-wise with availability flags
POST /landed-cost → recompute with user-supplied commodity price / FX / laytime / demurrage terms
GET  /execution-frontier → ONLY if §4 concluded "connect"
```

Frontend: landed-cost breakdown on the Voyage Desk showing each component, its provenance, and
whether it is real or a user assumption; backhaul opportunity surfaced with its evidence and its
limits; user-assumption inputs unmistakably marked as assumptions.

## Edge cases / fallback

- No observed backhaul pairs for a route → no score, stated, not zero-filled.
- Commodity price unavailable → landed cost returns freight + port components with the commodity
  component explicitly `None` and the gap shown.
- Insufficient handling data → literal retained, flagged.
- Missing commercial terms → user-assumption input, never a silent default.

## Tests

1. `tests/opt/test_backhaul.py` — opportunity score on real observed pairs; **credit applied only
   when the documented validation passes**; no pairs → no score.
2. `tests/opt/test_landed_cost.py` — component-wise breakdown; unavailable component surfaces `None`
   not 0; user assumptions labelled; arithmetic on a fixture; **no hardcoded commercial term**.
3. `tests/opt/test_operational_penalty.py` — empirical rate used only above the sufficiency gate.
4. If connected: `tests/backend/test_execution_api.py` plus a test that the screen's data source is
   genuinely `src/impact/`. If not connected: a test asserting `src/impact/` is not referenced by
   product claims.
5. Regression: CP-SAT and LSMC outputs unchanged on fixed inputs; full suite ≥684; `npm run build`.

## Acceptance criteria

- Backhaul ships as a validated credit **or** an informational opportunity score — decided by
  documented evidence.
- Landed cost shows every component with provenance, never hides an unavailable one, and invents no
  commercial term.
- Operational penalty uses empirical data only above the gate.
- `src/impact/` is genuinely reachable end-to-end **or** explicitly marked experimental and removed
  from moat claims.

## Commands to run

```
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m ruff check src/ tests/
cd frontend && npm run build
# run a real quote showing backhaul + landed cost; paste output
```

## Required final response

Files added/modified · tests added · counts · **the backhaul validation test you used and its
result** · landed-cost components available vs user-assumed · which handling rates became empirical ·
**the `src/impact/` decision, the P3 evidence behind it, and how you executed it** · limitations.

## Execution instruction

**Implement the work now. Do not return another plan. Inspect the current repo first, preserve
functioning work, and continue until each capability is connected end-to-end or explicitly and
honestly withdrawn. Do not stop after adding classes, interfaces, TODOs or disconnected modules.**
Where evidence is insufficient, ship the informational version and say so — do not manufacture a
commercial figure.

---
---

# PROMPT 7 — FINAL INTEGRATION + JUDGE-READY AUDIT

## Context

P1–P6 built the Berth Truth foundation, completed M4 and M1, fixed route forecasting and data
semantics, shipped DF and the regret ledger, and added the commercial layer.

This prompt introduces **almost no new modelling**. Its job is to verify everything genuinely ships,
fix demo-blocking defects, and honestly audit what was and was not closed.

## Objective

Verify end-to-end connectivity for every subsystem, fix operational defects, produce a defensible
closure report.

## Inspect first

- `run.bat`, `run.ps1` — both call `uv run uvicorn …`; **`uv` is not installed on the target
  machine**, so the one-command launcher fails as shipped. The working path is `.venv` directly.
- `src/ml/features/calendar.py:34` — deprecated/ambiguous `polars` `is_in` form
- Starlette/`httpx` deprecation warning in the suite
- `backend/main.py` — every endpoint added across P1–P6
- `frontend/src/pages/`, `components/shell/icon-rail.tsx` — every screen
- `docs/plan.md`, `raw_data/sources.md`, README claims

## Preserve

Everything that works. This is hardening and verification.

## Implementation requirements

### 1. Ship-verification matrix — the core deliverable

For **each** subsystem, verify every link **by executing it**, and record the providing
file/function:

| Subsystem | Chain that must be proven |
|---|---|
| **M1** | real data → validated physical model (absolute or relative) → forecast/decision support → API → UI → test |
| **M4** | port data → Berth Truth → wait/queue/handling → feasibility → optimizer → API → UI → test |
| **DF** | real M4 inputs → flip search → API → UI → test |
| **Forecast** | class-aware AND (route-aware OR explicit route-unavailable) → quote → UI → test |
| **Regret** | persisted recommendations → outcomes → scoring → API → UI → test, with live/replay separated |
| **Commercial** | backhaul score + landed cost → quote → UI → test |

**A subsystem is not "finished" because its tests pass while nothing calls it.** Any broken link is
either fixed here or reported as genuinely unshipped — **no third option.**

### 2. PS requirement coverage

Separately verify the direct problem-statement deliverables, in priority order:

1. freight forecasting **by vessel class and route** (or honest route-unavailable)
2. LOCK/WAIT market-entry recommendation
3. vessel-type recommendation for cargo + origin/destination with port constraints at **both** ends
4. idle/repositioning strategy
5. risk/early-warning
6. macro/commodity context
7. explainability
8. **PS-named country coverage** (Australia, US, Mozambique, **Russia**, Indonesia) and all seven
   Indian discharge ports — report any gap P1 could not close

### 3. Operational fixes

- Rewrite `run.bat`/`run.ps1` to use `.venv` directly (drop `uv`), verify from a clean shell,
  document the steps.
- Resolve the `polars is_in` deprecation and the Starlette/`httpx` warning, or justify each.
- **Frontend note**: `npm install` on this machine did not fetch
  `@rolldown/binding-win32-x64-msvc` (a known npm optional-dependency bug), breaking `npm run dev`.
  Document the workaround (`npm pack` the binding, place it in `node_modules/@rolldown/`) or pin a
  version that installs cleanly.

### 4. Performance and caching

Every endpoint warm-cached and measured. **No UI action may trigger a multi-second recompute** — the
M1 reconstruction alone is ~7 s cold. Paste real timings for every endpoint.

### 5. Labels and claims audit

- No screen shows placeholder or synthetic data — assert with a test.
- Every screen's title matches what it renders (the old "Execution Frontier" mislabel is the
  cautionary example).
- `docs/plan.md` and README claims match what validation supports. **Remove every claim P3/P4/P6 did
  not substantiate** — including the M1 framing if P3 took the relative-index branch, and the
  Almgren–Chriss claim if P6 marked it experimental.
- Provenance labels reach the UI where materially relevant.

### 6. Closure report against the original audit

For each defect below, state **closed / partial / open** with evidence:

1. Route-level forecasting absent / identical `$/day` across origins
2. Tonnage Field hash placeholder
3. Moats unreachable over HTTP
4. Optimiser measured edge ≈ zero
5. No macro/commodity indicators
6. Tonnage Field scale unvalidated + wrong-signed supply curve
7. Thin `$/day` series
8. PortWatch estimates treated as observations
9. No point-in-time data
10. Launcher assumes `uv`
11. PortWatch coverage holes (**verified: GANGAVARAM, SAGAR_SANDHEADS, GLADSTONE_AU,
    HAMPTON_ROADS, SINGAPORE have no PortWatch file**)
12. Berth Reality Engine ~1/3 complete (no tide, no berth-level, no queue)
13. `src/impact/` built but invisible

**Do not mark anything closed that you have not verified by running it.**

## Edge cases / fallback

- A subsystem that genuinely cannot be connected → say so plainly with the reason. An honest "not
  shipped" is required; a false "shipped" is not acceptable.
- Ports with no operational feed → confirm they degrade gracefully across every screen.

## Tests

1. `tests/test_integration_e2e.py` — per subsystem, an end-to-end test exercising data → model → API
   and asserting a real (not empty) payload.
2. Guard test: **no placeholder/synthetic data source anywhere in `frontend/src/`**.
3. Performance test: each endpoint under its documented warm budget.
4. Full regression: entire suite green.
5. `npm run build` and `npx tsc --noEmit` clean.

## Acceptance criteria

- Every link in the ship-verification matrix proven or explicitly reported unshipped.
- PS requirement coverage reported, including any country gap.
- Launcher works from a clean checkout with documented steps.
- Deprecations resolved or justified.
- Every screen loads against a running backend with real data.
- No placeholder anywhere.
- Documentation claims match validated reality.
- Full suite green; frontend builds.

## Commands to run

```
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m ruff check src/ tests/
cd frontend && npm run build && npx tsc --noEmit
# start backend + frontend from the fixed launcher
# exercise EVERY endpoint and EVERY screen; paste real output and timings
```

## Required final response

- The **complete ship-verification matrix** with the providing file/function per link
- The **PS requirement coverage report**, including PS-named country coverage
- The **13-point closure report** — closed / partial / open, with evidence
- Final test count with real output
- Endpoint timings
- Every screen and its state
- Launcher/setup steps verified from clean
- **Remaining honest limitations — what a judge can and cannot see**

## Execution instruction

**Do the verification and the fixes now. Do not return another plan. Inspect the current repo first,
preserve functioning work, and continue until every subsystem is either proven connected end-to-end
or explicitly reported as unshipped with a reason. Do not mark anything closed that you have not
verified by executing it.**
