# SIH26006 — Completing Original Moat 1 and Original Moat 4

**Canonical names used throughout this document:**

| Code | Name | Lives at |
|---|---|---|
| **M1** | Tonnage Field / Physical Freight Pressure | `src/tonnage/` |
| **M4** | Berth Reality Engine | *not yet a module — BT is its foundation* |
| **BT** | Berth Truth Data Foundation (BT-0/1/2) | `src/berth_truth/` + `opt/voyage.py`, `opt/fleetmix.py` |
| **DF** | Decision Fragility / Flip-Point | `src/fragility/` |

Audit date: **2026-08-28**. Every claim below was verified by reading source, running the
suite, or fetching the live document named. Nothing is carried over from a progress report.

---

# SECTION 1 — CURRENT REALITY

## 1.1 Verified baseline

```
686 tests collected · 684 passed · 2 skipped · 0 failed · exit 0
```

Per-module: `opt` 341 · `berth_truth` 124 · `ml` 63 · `tonnage` 46 · `impact` 38 ·
`fragility` 29 · `backend` 26 · `data_builders` 16.

## 1.2 What BT-0/1/2 and DF-1 actually changed

| Claim | Verdict | Evidence |
|---|---|---|
| BT-0 archives vessel schedules | **True, but tiny** | `src/berth_truth/archiver.py` works. On disk: **3 HTML captures total** (2 Dhamra, 1 Gangavaram). This is not a history. |
| BT-1 berth register exists | **True, 3 ports** | `rows_for_port`: Dhamra 20 rows/14 berths, Gangavaram 9, Visakhapatnam 29. **12 of 15 `PortEnum` members have no register entry.** |
| BT-2 rewired both call sites | **True** | `opt/voyage.py::_vessel_can_call` returns `FeasibilityVerdict`; `opt/fleetmix.py::_can_call` uses `verdict.binding_constraint`. Verified live: Gangavaram → berth `B5`, `limit_source=REGISTER`, margin 3.5 m; Newcastle → `PORTENUM_FALLBACK`; Dhamra → `draft_status=STALE_OR_UNAVAILABLE`. |
| DF-1 flip-point engine works | **True** | Full 8-variable sweep on a real cargo: 17 s, 57 solves, cap 200. Deterministic (identical `model_dump()` across runs). |

## 1.3 What is **not** true / not connected

**These are the findings that matter.**

| # | Finding | Evidence |
|---|---|---|
| **A** | **Tide is defined but never enforced.** `tide_allowance_m` and `tide_rule` exist on `BerthConstraint`. `grep` across all of `src/` shows they are **never read outside `models.py` / `registry.py`**. No decision consumes tide. | `grep -rn "tide_allowance_m\|tide_rule" src/` → only model + registry definition sites |
| **B** | **Wait time is still a scaled static baseline, not a queue.** `congestion._real_wait_days` returns `static_baseline * multiplier`, where the multiplier is a ratio of PortWatch *port-call counts*. One scalar. No distribution, no P75/P90, no queue. | `src/opt/congestion.py:68-95` |
| **C** | **M1's reconstruction reaches no decision.** Only `tonnage.basins` (CSV paths) and `tonnage.classmix.CLASS_MIDPOINT_DWT` (constants) are imported outside `src/tonnage/`. `stockflow`, `forward`, `supplycurve`, `validate` are consumed **only** by `src/impact/` — and `src/impact/` is imported by **nothing**. | `grep -rn "from tonnage"` / `"from impact"` |
| **D** | **`src/impact/` is dead code.** 38 passing tests, zero consumers, no endpoint, no UI. | as above |
| **E** | **Route-level forecasting still absent.** `BasisEntry` is defined in `opt/types.py:50` and **instantiated nowhere in `src/`**. `opt/quote.py:202` passes `basis={}` with a comment acknowledging it. | `src/opt/quote.py:197-202` |
| **F** | **Nothing new is reachable over HTTP.** Backend exposes exactly `/health`, `/meta`, `/ports`, `/quote`, `/quote/stream`. `tonnage`, `impact`, `fragility`, `berth_truth` are **not imported by `backend/` at all**. | `grep -n "@app\." backend/main.py` |
| **G** | **The frontend collapsed to a single page.** `frontend/src/pages/` contains only `voyage-desk-page.tsx`. The Tonnage Field and Execution Frontier pages are gone. | `ls frontend/src/pages/` |
| **H** | **The hash placeholder is gone — by deletion, not by wiring.** `frontend/src/lib/tonnage-model.ts` no longer exists. Defect #3 from the old audit is resolved in the sense that nothing fake is shown; it is *not* resolved in the sense that M1 now has **no UI at all**. | `test -f` → GONE |
| **I** | **No port-call fact table.** No `fact_port_call` or equivalent anywhere. `ScheduleRow` captures `ata/eta/pob/pd/atub` timestamps but has **no LOA, beam, draft, quantity, norm, or handled fields**. | `ScheduleRow.model_fields` |

## 1.4 Capability matrix

| Capability | Earlier state | Current implementation | Missing | Evidence | Closed by |
|---|---|---|---|---|---|
| M1 reconstruction | built, unshipped | unchanged | everything downstream | `src/tonnage/` | P8, P9, P10 |
| M1 fake frontend | hash placeholder | **deleted** | real UI to replace it | `pages/` has 1 file | P10 |
| M1 HTTP | none | none | endpoint + cache | `backend/main.py` | P10 |
| M1 absolute scale | 0.35×–26× vs Signal | unchanged | investigation | `tonnage/validate.py` | P8 |
| M1 supply-curve sign | wrong for Cape/Handy | unchanged | diagnosis | `tonnage/supplycurve.py` | P9 |
| M1 class mix | 1 eq, 4 unknowns | unchanged | labelled observations | `tonnage/classmix.py` | P8 |
| M1 IV / Kalman | absent | absent | justify-or-drop | — | P9 |
| BT berth constraints | none | **3 ports** | 12 ports | `registry.py` | P5 |
| BT live schedules | none | **2 ports** | adapters for the rest | `archiver.py` | P2, P3 |
| BT historical archive | none | **3 snapshots** | backfill | `raw_data/berth_truth/` | P3 |
| BT observed calls | none | none | `fact_port_call` | — | P3 |
| M4 tide | none | **fields only, never read** | enforcement + authority split | finding A | P5, P6 |
| M4 queue | scaled baseline | **still scaled baseline** | empirical distribution | finding B | P4, P6 |
| M4 handling rates | literals | literals | empirical | `network.py` | P4 |
| M4 API / UI | none | none | both | finding F/G | P7 |
| DF register use | — | uses BT-2 verdict ✓ | empirical waits | `fragility/engine.py` | P12 |
| DF wait variables | — | `UNAVAILABLE` (honest) | real injection point | `_WAIT_DAYS_UNAVAILABLE_REASON` | P12 |
| DF API / UI | — | none | both | finding F | P12 |
| Route basis | `basis={}` | **`basis={}`** | the whole mechanism | finding E | P11 |
| Regret ledger | none | none | everything | — | P13 |

---

# SECTION 2 — THE NUMBERING ERROR

**Yes. The previous work confused the numbering, and the confusion is real, not cosmetic.**

Precisely what happened:

1. **BT-0/1/2 are the foundation of M4, not M4.** They deliver two of M4's seven components —
   berth geometry (A) and draft truth (B). Tide (C) exists as *unread fields*. Queue (D),
   handling (E), and the feasibility UI (G–H) do not exist. Commodity-specific berth
   selection (F) works, but only for the 3 registered ports. **M4 is roughly 30% complete**,
   which is coincidentally where the original audit placed it — the BT work deepened the
   foundation for 3 ports rather than widening the moat.

2. **DF-1 is a genuinely new subsystem that is not on the eight-moat list at all.** It is
   valuable and it should be kept. But it was built *instead of* the remaining M1 and M4
   work, and — critically — **it shipped with no endpoint and no UI**, exactly repeating the
   `src/impact/` mistake the original audit called out.

3. **M1 received zero work across all four prompts.** Its state is identical to the audit,
   with one regression in visibility: the fake UI was deleted, so M1 now has *no* surface at
   all. "Not fake" is better than "fake", but the moat is still invisible.

4. The net effect: **three subsystems (`tonnage`, `impact`, `fragility`) now sit behind zero
   HTTP endpoints**, holding 113 passing tests between them, contributing nothing a judge can see.

**The correction this plan applies:** every prompt from P6 onward is only complete when
`DATA → MODEL → DECISION/API → FRONTEND → TEST` is connected end to end. Building a module
with tests is explicitly *not* "shipped".

---

# SECTION 3 — PORT COVERAGE RESEARCH

## 3.1 The port universe

`PortEnum` has **15 members** — this is the complete set the product can quote, route, or
reposition against. `berth_truth.PortId` has **3**. The gap is the work.

| # | Port | Country | Role | Register? | Live feed? |
|---|---|---|---|---|---|
| 1 | Paradip | IN | discharge (PS) | ✗ | — |
| 2 | Visakhapatnam | IN | discharge (PS) | **✓ 29 rows** | — |
| 3 | Gangavaram | IN | discharge (PS) | **✓ 9 rows** | **✓ BT-0** |
| 4 | Gopalpur | IN | discharge (PS) | ✗ | — |
| 5 | Dhamra | IN | discharge (PS) | **✓ 20 rows** | **✓ BT-0** |
| 6 | Sagar/Sandheads | IN | lighterage (PS) | ✗ | — |
| 7 | Haldia | IN | discharge (PS) | ✗ | — |
| 8 | Newcastle | AU | load | ✗ | — |
| 9 | Gladstone | AU | load | ✗ | — |
| 10 | Richards Bay | ZA | load | ✗ | — |
| 11 | Beira | MZ | load | ✗ | — |
| 12 | Muara Pantai | ID | load | ✗ | — |
| 13 | Balikpapan | ID | load | ✗ | — |
| 14 | Hampton Roads | US | load | ✗ | — |
| 15 | Singapore | SG | hub/reposition | ✗ | — |

## 3.2 Source matrix — **what I verified myself this session**

I fetched and parsed these live. Everything in this block is first-hand.

| Port | Source | Level | Automation | Verified content |
|---|---|---|---|---|
| **Paradip** | `paradipport.gov.in/uploads/YYYY/MM/dtrDDMM.pdf` (current) and `/Writereaddata/Daily_Traffic/dtrDDMM.pdf` (archive) | **C** — official PDF, daily | **Permitted.** `robots.txt` = `User-agent: * / Disallow: /wp-admin/ / Allow: /wp-admin/admin-ajax.php`. Neither path is disallowed. `.gov.in` primary source. | **7 pages, machine-readable.** Sections: **A** Working Vessels, **B** Vessels Waiting At Anchorage, **C** Expected Vessels, **D** Berthing Movements. Per vessel: name, **DRAFT, LOA, BEAM**, berth, cargo, shipper/receiver/stevedore, D/L flag, **ARVL / READY / BERTH timestamps (three separate)**, ETD/SLD, total, NORM, DAY'S actual, MQ/BQ-to-date, balance, remarks with event codes (`DC`, `DCOMP`, `MF`, `GD`, `IS`, `LC`, `FS`, `SLD`). |
| **Visakhapatnam** | `vpt.shipping.gov.in/admin_assets/uploads/…BERTHING PROGRAME - ETA.pdf` | **C** — official PDF | `.gov.in` primary source; index page still to be located | **1 page, machine-readable.** "VESSELS WAITING & EXPECTED". Per vessel: name, nationality, **position (`ROADS` = at anchorage)**, **draft**, arrival date/time, agent, tonnes, commodity, shipper/receiver. Grouped by commodity class ([A] Iron Ore, [E] Crude & POL, [F] Fertilizers…). **No berth-time column** — wait must be derived by differencing daily snapshots. |
| **Dhamra**, **Gangavaram** | `adaniports.com/…/vesselschedule` | **B** — official HTML | Already cleared in BT-0 (`archiver.py` docstring documents robots.txt + T&C review) | Already integrated |
| **Gopalpur** | — | **E (provisional)** | — | Searched: only commercial aggregators returned (V-OCEAN, Ruzave, ShipNext, VesselFinder, MarineTraffic). **No official operational feed found.** |

## 3.3 ⚠️ The aggregator trap

The V-OCEAN screenshots showing a Haldia line-up are **exactly the trap this plan must avoid.**
V-OCEAN, Ruzave, ShipNext, VesselFinder, MarineTraffic and vesseltracker are **commercial
aggregators**. Their content being visible in a browser does not make it licensed for
automated ingestion, and several explicitly forbid it. **Do not build an adapter against any
of them without first quoting their terms.** The same discipline BT-0 applied to Adani applies
here — and Haldia's *official* source is `smportkolkata.shipping.gov.in`, which is where P1
must look.

## 3.4 What I did **not** verify

Honest scope statement: I verified 4 of 15 ports first-hand. **Haldia, Gopalpur, Sagar/Sandheads
and all 8 foreign ports are unresearched.** Rather than guess, **P1 is dedicated entirely to
completing this matrix** with a fixed methodology, and every port must exit P1 with one of:

- `SUPPORTED_LIVE` — lawful official operational feed, integrated
- `SUPPORTED_PERIODIC` — official periodic report, integrated
- `SUPPORTED_STATIC` — authoritative berth/navigation constraints only
- `NO_PUBLIC_OPERATIONAL_FEED` — researched, nothing lawful found

The last status is **acceptable and expected** for several load ports. Fabricating a feed is not.

---

# SECTION 4 — FINAL ARCHITECTURE

```
   PUBLIC SOURCE EVIDENCE
   gov PDFs · official HTML · port circulars · PortWatch · Baltic-derived series
            │
            ▼
   ┌─────────────────────────────────────────────────┐
   │  berth_truth/providers/   (P2)                  │   adapter per source
   │  html · pdf · csv · json · static · manual      │   raw bytes + sha256 +
   │  → normalise → quarantine on low confidence     │   retrieval ts + doc date
   └─────────────────────────────────────────────────┘
            │
            ▼
   ┌──────────────────────┐      ┌──────────────────────────┐
   │ fact_port_call  (P3) │      │ BerthConstraint register │
   │ append-only, vintage │      │ (P5) effective-dated,    │
   │ arvl/ready/berth/sld │      │ 15 ports, tide authority │
   └──────────────────────┘      └──────────────────────────┘
            │                                │
            ├──────────────┬─────────────────┤
            ▼              ▼                 ▼
   ┌────────────────┐  ┌──────────┐  ┌────────────────────┐
   │ empirical      │  │   M1     │  │  M4 BERTH REALITY  │
   │ waits+handling │  │ tonnage  │  │  ENGINE      (P6)  │
   │ (P4) P50/75/90 │  │ (P8,P9)  │  │  geometry+draft+   │
   └────────────────┘  └──────────┘  │  tide+queue+handling│
            │               │        └────────────────────┘
            └───────┬───────┴─────────────────┘
                    ▼
        opt/  fleetmix · voyage(CP-SAT) · stopping(LSMC) · quote
                    │
                    ▼
              DF sensitivity (P12)
                    │
                    ▼
        backend/  /quote · /tonnage-field · /port-twin · /fragility · /ledger
                    │
                    ▼
        frontend/  Voyage Desk · Port Twin · Tonnage Field · Fragility · Ledger
                    │
                    ▼
              Regret Ledger (P13)  ── append-only, closes the loop
```

**Key architectural decisions:**

1. **Providers, not scrapers.** BT-0's single-site fetcher becomes an adapter registry. Source
   licence/access notes are a *first-class field*, not a docstring.
2. **`fact_port_call` is the join point.** It feeds M4's queue *and* M1's class mix. One
   ingestion effort, two moats. This is why P3 comes before both.
3. **Authoritative vs advisory is a type-level distinction**, not a comment. A tide model can
   never satisfy a check that requires a port-authority rule.
4. **Every moat gets an endpoint and a screen in the same prompt that finishes its model.**
   No repeat of `src/impact/`.

---

# SECTION 5 — SONNET IMPLEMENTATION PROMPTS

> **Standing rules — these apply to every prompt below and are repeated inside each one.**
> Do not assume file paths from any report — inspect first. Do not rewrite working modules.
> Execute the work; do not return another plan unless genuinely blocked by missing data or
> access. No invented data of any kind. No silent fallback — every fallback exposes its
> provenance. Respect robots.txt and terms of use, and quote them before adding a source.
> Add tests. Run the full suite (`684 passed, 2 skipped` is the floor — never regress it).
> Run `npm run build` in `frontend/` whenever frontend changes. Report exact files changed,
> exact tests added, exact commands run with their real output, and unresolved limitations.

---

## PROMPT 1 — PORT UNIVERSE & LAWFUL SOURCE REGISTER

### Goal
Produce the complete, verified, machine-readable source register for **all 15 supported ports**,
with a licence/automation decision recorded for every source. No ingestion code yet.

### Why this comes now
Every later prompt depends on knowing which ports have which class of source. Building adapters
before this is how you end up with a Dhamra-and-Gangavaram-only system again.

### Preserve
`src/berth_truth/archiver.py`'s licence-review discipline (its module docstring is the model to
follow). `PortEnum` and `berth_truth.PortId` as they are.

### Inspect first
`src/opt/network.py` (PortEnum, all 15), `src/berth_truth/models.py` (PortId), `raw_data/sources.md`.

### Required implementation
1. Create `src/berth_truth/sources.py` with a typed, frozen registry:
   `PortSource(port_id, source_name, source_url, level, doc_format, cadence, automation_status,
   licence_note, robots_checked_on, evidence_quote, confidence)`.
2. `level` ∈ `A` (API/machine-readable) · `B` (official HTML) · `C` (official PDF/periodic) ·
   `D` (official static berth/nav docs only) · `E` (nothing lawful found).
3. `automation_status` ∈ `PERMITTED` · `PROHIBITED` · `UNCLEAR` — with `evidence_quote`
   holding the **actual quoted text** from robots.txt or the terms page. `UNCLEAR` is never
   treated as permitted.
4. Add `PortCoverageStatus` per port: `SUPPORTED_LIVE` / `SUPPORTED_PERIODIC` /
   `SUPPORTED_STATIC` / `NO_PUBLIC_OPERATIONAL_FEED`.
5. **Research all 15 ports.** For each, search: `<port> vessel schedule` · `line up` ·
   `daily traffic` · `berthing programme` · `shipping movement` · `port position` ·
   `permissible draft` · `berth restrictions` · `marine department` · `tide` · plus the
   terminal operator where the berth is privately run.
6. Fetch and quote `robots.txt` for every domain you propose to automate.

### Already verified — seed these, then verify the rest
- **Paradip** — `paradipport.gov.in/uploads/YYYY/MM/dtrDDMM.pdf` + archive
  `/Writereaddata/Daily_Traffic/dtrDDMM.pdf`. Level **C**. robots.txt (fetched 2026-08-28):
  `User-agent: * / Disallow: /wp-admin/ / Allow: /wp-admin/admin-ajax.php` → **PERMITTED**.
- **Visakhapatnam** — `vpt.shipping.gov.in/admin_assets/uploads/…BERTHING PROGRAME - ETA.pdf`.
  Level **C**. Locate the index page that lists these.
- **Dhamra / Gangavaram** — already Level **B**, already cleared.
- **Gopalpur** — searched; only commercial aggregators found. Confirm or refute Level **E**.

### Explicit non-goals
No fetching pipeline, no parsers, no schema changes to `BerthConstraint`.

### ⚠️ Prohibited sources
Do **not** register an adapter against V-OCEAN, Ruzave, ShipNext, VesselFinder, MarineTraffic,
vesseltracker, Equasis, Signal, or any Baltic-derived content **unless** you quote terms that
explicitly permit automated use. Browser-visible ≠ automatable. Mark `UNCLEAR` and move on.

### Acceptance criteria
- All 15 ports present, each with a `PortCoverageStatus`.
- Every `PERMITTED` source carries a real `evidence_quote` and `robots_checked_on` date.
- No port silently omitted; `NO_PUBLIC_OPERATIONAL_FEED` is a valid, expected outcome.

### Tests
`tests/berth_truth/test_sources.py`: every `PortEnum` member maps to ≥1 `PortSource`;
no `PERMITTED` without an `evidence_quote`; `PortId` ⊆ `PortEnum` coverage; enum round-trips.

### Run
`.venv/Scripts/python.exe -m pytest -q` · `.venv/Scripts/python.exe -m ruff check src/ tests/`

### Report
Files changed · tests added · the full 15-port matrix as a table · every robots.txt verdict
with its quote · ports that came out Level E and what you searched before concluding that.

### Stop conditions
Stop and report if >6 ports land in `UNCLEAR` — that indicates the research method needs
review before committing to adapters.

---

## PROMPT 2 — PROVIDER ARCHITECTURE (GENERALISE BT-0)

### Goal
Turn the single-site fetcher into an adapter platform that can ingest any registered source,
without changing what already works for Dhamra and Gangavaram.

### Why this comes now
P1 produced the source list; nothing can be ingested until there is a shape to ingest into.

### Preserve
`archiver.py`'s hashing, new-content-vs-re-observation logic, `parse_confidence`, quarantine
behaviour, rate limiting, and the `ScheduleSnapshot`/`ScheduleRow` models. **The existing Adani
path must keep working byte-identically** — its tests are the regression guard.

### Inspect first
`src/berth_truth/archiver.py`, `store.py`, `parsers/adani_schedule.py`, `models.py`,
`tests/berth_truth/test_adani_schedule.py`, `test_store.py`.

### Required implementation
1. `src/berth_truth/providers/` with a `Provider` protocol:
   `supports(source) -> bool` · `fetch(source) -> RawCapture` · `parse(RawCapture) -> ParseResult`.
2. Adapters: `html_table.py`, `pdf_report.py`, `csv_xlsx.py`, `json_api.py`, `static_doc.py`,
   `manual_declaration.py`. Implement the interface for all; only `html_table` (existing Adani
   logic, moved not rewritten) and a stub `pdf_report` need full behaviour here.
3. `RawCapture` must carry: `source_url`, `retrieved_at`, `doc_published_date`, `content_sha256`,
   `parser_version`, `raw_bytes_path`, `licence_note`.
4. Registry dispatch: given a `PortSource` from P1, select the adapter. Unknown → explicit error,
   never a silent skip.
5. Refactor `archiver.archive_port` to call the registry. Keep the CLI surface identical.

### Explicit non-goals
No new ports ingested yet. No parser for Paradip yet (that is P3). No schema change to
`BerthConstraint`.

### Acceptance criteria
- `python -m berth_truth --port dhamra` behaves exactly as before.
- All 124 existing `berth_truth` tests pass unchanged.
- Adding a new source requires only a new adapter + a P1 registry row — demonstrate this in a test.

### Tests
`tests/berth_truth/test_providers.py`: protocol conformance for every adapter; registry selects
correctly per `doc_format`; unknown format raises; `RawCapture` provenance fields are mandatory;
**a regression test asserting the Adani path produces the same `content_sha256` and parse output
as before the refactor** (use the 3 existing captures in `raw_data/berth_truth/raw_html/`).

### Run
`.venv/Scripts/python.exe -m pytest -q` · `ruff check`

### Report
Files changed · what moved vs. what was rewritten · proof the Adani path is unchanged.

### Stop conditions
If preserving the Adani path byte-identically requires changing `ScheduleRow`, stop and report
the conflict rather than changing the model.

---

## PROMPT 3 — OPERATIONAL REPORT HARVESTER & `fact_port_call`

### Goal
Ingest the Indian daily operational reports into an append-only, vintage-preserving
`fact_port_call` table, and backfill history where the source exposes it.

### Why this comes now
This single table feeds **both** M4's queue model (P4/P6) and M1's class-mix identification
(P8). It is the highest-leverage prompt in the plan.

### Preserve
Everything from P2. Quarantine-over-guess is mandatory here — the PDF text stream is genuinely
interleaved.

### Inspect first
`src/berth_truth/providers/pdf_report.py` (from P2), `store.py`, `models.py`.

### Verified source structure — Paradip (fetched and parsed 2026-08-28)
7 pages, machine-readable via `pypdf`. Four sections:
`A. WORKING VESSELS` · `B. VESSELS WAITING AT ANCHORAGE` · `C. EXPECTED VESSEL` ·
`D. BERTHING MOVEMENTS`.

Per-vessel fields present: vessel name · **DRAFT · LOA · BEAM** · berth · cargo ·
shipper/receiver/stevedore · D/L flag · **ARVL, READY, BERTH timestamps (three distinct)** ·
ETD/SLD · TOTAL · NORM · DAY'S actual · MQ/BQ-to-date · BALANCE · remarks with event codes
(`DC` `DCOMP` `MF` `GD` `IS` `LC` `FS` `SLD`).

**Implementation warning, verified first-hand:** `extract_text()` returns columns *interleaved
out of order* — e.g. a row's times appear split across the stream. A naive line regex will
mis-associate fields. Use **positional extraction (character x/y coordinates)**, or validate
every parsed row against a checksum rule (e.g. `total = handled + balance`) and quarantine
failures. Budget for a large quarantine bucket on the first pass; that is success, not failure.

### Required implementation
1. `fact_port_call` model — nullable everywhere except identity/provenance:
   `port_id, terminal, berth, vessel_name, imo, loa_m, beam_m, arrival_draft_m, vessel_class,
   cargo_raw, commodity_class, load_discharge, shipper, receiver, stevedore, arrival_ts,
   ready_ts, berth_ts, sail_ts, eta_ts, etd_ts, total_qty_t, handled_qty_t, balance_qty_t,
   norm_tpd, actual_tpd, source_url, source_doc_date, retrieved_at, content_sha256,
   parser_version, extraction_confidence, quarantine_reason`.
2. **Null is always preferable to invention.** A field absent from the source stays `None`.
3. Append-only store, keyed by `(source_sha256, row_index)`. **Never overwrite a historical
   row** — a corrected later document creates a new record; supersession is recorded, not applied
   destructively.
4. Paradip parser + backfill walker over both URL patterns
   (`/uploads/YYYY/MM/dtrDDMM.pdf`, `/Writereaddata/Daily_Traffic/dtrDDMM.pdf`).
   **Rate-limit to ≥1 request per 3 s** and honour the `MIN_FETCH_INTERVAL_SECONDS` precedent.
5. Visakhapatnam parser for the berthing-programme layout (grouped by commodity class; `ROADS`
   = at anchorage; **no berth-time column** — derive waits by differencing consecutive daily
   snapshots, and record that derivation as `evidence_class`, not as an observed timestamp).
6. Extend BT-0's Adani `ScheduleRow` → `fact_port_call` projection so all sources land in one table.

### Explicit non-goals
No wait/handling statistics yet (P4). No berth register changes (P5). Do not attempt Haldia
via any aggregator.

### Acceptance criteria
- ≥90 days of Paradip history ingested, or an explicit report of how far back the site actually
  goes and what blocked more.
- Every row traceable to a source URL + sha256 + document date.
- Quarantined rows are *retained with a reason*, never dropped.
- Re-running the harvester is idempotent (same sha256 → no duplicate rows).

### Tests
`tests/berth_truth/test_fact_port_call.py` + `test_paradip_parser.py`: parse a **real committed
fixture PDF**; assert exact field extraction for ≥3 known vessels; assert interleaved-column
rows are quarantined not guessed; assert append-only (re-ingest → no dupes); assert null-not-zero
for absent fields; assert `total = handled + balance` validation catches a corrupted row.

### Run
`.venv/Scripts/python.exe -m pytest -q` · `ruff check` · the harvester CLI itself, and paste real output.

### Report
Rows ingested per port · date range achieved · quarantine rate with examples of *why* ·
fields that turned out consistently unavailable · exact rate limiting applied.

### Stop conditions
If quarantine exceeds 40% after a genuine positional-parsing attempt, stop and report the
layout problem rather than loosening validation to make the number look better.

---

## PROMPT 4 — EMPIRICAL WAIT & HANDLING DISTRIBUTIONS

### Goal
Replace the scaled-static-baseline wait number with real empirical distributions, and replace
literal handling rates with observed productivity — **only where sample size supports it**.

### Why this comes now
`fact_port_call` now exists. This converts it into the two quantities the optimiser actually uses.

### Preserve
`opt/congestion.dynamic_wait_days`'s signature and its `is_real_data` honesty flag — other code
depends on both. Extend alongside it; do not break it.

### Inspect first
`src/opt/congestion.py:68-125` (the current `static_baseline * multiplier`),
`src/opt/network.py` (`expected_wait_days`, `handling_rate_tph` literals).

### Required implementation
1. `src/berth_truth/empirical.py`:
   - `wait_hours = berth_ts - arrival_ts`; also `ready_ts - arrival_ts` and `berth_ts - ready_ts`
     where both exist (these are *different questions* — keep them separate).
   - Distributions by `(port, terminal, berth, vessel_class, commodity, month)`, falling back up
     that hierarchy when a cell is thin.
   - Return `WaitDistribution(n, p50, p75, p90, mean, source_level, is_sufficient)`.
2. **A minimum-sample rule is mandatory.** Below it (suggest n<20, justify your choice),
   `is_sufficient=False` and the caller keeps the existing baseline with `is_real_data=False`.
   Never emit a percentile from 3 observations.
3. Handling: `actual_tpd` and `norm_tpd` per berth/commodity → empirical productivity, same
   sufficiency gate.
4. Wire into `congestion.py` as a **new** function returning the distribution; leave
   `dynamic_wait_days` working for existing callers.

### Explicit non-goals
Do not change the CP-SAT encoding. Do not delete the static baselines — they remain the
documented fallback.

### Acceptance criteria
- A port with real history returns a genuine distribution with `n` visible.
- A port without history returns the old baseline, explicitly flagged.
- P90 > P50 always; no distribution emitted below the sample floor.

### Tests
`tests/berth_truth/test_empirical.py`: known fixture calls → hand-computed percentiles;
sample floor enforced; hierarchy fallback; ports with no data unchanged from today's behaviour;
a regression test that existing `congestion` tests still pass untouched.

### Run
`.venv/Scripts/python.exe -m pytest -q` · `ruff check`

### Report
Per-port sample sizes · which ports cleared the sufficiency gate · **the actual empirical P50/P90
vs the current static literal for each** — this comparison is a headline result, report it in full.

### Stop conditions
If no port clears the sufficiency gate, stop and report it. That is a real finding about
P3's coverage, not a reason to lower the threshold.

---

## PROMPT 5 — BERTH REGISTER TO ALL PORTS + TIDE AUTHORITY

### Goal
Extend the berth register from 3 ports toward all 15 using authoritative sources only, and make
tide a **typed, enforced, authority-ranked** constraint instead of an unread text field.

### Why this comes now
M4's feasibility engine (P6) needs constraints for every port and a real tide rule to enforce.

### Preserve
`BerthConstraint`, `LimitStatus`, `DraftSource`, `DraftStatus`, effective dating, supersession,
the exact-date resolver, and BT-2's `FeasibilityVerdict` contract. Existing 3-port data must
resolve identically after your change.

### Inspect first
`src/berth_truth/registry.py`, `resolver.py`, `models.py:291-350` (note `tide_allowance_m` and
`tide_rule` already exist), `src/opt/types.py::FeasibilityVerdict`.

### Verified finding to fix
`grep` confirms `tide_allowance_m` / `tide_rule` are **never read outside the model and registry**.
Tide is currently decorative.

### Required implementation
1. **Tide authority hierarchy as a type:**
   - `TideAuthority.PORT_RULE` — a port-authority operational notice/rule. **Authoritative.**
     May gate feasibility.
   - `TideAuthority.ADVISORY_MODEL` — FES, pyTMD, Open-Meteo, any scientific model.
     **Advisory only.** May *never* satisfy or override a navigational check. It may only
     widen uncertainty or annotate sensitivity.
   - Enforce this in code: a function that gates feasibility must reject an `ADVISORY_MODEL`
     input at the type level, not by convention.
2. Make the resolver read tide: a berth with a `PORT_RULE` tidal restriction and no valid
   tide window for the query returns `untested`/`conditional`, **never a silent pass**.
3. Populate constraints for the remaining ports from P1's Level D sources. Where only a
   port-level maximum exists (as `PortEnum` has today), record it as a single
   `is_published_constraint_berth=False` port-level row with `LimitStatus.ASSUMED` and its real
   provenance — **do not invent berth subdivisions**.
4. Keep `PORTENUM_FALLBACK` working for anything still unregistered.

### Explicit non-goals
Do not add a tide *model*. Do not fabricate berth counts. Do not convert observed vessel calls
into declared limits (that separation is P6).

### Acceptance criteria
- Every `PortEnum` member resolves to *something* with honest provenance.
- A `PORT_RULE` tide restriction demonstrably changes a feasibility outcome, with a test.
- An `ADVISORY_MODEL` tide value provably **cannot** clear a vessel — assert the rejection.
- The 3 existing ports resolve byte-identically to before.

### Tests
`tests/berth_truth/test_tide.py`: authority hierarchy enforced; advisory rejected as a gate;
tidal berth without a window → untested not passed. `test_registry.py`: all 15 ports covered;
existing 3 unchanged (regression).

### Run
`.venv/Scripts/python.exe -m pytest -q` · `ruff check`

### Report
Per-port constraint counts and source · which ports are berth-level vs port-level-assumed ·
every tide rule found, quoted, with its authority level.

### Stop conditions
If a port publishes tidal restrictions you cannot interpret unambiguously, record the rule text
verbatim with `LimitStatus.NOT_PUBLISHED` for the numeric field and report it — do not estimate.

---

## PROMPT 6 — M4 BERTH REALITY ENGINE

### Goal
Assemble geometry + draft + tide + queue + handling into one coherent engine producing an
uncertainty-aware, fully-attributed feasibility report. **This is the prompt that makes M4 exist.**

### Why this comes now
All five inputs are ready. Nothing here invents data; it composes P4's and P5's outputs.

### Preserve
BT-2's `FeasibilityVerdict` — extend it, do not replace it. `opt/voyage.py::_vessel_can_call`
and `opt/fleetmix.py::_can_call` keep their contracts. DF-1 depends on both.

### Inspect first
`src/opt/types.py::FeasibilityVerdict`, `src/berth_truth/service.py`, `empirical.py` (P4),
`registry.py` (P5), `src/opt/congestion.py`.

### Required implementation
1. `src/berth_truth/reality.py` → `PortRealityReport`:
   - verdict: `FEASIBLE` / `INFEASIBLE` / `CANNOT_VERIFY` (three states, never two)
   - qualifying berth(s), binding constraint, vessel requirement, available margin
   - official source + document date per constraint used
   - tide impact: `NONE` / `CONDITIONAL` / `BLOCKING` + authority level
   - expected wait: full distribution (P50/P75/P90, n, sufficiency)
   - expected handling rate + implied laytime
   - `untested_checks`, `stale_inputs`, overall confidence
2. **Declared vs observed stays separated.** Add `observed_envelope` (max LOA/beam/draft actually
   seen in `fact_port_call`) *beside* the declared limit. **Never** let an observation raise a
   declared limit. **Do** surface disagreement explicitly — a vessel observed larger than the
   published limit is a reportable finding, not a licence to relax the check.
3. Queue: use P4's distribution where sufficient, else the flagged baseline. Expose
   `P(wait > X days)`.
4. Commodity-specific berth selection across all registered ports.

### Explicit non-goals
Do not rewrite CP-SAT. Do not change `quote()`'s signature. No UI (P7).

### Acceptance criteria
- Three-state verdict, with `CANNOT_VERIFY` genuinely reachable and tested.
- Every number in the report carries a source and a date.
- Observed-only berths still never clear a vessel (BT-2 invariant preserved).
- A declared/observed disagreement is surfaced, not silently resolved.

### Tests
`tests/berth_truth/test_reality.py`: all three verdicts; observation never raises a limit;
tide blocking; wait distribution present when sufficient and flagged when not; disagreement
surfaced. Regression: all `opt` tests unchanged.

### Run
`.venv/Scripts/python.exe -m pytest -q` · `ruff check`

### Report
A worked example per port class · which ports produce `CANNOT_VERIFY` and why · every
declared-vs-observed disagreement found in real data.

### Stop conditions
If observed data contradicts a declared limit at any port, report it prominently — that is a
genuine finding and must not be normalised away.

---

## PROMPT 7 — M4 API + PORT TWIN UI

### Goal
Make M4 visible. Endpoint + a real Port Twin screen. **M4 is not shipped until a judge can click it.**

### Why this comes now
This is the anti-`src/impact/` checkpoint. The model exists; expose it before moving on.

### Preserve
The existing single-page Voyage Desk and its SSE `/quote/stream` flow. `frontend/src/lib/api.ts`
conventions. The `IconRail` shell.

### Inspect first
`backend/main.py` (5 endpoints today), `backend/serialize.py`,
`frontend/src/pages/voyage-desk-page.tsx`, `frontend/src/lib/api.ts`, `types.ts`,
`components/shell/icon-rail.tsx`.

### Required implementation
1. `GET /ports/{code}/reality` → `PortRealityReport`, with query params for vessel dims,
   commodity, and `as_of`.
2. `GET /ports/{code}/berths` → register rows with provenance and effective dates.
3. `GET /ports/{code}/calls` → recent `fact_port_call` rows (paginated).
4. Cache appropriately — reality reports must not re-read the register per request.
5. **Port Twin screen**: PORT → TERMINAL → BERTH drill-down. Show live line-up, berth
   constraints with source links and dates, observed calls, current draft status, wait
   distribution (not a single number), productivity, and the feasibility verdict.
6. Every displayed figure shows its source and date. `CANNOT_VERIFY` renders visibly
   differently from `FEASIBLE` — never as a soft pass.
7. Add it to the icon rail. Decide by inspection whether this is a new page or a Voyage Desk
   tab; do not create a duplicate screen if the desk can host it cleanly.

### Explicit non-goals
No auth, no rate limiting, no persistence layer. No redesign of the Voyage Desk.

### Acceptance criteria
- All three endpoints return real data for a registered port and honest `NOT_AVAILABLE`
  for an unregistered one.
- The screen renders for a Level-E port without crashing and without implying data exists.
- `npm run build` passes.

### Tests
`tests/backend/test_reality_api.py`: 200 with real payload; unknown port → 422; unregistered →
explicit unavailable, not an empty success. Frontend: `npm run build` + `npx tsc --noEmit`.

### Run
`.venv/Scripts/python.exe -m pytest -q` · `cd frontend && npm run build` · start both servers
and fetch the endpoints, pasting real output.

### Report
Endpoints added · screenshot-equivalent description of the screen · what a Level-E port shows.

### Stop conditions
If the Voyage Desk cannot host this without crowding, say so and justify the separate page.

---

## PROMPT 8 — M1 SCALE & CLASS-MIX CORRECTION

### Goal
Fix the two identified M1 defects: the free-tonnage scale error, and class-mix identification.

### Why this comes now
`fact_port_call` (P3) supplies exactly the labelled vessel observations `classmix.py` documents
it lacks.

### Preserve
`src/tonnage/basins.py` and `CLASS_MIDPOINT_DWT` — `opt/congestion`, `risk` and `repositioning`
import them and must keep working.

### Inspect first
`src/tonnage/stockflow.py`, `classmix.py`, `validate.py`, and `src/impact/__init__.py`
(its docstring records the known defects candidly — read it).

### Known defects to address
1. **Scale.** `stock_dwt` anchors to *total registered fleet*, not the free/ballasting fraction.
   Validation gave 0.35×–26× against 12 Signal points.
2. **Class mix.** Mean parcel size is one equation for four unknowns — it cannot identify
   four class shares. `classmix.py` documents this honestly.

### Required implementation
1. Separate `stock_total_dwt` from `stock_available_dwt` explicitly. Estimate the available
   fraction from evidence, not a chosen constant. If no defensible estimator exists, keep the
   total and **report the model as unscaled** rather than applying an unjustified factor.
2. Use `fact_port_call` named vessels (LOA/beam/draft → class) as a **labelled calibration set**
   for class mix at covered ports; anchor elsewhere and state the extrapolation explicitly.
3. Ballast state: treat probabilistically if vessel-level features legally exist; otherwise keep
   the aggregate estimator and state the uncertainty. **Do not use a single draught threshold as
   if it were maritime truth.**
4. Re-run `validate.py` and report the new ratio range honestly.

### Explicit non-goals
No IV, no Kalman (P9). No API (P10). Do not tune to make 12 points fit.

### Acceptance criteria
- Total vs available tonnage are distinct, separately reported quantities.
- Class mix at covered ports is validated against real labelled calls, with error reported.
- **12 calibration points can never support a universal accuracy claim** — the report must
  say so explicitly.

### Tests
`tests/tonnage/test_classmix.py`: labelled-call classification correctness; extrapolation
flagged. `test_validate.py`: new ratio range asserted against real output, not a target.

### Run
`.venv/Scripts/python.exe -m pytest -q` · `ruff check` · run the reconstruction and paste timings.

### Report
Old vs new scale ratios · class-mix error at covered ports · **what remains unvalidated**.

### Stop conditions
If the available-fraction cannot be estimated defensibly, stop and report that. Shipping an
honestly-unscaled index beats shipping a fudge factor.

---

## PROMPT 9 — M1 SUPPLY CURVE, FORWARD TIGHTNESS & VALIDATION

### Goal
Diagnose the wrong-signed supply curve, decide IV/Kalman on evidence, and produce a forward
physical-tightness forecast with real uncertainty.

### Why this comes now
Scale is corrected (P8); the sign problem can now be diagnosed without that confound.

### Preserve
`supplycurve.py`'s fitted-quantile-regression interface if it survives diagnosis.

### Inspect first
`src/tonnage/supplycurve.py`, `forward.py`, `validate.py`, `src/ml/frozen_test.py`.

### Known defect
Rate~tightness comes out `r=-0.02` (Capesize), `r=-0.24` (Handysize) — **wrong sign** for two of
four classes.

### Required implementation
1. **Diagnose before fixing.** Systematically test: scale error (may now be resolved by P8),
   confounding, sparse freight labels, regime effects, incorrect lag, aggregation level, target
   transformation. Report which explains it.
2. **Do not force the sign.** If the relationship does not validate after honest diagnosis,
   report a negative result and reduce the claim. A validated negative result is a real
   contribution; a forced positive one is fraud.
3. **IV:** determine whether a defensible instrument genuinely exists (relevance + exclusion,
   both argued). If not, **remove the IV claim from all documentation.** Do not add IV to sound
   sophisticated.
4. **Kalman/state-space:** implement only if it demonstrably improves out-of-sample latent-tonnage
   estimation. Show the comparison. Otherwise remove the claim.
5. `forward.py` must answer: *is available physical tonnage likely to tighten or loosen over the
   coming fixing window?* — with intervals, not a point estimate.
6. Validation: time-based holdout, no leakage (use the existing `frozen_test` machinery — do not
   build a second backtest system), lead/lag analysis, per-class and per-basin metrics, regime
   diagnostics, confidence intervals.

### Explicit non-goals
No API (P10). Do not create a parallel backtest framework.

### Acceptance criteria
- Sign problem explained, with the diagnostic evidence shown.
- IV and Kalman each either implemented-and-validated or **explicitly removed as claims**.
- Forward tightness carries uncertainty.
- No claim of predictive lead unless a holdout test demonstrates it.

### Tests
`tests/tonnage/test_supplycurve.py` diagnostics; `test_forward.py` intervals present and
ordered; leakage guard via `frozen_test`.

### Run
`.venv/Scripts/python.exe -m pytest -q` · `ruff check` · full validation, paste real metrics.

### Report
The sign diagnosis and its evidence · IV verdict with reasoning · Kalman verdict with the
comparison · holdout metrics per class · **every claim you removed from the docs**.

### Stop conditions
Report a negative result plainly if that is what the data shows. Do not proceed to P10 with an
unvalidated claim.

---

## PROMPT 10 — M1 API + TONNAGE FIELD UI

### Goal
Make M1 visible and real. **Confirm no placeholder returns.**

### Why this comes now
The model is defensible after P8/P9. Now — and not before — it earns a screen.

### Preserve
The Voyage Desk. Note `frontend/src/lib/tonnage-model.ts` (the FNV-hash placeholder) is
**already deleted** — verify it stays deleted and that nothing equivalent is reintroduced.

### Inspect first
`backend/main.py`, `src/tonnage/stockflow.py` (reconstruction takes ~7 s — caching is mandatory),
`frontend/src/pages/`, `components/shell/icon-rail.tsx`.

### Required implementation
1. `GET /tonnage-field` → current tightness by basin × class, with uncertainty, provenance, and
   sample sufficiency.
2. `GET /tonnage-field/forward` → forward tightness with intervals.
3. **Cache the reconstruction.** A 7-second recompute per UI interaction is unacceptable;
   compute on startup or on a TTL, and expose `computed_at`.
4. Tonnage Field screen: current tightness, forward tightness, class and basin breakdown,
   uncertainty bands, evidence quality, historical movement.
5. Every figure labelled with its provenance (`OBSERVED` / `ESTIMATED` / `MODEL_DERIVED` /
   `DECLARED` / `INFERRED`).
6. There is no map library in the frontend — **do not describe a grid as a map.**

### Explicit non-goals
No new charting dependency unless justified (`visx` is already present).

### Acceptance criteria
- No hash-generated or synthetic value anywhere in the path.
- Response < 500 ms warm.
- Uncertainty is visible, not hidden behind a point estimate.
- `npm run build` passes.

### Tests
`tests/backend/test_tonnage_api.py`: real payload, cache behaviour, `computed_at` present.
Frontend build + typecheck. **A guard test asserting no FNV/hash-derived data source exists in
`frontend/src/`.**

### Run
`.venv/Scripts/python.exe -m pytest -q` · `cd frontend && npm run build` · time the warm endpoint.

### Report
Endpoints · cache strategy and measured timings · confirmation the placeholder is absent.

### Stop conditions
If P9 concluded the tightness signal does not validate, **the screen must present it as a
descriptive physical-supply reconstruction, not a predictive signal.** Label it accordingly.

---

## PROMPT 11 — ROUTE BASIS + MACRO SIGNALS

### Goal
Fix the verified defect that route does not enter the rate model, and add macro/commodity
signals **only** where they demonstrably improve out-of-sample accuracy.

### Why this comes now
It is independent of M1/M4 and is the PS's headline ask: *forecasting by vessel class and route*.

### Preserve
`ml/frozen_test.py` leakage guards. The existing forecast pipeline shape.

### Inspect first
`src/opt/types.py:50` (`BasisEntry` — defined, never instantiated in `src/`),
`src/opt/quote.py:197-202` (**`basis={}` hardcoded, with a comment acknowledging it**),
`src/opt/calibration.py`, `src/ml/features/`.

### Verified defect
`$/day` is **identical to the cent** across Newcastle, Richards Bay, Balikpapan and Hampton
Roads for the same cargo. Only `$/MT` varies — and only because transit time differs. On a
dashboard this reads as route-aware pricing. It is not.

### Required implementation
1. Build a defensible route-basis mechanism from data that genuinely supports it.
2. If route fixture data is insufficient (likely), implement the honest decomposition:
   **class/benchmark forecast + evidence-based route adjustment + uncertainty**, with the three
   components *separately labelled* — directly observed / modelled / unavailable.
3. **Never manufacture a route premium.** A route with no evidence returns the class forecast
   with `route_adjustment=None`, clearly shown.
4. **Until route basis is real, stop presenting `$/MT` as a route quote** — either fix it or
   label it as class-rate ÷ transit time.
5. Macro/commodity signals: research lawful public sources (coking coal, iron ore, steel
   activity, bunker proxy, FX). For each candidate run economic rationale → lag analysis →
   leakage review → ablation → out-of-sample test. **Keep only what measurably helps.** Do not
   dump features into XGBoost.

### Explicit non-goals
No scraping of licence-restricted index data. Do not add a signal that only helps in-sample.

### Acceptance criteria
- Two different origins, same cargo/destination, produce **different `$/day`** — or an explicit
  statement that route basis is unavailable, surfaced in the API and UI.
- Every retained macro feature has a documented ablation result.
- Frozen-test leakage guard still passes.

### Tests
`tests/opt/test_basis.py`: **a regression test that four origins no longer yield identical
`$/day`, or that the unavailability is explicitly flagged.** `tests/ml/`: ablation results
asserted; leakage guard.

### Run
`.venv/Scripts/python.exe -m pytest -q` · re-run the four-origin comparison and paste the table.

### Report
The four-origin table before and after · every macro signal tested with its ablation result and
**whether it was kept or dropped** · what remains unavailable.

### Stop conditions
If no lawful route-basis data exists, implement the labelled decomposition and say so — do not
invent premiums.

---

## PROMPT 12 — DF UPGRADE + API + UI

### Goal
Feed DF the now-real M4 inputs, and expose it. DF's `UNAVAILABLE` honesty must survive.

### Why this comes now
DF's wait variables were correctly `UNAVAILABLE` because no honest injection point existed.
After P4/P6, one does.

### Preserve
`DecisionSignature`, the tiered escalation, memoisation, determinism, and the `UNAVAILABLE`
discipline. **`_WAIT_DAYS_UNAVAILABLE_REASON` may only be removed if a genuine injection point
now exists** — verify, do not assume.

### Inspect first
`src/fragility/engine.py` (read the per-variable tier table in its docstring — it documents
exactly why each variable sits where it does), `search.py`, `tiers.py`, `models.py`.

### Required implementation
1. Add variables now backed by evidence: empirical berth wait (P50/P90), berth-specific draft,
   operational draft status, tide sensitivity where a `PORT_RULE` exists.
2. Wire wait-day variables **only if** P4/P6 created a real override path. If not, keep
   `UNAVAILABLE` with an updated reason.
3. Target outputs of this shape:
   - *"Recommendation = Panamax, but +7,500 t flips to Capesize."*
   - *"Permissible draft margin = 0.18 m — operationally fragile."*
   - *"Destination P90 berth wait above 4.2 days flips LOCK → WAIT."*
4. `POST /fragility` endpoint. Bound and cache expensive solves — reuse DF's existing caps.
5. Fragility UI: base recommendation, nearest flip points, fragile-vs-stable ranking, and
   **unavailable variables with their reasons shown, not hidden**.

### Explicit non-goals
Do not weaken `UNAVAILABLE` to populate the screen. Do not remove the evaluation caps.

### Acceptance criteria
- Determinism preserved — the identical-sweep test still passes.
- A wait-driven flip is produced **only** where empirical data supports it.
- Unavailable variables remain visible with reasons in both API and UI.
- `npm run build` passes.

### Tests
Extend `tests/fragility/test_engine.py`: empirical-wait flip on a port with data; still
`UNAVAILABLE` on a port without; determinism; caps respected. `tests/backend/test_fragility_api.py`.

### Run
`.venv/Scripts/python.exe -m pytest -q` · `cd frontend && npm run build` · a real sweep, timed.

### Report
New variables and their evidence source · which stayed `UNAVAILABLE` and why · sweep timing ·
determinism proof.

### Stop conditions
If empirical waits are insufficient at every port, keep `UNAVAILABLE` and report it. Do not
manufacture a flip point.

---

## PROMPT 13 — LIVE REGRET LEDGER

### Goal
Close the loop: persist every recommendation, score it against realised outcomes, and report
regret against baselines.

### Why this comes now
It needs a stable decision surface, which exists after P11/P12.

### Preserve
`opt/stopping.py`'s LSMC — this adds accounting around it, not new decision logic.

### Inspect first
`src/opt/stopping.py`, `src/opt/backtest.py`, `src/ml/frozen_test.py`.

### Required implementation
1. Append-only ledger persisting: decision timestamp, **full input state**, forecasts,
   recommendation, uncertainty, the alternative considered, and the decision boundary.
2. As real outcomes arrive: realised outcome, what waiting would have cost, whether LOCK/WAIT
   was correct, regret, cumulative performance, and comparison against naive baselines
   (always-lock, always-wait).
3. `GET /ledger` + a Regret Ledger screen: recommendation history, realised outcomes,
   correct/incorrect, regret, cumulative vs baseline.
4. **Do not fabricate historical SAIL recommendations.** The ledger starts empty and fills
   forward. An empty ledger renders as empty, honestly.

### Explicit non-goals
No enterprise persistence — a file-backed append-only store is correct for this build.

### Acceptance criteria
- Append-only; entries are never mutated after the fact.
- Empty ledger renders honestly rather than with seeded examples.
- Baseline comparison is real.
- **Known context:** measured decision value is currently ~$0.60/day pooled, $0.00/day for
  Supramax. The ledger must report this honestly if it remains true.

### Tests
`tests/opt/test_ledger.py`: append-only enforced; regret arithmetic on a fixture; baseline
comparison; empty-state behaviour.

### Run
`.venv/Scripts/python.exe -m pytest -q` · `cd frontend && npm run build`

### Report
Schema · what real data exists so far · the measured value vs baselines.

### Stop conditions
If measured edge remains ~zero, **report it**. An honest ledger showing no edge is a stronger
artifact than a hidden one.

---

## PROMPT 14 — RETURN LEG + FINAL INTEGRATION

### Goal
Add the backhaul engine where real evidence supports it, then harden and validate the whole
system for demo.

### Why this comes now
Return-leg evidence (the `D/L` load/discharge flag) only exists after P3.

### Preserve
Everything. This prompt adds one module and then integrates; it rewrites nothing.

### Inspect first
`src/opt/repositioning.py` (the Poisson hazard model — reuse it), `src/opt/voyage.py`
(**do not touch the CP-SAT encoding**), `fact_port_call` (the `load_discharge` flag).

### Required implementation
1. `src/opt/backhaul.py` — score `(inbound parcel, outbound opportunity)` pairs using the
   existing hazard machinery plus observed load/discharge pairs from `fact_port_call`.
   East-coast India imports coking coal and exports iron ore **from the same berths in the same
   week** — the evidence is in the data P3 ingested.
2. Backhaul credit affects the vessel/freight recommendation **only if validated**; otherwise it
   is reported as informational.
3. Final integration: fix the `uv`-dependent launcher (`run.bat`/`run.ps1` call `uv run`, which
   is not installed — use the `.venv` directly), the `polars is_in` deprecation at
   `src/ml/features/calendar.py:34`, and the Starlette/httpx deprecation warning.
4. Performance pass: every endpoint warm-cached; no UI action triggering a 7-second recompute.
5. **End-to-end demo validation**: start both servers, exercise every screen, and paste the real
   output.

### Explicit non-goals
No auth, no rate limiting, no multi-tenant security. Do not rewrite CP-SAT.

### Acceptance criteria
- Launcher works from a clean checkout with documented steps.
- All deprecation warnings resolved or explicitly justified.
- Every screen loads against a running backend.
- Full suite green; `npm run build` passes.

### Tests
`tests/opt/test_backhaul.py`: pairing logic on real observed pairs; credit applied only when
validated. Full-suite regression.

### Run
`.venv/Scripts/python.exe -m pytest -q` · `cd frontend && npm run build` · both servers up ·
every endpoint exercised, real output pasted.

### Report
Final test count · every screen and its state · **remaining honest limitations** · what a judge
can and cannot see.

### Stop conditions
If backhaul does not validate, ship it as informational and say so.

---

# SECTION 6 — COMPLETION MATRIX

| Requirement | Prompt | Final acceptance test |
|---|---|---|
| All-port source research | **P1** | 15/15 ports have a `PortCoverageStatus` |
| Licence/robots guardrails | **P1** | No `PERMITTED` without a quoted evidence string |
| Generalise BT-0 beyond 2 ports | **P2** | Adani path byte-identical; new source = adapter + registry row |
| Adapter architecture | **P2** | Protocol conformance test per adapter |
| `fact_port_call` schema | **P3** | Real fixture PDF parsed; nulls preserved |
| Historical backfill | **P3** | ≥90 days Paradip, or documented limit |
| Append-only / vintage | **P3** | Re-ingest produces no duplicates |
| Quarantine over guessing | **P3** | Interleaved rows quarantined, not invented |
| Real waiting time | **P4** | Empirical P50/P90 vs static literal, per port |
| Real handling rates | **P4** | Observed productivity replaces literals where n suffices |
| Wait distribution not scalar | **P4** | P90 > P50; sample floor enforced |
| Berth constraints all ports | **P5** | Every `PortEnum` member resolves with provenance |
| Tide integration | **P5** | `PORT_RULE` changes a verdict; test proves it |
| Tide authority separation | **P5** | Advisory model provably cannot clear a vessel |
| Berth queue model | **P6** | `P(wait > X)` from empirical data |
| Declared vs observed split | **P6** | Observation never raises a declared limit |
| Three-state feasibility | **P6** | `CANNOT_VERIFY` reachable and tested |
| M4 API | **P7** | 3 endpoints returning real data |
| Port Twin UI | **P7** | PORT→TERMINAL→BERTH drill-down renders |
| M1 scale correction | **P8** | Total vs available reported separately |
| M1 class mix | **P8** | Validated against labelled real calls |
| M1 supply-curve sign | **P9** | Diagnosed with evidence, or negative result reported |
| IV identification | **P9** | Implemented **or claim removed** |
| Kalman filter | **P9** | Implemented **or claim removed** |
| Forward tightness | **P9** | Intervals present and ordered |
| M1 validation | **P9** | Time-based holdout, no leakage |
| M1 real backend API | **P10** | `GET /tonnage-field` < 500 ms warm |
| Remove hash placeholder | **P10** | Guard test: no FNV source in `frontend/src/` |
| Tonnage Field UI | **P10** | Uncertainty visible |
| Route basis | **P11** | Four origins no longer identical `$/day`, or flagged |
| `$/MT` mislabelling | **P11** | Labelled honestly or fixed |
| Macro/commodity signals | **P11** | Every kept feature has an ablation result |
| PortWatch provenance | **P10/P11** | `OBSERVED`/`ESTIMATED`/`MODEL_DERIVED` labels reach UI |
| DF empirical wait flips | **P12** | Flip only where data supports it |
| DF stays deterministic | **P12** | Identical-sweep test passes |
| DF API + UI | **P12** | `POST /fragility` + screen |
| DF `UNAVAILABLE` preserved | **P12** | Unavailable variables visible with reasons |
| Regret Ledger | **P13** | Append-only, baseline comparison |
| Return Leg | **P14** | Validated or informational-only |
| Launcher reliability | **P14** | Clean-checkout start documented |
| Deprecation warnings | **P14** | Resolved or justified |
| Performance/caching | **P14** | No 7-second UI action |
| Point-in-time/vintage | **P3/P14** | Snapshots archived forward; limits disclosed |

**No orphan requirements.** Items deliberately excluded are in Section 7 with reasons.

---

# SECTION 7 — WHAT SHOULD NOT BE BUILT

I am not going to claim everything on the list is achievable. These are excluded, with reasons.

| Item | Verdict | Why |
|---|---|---|
| **Port-to-Plant coupling (full)** | **Build only the port half** | Requires SAIL rake, stockyard, and plant burden-cover data. **None exists publicly and none is in the repo.** Build discharge-productivity → laytime/demurrage → parcel-size consequences from observed port data only, and label the scope honestly. Do not fabricate rake schedules. |
| **Verified Emissions (MRV join)** | **Investigate, expect low yield** | The EU MRV dataset is public and lawful, but joining it to Indian port reports requires an **IMO number**, which the Paradip report does **not** carry — only vessel names. Name-matching across registries produces false positives. Attempt only after P3, report real coverage, and let no-match remain no-match. Do not impute. |
| **Cyclone workability (quantified)** | **Defer past P14** | Needs enough historical cyclone events *co-observed* with port downtime to calibrate. With ~90 days of `fact_port_call`, the sample will not support it. Keep the existing risk alert; revisit after a season of data. |
| **CVaR portfolio** | **Leave opt-in** | `opt/portfolio.py` is mean–variance with a stockout penalty, not CVaR. Correctly opt-in because it needs SAIL burden-cover and stockout-cost figures nobody has. Either rename it accurately or leave it out of the demo. **Do not label it CVaR.** |
| **Almgren–Chriss / `src/impact/`** | **Decide explicitly in P14** | 38 passing tests, zero consumers. It is genuinely novel work. Either give it an endpoint and a screen, or **remove it from the pitch entirely.** Do not keep claiming a moat that no code path reaches. |
| **Compliance firewall / sanctions** | **Do not build** | Requires licensed vessel-identity and sanctions data. No lawful free source. Out of scope. |
| **Landed cost & blend** | **Only if P11 lands commodity data** | Depends entirely on P11 securing lawful coal/ore price series. If P11 fails to, this is not buildable. |
| **Broker circular harvester** | **Do not build** | Signal Group's terms forbid the extraction that produced `raw_data/signal_weekly/`. **This is an existing exposure, not a future one** — 12 usable data points obtained in a way that will not survive a sponsor's question. Consider removing the dependency rather than deepening it. |
| **Auth / rate limiting / multi-tenant** | **Do not build** | Correctly deferred. Not needed for the demo and not currently breaking anything. |
| **Tide as navigational authority** | **Never** | An advisory model must never gate a navigational check. Enforced at the type level in P5. |

## The licence exposure you should decide about deliberately

Not a build item, but the largest single risk in the repo and it is unaddressed as of today:

**Every trained model depends on `raw_data/investing_com/` (scraped Baltic-derived index data)
and `raw_data/signal_weekly/` (extracted from Signal Group articles).** `grep -niE
"licen|terms|copyright|permission"` across `raw_data/sources.md` and `docs/` returns **zero
matches**. This is a Ministry of Steel problem statement. "Where did the Baltic data come from"
is a question that will be asked, and there is currently no prepared answer.

This is a decision for you and your team, not something a Sonnet prompt should quietly patch.
Options, in order of defensibility: obtain permission; substitute a licensed or genuinely open
series; or document the limitation explicitly and frame the models as a methodology
demonstration on restricted data. Doing nothing is the only option that fails badly in public.

---

## Suggested execution order

**P1 → P2 → P3** are strictly sequential (research → architecture → data).
**P4/P5** can run in parallel after P3.
**P6 → P7** sequential. **P8 → P9 → P10** sequential, and can run in parallel with P4–P7.
**P11** is independent — run it any time after P3.
**P12** needs P6. **P13** needs P11/P12. **P14** is last.

If time is short, the highest-value subset is **P1, P2, P3, P4, P6, P7** — that completes M4,
which is the moat with the strongest primary-source evidence behind it and the one no other
team will have.
