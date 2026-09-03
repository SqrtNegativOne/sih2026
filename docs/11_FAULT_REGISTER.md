# Fault register

Everything wrong with the app, checked against the problem statement, ranked by how much damage
it does. Every entry was verified against the running system on 2026-08-29 — backend on
`127.0.0.1:8000`, frontend on `localhost:5173`, real data, real responses. Nothing here is
inferred from reading code alone unless the entry says so.

Companion document: [`10_HOW_IT_ACTUALLY_WORKS.md`](10_HOW_IT_ACTUALLY_WORKS.md).

**Severity key**

| | |
|---|---|
| **BLOCKER** | The demo fails, or the headline number is wrong. Fix before showing anyone. |
| **MAJOR** | A stated capability doesn't do what it says, or a required PS deliverable is missing. |
| **MODERATE** | Real defect; a judge who probes will find it. |
| **MINOR** | Polish and hygiene. |

---

## Part A — What is genuinely good

Stated first, because the fix list below is long and the foundations are not the problem.

- **The data sourcing is real and well documented.** Baltic index history back to 2012, IMF
  PortWatch satellite port calls for ~130 ports plus all 28 chokepoints, World Bank commodity
  prices, FRED FX. Nothing is synthetic.
- **The forecasting pipeline is methodologically sound.** Chronological splits with a 200-day
  embargo, walk-forward discipline, quantile outputs rather than point estimates, a fixed
  frozen test set, honest comparison against random-walk and AR(1) baselines.
- **The option-value treatment of LOCK/WAIT is genuinely sophisticated** and is the strongest
  intellectual asset in the project. Framing "wait" as an American option and pricing it with
  Longstaff–Schwartz is a real idea, correctly implemented, with two real numerical bugs
  already found and fixed.
- **The refusal to fabricate is consistent and unusual.** Missing handling tariffs, missing
  demurrage terms, missing route evidence, missing wait samples — all render as an explicit
  reason, never a silent zero. That instinct is worth protecting.
- **The build and the test suite genuinely pass.** `npm run build` and `tsc --noEmit` are both
  clean, and the full Python suite runs **1038 passed, 2 skipped** — exactly the figure the team
  guide claims. Verified end to end here, not taken on trust.
- **The chokepoint alert works correctly** and fired a real, correct signal in testing.

The problem is not the engine room. It is that the wiring between the engine and the screen is
wrong in ways that make the product contradict itself.

---

## Part B — Blockers

### F-01 · BLOCKER · The frontend cannot reach the backend out of the box

The dev server proxies `/api` to **port 8001**. `run.bat`, `run.ps1`, the README and the team
guide all start the backend on **port 8000** (uvicorn's default — no `--port` is passed).

Verified: `curl http://127.0.0.1:8001/health` → connection refused while the backend was
running normally on 8000. Every screen would show *"Could not reach the optimizer. Is the
backend running?"*

**Fix:** one of the two. Pass `--port 8001` in the launchers, or change the proxy target to
8000. Whichever — make the README, both launchers and the Vite config say the same number.

---

### F-02 · BLOCKER · The first quote a new user runs always fails

The "Price as of" field defaults to **your real calendar date**, not to the latest date the app
has data for.

Cause: the form's initial value is captured on the first render, before the `/meta` request
returns. At that moment the latest-data-date is still unknown, so it falls back to
`new Date()`. When `/meta` lands, the hint text updates but the field's value never does.

Verified end to end in a headless browser: open app → pick Newcastle → pick Paradip → press Run
Quote → red error box reading *"No real TC quote/forecast for Panamax as of 2026-08-29 — cannot
price a 75,000 dwt cargo lot."*

That is the first thing a judge sees.

**Fix:** initialise the date field from the meta response (or seed it server-side), and put
`max=` on the input so a later date can't be picked at all.

---

### F-03 · BLOCKER · The headline verdict recommends a vessel class the app itself says cannot berth

The vessel class in the Verdict panel comes from a pure size lookup on cargo tonnage. It never
consults the ports. The Fleet Mix panel *does* consult the ports. They disagree constantly, and
both are on screen at the same time.

Verified, Newcastle → Paradip, same request:

| Cargo | Verdict says | Rate it prices on | Fleet Mix says |
|---|---|---|---|
| 25,000 t | Handysize | $15,670 | Handysize ×1 ✔ |
| 50,000 t | Supramax | $20,698 | Supramax ×1 ✔ |
| **75,000 t** | **Panamax** | **$18,790** | *Panamax ×1 — cannot call Paradip: DWT 82,000 > port max 75,000.* Cheapest feasible: **Supramax ×2** |
| **120,000 t** | **Capesize** | **$40,170** | *Capesize rejected.* Cheapest feasible: **Supramax ×3** |
| **180,000 t** | **Capesize** | **$40,170** | *Capesize rejected.* Cheapest feasible: **Supramax ×4** |

And it is systematic across every problem-statement port. For a 150,000 t cargo from Newcastle:

| Destination | Verdict | Actually feasible |
|---|---|---|
| Paradip | Capesize | Supramax ×3 |
| Vizag | Capesize | Panamax ×2 |
| Gangavaram | Capesize | Panamax ×2 |
| Gopalpur | Capesize | Panamax ×2 via Vizag |
| Dhamra | Capesize | Panamax ×2 |
| Sagar-Sandheads | Capesize | Panamax ×2 via Dhamra |
| Haldia | Capesize | Panamax ×2 via Dhamra |

**Seven out of seven.** And the money is wrong too: at 180,000 t the verdict quotes one Capesize
at $40,170/day when the real answer is four Supramaxes at $20,698/day each — $82,792/day. The
headline understates the daily cost by 51%.

Worse, that wrong class then propagates into everything downstream: the ceiling, the LOCK/WAIT
call, the Monte Carlo savings, the option boundary, the ledger entry and the fragility sweep are
all computed on a ship that cannot enter the port.

**Fix:** make the class selection consult feasibility. The cheapest *feasible* configuration
from the fleet-mix frontier should be the one the verdict prices. This is the single highest-
value fix in the list.

---

### F-04 · BLOCKER · The same request returns different money every time

The savings simulation uses Python's global unseeded random generator. Four identical requests,
back to back:

| Run | Confidence | Expected savings/day | Headline total |
|---|---|---|---|
| 1 | 46.8% | −$184.14 | −$5,524 |
| 2 | 46.0% | −$206.39 | −$6,192 |
| 3 | 46.6% | −$172.67 | −$5,180 |
| 4 | 45.7% | −$244.95 | −$7,348 |

A 42% spread on the headline figure. Press the button twice on stage and the number changes.

It also means the one quantity that visibly differs between origins is **simulation noise, not
route information** — a judge clicking through origins would see the confidence % move and
reasonably conclude the model is route-aware. It is not (F-05).

**Fix:** pass a seeded generator. It is a one-line change to an existing, already-supported
parameter.

---

## Part C — Major: problem-statement deliverables that don't work

### F-05 · MAJOR · Forecasting is route-blind — the PS's central ask

The PS asks for freight forecasting "for various vessel types **and trade routes**", from
"Australia, the US, Mozambique, Russia and Indonesia."

Verified: identical cargo, laycan and date; six different origin countries:

| Origin | Ceiling $/day | 30-day p50 | Verdict | Entry window |
|---|---|---|---|---|
| Newcastle, Australia | 18,141.4554 | 19,078.49 | WAIT | days 27–33 |
| Hampton Roads, USA | 18,141.4554 | 19,078.49 | WAIT | days 27–33 |
| Beira, Mozambique | 18,141.4554 | 19,078.49 | WAIT | days 27–33 |
| Balikpapan, Indonesia | 18,141.4554 | 19,078.49 | WAIT | days 27–33 |
| Vostochny, Russia | 18,141.4554 | 19,078.49 | WAIT | days 27–33 |
| Richards Bay, S. Africa | 18,141.4554 | 19,078.49 | WAIT | days 27–33 |

Byte-identical to four decimal places. Only sailing distance differs.

The mechanism to adjust by route exists and is tested. It never activates, because the only
route-level rate evidence in the dataset is **three observations** of an Indonesia→East India
rate, quoted in $/tonne rather than $/day, which is below the app's own evidence bar. Every
route resolves to "route basis unavailable."

To be fair: the app *discloses* this with a "Class-only" badge. But a badge is not a feature.
Asked "should I lift from Australia or Mozambique this month?", the product's answer is the
same number twice.

**Fix options, cheapest first:** (a) derive a per-route basis from the sailing-distance and
bunker-cost differential you already compute, labelled as modelled, not observed; (b) harvest
more route-level anchors; (c) at minimum, make the class-only limitation loud rather than a
small grey badge.

**PARTIALLY RESOLVED (2026-09-03, F-93) — via option (b), and only for one family.**

`data_builders.harvest_route_rates` harvests handybulk's daily page of indicative charter levels,
which quotes lanes **in dollars per day** — the denomination this entry and `opt.basis` both
identify as the blocker. The mechanism then activated with no change to it, exactly as its
docstring promised.

The reproduction above no longer reproduces:

| Origin | Ceiling $/day | Route evidence |
|---|---|---|
| Balikpapan, Indonesia | **20,898.06** | MODELLED, +7.87% |
| Muara Pantai, Indonesia | **20,898.06** | MODELLED, +7.87% |
| Newcastle, Australia | 19,142.05 | ROUTE_RATE_BASIS_UNAVAILABLE |
| Richards Bay, S. Africa | 19,142.05 | ROUTE_RATE_BASIS_UNAVAILABLE |
| Hampton Roads, USA | 19,142.05 | ROUTE_RATE_BASIS_UNAVAILABLE |
| Beira, Mozambique | 18,061.72 | ROUTE_RATE_BASIS_UNAVAILABLE |

**This fault stays OPEN**, and the table is why. Of 92 lanes the source published on the day this
was built, exactly **one** route family gained evidence. Australia, the US, Mozambique and Russia
have no EC-India lane quoted at all — including Newcastle, the desk's most-quoted origin. South
Africa looked like a second family until the destination was checked: both its India lanes
discharge on the **west** coast, a different coast and a different market, and counting them would
have been quietly wrong.

So "should I lift from Australia or Mozambique this month?" is still answered class-only for both.
What has changed is that the answer is no longer *uniformly* class-only, the Indonesia families
move on real published evidence, and the honest branch is now visibly the exception rather than
the entire behaviour.

Two further caveats, recorded rather than buried:

- These are **indicative broker levels** ("fixed around $22,500"), not settled fixtures — ESTIMATED,
  the same standing as the Signal assessments already feeding this module, and never OBSERVED.
- The source publishes no archive, so this evidence accumulates forward from 2026-09-03 and cannot
  be backfilled. At `n=1` the family is MODELLED; it reaches `MIN_ROUTE_OBS` and becomes VALIDATED
  after five publication days, with no code change.

Option (a) — a distance-and-bunker-derived basis, which would cover *every* family including
Australia — remains unbuilt and is the obvious next move on this fault. It was not attempted here
because it is a modelling decision rather than a data one, and worth taking deliberately.

---

### F-06 · MAJOR · The savings label is inverted

`VerdictBlock` prints `Math.abs(savings)` under one of two hardcoded captions chosen by the
LOCK/WAIT flag. When the action is WAIT but the savings figure is positive, the caption says
the opposite of what the number means.

Verified live (55,000 t Newcastle→Paradip, with vessel):

```
lock_action                  : WAIT
today_quote_usd_per_day      : 20,698
ceiling_usd_per_day          : 19,477.52
expected_savings_usd_per_day : +252.34      ← POSITIVE
expected_savings_usd_total   : +7,570.08    ← POSITIVE
prob_savings_positive        : 0.596        ← 60%
```

The screen renders:

> **Verdict: WAIT**
> Waiting avoids an expected loss of **$7,570**
> Confidence locking beats spot **60%**

The number $7,570 means *locking today beats expected spot by $7,570 over the term.* The caption
says waiting avoids a loss of that amount. Directly inverted.

Note also the panel is internally incoherent: a giant WAIT, above a line saying locking wins by
$7,570, above a line saying locking wins 60% of the time. Both are defensible individually — the
option value of waiting exceeds the naive edge — but nothing on screen says so.

**Fix:** two things. Choose the caption from the *sign* of the number, not from the action. And
show the option value explicitly, so "the naive comparison favours locking, but the right to
wait is worth more" is visible rather than implied.

---

### F-07 · MAJOR · The recommended entry window can fall after the cargo has to load

The "wait for trough" date is the minimum of the daily p50 curve across the next 90 days. It
never looks at your laycan.

Verified: laycan **12–19 September**, recommended entry window **16–22 September**. The app
advises fixing after the loading window closes, and shows both facts on the same screen without
comment.

Also verified: the window is **days 27–33 regardless of contract term** — the same calendar
answer for a 7-day charter and a 365-day charter.

**Fix:** constrain the trough search to the window in which fixing is still useful (before
laycan start, minus positioning time), and say so when no such trough exists.

---

### F-08 · MAJOR · Port-congestion alerts are silently dead for most ports

The risk engine is handed each port's display name (`Vizag`, `Richards_Bay`, `Beira`,
`Balikpapan`), while the satellite data files are named by their source label
(`Visakhapatnam`, `Richards_Bay_ZA`, `Beira_MZ`, `Balikpapan_ID`). The lookup misses, the file
isn't found, and the check returns nothing — no error, no disclosure.

Verified by calling the alert function directly with exactly what the API passes it:

| Port passed | File found? |
|---|---|
| Paradip, Gopalpur, Dhamra, Haldia, Newcastle_AU, Vostochny_RU | yes |
| **Vizag, Gangavaram, Richards_Bay, Beira, Balikpapan, Hampton_Roads, Singapore, Muara_Pantai, Sagar_Sandheads, Gladstone_AU** | **no — alert skipped silently** |

The correct name mapping already exists in the codebase and is used correctly by the wait-days
estimator. It just isn't used here.

**Fix:** route this lookup through the existing mapping table.

---

### F-09 · MAJOR · Even for the ports that do resolve, congestion tests the wrong day

The port-call data files are not stored in date order (Paradip has 755 out-of-order steps in
2,782 rows). The congestion check reads the file and takes the **last row in file order** as
"today", without sorting.

Verified for Paradip with `as_of = 2026-08-20`:

```
rows after date filter                 : 2,783
last row in FILE order (what it tests) : 2025-10-21, value 0.0
last row in DATE order (what it should): 2026-08-14, value 4.0
the 60-row "baseline" it compares against, by date:
   2025-09-05, 2024-09-03, 2024-09-04, 2025-09-06, 2024-09-05, 2025-09-07, ...
```

So it z-scores a day from **October 2025** against a scrambled window spanning three different
years, and reports the result as current congestion as of August 2026.

The sibling chokepoint check *does* sort correctly — which is what makes this an oversight
rather than a design choice.

**Fix:** sort by date before slicing. One line.

---

### F-10 · MAJOR · Port handling rate is corrupted, and it silently quadruples every voyage

The scheduler replaces each port's published handling rate with an "empirical" one derived from
observed data. At Paradip that substitution produces **280 t/hour** against a published
**1,200 t/hour**.

Verified:

```
PARADIP effective handling rate : 280.0 t/h   (empirical)
static published literal        : 1200.0 t/h
median observed actual_tpd      : 6,720 t/day   (n = 1,211)
median observed norm_tpd        : 18,500 t/day  (n = 551)
→ discharging 75,000 t at Paradip:
     with the empirical rate : 267.9 hours = 11.2 days
     with the published rate :  62.5 hours =  2.6 days
```

Three independent problems produce that 280:

1. **75% of the rows are duplicates.** 1,224 rows are only **304 distinct port calls** — every
   vessel is re-ingested from each daily report it still appears in (one ship appears 56 times).
2. **210 rows have `actual_tpd = 0`** — vessels listed on a day they did no cargo work. They
   drag the median down.
3. **The sample is not dry bulk.** By cargo description: ~609 rows dry-bulk-ish, **149 liquid
   or gas** (crude oil, high-speed diesel, propane, butane, motor spirit), 14 container, 452
   unclassified. Oil tankers at a mooring buoy are being used to set a coal ship's discharge
   rate.

That 11.2-day discharge then flows into voyage time, laycan feasibility, demurrage exposure and
idle-time economics — invisibly, because the Port Twin panel that would show it says
"insufficient data (n=0)" (it filters by commodity, and commodity is blank on every row).

The same corruption drives the "Actual / Norm = 36%" figure on Port Twin, which is additionally
a ratio between medians of two *different* row sets (1,211 vs. 551) — not a paired comparison.

**Fix:** deduplicate on (vessel, arrival timestamp) at ingestion; drop zero-work rows; classify
and filter by trade before computing a dry-bulk rate; and don't substitute an empirical rate for
a published one unless the filtered sample clears the gate.

---

### F-11 · MAJOR · Wait-time percentiles are length-biased ~35% high

Same duplication, different consequence. A ship that waits longer appears in more daily reports,
so it is counted more times. That is a textbook length-biased sample.

Verified:

| | n | P50 | P90 |
|---|---|---|---|
| As shipped (all rows) | 1,211 | **73.9 h** | **299.7 h** |
| Deduplicated | 303 | **54.8 h** | **232.0 h** |

The advertised "n≈1,200 real observations" is really n≈304, and the headline median is 35% too
high. This feeds Port Twin, the landed-cost wait component and the fragility context chips.

**Fix:** as F-10. Deduplicate first, then recompute.

---

### F-12 · MAJOR · Port Twin states something factually false

The Observed Envelope panel prints, at Paradip:

> Max observed draft **21.5 m** · Max observed LOA **339.8 m** · Max observed beam **60.1 m**
> *"No conflicts — every observed call falls within the declared limits."*

While the panel next to it says the declared limit is **14.3 m draft / 225 m LOA / 32.2 m beam**,
and rejects a 14.5 m ship as INFEASIBLE.

Cause: the conflict check returns an empty list immediately when a port has no berth register
entry — which is exactly the case for Paradip, the only port with real observations. It never
falls back to comparing against the constants the verdict itself just used.

(Separately: the 21.5 m figure is a **crude oil tanker at an offshore mooring buoy** — the
*Atherina*, 339.76 m, cargo "CRUDE OIL". 35 of the calls exceed 16 m draft and every one of them
is at an SPM buoy or an oil jetty.)

**Fix:** compare observations against whatever limit the verdict actually used, and label the
source. And filter the observed envelope to dry-bulk berth calls.

---

### F-13 · MAJOR · Confidence 100% on an unsourced constant

Port Twin reports **Confidence 100%** for a verdict whose own fields read:

```
limit_source     : PORTENUM_FALLBACK
binding_constraint: null
berth_id         : null
```

There is no berth register for Paradip. The limit is a hardcoded number in the source with no
citation. The confidence score doesn't penalise falling back to it.

The register coverage across the seven PS-named ports:

| Port | Berths | with draft | with LOA | with beam | with DWT |
|---|---|---|---|---|---|
| Dhamra | 20 | **0** | 13 | 0 | **0** |
| Gangavaram | 9 | 9 | 9 | 0 | **0** |
| Vizag | 29 | 28 | 28 | 8 | **0** |
| Paradip, Gopalpur, Haldia, Sagar-Sandheads | **no register at all** | | | | |

**No port publishes a maximum DWT** — and DWT is the limit that actually rejects ships in this
app. Every DWT decision in the product runs on an unsourced constant.

**Fix:** subtract confidence for a fallback limit; show the limit's source on screen; and either
source the DWT limits properly or stop using DWT as the binding check (see F-14).

---

### F-14 · MAJOR · One wrong constant makes Capesize impossible from every PS-named country

Verified: 150,000 t into Vizag (a deep port, 17 m draft, 180,000 DWT limit), from each origin:

| Origin | Capesize feasible? | Reason |
|---|---|---|
| Newcastle, Australia | **no** | port max DWT 85,000 |
| Gladstone, Australia | **no** | port max DWT 150,000 |
| Hampton Roads, USA | **no** | port max DWT 100,000 |
| Beira, Mozambique | **no** | port max DWT 30,000 |
| Balikpapan, Indonesia | **no** | port max DWT 75,000 |
| Muara Pantai, Indonesia | **no** | port max DWT 80,000 |
| Vostochny, Russia | **no** | port max DWT 170,000 |
| Richards Bay, S. Africa | yes | — |
| Singapore | yes | — |

Capesize is feasible from exactly two origins, **neither of which the problem statement names.**
Australia, USA, Mozambique, Russia and Indonesia are all excluded.

Two things are wrong here:

1. **The constants are wrong.** Newcastle is the world's largest coal export port and loads
   Capesize and Newcastlemax routinely; 85,000 DWT is not a real limit. The source comment even
   concedes the figure "looks conservative next to what was found while sourcing this."
2. **The check itself is the wrong physics.** It tests the *ship's deadweight* against a port
   DWT figure. Part-loading — bringing a 180,000 DWT ship in at 85,000 t of cargo — is how the
   trade actually works. Draft is the real constraint; DWT is not.

Consequence: the app can recommend only three of the four PS-named vessel classes on PS-named
routes.

**Fix:** source the port DWT/draft limits properly with citations, and change the feasibility
test to draft-at-intended-loading rather than ship deadweight.

---

### F-15 · MAJOR · "Price as of" must be an exact trading day or it 503s

The date is matched exactly against the data, with no as-of fallback. Verified:

| Date | Day | Result |
|---|---|---|
| 2026-08-15 | Saturday | **503** |
| 2026-08-16 | Sunday | **503** |
| 2026-08-20 | Thursday | 200 |
| 2026-07-04 | Saturday | **503** |

Roughly one date in three that the picker will happily accept fails outright, with a technical
error message. This is inconsistent with the rest of the codebase, which uses backward as-of
joins everywhere else.

**Fix:** resolve `as_of` backwards to the most recent trading day and say which day was used.

---

## Part D — Major: PS deliverables absent or hollow

### F-16 · MAJOR · The stated objective — spot → period/COA — has no user interface

The PS's Objective section is explicit: *"moving from multiple single spot contracts to short
term / medium term multiple voyage contracts."*

The code to answer that exists (`run_portfolio_analysis` — a spot / period-TC / COA coverage
mix optimiser). **It has no API endpoint and no screen.** It is unreachable from the running
product.

It needs three business inputs the app can't infer (plant buffer stock days, cost of a stockout,
how fast SAIL can source spot tonnage) — which is a good reason for it to be an explicit,
opt-in screen with those as fields, not a reason for it to be invisible.

**Fix:** add an endpoint and a screen with those three inputs. This is the PS's own stated
objective and is currently the largest single scope gap.

---

### F-17 · MAJOR · Only one cargo is ever priced — "multiple voyage" is not modelled

The quote path constructs exactly one cargo parcel. The multi-parcel machinery in the scheduler
is real, but the API surface offers no way to submit a second parcel.

So "N voyages over a period", the thing a COA actually is, cannot be expressed.

**Fix:** accept a list of parcels on the quote request.

**RESOLVED (2026-09-03, F-87/F-88).** Via a dedicated `POST /season-plan` and a Season Plan screen
rather than by widening `/quote`, which is the better shape: scheduling six lots together is a
different problem from pricing six lots separately, so it deserves its own request and its own
answer. `opt.voyage.schedule_voyages` — the multi-parcel CP-SAT model this entry calls "real but
unreachable" — is what serves it, unchanged.

Worth recording that this entry was accurate and stayed marked *deferred* for two releases after it
had actually been built. A register that under-reports its own progress is a smaller problem than
one that over-reports it, but it is still a wrong answer to the question the register exists to
answer.

---

### F-18 · MAJOR · Repositioning advice has no economics in it

The PS asks for idle-time management: repositioning, alternative employment, reduced
deadheading.

What the app computes is `P(cargo) × market rate × 30 days − ballast fuel − wait × $500/day`.
Two problems:

1. The market rate term is the **class-level rate, identical at every candidate port**. So the
   only things that differentiate ports are the cargo probability, the distance and the wait.
   There is no "rates are better in the Atlantic this month" signal, because the forecast has no
   geography (F-05).
2. `P(cargo)` **saturates near 1.0** at any port with satellite coverage and is a flat 0.5
   placeholder at any port without. So the ranking collapses to *"go to the nearest port we
   happen to have data for."*

The same applies to the Backhaul panel, whose displayed "score" **is** that probability and
nothing else — no distance, no cost, no money.

**Fix:** put a real value differential into the score, and cap or calibrate the hazard rate so
it discriminates between covered ports instead of saturating.

---

### F-19 · MAJOR · The headline model accuracy figure is a trend artifact

The reference table reports **89.7% pooled directional hit-rate at 90 days** on the test set
(95.8% for Supramax). That is not credible for freight rates 90 days out, and the same file
explains why.

Directional hit-rate is defined as `sign(prediction − today) == sign(actual − today)`. The
random-walk baseline predicts no change, so it can only score when the market falls. Its test
score at 90 days is **26.5%** — meaning **the market rose in 73.5% of the 90-day windows in the
test period**.

The test period is 2025-01 to 2026-05, during which the BDI roughly tripled. A constant
"predict up" model would score ~73.5%. The model's 89.7% is a real improvement on that, but the
honest framing is *"+16 points over always-predict-up in a sustained bull market"*, not
"89.7% accurate."

Also worth stating plainly: the test set is **818 rows** — 234 Capesize, 234 Panamax, 236
Supramax, **114 Handysize**.

**Fix:** report directional skill against an always-up baseline as well as against random walk,
and lead with pinball loss, which does not have this pathology.

---

### F-20 · MODERATE · "Real-time port congestion" is not real-time and not for both ends

The PS asks for real-time congestion at origin *and* destination. What exists:

- Satellite arrival counts run to **2026-08-14**, six days behind the market data and 15 behind
  today. Not real-time, but reasonable.
- Real berth-level queue data exists for **Paradip only**, six weeks of it (13 Jul – 26 Aug
  2026), and is 75% duplicated (F-11).
- The congestion *alert* channel is broken for most ports (F-08, F-09).
- Five ports have no satellite coverage at all: Gangavaram, Sagar-Sandheads, Gladstone,
  Hampton Roads, Singapore.

---

### F-21 · MODERATE · Congestion and hazard estimates ignore the requested date

`dynamic_wait_days` takes no date argument at all — it always reads the last 74 rows of the
sorted file. `cargo_probability_within_window` uses the trailing 180 days of the file's own
maximum date. The route-basis table is built once with no date.

So a "reproducible historical quote" for, say, March 2025 gets **August 2026 congestion**.
The `as_of` parameter is honoured by the forecast and the risk alerts but not by these.

---

### F-22 · MODERATE · The macro/commodity requirement is answered with a negative result

The PS asks for "global economic indicators, commodity price trends." Real World Bank and FRED
data was harvested. It was A/B tested as a forecasting feature and **rejected** — none of the
five candidates cleared the adoption bar.

That is a legitimate scientific outcome and it is disclosed. But as a product, the answer to
"does this system use commodity prices and macro indicators?" is: only to convert a landed cost
into rupees, and only if you tick a box. The two modules that implement macro features are dead
code, imported by nothing but their own tests.

---

## Part E — Frontend

### F-30 · MAJOR · Source code and file names are rendered to the end user

This is not a metaphor. Verbatim from the Fragility screen, in the findings list a user reads:

> *"No parameter on `opt.quote.quote()`, `quote_envelope`, `opt.fleetmix.enumerate_fleet_mix`,
> or `opt.stopping.solve_lock_or_wait` overrides wait days independently of live
> PortWatch/empirical data — verified directly against the current code (P5, re-checked after
> P2/P3/P4): `opt.fleetmix` never reads `dynamic_wait_days` at all, `opt.stopping.
> solve_exercise_boundary` takes no wait-day argument, and the only consumer, `opt.voyage`'s
> CP-SAT objective, requires real vessels and does not feed any `DecisionSignature` component
> even then…"*

Roughly 90 words, twice on the page. And the Ledger screen prints a filename:
*"per `run_optimizer_backtest.py`'s own methodology."*

**Fix:** these are developer notes. Move them to an API field the UI doesn't render, or rewrite
as one plain sentence: *"Wait-time sensitivity can't be tested — nothing in the decision path
takes a wait-day override yet."*

---

### F-31 · MAJOR · Internal build labels are used as screen titles

| On screen | What a user makes of it |
|---|---|
| `TONNAGE FIELD — M1 · PHYSICAL FREIGHT PRESSURE` | "M1"? |
| `PORT TWIN — M4 BERTH REALITY ENGINE — PORT → TERMINAL → BERTH` | "M4"? |
| `DECISION FRAGILITY — DF — HOW FAR IS THIS RECOMMENDATION FROM CHANGING?` | "DF"? |
| `LIVE DECISION LEDGER — LIVE_DECISION_LEDGER` | an enum |
| `HISTORICAL MODEL REPLAY — HISTORICAL_MODEL_REPLAY` | an enum |
| `A: EXISTING FORECAST · B: EXISTING + M1 PHYSICAL-PRESSURE FEATURES` | an A/B experiment |
| `LIMIT SOURCE: PORTENUM_FALLBACK` | a Python class name |
| `T1 CLOSED_FORM ASSUMPTION`, `T3 FULL_QUOTE USER_INPUT` | tier codes |
| `Config: Supramax:2:D` | an internal signature |
| `feasibility_set flips` | a variable name |

M1 / M4 / DF / P1–P7 are the internal build-phase labels from the project plan. They mean
nothing outside the repo.

---

### F-32 · MAJOR · A sort sentinel is displayed as a score

The Fragility findings show **"score 1,000,000,000,000.0"** on every stable finding. Verified
from the API: `fragility_score: 1000000000000.0`. It is an internal constant used to push
"nothing found" entries to the bottom of a sort. Four of the eight findings display it.

**Fix:** show "—" or "no flip found" for these.

---

### F-33 · MAJOR · The Landed Cost input form is cut off and unreachable

The panel is a fixed 260 px tall. The five cost rows plus the total fill it. The
"Fill the gaps with your own assumptions" form — handling rate, demurrage, laytime, commodity,
currency toggle and the Recompute button — sits below the fold, inside an inner scroll container
with no visible scrollbar. Confirmed in a 1600×1000 screenshot: the section heading is visible,
none of the inputs are.

The panel that lets a user turn "1/5 components real" into "5/5" is effectively hidden.

---

### F-34 · MODERATE · The navigation lies about what the app contains

- The left rail shows **12 destinations**. Four are screens; six are scroll anchors on one
  page; two are disabled.
- **"Estimates" is highlighted as active whenever you're on the Voyage Desk**, because the desk
  has no rail entry of its own. The highlight points at the wrong thing.
- **"SCHEDULING" is clipped at both edges** — the label is wider than the 56 px rail.
  "Tonnage Field" wraps to two lines.
- The top bar's Forecast/Fleet/Ports/Risk/Map is a *second* set of anchors to the *same* six
  panels. "Forecast" is hardcoded as the active tab and never changes.

---

### F-35 · MODERATE · Four dead controls in the top bar

The search box, the notification bell, the settings gear and the help "?" are all rendered,
all styled as interactive, and all wired to nothing.

---

### F-36 · MODERATE · The map is always zoomed to half the world

The map fits its bounding box over **every port in the system**, not the ports on your route.
Since the list spans Hampton Roads (76°W) to Newcastle (152°E), every quote renders a world map
with a thin line on it — even a 2,626 nm Balikpapan→Paradip hop.

**Fix:** fit to the visible routes' own coordinates.

---

### F-37 · MODERATE · Computed explainability is never rendered

Already in the API response, already typed in the frontend, shown nowhere:

| Field | What it contains |
|---|---|
| `explanations` | Plain-English "why" for the lock/wait call, each assignment, repositioning, savings, fleet mix — with the method named |
| `stopping_result` | The full day-by-day exercise boundary and the option value in $/day |
| `review_trigger` | "Re-solve weekly, or immediately if [the specific live alert]" |
| `p10_savings` / `p90_savings` | The downside and upside range, not just the middle |

A real example the engine produced and the screen discarded:

> *"Option value of waiting: $1,538/day (the expected benefit of being able to keep watching and
> lock later instead of deciding only today), from 4,000 simulated market paths. That option
> value pulls today's threshold down to $19,478/day, below the $21,016/day it would be without
> accounting for waiting."*

Explainability is PS deliverable (7). The engine delivers it; the UI throws it away.

---

### F-38 · MODERATE · The Ledger fills with junk and cannot be cleaned

Every `/quote` call — including every exploratory click — writes a permanent, append-only entry.
There is no delete. 17 rows accumulated during testing, all identical-looking, all "pending".

Every performance metric reads "—" until a human types in a realised rate for a future date, so
at demo time the screen shows a table of junk under a row of dashes.

`exercise_boundary_usd_per_day` is null on **every** entry (it isn't threaded through), so the
most valuable stored field is always empty.

**Fix:** don't auto-record exploratory quotes — add an explicit "record this decision" action.
Ship a way to clear the log. *A `raw_data/ledger/ledger_entries.jsonl` with 17 test rows exists
in your working tree from this audit — delete that directory.*

---

### F-39 · MINOR · Inconsistent presentation and dead code

- Port names appear as `Newcastle_AU` in the quote form and `Newcastle AU` on other screens.
- Port picking uses a typeahead for origin/destination but a plain `<select>` for a vessel's
  current port.
- "Cargo DWT" on Port Twin is actually the *vessel's* DWT.
- The confidence bar can never fall below 50% by construction — it is always at least half full.
- The browser tab is titled **"frontend"**.
- Rejected fleet-mix rows display `$0` and `0.0` in the cost and voyage-day columns.
- **~1,500 lines of unused UI components** ship in the bundle: `sidebar.tsx` (700 lines),
  `sheet`, `tabs`, `select`, `card`, `table`, `tooltip`, `skeleton`, `separator`, `button` —
  none reachable from the app. Plus `hero.png`, `react.svg`, `vite.svg`.
- `npm run build` produces a bundle with no proxy, so the production build can never reach the
  API. Only `npm run dev` works.
- Three different "$/MT" figures appear on the Voyage Desk at once ($16.59 fleet-mix, $4.45
  rate-forecast, $4.55 landed-cost) with no explanation that they measure different things.

---

## Part F — Backend hygiene

### F-40 · MODERATE · Hardcoded operating cost, off by an order of magnitude

The vessel's daily running cost in the scheduling objective is **$500/day**, hardcoded, not
exposed in any form. A real Panamax is $5,000–6,000/day. It prices idle time, queue waiting and
early arrival in the CP-SAT objective and the repositioning score.

### F-41 · MODERATE · Deadweight is used as cargo tonnage throughout

The form asks for "Cargo volume (DWT)". Deadweight is a *ship* capacity including bunkers,
stores and ballast — cargo intake is typically ~95% of it. That figure is then passed to the
landed-cost calculator as `cargo_volume_mt` and divided into every $/MT on screen.

### F-42 · MODERATE · Risk tolerance is non-monotonic

Verified: ceiling $18,141 at 0, $18,439 at 0.5, **$18,397 at 1.0**. It should increase
monotonically. It decreases, because simulation noise (F-04) is larger than the effect. The
documentation also contradicts the code: two docstrings say risk tolerance blends toward **P10**;
the code blends toward **P90**.

### F-43 · MINOR · One documentation claim overstates what ships

The team guide states *"Verdict — LOCK/WAIT recommendation, with the LSMC option-value
reasoning."* The Verdict panel renders no LSMC reasoning at all (F-37) — the option value is
computed, returned by the API, and never drawn.

Its "1038 passed, 2 skipped" claim, by contrast, is **exactly right** — verified here.

### F-43b · MINOR · The test suite takes 42 minutes

`pytest -q` runs in **2,508 seconds — 41 minutes 48 seconds** on this machine. That is long
enough that nobody will run it before pushing, which is how a green suite quietly stops being a
safety net. Nothing in the documentation mentions the runtime.

**Fix:** mark the slow tests (the PSO calibration, the Monte Carlo backtest, the tonnage
reconstruction) and default to a fast subset, with the full suite on demand or in CI.

### F-44 · MINOR · Product code that exists only to be tested

| Module | Lines | Status |
|---|---|---|
| `src/impact/` (market-impact scheduling) | 467 | Deliberately disconnected; a test enforces it |
| `src/tonnage/vesselclass.py` | 352 | Imported by nothing but its own test |
| `src/ml/macro_features.py` + `features/macro.py` | 327 | Ablation-rejected; imported by nothing but their tests |
| Weather-event and port-logistics branches in the scheduler | ~100 | Reachable in code, never populated by the API |

~1,150 lines of Python and ~1,500 lines of TypeScript that no user path touches. This inflates
the test-count headline.

### F-45 · MINOR · Build artifact committed

A 53 KB `.coverage` file sits in the repository root.

---

## Part G — Data coverage, stated plainly

| Data | Coverage | Ends |
|---|---|---|
| Baltic indices (BCI/BPI/BSI/BHSI/BDI) | 2012-07-04 → 2026-08-20, ~68% of calendar days | **2026-08-20** |
| $/day TC averages — Capesize | 279 observations, 2020-04 → 2026-08-20 | 2026-08-20 |
| $/day TC averages — Panamax | 275 observations, 2024-03 → 2026-08-20 | 2026-08-20 |
| $/day TC averages — Supramax / Handysize | **185 observations each, 2025-11 → 2026-08-20** | 2026-08-20 |
| Satellite port calls (11 relevant ports) | 2,783 days, 2019-01-01 → 2026-08-14 | 2026-08-14 |
| Chokepoint transits (28) | 2,785 days, 2019-01-01 → 2026-08-16 | 2026-08-16 |
| Route-level rate anchors | **1 to 11 observations per series** | — |
| Berth-level port calls | **Paradip only**, 304 distinct calls, 43 daily reports | 2026-08-26 |
| Berth register | Dhamra, Gangavaram, Vizag only; no DWT anywhere | — |

Two things worth internalising:

- **Supramax and Handysize have nine months of $/day history.** Every dollar figure the app
  quotes for those classes rests on that.
- **There are 5 route-family anchors in the entire dataset**, and none in the right unit. That
  is the root cause of F-05, and no amount of modelling fixes it — it needs data.

---

## Part H — Fix order

**Before you show anyone (half a day):**

1. F-01 — make the ports agree. One number in three files.
2. F-02 — default "Price as of" to the real data date; add `max=`.
3. F-04 — seed the simulation.
4. F-06 — fix the inverted caption.
5. F-32 — stop printing the sort sentinel.
6. F-30 — delete the code-prose from the two Fragility findings.
7. Delete `raw_data/ledger/` (test residue from this audit).

**This week — makes the product coherent (2–3 days):**

8. F-03 — price the verdict on the cheapest *feasible* class. Highest-value fix in the document.
9. F-37 — render the explanation, the option value and the review trigger. The best content in
   the system is already computed and free to display.
10. F-33 — let the Landed Cost form be reachable.
11. F-07 — constrain the entry window to the laycan.
12. F-09, F-08 — sort by date; use the existing name mapping. Two small changes that revive an
    entire PS deliverable.
13. F-31 — rewrite every panel title in charterer's language.

**Next — the credibility work (a week):**

14. F-10, F-11 — deduplicate the port-call store and filter to dry bulk. This changes real
    numbers, so do it deliberately and re-baseline.
15. F-12, F-13 — make the conflict check and the confidence score honest.
16. F-14 — source the port limits with citations; switch feasibility to draft, not deadweight.
17. F-15 — as-of resolution for dates.
18. F-19 — restate the accuracy claim against an always-up baseline before a judge does it for
    you.

**Then — the scope gaps (a week+):**

19. F-16 — build the spot/period/COA screen. It is the PS's own stated objective.
20. F-17 — accept multiple parcels.
21. F-05 — get more route-level rate data, or ship a distance-and-bunker-derived basis clearly
    labelled as modelled.
22. F-18 — put real economics into repositioning and backhaul.

**Consider cutting rather than fixing:** the Tonnage Field screen is honest, expensive and, by
the project's own ablation, disconnected from every recommendation. It is a strong slide and a
weak screen. Decide which it is before demo day.
