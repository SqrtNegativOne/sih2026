# Master reference — SIH26006

**Purpose of this document.** One place a teammate can read, cold, and then build a
winning deck and answer any judge's question without opening the code. Everything
below was read off the running system, the real data files on disk, or the source
itself on 2026-08-29. Where a number could not be verified, it has been left out
rather than approximated.

**Companion documents.** `docs/00_og_problem_statement.md` (the PS as issued),
`docs/10_HOW_IT_ACTUALLY_WORKS.md` (a screen-by-screen plain-language walkthrough,
written against an earlier state — several faults it describes are now fixed),
`docs/11_FAULT_REGISTER.md` (the self-audit), `docs/12_fix_changelog.md` (what was
fixed, why, and how it was verified).

---

## 1. What problem this solves

SAIL buys ocean freight for imported bulk cargo into East Coast India ports mostly
one spot fixture at a time, decided by ringing the market that morning. That is
reactive: no forward view of where rates are going, no systematic answer to "which
ship size", no check that the chosen ship can physically enter both ports, and no
early warning of the things that blow a voyage up. The result is avoidable freight
cost, avoidable idle time, and a chartering strategy nobody can audit afterwards.

This system replaces that with a forecast, a dated recommendation, and a written
reason for every number it shows.

---

## 2. The solution, in one paragraph

You describe one real cargo lot — how many tonnes, from where, to where, when it
must load, and how long a contract you are considering. The system stands on the
most recent real trading day it has market data for, forecasts where each ship
size's charter rate is heading at 7, 30 and 90 days out (with a low/middle/high
band, not a single number), works out the highest daily rate at which signing a
period contract today still beats staying in the spot market, prices the *option*
of not deciding yet, and returns **LOCK** or **WAIT** with the arithmetic shown.
Alongside that it ranks every ship class by what the voyage would actually cost —
rejecting any class that cannot physically enter the load or discharge port on
draft, length, beam or deadweight — estimates queue time at both ends from real
satellite arrival counts, checks four independent live disruption signals, projects
the carbon rating the voyage would earn the vessel under international regulation,
and draws every route the solver considered on a map. A separate screen answers the
problem statement's own stated objective directly: what mix of spot, period charter
and volume contract should cover a whole season's requirement, given how much buffer
stock the plant is carrying.

```
        ┌──────────────────────────────────────────────────────────────┐
        │  YOU TYPE:  tonnes · origin · destination · laycan · term     │
        └───────────────────────────┬──────────────────────────────────┘
                                    │
        ┌───────────────────────────▼──────────────────────────────────┐
        │  MARKET LAYER                                                 │
        │  14 years of daily published freight indices + charter rates  │
        │  → gradient-boosted forecast, 7/30/90 days, low/mid/high      │
        └───────────────────────────┬──────────────────────────────────┘
                                    │  rate fan ($/day)
        ┌───────────────────────────▼──────────────────────────────────┐
        │  DECISION LAYER                                               │
        │  ceiling  →  option value of waiting (4,000 simulated paths)  │
        │           →  weather/cyclone delay charged to the WAIT branch │
        │           →  LOCK or WAIT + the boundary that produced it     │
        └──────┬──────────────────────────────────────────┬─────────────┘
               │                                          │
    ┌──────────▼─────────────┐               ┌────────────▼─────────────┐
    │  PHYSICAL LAYER        │               │  RISK LAYER              │
    │  ship class frontier   │               │  rate regime anomaly     │
    │  port fit: draft/LOA/  │               │  port congestion anomaly │
    │    beam/DWT, both ends │               │  chokepoint transit drop │
    │  real sea routing      │               │  cyclone climatology     │
    │  queue time from AIS   │               │  (per-route, per-basin)  │
    │  CP-SAT vessel↔cargo   │               └────────────┬─────────────┘
    └──────────┬─────────────┘                            │
               └───────────────────┬──────────────────────┘
                                   │
        ┌──────────────────────────▼───────────────────────────────────┐
        │  OUTPUT: verdict · fan chart · class frontier · port limits   │
        │  landed cost · carbon rating · risk feed · route map          │
        │  every recommendation written to an append-only ledger        │
        └───────────────────────────────────────────────────────────────┘

        SEPARATE SCREENS: Portfolio mix · Port Twin · Tonnage Field ·
                          Fragility · Ledger
```

---

## 3. Every screen

### 3.1 Voyage Desk — the main screen

**What you input** (one form, opened from "New Quote"):

| Field | What it drives |
|---|---|
| Cargo volume (tonnes) | The starting point for ship-size selection; the final class comes from the feasible-and-cheapest frontier, not this number alone |
| Cargo type | Carried through and displayed; does not move the price |
| Origin port / Destination port | Sea distance, voyage time, which classes physically fit, which ports get congestion and berth checks, which chokepoints the route crosses, which cyclone basins apply |
| Price as of | Which market day to stand on. Defaults to the latest real trading day on disk. A weekend or holiday no longer fails — it resolves backward to the last real trading day and the response says which day it actually used |
| Contract term (days) | How the 7/30/90-day forecasts are blended into one expected cost, and the horizon the savings figure is multiplied over |
| Laycan start / end | Sanity-checked, used to clamp the "wait for a trough" window so the system can never advise fixing after the cargo was due to load, used to pick the cyclone weeks, and used as the time window for vessel scheduling |
| Risk tolerance | 0 prices on the middle forecast, 1 on the pessimistic one. Moves the ceiling by roughly 1–2% on a typical lot |
| Add vessel (optional, 11 fields) | Turns on voyage scheduling, repositioning, backhaul and the carbon projection. Without a vessel the system still answers "what should I do about this cargo" |
| Cargo revenue (optional) | Required before any vessel is assigned. The system refuses to invent what your cargo is worth |

**What you get back:**

- **Verdict** — a single LOCK or WAIT, the recommended ship class, the term, and a
  money table: what locking today costs over the term, the lowest forecast trough
  and the dates it lands on, and the ceiling. Below it: the expected saving with its
  low/high range, the confidence figure with an explanation of what it does and does
  not mean, the specific rate move that would flip the recommendation, and an
  expandable "why this verdict" showing the real factors — including, in plain
  English, what the option of waiting is worth per day and how far it pulled the
  threshold down.
- **Rate Forecast** — the fan chart plus a row per horizon: low/middle/high daily
  rate, the direction, a confidence figure, and a per-tonne conversion. A badge
  states honestly when the price came from the ship-size benchmark rather than a
  route-specific rate.
- **Risk Feed** — live alerts or an explicit "no signals crossed their thresholds"
  listing the four channels being watched.
- **Fleet Mix Frontier** — one row per ship class, cheapest feasible first, with the
  number of ships, voyage days, cost per tonne and a reliability grade; rejected
  classes are listed greyed out **with the actual binding reason** ("vessel draft
  16.5 m exceeds port limit 16.2 m"), not silently dropped.
- **Port Constraints** — load port against discharge port: maximum deadweight,
  draft, length, beam, expected wait in days and a congestion band, both ends side
  by side.
- **Voyage Assignments** — when a vessel and a revenue figure are supplied: which
  vessel serves which parcel, arrival, wait, finish, ballast hours and profit,
  produced by a constraint solver rather than a heuristic.
- **Landed Cost** — freight, waiting cost, handling, demurrage and commodity price
  as five separately-labelled components, each carrying where it came from, with an
  explicitly-labelled partial total and a list of which components are missing. You
  can type your own commercial terms and recompute.
- **Backhaul Opportunity** — after discharge, where could this ship pick up its next
  cargo, scored in real dollars (probability of a suitable cargo × today's real
  charter rate × window, minus the real fuel cost of the empty leg).
- **Carbon Intensity** — per vessel: the A–E carbon rating the voyage would earn
  under international regulation, the attained and required intensity, and the margin.
- **Route Exploration** — every routing the solver looked at drawn on a world map,
  chosen vs considered vs rejected, using real sea routes where they resolve.

**Why it matters commercially.** This is the screen that replaces the morning phone
call. It converts "the market feels expensive today" into a dated, defensible number
with a walk-away price, and it catches the expensive class of mistake — booking a
ship that cannot enter Paradip — before the fixture, not at the pilot station.

### 3.2 Portfolio — the problem statement's own objective

**Input:** ship class, coverage horizon, and three business facts only SAIL knows —
how many days of buffer stock the plant carries, what a production stockout costs,
and how quickly spot tonnage can be sourced. Optionally a route, and a risk-aversion
slider.

**Output:** the recommended split across **spot / period time charter / contract of
affreightment**, shown as a stacked bar, plus an eight-point efficient frontier —
the whole cost-versus-risk trade-off, chart and table — so you can see what buying
more certainty actually costs.

**Why it matters commercially.** The PS's stated objective is literally "moving from
multiple single spot contracts to short term / medium term multiple voyage
contracts." This screen answers exactly that question, and it answers it with a real
stockout penalty tied to plant cover rather than a preference setting. The three
required inputs have no defaults on purpose: a recommendation built on a guessed
stockout cost would look like advice while being fiction.

### 3.3 Port Twin

**Input:** a port, and a ship's dimensions.

**Output:** a three-state verdict — FEASIBLE, INFEASIBLE, or CANNOT_VERIFY — with the
margin in metres on each dimension and an explicit statement of where the limit came
from; whether a published tide rule binds; the largest ship actually observed calling
there; the real waiting-time distribution (median, 75th, 90th percentile); observed
cargo-handling productivity against the port's published norm; and the underlying
berth register and recent vessel calls as evidence.

**Why it matters commercially.** "CANNOT_VERIFY" is the commercially valuable state
and almost nobody builds it. It says *we do not have a berth register for this port,
here is the general reference figure we fell back to, and here is why our confidence
is below 100%* — which is the difference between a tool a chartering desk can trust
and one it quietly stops using after the first wrong answer.

### 3.4 Tonnage Field

**Input:** none; it loads on open.

**Output:** a "tightness" figure per ship class and per ocean basin — how fast free
ship capacity is being consumed — a forward projection, and an evidence-quality
panel that reports, in the product itself, that the absolute scale is **not**
validated.

**Why it matters commercially.** It is a supply-side pressure signal built from
satellite port-call data rather than broker sentiment. Its honesty panel is the
point: it states its own measured error range and reports that an A/B test found it
does not improve the rate forecast. A screen that tells you not to over-trust it is
worth more than one that hides the same weakness.

### 3.5 Fragility

**Input:** origin, destination, cargo size, laycan. Press "Run sweep".

**Output:** for each of eight inputs, how far that input would have to move before
the recommendation changes — reported as FRAGILE with the distance to the flip,
STABLE with the range searched, or UNAVAILABLE with the reason.

**Why it matters commercially.** This is the answer to a judge's or a manager's best
question: *how much do I have to be wrong before your advice is wrong?* On a real
worked example it found the permissible-draft margin was **three centimetres** — a
genuinely actionable warning that no accuracy metric would ever surface.

### 3.6 Ledger

Two sections, deliberately kept apart.

- **Live Decision Ledger** — every recommendation the system actually made, written
  automatically and permanently as it was made. Later you record what the rate turned
  out to be, and it scores itself: correctness, regret, and performance against
  always-lock and always-wait. Individual entries cannot be edited or deleted; the
  only clear is all-or-nothing, so favourable history cannot be kept while
  unfavourable history is quietly removed.
- **Historical Replay** — a genuine backtest over the frozen historical test period,
  labelled retrospective, kept structurally separate so its numbers can never be
  presented as live performance.

**Why it matters commercially.** Anti-cherry-picking is enforced by the data
structure, not by a promise. That is what makes the performance number credible.

---

## 4. Every major engine

Question answered · method · why that method.

### Market and decision

| Engine | Question | Method | Why this method |
|---|---|---|---|
| **Rate forecast** (`ml/`, `src/data/models/`) | Where does each ship size's charter rate go in 7/30/90 days? | Gradient-boosted trees (XGBoost) predicting the 10th/50th/90th percentile of the log rate at each horizon, from 37 features: own lags to 63 days, 30/90-day rolling mean/std/z-score, returns, calendar (monsoon, Chinese New Year, Indian fiscal quarter), satellite port-call counts, cross-index levels | Beat random-walk, AR(1), LightGBM (tuned and untuned) and an LSTM on pinball loss, pooled and per class, on a fixed validation split. Quantiles rather than a point forecast because every downstream engine needs the uncertainty, not the mean |
| **Ceiling calculator** (`opt/ceiling.py`) | What is the highest fixed daily rate at which locking beats staying spot? | Horizon-blended weighted average of the forecast quantiles over the contract term (days 1–18 priced off the 7-day forecast, 19–60 off the 30-day, 61+ off the 90-day), adjusted by route basis and risk tolerance | Deliberately physics-free — it prices the *market* only. Fuel, distance and port costs belong in the voyage estimator, and mixing them here would make the walk-away price un-auditable |
| **Optimal stopping** (`opt/stopping.py`) | What is the right to keep waiting actually worth? | Least-Squares Monte Carlo (Longstaff–Schwartz, 2001) over 4,000 simulated price paths, producing an **exercise boundary** — a lock/wait price for every day of the horizon, not one number | "Wait" is an American option, not a coin flip: it is the right, not the obligation, to fix later. LSMC is the standard method for pricing exactly that, and it is why this engine is harder to convince to lock than a naive comparison. It is fused into the production decision, not carried as a side channel |
| **Weather tax on the wait branch** (`opt/weather_window.py` → `opt/stopping.py`) | Should bad weather make you fix sooner? | Expected delay days (forecast + cyclone climatology) × today's daily rate, added to the exercise boundary — explicitly and only on the WAIT branch | A storm-delayed voyage costs real hire days, so waiting is more expensive than the option maths alone says. Applied as a one-directional adjustment that can turn WAIT into LOCK but never the reverse, with a zero default that is byte-identical to the untaxed result |
| **Savings distribution** (`opt/monte_carlo.py`) | What is the range of outcomes, not just the average? | 2,000 simulated futures with mean-reverting price paths (Ornstein–Uhlenbeck toward the forecast median), returning P10/P50/P90 savings and the probability that locking wins | Freight rates mean-revert; a flat random shock over-states long-contract uncertainty. Seeded deterministically from the request, so the same question gives the same answer twice |
| **Portfolio mix** (`opt/portfolio.py`) | What split of spot / period TC / volume contract covers a season? | Grid search over the three-way coverage simplex, minimising expected cost plus a risk-aversion multiple of cost variance, with a Poisson-hazard stockout probability on the uncovered fraction | Three real channels with real, separate cost and variance contributions. Risk aversion is normalised to *spot-cost standard deviations* so the same setting means the same thing for a Capesize year and a Handysize month |
| **Decision-value backtest** (`opt/backtest.py`) | Does acting on these recommendations actually save money? | Replays five strategies (always-lock, always-wait, this optimizer, and baselines) over the frozen historical test split, measuring savings and regret against a perfect-foresight oracle | Forecast accuracy is not the business question; realised savings is. The test split is guarded so it can only be touched once, at the end |

### Physical and operational

| Engine | Question | Method | Why this method |
|---|---|---|---|
| **Fleet-mix frontier** (`opt/fleetmix.py`) | Which ship size, and how many? | Enumerate one representative configuration per class, price each under the real forecast and real port limits, reject infeasible ones with the binding reason, return a ranked frontier | A real chartering decision trades cost against schedule reliability; collapsing it to one answer hides the trade-off. The verdict's recommended class is taken *from* this frontier, so the two panels can no longer disagree |
| **Voyage scheduler** (`opt/voyage.py`) | Which vessel serves which parcel, in what order? | Constraint programming (Google OR-Tools CP-SAT), maximising revenue minus laden and ballast fuel, idle/queue/early-arrival cost at the vessel's real daily operating cost, and demurrage; subject to port fit at both ends, laycan windows and physical sequencing | Assignment-with-sequencing under hard feasibility constraints is exactly what CP-SAT is for. Fuel is priced at the real bunker price of the port each leg departs from, not one global constant |
| **Berth Reality Engine** (`berth_truth/`) | Can this ship actually use this port, and what will it be like? | Three-state verdict composing a berth-constraint register, tide authority, and empirical wait and handling distributions from real port-call records — with a confidence score penalised separately for weak arrival data and for an unsourced limit | Two independent provenances must be tracked separately: where the arrival data came from, and where the draft limit came from. Conflating them once produced a 100%-confidence verdict resting on an uncited constant |
| **Port congestion / dynamic wait** (`opt/congestion.py`) | How long will this ship queue? | The port's baseline wait scaled by recent arrival counts (last 14 days) against its own trailing 60-day norm, from real satellite port-call data, trimmed to the date the quote claims to price | A queue estimate is only honest if it uses what was knowable on the date being priced — otherwise a historical quote silently gets today's congestion |
| **Route tracing** (`opt/route_trace.py`, `opt/geography.py`) | What path does this voyage actually take? | Shortest sea route over the Eurostat marine network graph, with a great-circle fallback that is **flagged in the result** when the routing library cannot resolve a pair | A fallback that is not visible in the output is a lie by omission. The flag is why the chokepoint and distance figures can be trusted or discounted appropriately |
| **Per-route chokepoint detection** (`opt/chokepoints.py`) | Which chokepoints does *this* route cross? | Point-to-great-circle-**segment** distance against 28 real chokepoint circles, coordinates taken from the IMF's own chokepoint database | Route polylines are coarse — tens of points across an ocean — so a point-to-vertex test misses real transits. Verified directly: two polyline points 161 and 173 nm from Hormuz, whose connecting segment passes 2.66 nm away |
| **Landed cost** (`opt/landed_cost.py`) | What does this cargo cost delivered, not just shipped? | Freight + empirical wait cost + handling + demurrage + commodity price + FX, each a separately-labelled component with its own provenance | A component that cannot be computed renders as unavailable, never as zero. There is deliberately **no** single all-in total field — folding an unknown into a sum misrepresents "we don't know" as "this cost doesn't exist" |
| **Backhaul / repositioning** (`opt/backhaul.py`, `opt/repositioning.py`) | Where should the ship go next? | Probability of a suitable cargo departing within the window (Poisson hazard from satellite-observed export tonnage ÷ typical parcel size) × today's real charter rate × window days, minus the real distance-and-fuel cost of the ballast leg | Turns a bare probability into money. Disclosed limitation: the rate used is class-level and identical at every candidate port, so candidates are differentiated by cargo probability and ballast cost, not by "rates are better here" |
| **Carbon intensity** (`emissions/`) | What carbon rating would this voyage earn the vessel? | The IMO's own attained-CII arithmetic and A–E rating bands, with every constant taken from the primary resolutions — fuel-to-CO₂ factors, bulk-carrier reference-line parameters, annual reduction factors, and the rating boundary vector | Regulatory arithmetic must match the regulation exactly, so the constants were read out of the actual resolution PDFs and the rating logic checked against the resolution's own worked example. Published reduction factors exist only to 2026, so the engine returns nothing rather than extrapolating |

### Risk and analysis

| Engine | Question | Method | Why this method |
|---|---|---|---|
| **Rate regime alert** (`opt/risk.py`) | Is today's market move abnormal? | z-score of today's log return against the index's own trailing 90-day return distribution | A fixed "5% move" rule means something completely different in a calm market than a volatile one; a z-score self-calibrates |
| **Port congestion alert** | Is this port unusually busy? | z-score of daily dry-bulk call count against its own trailing baseline, date-sorted before slicing | Same reasoning. The sort matters: the source file is genuinely out of order, and reading "the last row in the file" once tested a date from ten months earlier as though it were today |
| **Chokepoint disruption alert** | Is a strait blocked? | z-score of daily dry-bulk transit count, firing on **drops only** | A blockage shows up as transits falling. A spike is not a disruption signal, and treating it as one would produce alerts in exactly the wrong direction |
| **Cyclone alert** (`opt/risk.py` + `data_builders/build_cyclone_climatology.py`) | Is the laycan in an active storm window for *this* route? | Per-basin, per-ISO-week strike rate built from NOAA's IBTrACS best-track archive (satellite era, 1980 onward), keyed off the actual ports and the actual laycan weeks, with a threshold set from the built table's own median | Replaced a hardcoded "October to December" calendar rule that fired identically for Mozambique and Australia. Basins are explicit latitude/longitude boxes, not the archive's own coarse basin column, which mixes the Bay of Bengal with the Arabian Sea |
| **Marine window** (`opt/weather_window.py`) | Will weather delay this voyage? | Seven-day wave-height forecast at both ports, combined with the cyclone climatology for the part of the laycan the forecast does not reach — prorated so the two never double-count | A rolling forecast cannot be harvested once at build time; this is the one deliberate, documented exception to the project's build-time-fetch rule, and it degrades to no-signal on any network failure rather than raising into a quote |
| **Fragility sweep** (`fragility/`) | How far must an input move before the answer changes? | Tiered flip-point search: a closed-form check where one is exact, escalating to a full re-solve only where necessary; binary search on the flip point | Never runs an expensive tier where a cheap one is provably definitive, and never claims a variable is stable when it structurally cannot be searched — those report UNAVAILABLE with the reason |
| **Tonnage field** (`tonnage/`) | How tight is ship supply? | Stock-flow reconstruction of free capacity from satellite port-call counts at 128 ports, ship class inferred from average parcel size; tightness = trailing loading tonnage ÷ reconstructed free capacity; a formal identification gate decides whether the result may be called absolute or only relative | The gate is the interesting part: two independent checks both fail the bar for an absolute number, so the product labels the index RELATIVE and says so on screen |
| **Decision ledger** (`opt/ledger.py`) | Was the advice any good? | Two append-only logs — recommendations and outcomes — linked by id; per-entry edit and delete always raise | Cherry-picking is prevented structurally. Pending entries are excluded from performance rather than backfilled with a guess |

### Built and tested, not yet wired into the product

| Engine | Question | Method | Status |
|---|---|---|---|
| **Demand impact and optimal execution** (`impact/`) | How much does SAIL's *own* buying move the rate it pays, and how should a large requirement be split over time? | Local elasticity via the chain rule through tightness (the demand→tightness derivative is definitional, not fitted), then Almgren–Chriss (2000) optimal execution, then a fixed-point solve so the schedule and the elasticity it is priced against agree | Real code, 30 tests, no endpoint or screen. A design-time literature search found no prior application of Almgren–Chriss to freight chartering |
| **Chokepoint fracture index** (`opt/fracture.py`) | How close is a chokepoint on this route to breaking? | Combines the transit-drop z-score, a conflict-intensity z-score from a 12-month GDELT harvest (compared only against each chokepoint's own history), and membership of a real London-market war-risk Listed Area, into a banded index plus an illustrative war-risk premium | Written this session; no endpoint, no screen, no tests yet |

---

## 5. Data sources

| Source | What it is | What it feeds | Real / modelled / user-supplied |
|---|---|---|---|
| Baltic Exchange daily indices (BCI, BPI, BSI, BHSI, BDI) and time-charter averages, 2005–2026-08-20, scraped | 136,933 rows across 79 series in the master table | The forecast model's target and its lag/rolling/cross features; today's real daily rate | **Real, observed** |
| Signal Ocean weekly route assessments (`SG_*` series) | Real route-level rate quotes, mostly non-India destinations | The route-basis mechanism — and the honest finding that only one series is a genuine East-Coast-India rate, on three observations, which is below the evidence bar | **Real, observed** — but too thin to use, and said so |
| IMF PortWatch daily port calls, 161 ports | Satellite-derived (AIS) vessel arrival counts and import/export tonnage | Congestion and queue-time estimates, the port-congestion alert, tonnage-field reconstruction, backhaul cargo probability, model congestion features | **Real, observed** |
| IMF PortWatch chokepoint transits and chokepoint database | Daily dry-bulk transits and the definitional coordinates of 28 chokepoints | Chokepoint disruption alerts; the geometry used for per-route chokepoint detection | **Real, observed** |
| NOAA IBTrACS v04r01 best-track archive | 726,241 storm fixes, 331 MB, reduced to 136 basin/week rows | The cyclone-season alert and the climatology half of the weather delay | **Real, observed** → reduction is **modelled** |
| Open-Meteo Marine Weather API | Seven-day wave-height forecast at both ports | The forecast half of the weather delay | **Real, observed**, cached 12 hours, degrades to no-signal offline |
| GDELT 2.0 Event Database, 12 months | 1,484 weekly conflict-event rows across all 28 chokepoints | The fracture index's conflict-intensity signal (not yet exposed) | **Real, observed** *of media coverage* — explicitly not of ground truth |
| Port authority and terminal daily traffic reports (PDF/HTML) | 1,211 stored report rows resolving to 334 distinct real port calls at Paradip | Empirical wait distributions, observed size envelope, handling productivity | **Real, observed** |
| Berth constraint registers | 58 real berth rows: Visakhapatnam 29, Dhamra 20, Gangavaram 9 | Berth-level feasibility where a register exists | **Real, declared by the port** |
| Port physical limits for the 16 ports in the network | Max deadweight, draft, LOA, beam, handling rate, baseline wait, bunker price | Every feasibility check, the fleet-mix frontier, voyage scheduling | **Declared constants**, sourced and cited per port; no port register publishes a maximum deadweight, so that specific limit is always a reference constant |
| World Bank Pink Sheet commodity prices | Iron ore and coal benchmark prices, FX | The landed-cost commodity component | **Real, observed** |
| IMO resolutions (MEPC.308(73), 338(76), 352(78), 353(78), 354(78)) | Regulatory constants read from the primary PDFs | The carbon-intensity engine | **Real, declared by the regulator** |
| Joint War Committee Listed Areas circular | Current war-risk listed areas and named countries | The fracture index's war-risk flag (not yet exposed) | **Real, declared by the market** |
| Cargo revenue, handling tariff, demurrage rate, laytime, plant cover days, stockout cost, spot sourcing rate | Business facts the system cannot infer | Voyage assignment profit, landed-cost components, the portfolio mix | **User-supplied, required, never defaulted** |

Every field carries one of five provenance labels — OBSERVED, ESTIMATED, INFERRED,
MODEL_DERIVED, DECLARED — and anything computed from a real input is at best
MODEL_DERIVED. A computed value never inherits its input's label.

---

## 6. What makes this different from an obvious solution

The obvious solution is a rate forecast with a dashboard. Here is what this is that
that is not.

**1. It prices the option to wait, not just the expected price.**
Nearly every entrant will compare today's rate to a forecast and pick the smaller
number. That throws away the only thing that makes waiting valuable — the *right*,
not the obligation, to fix later at a better price. This system prices that right
properly, with the standard method from financial mathematics for exactly this class
of problem, and returns a lock/wait price for every day of the horizon rather than a
single threshold. On a real worked example the option to wait was worth $1,538 a day
and moved the walk-away price from $21,016 to $19,478 — a decision reversal that a
naive comparison cannot produce, and that the system explains in one sentence on
screen.

**2. It refuses to invent numbers, and the refusal is enforced by the build.**
A missing cost component renders as unavailable, never zero. There is deliberately no
single all-in landed-cost figure, because summing an unknown into a total
misrepresents "we don't know" as "this doesn't cost anything." A screen with real
arrival data but no berth register reports CANNOT_VERIFY with a confidence below
100%, not a confident guess. And an automated check fails the build if anything
resembling fabricated data — random numbers, hash-derived values, synthesised series
— appears anywhere in the interface. Most teams will demo a full-looking dashboard;
this one demos an honest one, and can prove the difference mechanically.

**3. It knows the difference between a forecast being accurate and a decision being
good, and measures the second.**
The headline is not model accuracy. It is realised saving against always-lock and
always-wait baselines, replayed over a frozen historical period the model has been
structurally prevented from ever training on. Alongside that, every live
recommendation is written to an append-only record the moment it is made, which can
be cleared entirely but never edited entry-by-entry — so favourable history cannot be
kept while unfavourable history quietly disappears. Anti-cherry-picking is a property
of the data structure, not a promise in a slide.

**4. It answers "how wrong can I be before you're wrong?"**
The fragility screen re-runs the whole decision, nudging one input at a time, and
binary-searches for the point where the recommendation flips. On a real worked
example it found the permissible draft had **three centimetres** of margin before the
answer changed — a genuinely actionable warning that no accuracy metric produces. It
also reports honestly when a variable structurally cannot be searched, rather than
labelling it stable.

**5. The physical world is modelled at the resolution the decision actually needs.**
Chokepoints are detected per route by real spherical geometry, not assumed from a
fixed list — before this, every quote carried a Suez flag whether or not the voyage
went anywhere near it. Cyclone risk comes from a real per-basin, per-week strike
climatology built from the global storm archive, keyed off the actual ports and the
actual laycan, not a hardcoded "October to December." Weather delay is charged to the
waiting branch of the decision in dollars, so bad weather can make the system fix
sooner — and the adjustment is provably one-directional, so it can never talk you out
of locking.

**6. It carries a published, dated audit of its own faults.**
There is a fault register listing every defect found in a hostile self-audit —
severity, evidence, and status — and a changelog recording what was fixed, why, and
how the fix was verified against the original evidence. Thirty-plus faults closed,
each with a re-run check; the handful still open are marked deferred with the
reasoning written next to them. Judges can be handed the list of things that are
wrong with this system. Very few teams can do that, and it is a stronger credibility
signal than any accuracy number.

**7. Two pieces of genuinely new transfer work.**
An optimal-execution framework from equity trading (Almgren–Chriss) has been
transplanted to freight chartering to answer how a large seasonal requirement should
be split across time so that SAIL's own buying does not move the price against
itself — with a fixed-point solve so the schedule and the price impact it is priced
against agree with each other. A literature search at design time found no prior
application of this to chartering. Separately, a chokepoint fracture index fuses
transit anomalies, conflict-news intensity and real war-risk insurance listings into
one banded score. Both are real, working code; both are honestly listed below as not
yet wired into the product.

---

## 7. Honest limitations

Disclosed in the product, in the code, or both. Nothing here is hidden.

**The forecast is route-blind.** It predicts the percentage change in a published
market index per ship size, then applies that to today's real published rate for that
size. It does not know which route you picked. Six different origins return the same
daily rate to four decimal places; only sailing time differs. The reason is real
rather than lazy: the only route-level rate evidence in the entire dataset is three
observations of one Indonesia-to-East-India rate, which is below the evidence bar the
system sets for itself. The interface carries a badge saying so on every quote. Fixing
this needs new licensed data, not new code.

**The market data ends 2026-08-20.** The scrape stopped there, so the system stands
on 20 August and forecasts forward from it. It is not predicting the past; its world
simply ends nine days before the calendar date.

**Only one cargo lot is priced at a time.** Multi-parcel campaign optimisation is a
real scope gap, deliberately deferred as an interface change on the busiest path in
the system rather than attempted late.

**Every maximum-deadweight limit is a reference constant.** No berth register on disk
publishes one, and deadweight is the limit that most often rejects a ship. The
registers that do exist cover three ports and 58 berths; the one port with substantial
real arrival history has no register at all.

**Feasibility tests a ship's full nameplate draft.** Real Capesizes routinely present
part-loaded at draft-constrained ports. The relaxed-tolerance mechanism exists but
only activates when no class at all is feasible, not when one class fails at one port
while another succeeds. Deferred explicitly as a modelling change to the core
feasibility path.

**The tonnage-field scale is unvalidated, and the product says so.** Against real
commercial ballaster counts the reconstruction ranges 0.35× to 25.75× across ten
comparison points, so the index is formally labelled RELATIVE by its own
identification gate. An A/B test in the product reports that adding this signal does
not improve the rate forecast — which means the screen is, by the project's own
measurement, currently disconnected from the recommendations.

**Waiting-time evidence is one port deep.** All 1,211 stored port-call rows, resolving
to 334 distinct calls, are Paradip. Every other port falls back to a baseline.

**The backhaul rate is class-level, not port-level.** Candidates are differentiated by
real cargo probability and real ballast cost, never by "rates are better here." Stated
in the panel itself.

**Two engines are built but not exposed.** The demand-impact and optimal-execution
work has real code and 30 passing tests but no endpoint and no screen. The chokepoint
fracture index was written this session and has no endpoint, no screen, and no tests
yet. Neither should be claimed as a shipping feature.

**Some risk signals are climatology, not nowcast.** The cyclone alert says "this basin
and week have historically been active," not "a storm is approaching." The conflict
signal counts *media coverage*, which tracks news attention as much as ground truth —
which is why it may only ever be compared against a single chokepoint's own history
and never across chokepoints. Verified directly on the real 12-month harvest, where
raw totals rank the Malacca Strait and the Cape of Good Hope far above Bab el-Mandeb
simply because large cities sit inside those circles.

**Congestion date-awareness is partial.** The port-constraints panel prices congestion
as of the date the quote claims. The vessel scheduler and the repositioning engine
still use current congestion even for a historically-dated quote. Documented as a gap
rather than left silent.

**Live ledger performance is empty until outcomes are recorded.** By design — it fills
forward from real use and is never seeded with examples. At demo time it shows the
mechanism, not results; the historical replay is where realised performance lives.

**Two navigation items are visibly disabled**, and four top-bar controls are disabled
with honest tooltips rather than being live-looking controls that do nothing.

---

## 8. Numbers worth quoting on a slide

Every figure below was verified from the repository or a real run. Nothing is
estimated.

**Testing and engineering discipline**
- **1,130 automated tests passing**, 2 skipped, on the last full run (10 min 44 s).
- **90 test files, 1,059 test functions** (the higher count above reflects
  parameterised cases).
- Coverage by area: 431 in the optimizer, 196 in the port-reality engine, 99 in the
  API, 95 in tonnage, 75 in machine learning, 46 in data building, 41 in fragility,
  30 in demand impact, 13 in emissions, 33 repository-wide.
- **21 HTTP endpoints**; a build-failing automated check for fabricated data anywhere
  in the interface.
- **30+ self-audited faults closed**, each re-verified against its original evidence;
  every remaining one marked deferred with a written reason.

**Data volumes**
- **136,933 rows** of real market history across **79 series**, from **2005-01-04 to
  2026-08-20**.
- Machine-learning splits, fixed and embargoed: **9,512 training rows**
  (2012-07-04 → 2022-06-15), **1,413 validation** (2023-01-03 → 2024-06-14), **818
  test** (2025-01-02 → 2026-05-22), 46 columns.
- **161 ports** of satellite port-call data (53 MB); **128 ports** actually used in the
  tonnage reconstruction.
- **726,241 storm fixes** from the global cyclone archive (331 MB), reduced to **136**
  basin-week climatology rows across 4 named basins.
- **1,484 weekly conflict rows** across **28 chokepoints**, 12 months.
- **1,211 port-call report rows** resolving to **334 distinct real calls**; **58 real
  berth rows** across 3 ports.

**Coverage**
- **16 ports** priced: 7 East Coast India discharge ports (Paradip, Visakhapatnam,
  Gangavaram, Gopalpur, Dhamra, Sagar-Sandheads, Haldia) and 9 load ports across
  Australia, the USA, Mozambique, South Africa, Indonesia, Russia and Singapore.
- **4 vessel classes** (Handysize, Supramax, Panamax, Capesize) with representative
  specifications from 32,000 to 180,000 dwt.
- **28 chokepoints** with real coordinates, detected per route.
- **3 forecast horizons** × **3 quantiles** × **4 classes** = 36 numbers per quote.

**Model performance** — pooled, on the frozen test split, against a random walk:

| Horizon | Model pinball loss | Random walk | Model directional hit | Random walk |
|---|---|---|---|---|
| 7 days | **0.0357** | 0.0391 | **68.2%** | 44.7% |
| 30 days | **0.0849** | 0.0925 | **63.0%** | 37.2% |
| 90 days | **0.0886** | 0.1181 | **89.7%** | 26.5% |

Read the 89.7% honestly, and say so on the slide: the market itself rose in 73.5% of
real 90-day test windows, so a zero-intelligence "always predict up" model scores
close to that by construction. The real, defensible claim is a **~16-point edge over
that baseline at 90 days**, and a **25% reduction in pinball loss versus a random
walk** — which is the metric that actually matters, because the whole system consumes
the uncertainty band, not the point estimate.

**Measured performance**
- Tonnage-field reconstruction: **4.5 seconds** across 128 ports.
- Optimal-stopping solve: **4,000 simulated paths**; savings distribution: **2,000**.
- A full eight-variable fragility sweep: roughly 65 evaluations, multiple seconds.
- Deterministic by construction — the same request returns identical figures.

**Two findings worth quoting as evidence of rigour**
- The permissible-draft margin on a real Newcastle→Paradip lot: **3 centimetres**
  before the recommendation flips.
- Correcting three ports' published limits revealed that a representative Capesize is
  genuinely, marginally too deep for Newcastle (16.5 m against a real 16.2 m channel)
  — a true constraint that had been masked by a wrong deadweight figure failing first.

---

## 9. Everything else worth knowing

### 9.1 How to run it

```
uv run uvicorn backend.main:app --reload        # API on :8000, docs at /docs
cd frontend && npm run dev                       # UI on :5173, proxies /api to :8000
uv run python -m pytest -q                       # full test suite (~11 min)
uv run ruff check src backend                    # lint
```

Stack: Python 3.12+, FastAPI, Polars (not Pandas), XGBoost/LightGBM/PyTorch,
OR-Tools CP-SAT, Pydantic v2 with frozen models, searoute; React + TypeScript + Vite
on the front end, with hand-rolled SVG charts and no charting library.

### 9.2 Repository layout

```
raw_data/            raw external pulls, never hand-edited
src/data/            processed parquet/csv built from raw_data
src/data_builders/   the harvesters and transforms that do that
src/ml/              features, models, live forecast
src/opt/             optimizer, quote, risk, stopping, landed cost, portfolio
src/tonnage/         ship-supply reconstruction
src/berth_truth/     port reality, berth registers, empirical waits
src/fragility/       flip-point search
src/emissions/       IMO carbon intensity
src/impact/          demand elasticity + optimal execution (not yet wired)
backend/main.py      every HTTP route
frontend/src/        the trading-desk UI
docs/                problem statement, walkthrough, fault register, changelog
```

### 9.3 The API surface (21 endpoints)

`GET /health` · `GET /meta` · `GET /ports` · `GET /ports/{code}/reality` ·
`GET /ports/{code}/berths` · `GET /ports/{code}/calls` · `GET /ports/{code}/waits` ·
`GET /tonnage-field` · `GET /tonnage-field/forward` · `GET /tonnage-field/validation` ·
`POST /quote` · `POST /quote/stream` (server-sent events, one per pipeline stage) ·
`POST /fragility` · `GET /ledger/live` · `GET /ledger/live/performance` ·
`POST /ledger/outcome` · `DELETE /ledger/live` · `GET /ledger/replay` ·
`POST /landed-cost` · `POST /backhaul` · `POST /portfolio`

Conventions that hold everywhere: ports are code strings resolved by one shared
helper; an unknown port or class returns 422 with the valid list; missing market data
returns 503; a structurally infeasible quote still returns 200 with a typed envelope
naming the problem, because "no ship fits" is a real answer, not an error.

### 9.4 The provenance system

Five labels — OBSERVED, ESTIMATED, INFERRED, MODEL_DERIVED, DECLARED — applied per
field. The rule that makes it meaningful: a value computed from a real input is at
best MODEL_DERIVED and never inherits its input's label. This is why the interface can
show, per cost component, exactly how much of a landed cost is measured versus
assumed.

### 9.5 House rules the code is held to

1. Never fabricate a number. If the data to compute something honestly is not on
   disk, return nothing, raise a named error, or state the gap — never invent a
   plausible-looking value. Enforced by a build-failing check across the interface.
2. Label every field's provenance explicitly.
3. Docstrings state what a module does **not** do, and cite the real source
   (resolution number, dataset, URL) for any external constant.
4. Prefer a named exception to a silent fallback; where a fallback is correct, flag it
   in the returned value so a consumer can see it happened.
5. Every external fetch is a build-time harvester writing to disk, cached, and
   offline-safe. The one exception — a rolling seven-day marine forecast, which cannot
   be harvested once — is documented in the code as a deliberate exception with its
   reasoning, not made silently.
6. Every change is logged with what changed, why, and how it was verified.

### 9.6 Anticipated judge questions

**"Is 89.7% your accuracy?"** No, and we would not lead with it. It is directional hit
rate at 90 days on the frozen test split. The market rose in 73.5% of those windows,
so the honest claim is a ~16-point edge over a zero-intelligence baseline, plus a 25%
reduction in pinball loss versus a random walk. Pinball loss is the number that
matters because the system consumes the whole uncertainty band.

**"Does it work for a route you have no rate data for?"** The daily rate is
class-level, and the interface says so with a badge on every quote. What *is*
route-specific: distance, transit time, port feasibility at both ends, queue time,
chokepoints crossed, cyclone basins, fuel at real per-port bunker prices, and the
landed cost. The rate is the one route-blind component, and it is disclosed rather
than papered over.

**"What stops you cherry-picking a good demo?"** Every recommendation is written to an
append-only record as it is made. Individual entries cannot be edited or deleted; the
only clear is all-or-nothing. And the historical backtest runs on a test split the
model is structurally prevented from ever training on.

**"How do I know the ship actually fits?"** Every class is checked against draft,
length, beam and deadweight at both ports, and rejected classes show the binding
reason. Where a port has a real berth register, that is used; where it does not, the
system says CANNOT_VERIFY and lowers its own confidence rather than guessing.

**"What's wrong with it?"** Section 7, and the fault register — a dated list of every
defect found in a hostile self-audit, with severity, evidence, and status.

### 9.7 Demo notes

- Open the app cold; the pricing date self-corrects to the latest real trading day.
  Do not type a date after 2026-08-20.
- A route worth showing for physical constraints: 75,000 t Newcastle → Paradip. The
  frontier correctly rejects a Panamax on Paradip's deadweight limit and recommends
  two Supramaxes — and the verdict panel agrees with it, which it did not before the
  audit.
- A route worth showing for chokepoints: Hampton Roads → Visakhapatnam resolves
  Gibraltar, Suez and Bab el-Mandeb. Newcastle → Paradip resolves Torres, Ombai and
  Malacca — and correctly carries no Suez flag.
- Expand "why this verdict" on the verdict panel. The sentence about what the option
  to wait is worth is the single best line the system produces.
- The Portfolio screen shows a real cost/risk trade-off for a Capesize and a
  degenerate "period charter dominates at every risk setting" case for a Panamax.
  Both are genuine current market findings, not a bug.
- Clear the ledger before demoing so it does not carry rehearsal quotes as history.
