# SIH26006 — Rebuild Plan: from "freight forecaster" to a reflexive chartering platform

## Context

the ML teammate built a real, working first pass at SIH26006 in `sih2026-main/`: a data pipeline
(Baltic index history, IMF PortWatch port calls, Signal weekly route anchors), six quantile
forecasting models at h=7/30/90, a CP-SAT voyage scheduler, a lock/wait ceiling rule, a Monte
Carlo savings estimator, PSO calibration, and a decision-value backtest. The data-sourcing work
in particular is genuinely good — `raw_data/portwatch/PULL_NOTES.md` is the kind of artifact most
hackathon teams never produce.

Two problems:

1. **It has correctness bugs that break the headline numbers.** The ML model forecasts Baltic
   *index points* while every downstream component labels the result *USD/day*; the distance
   function silently returns 10,000 nm for any port pair not in a hardcoded list of 46; the
   repositioning engine is mathematically incapable of ever recommending a reposition; the PSO
   calibrates its hyperparameters on the same test split it reports performance from.
2. **It has no moat.** Every deliverable maps 1:1 onto a bullet in the problem statement. Twenty
   other teams will ship XGBoost + OR-Tools + a dashboard. Nothing here makes a judge sit up.

This plan does three things: fixes what's wrong, builds what the PS asks for and the ML teammate missed, and
adds one architectural capability that reframes the problem entirely.

**Decisions taken:** full rewrite latitude across `src/`; ~2–3 month runway; harvest ~150 curated
global dry-bulk ports (not 14, not 2,065).

---

## Part 1 — Audit of the existing code

### What is good and stays

| Asset | Why it survives |
|---|---|
| `raw_data/` + `PULL_NOTES.md` | Real IMF PortWatch ArcGIS harvest with pagination quirks documented. Reuse the pull pattern, scale it 10×. |
| `raw_data/baltic_routes.csv` | Correct Baltic route dictionary with TC weighting formulas. Currently unused — becomes a real join table. |
| `raw_data/signal_weekly/` | Contains per-class **per-basin ballaster counts** and tonne-mile indices. the ML teammate treated these as sparse rate anchors; they are far more valuable as supply-side ground truth (see moat). |
| Long-format `master_long.parquet` design | `series_id \| date \| value \| unit \| source` with source-rank dedupe and conflict dropping is the right shape. Keep. |
| Decision-value backtest concept (`opt/backtest.py`) | Measuring $/day saved vs `always_lock` / `oracle` rather than only pinball loss is the right instinct. Keep the idea, fix the methodology. |
| CP-SAT formulation skeleton | Flow conservation + laycan windows + alternative-destination sub-parcels is a sound PDPTW encoding. Keep, then fix and extend. |

### Defects, ranked by blast radius

**P0 — these produce wrong numbers in the demo**

1. **Unit mismatch: index points sold as USD/day.**
   `sih2026-main/src/config/samples.toml` sets targets to `BC_INDEX/BPI_INDEX/BSI_INDEX/BHSI_INDEX`
   — Baltic *index points*. `ForecastFan` in `sih2026-main/src/opt/types.py` and every consumer
   treat those numbers as USD/day. A "Capesize ceiling of $4,100/day" is really BCI = 4100 ≈
   $33,678/day. The verified relation is not even a clean scalar (BCI 4,100 ↔ $33,678; BCI 4,429 ↔
   $40,170 — ratios 8.2 and 9.1), so it needs empirical calibration, not a constant.

2. **Silent distance fallback.** `_get_distance_nm` returns `10_000.0` in
   `sih2026-main/src/opt/voyage.py` and `5_000.0` in `sih2026-main/src/opt/repositioning.py` for
   any pair missing from `RouteEnum`. 15 ports = 105 pairs; only ~46 are defined. Newcastle→Gopalpur
   silently becomes 10,000 nm, which sets transit time *and* fuel cost *and* the objective.

3. **Repositioning cannot reposition.** In `sih2026-main/src/opt/repositioning.py`, `basis_mean` is
   hardcoded to `0.0`, so `port_tce` is identical at every candidate port. Score reduces to
   `const − ballast_cost − wait_cost`, which is maximised by not moving. The engine is structurally
   incapable of expressing "there is more cargo at Singapore than at Paradip" — which is exactly
   PS deliverable (c).

4. **PSO calibrates on the test set.** `calibrate_pso` in `sih2026-main/src/opt/calibration.py`
   searches `(theta, sigma_long, risk_tolerance)` to maximise decision value *measured on the same
   rows* the backtest reports. Reported savings are in-sample. A sharp judge kills the project on
   this single point.

5. **Survivorship bias in the backtest.** `_build_fan` in `sih2026-main/src/opt/backtest.py` silently
   `return None`s when the model's quantiles cross — dropping precisely the rows where the model was
   most confused, then reporting metrics on the survivors.

6. **The demo script is broken.** `sih2026-main/run_blackbox_scenario.py` reads `rec.lock_wait_text`
   and four sibling fields that no longer exist on `OptimizerRecommendation`. It crashes on run.

**P1 — PS requirements not implemented**

7. **No vessel-type recommendation engine.** PS deliverable (b) — "recommend the most suitable vessel
   type for a given cargo volume and O/D pair". Nothing in the codebase selects a class. CP-SAT
   assigns *given* vessels to parcels; it never answers "one Panamax or two Supramaxes?"

8. **LOA and beam are never checked.** `Port.max_loa_m` / `max_beam_m` exist in
   `sih2026-main/src/opt/network.py`; `_vessel_can_call` only tests DWT and draft, and `Vessel` has
   no `loa_m`/`beam_m` fields at all. The PS names LOA and beam explicitly.

9. **Risk mitigation is a hardcoded string.** `sih2026-main/src/opt/api.py` returns
   `ReviewTrigger(schedule="WEEKLY", conditions=["BDI jumps >5%"])` — literal, always. PS deliverable
   (d) is unimplemented.

10. **No macro or commodity features.** The PS explicitly requires "global economic indicators,
    commodity price trends". There is not one: no coal price, no iron ore price, no bunker/Brent,
    no FX, no China steel activity.

11. **Congestion is static.** `expected_wait_days` is a per-port literal in `network.py`. PortWatch
    daily data is used only as a raw same-day call count, never converted to a queue or waiting-time
    estimate. "Real-time port congestion" is not real.

12. **No basis calibration script.** The design in `docs/02_overview.md` specifies a stats script that
    regenerates the route-family BASIS table from Signal anchors. It doesn't exist; the demo
    hardcodes the numbers.

13. **US and Russia origins missing** from the data entirely (no PortWatch pull, no ports); Gangavaram
    unresolved in PortWatch.

**P2 — methodology and engineering**

14. **Horizon semantics.** `y_step_h{h} = log_value.shift(-h)` is *h rows* (trading days), but
    `docs/03_model_guide.md` and `ceiling._horizon_weight` treat h as calendar days. h=90 is really
    ~126 calendar days, so contract-term weighting is wrong.
15. **Lags computed after the target-null drop** in `build_samples.py` — `lag_k` is k *surviving rows*
    back, not k trading days back.
16. **Invalid model comparison.** `docs/03_model_guide.md` tables LSTM beside XGB, but LSTM predicts
    only n=108 of 917 test rows.
17. **Test set is burned.** `baseline_metrics.csv` holds test rows for all six models. Their own rule
    was "touch test once, ever."
18. **No speed optimization.** `fuel_consumption_tpd` is a scalar independent of speed; there is no
    speed–consumption curve. Slow steaming is the single largest operational lever in real chartering
    and it isn't representable.
19. **Bunkers.** One global constant `BLENDED_BUNKER_USD_PER_TONNE = 610.0`; per-port
    `bunker_price_usd` defined and never read.
20. **CP-SAT scale.** `trans` is O(|V|·|C|²) with |C| = parcels × alternative destinations. 10 vessels
    × 20 parcels × 3 dests = ~36k booleans, under a 5-second solve limit.
21. **Monte Carlo re-sorts the fan list inside the innermost loop** (`_interpolate_fan_for_day`), and
    PSO calls it n_particles × n_iterations × rows times.

### PS coverage today

**Superseded by the P7 audit (2026-08-28) -- the table below is the as-found snapshot from
before any of P0-P6's work and is kept only as a historical record of the starting point.**
The real, current, verified-by-execution state is:

| PS deliverable | Status (P7, verified by execution) |
|---|---|
| Forecast rates by class × route | Class-aware: real (`ml.live_forecast`, per-class XGBoost). Route-aware: the real evidence-gating mechanism exists and is tested (`opt.basis`), but all 8 route families currently resolve `ROUTE_RATE_BASIS_UNAVAILABLE` -- verified live: `today_quote_usd_per_day` is identical across different real origins for the same class/destination today, honestly labelled via `route_evidence`, not silently claimed as route-aware. |
| (a) Optimal market entry timing | Real: `opt.stopping`'s LSMC exercise-boundary solve is fused into the production LOCK/WAIT decision (not merely additive), verified via `tests/opt/test_stopping.py` (27/27 real tests). |
| (b) Vessel type optimization | Real: `opt.fleetmix`, live on every `/quote` call (`fleet_mix` field). |
| (c) Idle scenario management | Real: `opt.repositioning` rewritten with real per-port Poisson hazard rates; verified live it distinguishes real ports by cargo probability (not the original hardcoded-identical-TCE defect). |
| (d) Risk mitigation / early warning | Real: `opt.risk` (regime/congestion/chokepoint/cyclone signals from real data), wired into `run_optimizer`'s `review_trigger`. |
| Port infra constraints | Real, both ends: DWT/draft/LOA/beam via `opt.voyage._vessel_can_call`, real berth-level register data (Dhamra/Gangavaram/Vizag), real tide authority/impact, real empirical wait + handling productivity where the P2 sufficiency gate passes -- verified live via `berth_truth.reality.get_port_reality`. |
| Macro / commodity indicators | Real data exists (World Bank Pink Sheet + FRED, P4) and is used in the Commercial landed-cost chain (P6, real commodity price + FX). Honestly tested and rejected as a forecasting *feature*: all 5 real candidates ran a genuine A/B XGBoost ablation and none cleared the adoption threshold (verified live, `ml.macro_features.run_all_macro_ablations()` -- see the P7 completion report for the exact real numbers). Not a gap; a disclosed negative result. |
| Real-time congestion | Real: `opt.congestion.dynamic_wait_days` scales each port's static baseline by real, current PortWatch activity, not a fixed constant. |
| Dashboard | Real: a five-view React/TS frontend (Voyage Desk, Port Twin, Tonnage Field, Fragility, Ledger), each backed by a real API call -- verified live per-screen in the P7 completion report. |
| Objective: spot → multi-voyage period contracts | Real: `opt.portfolio` optimizes the spot/period-TC/COA mix against real forecast-driven cost and variance; deliberately not auto-wired into `run_optimizer` (needs real SAIL burden-cover/stockout figures this repo does not have) -- exposed as an explicit opt-in (`run_portfolio_analysis`), not silently defaulted. |

<details>
<summary>As-found snapshot (pre-P0, superseded -- click to expand)</summary>

| PS deliverable | Status |
|---|---|
| Forecast rates by class × route | Partial — class only, in wrong units, route basis uncalibrated |
| (a) Optimal market entry timing | Weak — ceiling rule + argmin-of-P50 scan; no option value of waiting |
| (b) Vessel type optimization | **Missing** |
| (c) Idle scenario management | **Broken** (cannot ever recommend a move) |
| (d) Risk mitigation / early warning | **Missing** (hardcoded string) |
| Port infra constraints | Partial — DWT + draft only; no LOA, beam, berth, tide, handling capacity |
| Macro / commodity indicators | **Missing** |
| Real-time congestion | **Missing** (static constants) |
| Dashboard | **Missing** |
| Objective: spot → multi-voyage period contracts | Partial — TC vs spot only; no COA structuring |

</details>

---

## Part 2 — The moat

### The reframe

Every freight model in this competition — and essentially every one in the literature — makes the
same two assumptions without stating them:

1. The freight market is an **exogenous** stochastic process. You observe it and react.
2. Rates are best predicted from **their own history** (lags, returns, volatility, seasonality).

Both are wrong in ways that matter specifically to SAIL.

The freight rate is not a random walk with drift — it is a **clearing price** between physical
tonne-mile demand and the physical supply of ships that can reach the load port in time. Brokers do
not trade off BDI autocorrelation; they trade off *how many ballasters are in the Indian Ocean this
week*. Signal Ocean sells exactly this number, per class, per basin, and it is the acknowledged
leading indicator of the market. the ML teammate already scraped a dozen weeks of it and used it as a footnote.

And SAIL is not a price-taker at the level where it actually operates. Globally SAIL is noise. But
in the decision that matters — *this laycan week, this lane, this class* — SAIL taking three
Panamaxes out of a regional spot list of fifteen-to-twenty-five available ballasters is 12–20% of
local supply. The forecast the model produces is invalidated by the act of trading on it.

### The capability: **Tonnage Field** + **Footprint**

Two interlocking subsystems that no other team will build.

#### Component 1 — Tonnage Field: a physical supply-side twin of the dry bulk market

Reconstruct, from free public data, a daily estimate of **how much carrying capacity of each vessel
class will be available in each ocean basin on each future date** — the number brokers pay Signal
Ocean for — and then forecast rates *structurally* from supply/demand tightness rather than from
price autocorrelation.

Mechanism:

- **Harvest** ~150 globally significant dry-bulk load and discharge ports from IMF PortWatch
  (2,065 available, free, daily, satellite-AIS-derived) plus all **28 chokepoints** (Suez, Panama,
  Bab el-Mandeb, Malacca, Cape of Good Hope, Hormuz…). the ML teammate used 14 ports and zero chokepoints.
- **Infer class mix without vessel identity.** PortWatch gives counts and tonnage, not ship names.
  But `export_dry_bulk / portcalls_dry_bulk` is the **mean parcel size per call at that port** — a
  direct observable proxy for the size-class distribution calling there. Port Hedland resolves to
  Capesize, Indonesian river terminals to Supramax/Handysize, US Gulf to Panamax. Deconvolve the
  per-port parcel-size distribution against the four class DWT bands to get class-attributed
  departures. *This is the trick that makes the whole thing possible from free data.*
- **Stock-flow state estimation.** Model the fleet as compartments — one per (class × basin) plus
  in-transit lanes — with departures observed at load ports, arrivals observed at discharge ports,
  and transit times from a real sea-distance graph. The latent stock (ships free in each basin) is
  unobserved; estimate it with a constrained Kalman filter, with chokepoint transits as flow
  measurements on the long lanes and a mass-balance constraint against UNCTAD annual fleet totals.
- **Forward projection.** Convolve today's in-transit positions forward through voyage physics to
  get `available_tonnage[class, basin, date]` out to 90 days. This is a genuine forward supply curve,
  not a forecast — most of it is already determined by ships currently at sea.
- **Tightness index and structural supply curve.** Define
  `tightness[class, basin, t] = forward_tonne_mile_demand / available_tonnage`, then estimate
  `rate = f(tightness)` by quantile regression, instrumenting with weather- and chokepoint-driven
  supply shocks to break the simultaneity between rate and tonnage.
- **Validate against ground truth.** Scrape the Signal weekly archive (free, republished on Hellenic
  Shipping News, ~2 years of weekly monitors) for their published per-class per-basin ballaster
  counts and tonne-mile indices. Plot our reconstruction against theirs. *"We rebuilt a commercial
  analytics product from free IMF data — here is the correlation"* is the single strongest slide in
  the deck.

#### Component 2 — Footprint: pricing our own market impact, and executing around it

Once you have a supply curve, you can price your own demand against it. This turns the forecast from
`forecast(market_state)` into `forecast(market_state, our_plan)` — an interface change that ripples
through the entire system.

- **Local elasticity.** The slope of the estimated supply curve at current tightness gives
  `∂rate/∂demand` for a given (class, basin, week). Report it with confidence intervals and be honest
  that global impact ≈ 0 while local impact is material.
- **Fixed-point solve.** The optimizer's plan perturbs local tightness → shifts the clearing rate →
  changes the optimal plan. Solve for the fixed point (damped iteration; typically converges in 3–5
  passes). The output is a **self-consistent** plan, not one that invalidates its own forecast.
- **Optimal charter execution.** A large requirement — say 480 kt over Q3 — should not be fixed as
  one clip. Transplant the **Almgren–Chriss** optimal-execution framework from equity markets:
  split the requirement into child charters across *time*, *vessel class*, and *load port*, trading
  **impact cost** (concentrating demand moves the price against you) against **timing risk**
  (spreading it out exposes you to market drift). Output an efficient frontier of
  (expected cost, cost variance) and let the manager pick a risk point. Searching the literature and
  the web turns up no application of Almgren–Chriss to freight chartering — this is a genuinely
  novel cross-domain transfer.
- **Footprint score.** Every recommended plan carries a number: what fraction of forecast regional
  availability it consumes in its laycan window, and the estimated $/day the plan costs itself.

### Why this is a moat

- **It is a reframe, not a feature.** "Stop forecasting the price; model the market that makes it,
  and account for being part of it."
- **It changes the architecture.** A state-estimation layer is inserted between raw data and the
  forecaster; the optimizer becomes a fixed-point solver; the forecast interface takes our own plan
  as an argument.
- **It is not what a generic AI assistant says when asked for "another innovative feature."** That
  prompt returns sentiment analysis, digital twins, explainable AI, or an RL agent. It does not return "deconvolve
  per-port mean parcel size to attribute AIS port calls to vessel classes, then Kalman-filter a
  fleet compartment model."
- **It requires five things at once:** a domain insight (ballasters set the price), a data insight
  (PortWatch is global and free, and parcel size encodes class), an algorithmic insight (constrained
  state-space estimation + IV identification), a cross-domain insight (optimal execution theory), and
  a systems insight (the forecast/optimizer loop must close).
- **It feeds the PS deliverables rather than sitting beside them.** Entry timing (a) gets a real
  supply-driven signal; idle management (c) gets a defensible `P(cargo at port p within N days)`;
  risk mitigation (d) gets physical early warnings (chokepoint disruption, ballaster collapse) instead
  of a string literal.

### Ideas considered and rejected

Recorded so we don't relitigate them: LLM/RAG over shipping news (every team will do it, zero moat);
RL chartering agent (2.4k training rows, judges who know ML will penalise it); carbon/CII-aware
chartering (real but a cost term, not an architecture — folded in as a feature); agent-based market
simulation (unvalidatable); FFA hedging portfolio (forward-curve data isn't free, and derivatives
trading is politically awkward for a PSU); freight term-structure surface (circular — we'd be
bootstrapping a curve from our own forecast); joint origin-sourcing arbitrage (high value but
copyable in one sentence — folded in as a P2 feature); plant-level burden inventory and rake
evacuation (excellent Ministry-of-Steel resonance but drifts into a different problem statement —
folded in as an optional objective term).

---

## Part 3 — Target architecture

```
sih2026/
  backend/                    FastAPI service, async job runner, SSE progress
  frontend/                   React + TypeScript + Vite dashboard
  src/
    data_builders/
      harvest_portwatch.py    ~150 ports + 28 chokepoints, resumable, rate-limited
      harvest_macro.py        World Bank Pink Sheet, FRED, EIA, HBA
      harvest_signal.py       Signal weekly archive -> ballaster + tonne-mile ground truth
      build_master.py         (rewritten) long table, all sources
      build_geography.py      searoute-py distance matrix + basin polygons
      build_samples.py        (rewritten) calendar-day horizons, correct lag ordering
    ml/
      features/               + macro/, tonnage/, congestion/ (rewritten)
      models/                 lgbm, xgb, tft/patch-transformer, structural
      calibrate/              conformal quantile calibration, coverage tests
      units.py                index <-> USD/day calibration, validated
      walkforward.py          rolling-origin retraining harness
    tonnage/                  ---- MOAT 1 ----
      basins.py               basin partition matched to Signal's regions
      classmix.py             parcel-size soft class-weighting (not a deconvolution -- see module docstring)
      stockflow.py            anchored flow-conservation integrator (proposed as a constrained
                               Kalman filter below; shipped without one -- see Part 7/P3, no per-day
                               observation exists in free data to filter against)
      forward.py              forward availability projection (trend + widening interval, not IV-fitted)
      supplycurve.py          rate = f(tightness), direct quantile regression (IV proposed below,
                               not attempted -- see Part 7/P3: no defensible instrument identified)
      validate.py             reconstruction vs Signal ground truth (0.35x-26x, relative index only)
    impact/                   ---- MOAT 2, MARKED EXPERIMENTAL, NOT CONNECTED (P6) ----
      elasticity.py           local d(rate)/d(demand) with CIs -- built on supplycurve's
                               fitted slope and stockflow's stock_dwt, both since found
                               unfit for this use by the P3 identification gate and sign
                               diagnosis (RELATIVE not ABSOLUTE scale; wrong-signed for
                               Capesize/Handysize, regime-unstable for Panamax/Supramax --
                               see tonnage/identification.py and src/impact/__init__.py)
      execution.py            Almgren-Chriss charter execution scheduler + frontier (own
                               docstring: no historical ground truth exists to validate a
                               normative optimization framework against)
      fixedpoint.py           plan <-> forecast damped fixed-point solver
    opt/
      stopping.py             (replaces ceiling.py) LSMC optimal stopping, exercise boundary
      fleetmix.py             NEW - PS (b): vessel class & parcel configuration engine
      voyage.py               (rewritten) speed optimization, per-port bunkers, lightering
      repositioning.py        (rewritten) hazard model over tonnage field
      risk.py                 NEW - PS (d): regime + anomaly + disruption early warning
      portfolio.py            NEW - spot/TC/COA mix, the stated PS objective
      backtest.py             walk-forward, no test-set tuning, no row dropping
      api.py                  orchestration
```

---

## Part 4 — Implementation phases

### P0 — Correctness foundation (week 1–2)

Nothing downstream is trustworthy until these land.

- **`src/ml/units.py`** — fit and validate the index↔USD/day map per class on the overlapping
  `*_TCAVG` rows (279/275/185/185 rows). Test the affine fit; if unstable, use the safe path:
  forecast log-returns of the index (which the models already do) and anchor them to *today's observed
  USD/day TC average*. Ship a test asserting return-transfer error < 1% on the overlap. Backfill more
  overlap from the HandyBulk monthly archive (2016+, path documented in `raw_data/sources.md`).
- **`src/data_builders/build_geography.py`** — replace `RouteEnum` distances with a real matrix built
  from `searoute-py` (free, Eurostat marnet graph + Dijkstra, handles land-blocking correctly). Delete
  both silent fallbacks; a missing pair must raise, never return 10,000.
- **Rewrite `build_samples.py`** — calendar-day horizons resolved to the next trading day, lags
  computed before the target-null drop, contiguous-run guards.
- **Fix the backtest** — walk-forward rolling-origin evaluation; isotonic repair of crossed quantiles
  instead of dropping rows; hyperparameters tuned on `valid` only. Regenerate splits and **freeze the
  test set** with a tripwire that fails CI if it's read outside the final report.
- **Add LOA/beam/tide** to `Vessel` and `Port`; enforce all four constraints in `_vessel_can_call`.
- Delete `run_blackbox_scenario.py` and replace with scenario fixtures driven through the new API.

### P1 — Data layer (week 2–4)

| Source | What it gives | Access |
|---|---|---|
| IMF PortWatch — ~150 ports | Daily dry-bulk port calls + import/export tonnage, 2019→ | Free ArcGIS REST; pull pattern already documented in `PULL_NOTES.md` |
| IMF PortWatch — 28 chokepoints | Daily transit counts/volumes: Suez, Bab el-Mandeb, Panama, Malacca, Cape | Free, same endpoint family, currently unused |
| Signal weekly archive (~100 issues) | Per-class per-basin **ballaster counts**, tonne-mile indices, route $/day | Free on Hellenic Shipping News; the ML teammate scraped 12, we need the archive |
| World Bank Pink Sheet | Monthly coal (Newcastle 6000kc, Richards Bay), iron ore 62% Fe CFR China, crude | Free monthly PDF/XLSX |
| FRED | Brent, USD index, US IP, China activity proxies, NY Fed GSCPI | Free API |
| Indonesian HBA | Monthly government coal reference price — directly relevant to the Indonesia→EC-India lane | Free, ESDM |
| EIA | US coal export volumes and prices (covers the missing US origin) | Free API |
| IPA India | Monthly commodity-wise traffic at major ports incl. all 7 EC-India ports | Free |
| Open-Meteo Marine + archive | Wave height forecast and history, no key, back to 1979 | Free — the ML teammate already uses the forecast endpoint |
| NOAA IBTrACS | Historical cyclone tracks for Bay of Bengal seasonal risk | Free |
| UNCTAD | Annual fleet by type/DWT — mass-balance anchor for the stock-flow model | Free |

Also in this phase: dynamic `expected_wait_days` estimated per port per class from PortWatch call
density and berth counts, replacing the hardcoded literals; and the missing origins (US Gulf/Hampton
Roads, Russian Far East) added, with Gangavaram handled explicitly as folded into Visakhapatnam.

### P2 — The moat (week 4–8)

Build `src/tonnage/` then `src/impact/`, in the order listed in the architecture above. Gate: the
Tonnage Field reconstruction must correlate with the scraped Signal ballaster counts before anything
is built on top of it. If correlation is weak, we fall back to publishing the tightness index as a
feature only and drop the structural forecast — the execution scheduler still stands on the elasticity
estimate.

Milestones:
1. `classmix.py` deconvolution validated against known class-segregated ports (Port Hedland ≈ Cape).
2. `stockflow.py` producing basin stocks; mass balance closes against UNCTAD to within tolerance.
3. `validate.py` correlation plot vs Signal — **the demo's centrepiece**.
4. `supplycurve.py` quantile regression; compare structural forecast against XGB on the frozen test.
5. `elasticity.py` → `execution.py` → `fixedpoint.py`.

### P3 — PS deliverables, properly (week 6–10, parallel with P2)

- **(b) `opt/fleetmix.py`** — the missing engine. Given tonnage requirement + O/D + time window,
  enumerate feasible configurations (6×80kt Panamax vs 4×120kt Cape part-cargo vs 9×55kt Supramax vs
  Cape-to-Dhamra + coastal transshipment to Haldia), price each under the forecast, port constraints
  (LOA, beam, draft, **tidal window** — Haldia is tide-dependent, Sandheads lightering is how it
  actually works), handling rates and berth availability. Return a cost/risk/reliability frontier.
- **(a) `opt/stopping.py`** — replace the ceiling threshold with a proper optimal-stopping solve
  (Least-Squares Monte Carlo) over the forecast-implied price process, yielding an **exercise
  boundary** over (decision date × contract duration) rather than a single number. Waiting has option
  value; the current rule ignores it.
- **(c) `opt/repositioning.py`** rewrite — `P(cargo for class c at port p within N days)` from the
  tonnage field plus historical cargo-arrival intensity (hazard model), then ballast-vs-wait as
  optimal stopping. This is the first version capable of saying "sail to Singapore."
- **(d) `opt/risk.py`** — regime detection (HMM / change-point on rates), port congestion anomaly
  z-scores from PortWatch, chokepoint disruption monitor, cyclone risk from IBTrACS climatology +
  Open-Meteo forecast, and a trigger engine emitting explained "re-solve now" events. Replaces the
  string literal.
- **Objective: `opt/portfolio.py`** — the actual stated goal ("move from multiple single spot
  contracts to short/medium-term multiple-voyage contracts"). Optimize the *mix* of spot / period TC /
  COA against a demand schedule, with a stockout-risk penalty tied to plant burden cover. Not just
  "TC vs spot" for one class.
- **`opt/voyage.py`** rewrite — speed–consumption cubic curves per class (slow steaming becomes a
  decision variable), per-port bunker prices, port dues, laytime/demurrage, lightering legs. Restructure
  the CP-SAT encoding to cut the O(|V|·|C|²) transition blowup (arc-flow with time-indexed windows, or
  a route-enumeration + set-partitioning decomposition) and raise the solve budget.
- Optional if time: EU ETS / CII cost term; joint origin-sourcing arbitrage using the commodity prices
  landed in P1.

### P4 — Product (week 8–12)

- **Backend** — FastAPI per `AGENTS.md`; endpoints for forecast, tonnage field, fleet mix, plan,
  execution schedule, risk feed. Long solves run as async jobs with SSE progress.
- **Frontend** — React + TypeScript. The screens that matter:
  1. **Tonnage Field map** — basins coloured by tightness, forward slider out to 90 days. This is
     the "what *is* that?" screen.
  2. **Decision surface** — the exercise boundary as a heatmap over (start date × contract duration),
     with LOCK/WAIT regions, not a single ceiling number.
  3. **Execution frontier** — impact cost vs timing risk, draggable risk point, with the resulting
     charter ladder rendered as a Gantt.
  4. **Fleet-mix comparison** — configurations ranked, port constraints shown as the binding reason
     each rejected option failed.
  5. **Risk feed** — live triggers with their causes.
- Every recommendation carries provenance: which data, which model, which assumption, and the
  footprint score.

---

## Part 5 — Validation

The project's credibility rests on this section, not on pinball loss.

1. **Frozen test set, touched once.** CI tripwire on reads.
2. **Walk-forward rolling origin** for every model — retrain at each origin, never a single fit.
3. **Quantile coverage** — assert `share(y < p10) ≈ 0.10` and `share(y < p90) ≈ 0.90`; conformal
   calibration where it fails. the ML teammate's docs mention this check; no code does it.
4. **Decision-value backtest** with hyperparameters tuned on `valid` only, reported against
   `always_spot` / `always_lock` / `calendar_lock` / `oracle`.
5. **Tonnage Field vs Signal ballaster counts** — the reconstruction correlation.
6. **Ablation table** — decision value with and without the tonnage features, with and without the
   fixed-point correction. This is what proves the moat earns its place rather than just existing.
7. **Honest error bars on elasticity.** State plainly that global impact ≈ 0 and lane-week impact is
   12–20%. Judges reward calibrated honesty and punish overreach.

---

## Part 6 — Demo narrative

1. "Here is the problem statement's ask: forecast rates, pick ships, avoid idling, warn on risk. We do
   all four." *(30 seconds, then move on — this is table stakes.)*
2. "But every model here, including ours at first, assumed the market is something that happens *to*
   you. It isn't. It's a clearing price between tonne-mile demand and the ships that can physically
   reach the load port."
3. **Tonnage Field map.** "This is where every Capesize in the world will be, for the next 90 days,
   reconstructed from free IMF satellite data. Brokers pay for this number. Here it is against the
   commercial product." *(correlation plot)*
4. "And once you can see supply, you can see something else: **you are part of it.** When SAIL fixes
   three Panamaxes in one laycan week, that's 12–20% of the regional ballaster list. The forecast
   invalidates itself the moment you act on it."
5. **Execution frontier.** "So we don't just tell you the price. We tell you how to buy without moving
   it — borrowed from how large equity orders are executed, applied to chartering. Nobody has done
   this."
6. Land on the money: ablation table, decision value, footprint savings.

---

## Part 7 — Risks and mitigations

| Risk | Mitigation |
|---|---|
| Tonnage Field reconstruction correlates poorly with Signal | Gate at P2 milestone 3. Fall back to tightness-as-feature; execution scheduler survives on elasticity alone. |
| Class-mix deconvolution is under-identified | Anchor on known class-segregated ports; treat as a prior-constrained estimation, report uncertainty rather than a point estimate. |
| PortWatch harvest is slow or rate-limited | Resumable, checkpointed, ~400 ms delay (the ML teammate measured no limiting at that rate). Run overnight. |
| Signal archive scrape breaks or is blocked | The 12 issues already in `raw_data/` are a floor; degrade to a smaller validation set. |
| Scope is large for a student team | Strict P0→P4 gating. P0+P1+P3 alone already beat the current state and cover the PS fully; the moat is additive, not load-bearing for correctness. |
| Elasticity claim challenged by a judge | Pre-empt it in the deck with the honest global-vs-local decomposition and confidence intervals. |

---

## Immediate next step on approval

Start P0 in order: `src/ml/units.py` (unit calibration + test) → `build_geography.py` (searoute
distance matrix, delete both silent fallbacks) → `build_samples.py` rewrite → backtest methodology
fixes. Each lands with tests. Nothing in P1–P4 begins until the numbers coming out of the existing
pipeline are trustworthy.

---

## P0 progress log (updated 2026-08-24)

**Environment.** `.venv` created in `sih2026-main/` with all deps plus the project
installed editable. `uv` is still absent on this machine; run everything with
`.venv/Scripts/python.exe -m <module>`. `[tool.pytest.ini_options] pythonpath = ["src"]`
added so tests import without an install.

**Done — P0.1 units (`src/ml/units.py`, 28 tests).** The Baltic index/USD-day relation is
exactly affine and recoverable to ~$5/day, but it moves without notice: Capesize changed
three times in 20 months (slope 8.2931 -> 9.0696 in Jan 2026, plus a -$3,503 offset that
persisted to Jun 2026). The Jan-2026 change is a real Baltic methodology change,
confirmed independently by the BDI index identity breaking on exactly that date and
staying broken. The map is therefore fitted as-of a date, confined to the current regime,
with the intercept dropped unless the data demands it, single-day source glitches removed
by a leave-one-out local fit, and `is_fresh_regime` exposed when a break is too recent to
trust. Verified slopes: Panamax 9.000, Supramax 12.641, Handysize 18.00, Capesize 9.069.

**Done — P0.2 geography (`src/data_builders/build_geography.py`, `src/opt/geography.py`,
19 tests).** Distances now come from `searoute` over the Eurostat marine graph, with
coordinates pulled from the PortWatch ports database (cached to
`raw_data/portwatch/port_coords.csv`); three ports absent from PortWatch carry documented
approximations. Both silent fallbacks are gone and a missing pair raises.
*Two findings beyond the audit:*
- **The legacy African distances were roughly double the truth.** Richards Bay -> Vizag
  was entered as 8,250 nm against a great circle of 4,073 nm -- geometrically impossible.
  Corrected, that leg is 13.3 days and $244k/voyage cheaper. Across the 47 legs that
  existed, $2.08M of fuel mispricing; 58 of 105 pairs never existed at all.
- **searoute snaps nearby ports to one graph node**, returning 0 nm for Dhamra/Paradip,
  Gangavaram/Vizag and Paradip/Sandheads. Handled with a great-circle floor and a 1.25
  coastal detour factor, recorded per row in a `method` column.

**Done — P0.3 calendar horizons (`src/ml/targets.py`, 9 tests).** Confirmed and worse than
estimated: `y_step_h{h}` was exactly `shift(-h rows)`, giving median calendar gaps of
**9 / 42 / 132 days** for labels of 7 / 30 / 90. Rebuilt on true calendar horizons with
recorded, bounded resolution slip (now median 0 days). Target values moved by a median of
6% / 19% / 46.5%. Splits regenerated, models and `baseline_metrics.csv` retrained,
`docs/03_model_guide.md` table replaced. XGBoost now wins every horizon on valid.
Also fixed: targets emitted numpy NaN, which polars does not treat as null, so every
`drop_nulls` guard downstream silently passed them through as numbers.

**Audit correction.** Defect 15 ("lags computed after the target-null drop") is **not a
real defect**. Measured: `lag_1` matches the previous row for all 2,512 Capesize train
rows. The drop is tail-only because targets are forward-looking, so lags are unharmed.

**New defect found.** The embargo assert added lag *rows* to horizon *days*
(`63 + 90 = 153 <= 160`) and passed while the true calendar requirement was ~223 days
under the old targets. Splits were leaking. Embargo raised to 200 days with a real
calendar-day check (`ml.targets.required_embargo_days`).

**Done — P0.4a survivorship bias (`src/ml/quantiles.py`, 12 tests).** The audit
claim was real and measurable: LightGBM's h=90 quantile forecasts cross on 188 of
818 frozen test rows (23%) -- p10 > p50 on a confused prediction, not an edge case.
`opt.backtest._build_fan` used to treat a crossed row as "no forecast" and silently
drop it, which biased every reported metric toward the rows the model was most
confident about. Fixed with quantile rearrangement (sort the three values ascending
-- exact for three points, no retraining needed); `_build_fan` now returns
`(fan, was_repaired)` and `simulate()` logs a repair-rate summary instead of hiding
it. A true no-forecast row (nothing in `preds` for that date) is still `None`; a
row that underflows to a non-positive spread even after sorting is still dropped
(only reachable via `math.exp()` underflow on an extreme log-value -- a genuinely
broken model output, not a data gap).

**Done — P0.4b frozen test set guard (`src/ml/frozen_test.py` + repo-wide static
check `tests/test_frozen_test_guard.py`, 10 tests).** `opt.calibration.calibrate_pso`
takes an `eval_split` frame and tunes hyperparameters to maximise decision value
*on whatever it's handed* -- nothing stopped a future caller from handing it the
frozen test split (the parameter used to be named `test_split`, which pointed
straight at the mistake). `run_optimizer_backtest.py` currently didn't even call
`calibrate_pso` -- it hand-picked `risk_tol = 0.3` -- so the leak was latent, not
live, but exactly the landmine described in Part 1 defect 4. Fixed two ways:
`ml.frozen_test.load_frozen_test()` is now the only way to read `samples_test.parquet`,
and it raises unless called inside `allow_test_set_access("reason")`; a repo-wide
grep-based test fails CI if any other file calls `load_split("test")` directly
(verified live by injecting a violation file and confirming the test catches it,
before deleting the file). Both `simulate()` and `calibrate_pso()` had their
`test_split` parameter renamed to `eval_split` throughout source and tests.

**Done — P0.4c Monte Carlo hot-path fix (`src/opt/monte_carlo.py`).** Confirms audit
defect 21. `_interpolate_fan_for_day` was being called `num_simulations x
contract_term_days` times per row despite depending only on `day`, not on the
simulated path -- profiled at 90,020 calls (with a fresh `sorted()` each time) for
just 20 rows at 150 sims. Added `precompute_daily_fan()`, called once per row instead
of once per (simulation, day); `opt.api`'s entry-window scan gets the same fix.
Zero behavioural change (36 existing tests, including exact-value reproducibility
tests, pass unmodified) -- confirmed identical `decision_value` before/after on a
real benchmark row.

**Done — P0.4d wired `run_optimizer_backtest.py` into two honest phases.** Phase 1
calibrates `(theta, sigma_long, risk_tolerance)` via PSO on `valid` only. Phase 2
reads `test` exactly once through `allow_test_set_access(...)`, applies the
calibrated parameters, and prints a report labelled "FINAL TEST REPORT." PSO
settings (`n_particles=10, n_iterations=12, mc_num_simulations=80`) are sized for
a ~10-15 minute run on this machine's pure-Python Monte Carlo, not for the most
thorough possible search -- documented in the script's module docstring, with the
knobs to raise before a real submission if a longer search is wanted.

**Deferred, not forgotten.** Full walk-forward *retraining* (re-fitting the ML
models at multiple rolling origins across the test period) is out of scope for
P0 -- it belongs to the `ml/walkforward.py` rolling-origin harness in the P1 target
architecture. What P0 needed fixed was methodological leakage in decision-parameter
tuning, which is now closed; retraining cadence is a separate, larger piece of work.

**Done — P0 defect 8, LOA/beam constraints (`src/opt/types.py`, `src/opt/voyage.py`,
5 new tests).** `Vessel` had no `loa_m`/`beam_m` fields at all, and `_vessel_can_call`
checked only DWT and draft, despite `Port.max_loa_m`/`max_beam_m` being populated for
every port and the problem statement naming LOA and beam explicitly. Added both
fields (required, matching how `dwt`/`draft_m` are already required with no silent
default), added the two checks to `_vessel_can_call` in the same dwt/draft/loa/beam
order, and updated all 7 `Vessel(...)` construction sites across source and tests
with dimensionally realistic values per class (Supramax 190m/32.0m, Capesize
292m/45.0m, Handysize 180m/30.0m) -- checked against every port each test vessel
is actually routed to before picking numbers, so no existing feasibility assertion
flipped silently. New tests cover "too long/wide blocked", "exactly at limit
allowed", and "no LOA data on record does not silently block" (the origin ports
have no LOA/beam figures in PortWatch, same as they already lacked draft/DWT-only
constraints in some cases).

**Housekeeping.** Ran `ruff check` across the files touched this session; the 8
issues in newly-written modules (unsorted imports, nested-`with` style) were
auto-fixed. The 47 pre-existing issues elsewhere in the ML teammate's original code (mostly
`Optional[X]` vs `X | None`, a few missing-tzinfo datetimes) were left alone --
out of scope for P0, not something this session's changes touched.

Full suite: 203 passed.

**Results of the real calibrate-on-valid / report-on-test run.** Calibration
finished in 723s and converged to `theta=0.371, sigma_long=0.109,
risk_tolerance=0.055` -- but the convergence history was flat (`10.51 -> 10.51`
across all 12 iterations), meaning an early particle already found the optimum
and nothing beat it. Plausible rather than alarming: `risk_tolerance` near the
low edge of its range is a strong choice in a period where locking almost always
beat the realised future spot (this test window is the rate rally captured
correctly for the first time by the P0.3 calendar-horizon fix), so a corner
solution being found immediately is consistent with the data, not obviously a
search-mechanism bug (`calibrate_pso`'s own reproducibility/convergence/PSO
correctness tests all pass). Flagged rather than re-run with a longer search --
chasing a nicer-looking convergence curve isn't the goal here.

**The honest finding underneath the numbers.** On the frozen test split, the
calibrated optimizer's `decision_value` (its edge over blindly always-locking)
is essentially zero: **$0.60/day pooled, $0.00/day for Supramax** -- for
Supramax specifically the optimizer LOCKed on literally every decision point,
identical to `always_lock` row for row. Meanwhile `oracle` shows real money on
the table: **$56.90/day pooled, $18.40/day Supramax** that a hindsight-perfect
rule would have captured by selectively waiting (oracle's lock_rate is 0.7, not
1.0). The gap between "optimizer" and "oracle" here is not a bug -- every
methodology fix in this session (units, geography, calendar horizons, quantile
repair, split hygiene) is upstream of this number and all now check out. It is
a genuine modelling-capacity limit: the ceiling rule, even in MC-informed mode,
is a single point-in-time threshold and cannot express the *option value* of
waiting, so calibration collapsed toward "lock almost always" as the least-bad
blunt strategy for this market regime. This is exactly the gap Part 4 P3
already commits to closing with `opt/stopping.py` (LSMC optimal stopping,
yielding a real exercise boundary instead of one number) -- this run is the
first empirical evidence that replacement is load-bearing, not cosmetic.

Full FINAL TEST REPORT table and interpretation guide captured in the session
log; re-run via `python run_optimizer_backtest.py` (~12-15 min).

**Next:** P1 data layer -- scale the PortWatch harvest to ~150 ports + 28
chokepoints, pull the Signal weekly archive, land the macro/commodity sources.
The stopping-rule gap just found is real but is better closed after the P1
supply-side features land, since a smarter decision rule over the same thin
feature set is a smaller win than the rule change plus real signal together.

---

## Repo reconnection + reconciliation with the ML teammate's live work (2026-08-24)

**Workflow change.** This project was downloaded as a zip, not cloned -- no git
history. The user wants PRs to flow into the ML teammate's real GitHub repo
(`SqrtNegativOne/sih2026`), with collaborator access granted. Reconnected via
`git init` + `remote add` + `fetch` + `reset origin/main` (mixed reset: moves
git's bookkeeping to match his history, never touches working-directory files
-- the standard, safe way to adopt an untracked directory into existing
history). Everything up to `git push` is done; push is held pending explicit
go-ahead per this session's standing rule on shared-visibility actions.

**Real finding during reconnection: the zip predates his current `main` by a
meaningful margin.** `git status` after the reset surfaced two things that
needed careful handling before anything could be committed, not blindly
`git add -A`:
1. `tests/optimizer/` (the zip's naming) vs `tests/opt/` (his current naming)
   -- a plain directory rename he'd made since the zip, resolved by renaming
   locally to match rather than fighting it.
2. **He has already started fixing the exact cost-model issues he separately
   raised as a question** (see below) -- `idle_penalty_usd_per_day` and
   `ballast_penalty_usd_per_day` are gone from his `main`, replaced by
   `opex_usd_per_day`; `Vessel.fuel_consumption_tpd` is split into
   `laden_fuel_consumption_tpd`/`ballast_fuel_consumption_tpd`;
   `CargoParcel.demurrage_usd_per_day` exists. None of this was in the zip.

Rather than reverting his in-flight work by committing the zip's stale
versions, re-read his current `types.py`/`voyage.py`/`repositioning.py`/
`run_blackbox_scenario.py`/test files in full via `git show origin/main:<path>`,
took them as the new base, and reapplied only this session's genuinely
independent deltas on top: `loa_m`/`beam_m` fields + `_vessel_can_call` checks,
and the `opt.geography` distance-matrix swap (his `voyage.py`/`repositioning.py`
still had the old inline `RouteEnum` scan with the 10,000nm/5,000nm silent
fallbacks -- that fix was not superseded by his changes and was fully
re-applied). Verified clean via `git diff --stat` showing only the expected
small deltas per file, then 218 tests green.

**Answering his query, now grounded in his actual code (not speculation):**
- `opex_usd_per_day` replacing idle/ballast penalties: correct call, kept as-is.
  Opex is the vessel's real daily running cost (crew, insurance, stores) that
  accrues whether it's earning or not -- charging it during wait/idle/early-
  arrival time is the genuine cost of unproductive time, not an invented
  scheduling knob.
- `late_delivery_penalty_usd_per_day`: confirmed dead code before its removal.
  Verified independently by reading the *original* (pre-his-edits) `voyage.py`
  at the start of this whole session -- it was declared on `OptimizerInputs`
  and never referenced anywhere in the CP-SAT objective. Removing it was right.
- `demurrage_usd_per_day` on `CargoParcel` (not fleet-wide): right level --
  demurrage is a per-fixture contractual rate. But grepped his entire `opt/`
  tree and confirmed it was referenced nowhere -- same "declared but dead"
  pattern as the field he'd just killed. Not yet wired in.
- `demurrage_wait_days`: did not exist anywhere. This is the real gap -- real
  demurrage isn't "$X/day for any wait," it's "N free days of laytime, then
  $X/day after." Without the threshold field there's no way to express that.

**Done — wired demurrage properly (`src/opt/types.py`, `src/opt/voyage.py`,
4 new tests, on top of the merge above).** Added `demurrage_wait_days` next to
his `demurrage_usd_per_day`, both defaulting to 0.0. Charged in the CP-SAT
objective as `excess_hours * (demurrage_usd_per_day/24)` where `excess_hours`
is the port time (load + discharge, already computed as `actual_port_var`)
beyond `demurrage_wait_days * 24` -- using the epigraph relaxation (`excess >=
actual - allowed`, `excess >= 0`, appears only in the negative profit term) so
the solver pushes it to exactly `max(0, ...)` on its own, no boolean indicator
needed. Explicitly documented as a simplification: real laytime has separate
load/discharge allowances and rules about whether waiting-for-berth counts,
which this model doesn't attempt. Verified with an exact numeric assertion
(Paradip->Vizag, 50,000 dwt: 61h port time, 48h allowance -> 13h billed at a
known rate, matched to $1) rather than only a directional "cost went down"
check, plus a true-no-op test (defaults must cost *nothing*, checked via
`total_profit_usd` equality against a huge-allowance baseline).

Two local commits on `feature/p0-p1-foundation`, sitting on his `main` tip
(`15e5c03`), not yet pushed. 222 tests passing.

**Naming note:** per the user's instruction, "Ark" is retired from all
references in this document and future conversation; the teammate who built
the original backend is referred to as "the ML teammate" only.

---

## P2 progress log (the moat, built 2026-08-25)

**Scope.** Built `src/tonnage/` (Component 1, Tonnage Field) and `src/impact/`
(Component 2, Footprint) per Part 3's target architecture and Part 4's P2
milestones, then stopped as instructed -- P3/P4 are not started. 9 new modules,
84 new tests (83 pass, 1 skips honestly when its precondition -- a real
zero-activity port -- doesn't exist in this harvest), full repo suite green
throughout: 222 (P0/P1 baseline) -> 305 passed + 1 skipped (306 total) with
all of P2 landed. Every test in `tests/tonnage/`
and `tests/impact/` runs against real data already on disk (the P1 harvest, the
real Signal weekly archive, the real unit-calibrated `master_long.parquet`) --
none of it is synthetic fixtures, per the explicit instruction to test this for
real. Where a component is a normative optimization (the AC execution scheduler,
the fixed-point solver) rather than an empirical model, "real testing" instead
means proving it against known closed-form mathematical properties, plus one
end-to-end run on real elasticity numbers -- there is no historical ground truth
to check a decision rule against the way there is for a reconstruction.

**Real data reconnaissance before writing any code.** Checked what's actually on
disk rather than assuming the plan's data-sourcing hopes held up:
- Signal weekly archive: only **3 of the 15** issues report per-class per-basin
  ballaster counts at all (the rest report freight rates); those 3 cover
  different, non-overlapping (basin, class) pairs at 3 dates -- 12 real points,
  not a time series. Too sparse for a correlation coefficient; used for a direct
  magnitude comparison instead (see the honest finding below).
- UNCTAD fleet-by-class DWT: not on disk, no single clean published table found
  either. Sourced two independent real numbers instead -- total world dry-bulk
  fleet 974M dwt as of 1 Jan 2023 (UNCTAD Review of Maritime Transport) and
  per-class vessel counts as of Sept 2025 (Clarksons-sourced, via Breakwave
  Advisors) -- and derived class DWT totals from the second, cross-checked
  against the first (~1,074M vs 974M, consistent with ~2 years of real fleet
  growth). Documented as derived, not authoritative, in `stockflow.py`.
- The P1 harvest's basin/role index (`port_index_extended.csv`, 129 candidates)
  turned out to **exclude the 14 ports pulled before P1** -- which include six of
  the seven problem-statement-named EC-India discharge ports (Paradip,
  Visakhapatnam, Gopalpur, Dhamra, Haldia, Kolkata) plus key origins (Newcastle,
  Richards Bay, Beira, Nacala, Maputo, Balikpapan, Samarinda, Hay Point). Fixed
  by merging in a small real-coordinate registry (`tonnage/basins.py`,
  `_ORIGINAL_HARVEST_PORTS`) rather than re-running the P1 harvest -- the
  Tonnage Field's port universe would otherwise have been blind to the actual
  destination side of the problem statement.

**Done -- `tonnage/basins.py` (11 tests).** Loads the P1 basin/role tagging,
merged with the 14 recovered original ports (128 total, real CSVs confirmed
present for all). Basin-to-basin transit time comes from a *real port* per
basin (closest to the basin's coordinate mean, never a synthetic centroid that
could land on land), routed through the same tested `searoute` + geodesic-
fallback code P0 built for the optimizer's own distance matrix. Intra-basin
transit uses the median distance from that hub to every other port in the
basin, not the single farthest one -- the first version used farthest-port and
produced a nonsensical result (intra-Pacific transit exceeding Pacific<->Indian
Ocean transit, because "pacific" spans the entire Pacific Rim); median fixed it
cleanly, verified live (all cross-basin pairs now exceed all intra-basin ones).

**Done -- `tonnage/classmix.py` (9 tests).** PortWatch gives one real observable
per port -- mean parcel size (tonnage / calls) -- which is one equation for four
unknown class shares: not identified, no matter how it's fit. Rather than claim
an EM/deconvolution the data can't support, this computes a smooth log-DWT
Gaussian-kernel soft weight, explicitly documented as "resembles this class,"
not "is this fraction this class." Validated on real ports: Port Hedland
(world's largest iron-ore export port) resolves to 168,171 t/call, 92% Capesize
weight; Kwinana (grain) resolves to Handysize-dominant; Newcastle (genuinely
mixed coal port) does not saturate onto one class the way the single-class
ports do. Run across all 128 real ports and eyeballed by role: coal and
iron_ore roles span the *full* size range from world-scale terminals (Hedland,
Dampier, Ponta da Madeira -- all correctly Capesize-dominant) down to
draft-constrained secondary ports (Duluth on the Great Lakes -- correctly 99%
Handysize, since Great Lakes shipping is physically incapable of taking
anything larger) -- a real, physically-sensible size gradient recovered from
nothing but public call/tonnage aggregates.

**Done -- `tonnage/stockflow.py` (6 tests).** Flow-conservation reconstruction
of free tonnage per (basin, class, day): departures (export calls, class-
weighted) draw the pool down, arrivals (import calls, lagged by an assumed
port-turnaround time per class) refill it, no assumption needed about which
basin a freed ship chooses to ballast to next since flows are accounted per
basin from that basin's own port activity. **Real bug found and fixed during
development:** the first version integrated net flow as an unbounded cumulative
sum since 2019, anchored periodically to the UNCTAD total -- and drifted to
*billions* of tonnes off (vs a ~300M dwt real Capesize fleet) within the real
data. Root cause, confirmed by direct measurement: the 128-port harvest is
curated toward major *load* ports plus a smaller set of import hubs, not a
closed system, so real total export tonnage runs 43% above real total import
tonnage across the whole pull -- a genuine data-coverage gap, not a code bug.
Fixed by bounding the integration to a trailing residence window (21 days,
documented as an assumed typical re-fixing horizon) instead of the full
history -- clipped_fraction dropped from 0.235 to 0.0 on the real
reconstruction, and mass balance across the three basins now holds exactly
(verified) at every real day. Regression-tested explicitly so this doesn't
silently reappear.

**Done -- `tonnage/forward.py` (6 tests).** Forward projection is a documented
persistence/random-walk-with-drift extrapolation of the trailing 90-day real
flow trend, not a forecast model -- p50 continues the recent trend in a
straight line, p10/p90 widen as sqrt(horizon), honest about not seeing a demand
shock or a chokepoint closure coming. Verified against the real reconstruction:
day-1 projection lands within 5% of the actual last observed value, band width
grows with horizon at close to the theoretical sqrt(18) ratio between day 5 and
day 90.

**Done -- `tonnage/supplycurve.py` (8 tests).** Tightness = trailing smoothed
export flow / stock, both derived from data already reconstructed above -- no
external demand series invented. Rate side deliberately uses the real,
already-correctly-united `*_TCAVG` series directly rather than back-converting
the longer Baltic index history through `ml.units` -- that module's own
docstring explicitly warns against extrapolating its fitted map across regime
changes it can't see, so stitching a decades-long "USD/day" series for this
would have repeated a mistake P0 already fixed elsewhere. **Honest finding, not
hidden:** the direct rate~tightness quantile regression came out weak and
sign-inconsistent on levels for two of four classes (Capesize r=-0.02,
Handysize r=-0.24 -- wrong-signed; Panamax r=+0.31, Supramax r=+0.75 -- real).
First-differencing (the standard remedy for two trending series producing a
spurious level correlation) flips Capesize/Handysize positive but weak, and
*weakens* Panamax -- consistent with real short-run noise plus partial
trend-confounding from the unaddressed simultaneity problem (no IV
identification was attempted -- flagged as future work, not silently skipped),
not a code bug. Quantile coverage calibrates cleanly (~10/50/90%) for all four
classes regardless. Added a `weak_signal` flag (`|r| < 0.3`) so downstream
consumers gate on it rather than trust every class equally; this is the
plan's own Part 7 risk ("if correlation is weak, fall back to tightness-as-
feature") actually triggering, for two of four classes specifically.

**Done -- `tonnage/validate.py` (6 tests) -- the load-bearing honest finding of
P2.** Compared the reconstruction against all 12 real Signal ballaster points
(grouped to 10 basin/class/date comparisons, since Signal's regions are finer
than this reconstruction's 3-basin split). **Ratios ranged 0.35x to 26x with no
single correction factor that fixes all ten** -- ruled out basin-granularity
mismatch alone as the explanation (checked directly) and identified the real
cause: `stock_dwt` is anchored to each class's *total registered world fleet*,
not the free/ballasting fraction of it, and most of a real fleet is laden or
under period charter at any moment, not idle. A free-fraction correction would
need real data this session doesn't have (and deliberately did not fit one
from these same 12 validation points -- that would repeat the exact PSO-on-
test-set mistake P0 already found and fixed in `opt.calibration`). Consequence,
carried through explicitly rather than laundered into a clean number: the
plan's illustrative "12-20% of the regional ballaster list" demo framing does
not hold at the reconstruction's current absolute scale -- the real computed
footprint fraction for "3 Panamaxes in the Pacific" comes out around 0.1%, not
12-20%, because the denominator is fleet size, not ballaster count. `stock_dwt`
and everything built on it should be read as a *relative, within-(basin,
class) over time* signal, not a calibrated absolute headcount -- stated
prominently in both `stockflow.py`'s and `validate.py`'s module docstrings, and
regression-tested (the test suite asserts the real 0.35x-26x spread and the
`absolute_scale_validated=False` flag explicitly, so this finding can't
silently regress into a false "it validated" claim later).

**Done -- `impact/elasticity.py` (9 tests).** `d(rate)/d(demand)` by the chain
rule through tightness: `d(tightness)/d(demand) = 1/stock` is exact and
mechanical (no fitting error), `d(rate)/d(tightness)` is `supplycurve`'s fitted
slope and inherits its `weak_signal`/`low_confidence` flags rather than
presenting all four classes as equally trustworthy. `footprint_fraction`'s
docstring carries the validate.py finding forward explicitly (the real
"3 Panamaxes" number and why it reads far below the plan's illustrative 12-20%).

**Done -- `impact/execution.py` (18 tests) -- Almgren-Chriss optimal charter
execution.** A literature/web search during design turned up no prior
application of AC to freight chartering -- this transfer appears to be new.
Implements the AC00 closed-form sinh trajectory; verified against the
framework's own well-known analytical limits rather than historical data (there
is no historical ground truth for a normative decision rule): risk_aversion=0
gives the exact uniform/TWAP schedule, higher risk aversion strictly
front-loads execution, cost and variance move in opposite directions
monotonically along the frontier, zero volatility collapses every setting to
uniform. **Real bug found and fixed during development:** the first version of
the elasticity-to-AC-parameter bridge paired a day-rate elasticity (USD/day per
dwt) directly with raw dwt as AC's "shares," and priced the plan's own demo
scenario (480kt over a quarter) at **$52 billion** in permanent-impact cost --
caught by a back-of-envelope real-freight-cost sanity check, not a unit test
that happened to pass. Root cause: a day-rate is a price per ship-day, not per
dwt, so pairing it with raw dwt (hundreds of thousands) rather than an
economically meaningful "shipload count" inflates the squared term by roughly
(representative dwt) x. Fixed by normalizing through `representative_dwt`
(e.g. `CLASS_MIDPOINT_DWT[cls]`) in `impact_parameters_from_elasticity`; the
same real scenario now prices at ~$690k, the right order of magnitude for a
real freight cost. Regression-tested explicitly (`test_execution_end_to_end.py`
pins both the plausible real number and a `< 1e8` sanity ceiling).

**Done -- `impact/fixedpoint.py` (11 tests).** The plan's "optimizer's plan
perturbs local tightness -> shifts the clearing rate -> changes the optimal
plan" loop, made concrete: a schedule's own cumulative fixtures draw down the
same basin stock its elasticity is computed from, so later periods should see
higher marginal impact than the single-slope AC model assumes. Iterates
(damped, per the plan's own language) between "price a schedule at slope S" and
"what average slope does that schedule's own draw-down path actually imply,"
until self-consistent. Verified on real data: effective slope is always >= the
naive local slope (mechanically guaranteed, since drawing down stock can only
raise `1/stock`), a larger demand relative to real basin stock produces a
larger correction (0.1% at 228kt, 1.9% at 5,000kt against real ~230M dwt Pacific
Panamax stock), different damping values converge to the same fixed point at
different speeds (0.02% apart between damping=0.3 and damping=1.0), and -- kept
as an honest edge case rather than tuned away -- a large demand with weak
damping and a tight iteration budget genuinely fails to converge within 10
passes on this real data, reported as `converged=False` rather than silently
accepted.

**Housekeeping.** `pyproject.toml`'s hatch wheel packages list extended to
include `src/tonnage` and `src/impact`. `ruff check` clean across every new
file (14 issues surfaced, all fixed -- import sorting, a genuine leftover dead
variable in `stockflow.py`, minor style). Full repo suite: 305 passed, 1
skipped (the classmix zero-activity-port test, which honestly skips because
every real harvested port happens to have nonzero dry-bulk activity).

**What P2 deliberately does not claim.** No IV/instrumental-variables
identification for the supply curve (flagged, not attempted). No true Kalman
filter in `stockflow.py` (no per-day stock observation exists in free data to
filter against -- implemented and documented as the honest anchored-integrator
equivalent instead). No validated absolute ballaster headcount (0.35x-26x
spread against real Signal data, reported not hidden). No claim that Almgren-
Chriss transfers to chartering with textbook rigor -- the elasticity-to-impact-
parameter bridge is a stated modeling choice with two explicit assumptions
(charter duration, temporary/permanent split), not a derived identity.

**Next, not started per instruction:** P3 (fleetmix.py, stopping.py,
repositioning.py rewrite, risk.py, portfolio.py, voyage.py speed/bunkers
rewrite) and P4 (backend/frontend). Per the plan's own P4 architecture, P3's
`repositioning.py` rewrite and `risk.py` are the natural first consumers of
`tonnage`'s basin-level stock/tightness output; P4's dashboard "Tonnage Field
map" and "execution frontier" screens are the natural consumers of
`forward.py` and `impact.execution`'s frontier respectively.

---

## P3 progress log (PS deliverables, properly -- built 2026-08-25/26)

**Scope and approach.** Built all six named P3 items -- `fleetmix.py`,
`risk.py`, `stopping.py`, `repositioning.py` rewrite, `portfolio.py`, and a
scoped `voyage.py` fix -- on `feature/p2-tonnage-field` (no new branch, per
instruction). Given the explicit instruction to be "extremely careful" and
avoid "late-game errors causing huge breaks," every new/rewritten piece landed
with real tests and a full-suite run *before* moving to the next, exactly the
P0 rhythm, rather than one large batch verified only at the end. Two
deliberate, conservative scope decisions were made and are documented in the
code itself, not just here: `opt.stopping` is wired in as an *additive*
signal, not a replacement of the already-validated `opt.ceiling` lock/wait
decision path; and `opt.voyage`'s fuller rewrite (speed as a decision
variable, CP-SAT arc-flow restructuring for scale) was scoped *out* as
separate future work, in favour of only the safe, real, additive part (real
per-port bunker prices) -- both explained below.

**Done -- `opt/fleetmix.py` (PS b, previously missing entirely, 11 tests).**
Enumerates one configuration per vessel class for a tonnage requirement + O/D
pair, prices each via the real forecast (reusing `opt.ceiling.compute_ceiling`),
filters by real port LOA/beam/draft/DWT limits (reusing `opt.voyage`'s own
`_vessel_can_call`), and offers a hub-and-lighter transshipment fallback for
classes that can't call the destination directly -- the plan's own named
example (Capesize -> Dhamra -> lighter to Haldia) implemented literally, not
just referenced. **Two real modeling errors found and fixed by testing
against real port data, not assumed correct:** a Supramax reference beam
borrowed from the Panamax canal-lock figure (32.26m) missed Paradip's real
recorded limit (32.2m) by 6cm; a Capesize reference draft (18.0m) exceeded
even Dhamra's real recorded limit (17.0m, the deepest EC-India transshipment
hub in the data), which made the transshipment fallback itself infeasible.
Both fixed to genuinely representative, still-real values.

**Done -- `opt/risk.py` (PS d, previously a hardcoded string literal, 16
tests).** Four real early-warning signals: rate-regime anomaly (z-score on the
real Baltic index's own return distribution), port congestion and chokepoint
disruption (both z-scores on real IMF PortWatch data), cyclone-season
climatology (reusing the same Oct-Dec Bay of Bengal window the ML pipeline's
own calendar features already encode). **Real data-quality bug found and
fixed:** `BC_INDEX` carries 44 real rows with impossible negative values in
early 2020 (the Capesize market crash was real; a negative index reading isn't),
silently poisoning `log()` before being filtered out. **A live real anomaly
was caught while testing** (Malacca Strait dry-bulk transits dropped to a
real z~-2.2 against their own 60-day baseline as of 2026-08-16) and is pinned
as a regression test rather than discarded. Wired into `opt.api.run_optimizer`
as the real `review_trigger`, replacing "BDI jumps >5%" outright.

**Done -- `opt/stopping.py` (PS a, additive, 14 tests).** LSMC
(Longstaff-Schwartz) optimal stopping: prices the option value of waiting,
which the existing ceiling threshold ignores entirely, via backward induction
on a piecewise log-normal process calibrated exactly to the real forecast
fan's own p10/p50/p90 at its own horizons (verified: simulated quantiles
reproduce the real fan within a few percent). **Real numerical-stability bug
found and fixed:** the closed-form exercise-boundary solve divided by a
near-zero denominator whenever the fitted regression slope was close to -1,
producing boundary values as extreme as $246,664/day on a $16,000/day
process. Fixed with a robust crossing-search fallback that cannot blow up by
construction. Validated against real mathematical properties (the optimal
value must dominate any fixed non-adaptive rule -- checked directly against
"always lock day 1" and "always wait to maturity" on the same simulated
paths) rather than hardcoded numbers, since there is no historical ground
truth for an optimal-stopping algorithm the way there is for a forecast.
**Deliberately not swapped in as the production lock/wait decision** -- the
plan's architecture diagram calls this "replaces ceiling.py," but
`lock_action`/`ceiling_usd_per_day` are directly, exactly regression-tested
in `test_api.py` and had just been independently verified end-to-end against
real trained models; swapping the validated decision path in the same pass
that builds its replacement was judged the single highest-risk move available
here and deliberately not taken. Exposed as new `stopping_result` field
instead, computed alongside the unchanged original.

**Done -- `opt/repositioning.py` rewrite (PS c, the audit's original defect
3, 6 tests).** The original engine hardcoded `basis_mean = 0.0`, so expected
TCE was identical at every candidate port -- structurally incapable of ever
preferring one real port over another, confirmed still true and unfixed
immediately before this rewrite (P0 only fixed the distance/ballast-cost half
of this defect). Now uses a real Poisson hazard rate -- P(cargo within N days)
= 1 - exp(-lambda*N) -- with lambda estimated from real, trailing-window
PortWatch export tonnage at that *specific port*, class-attributed via the P2
tonnage field's own classmix weights. **Real design bug found and fixed
before real testing caught it as a *usefulness* problem, not a crash:** the
first version used `tonnage.stockflow`'s already basin-aggregated flows
directly, and every basin's rate was high enough that P(cargo within 30 days)
saturated to ~1.0 everywhere -- true but useless for choosing between two
ports in the same basin. Rebuilt at the per-port level; real result now
correctly distinguishes e.g. Capesize at Newcastle (P~0.79, a major real coal
port) from Capesize at Haldia (P~0.0000, genuinely too shallow for the
class) -- the first version of this engine actually capable of the PS's own
example ("capable of saying 'sail to Singapore'"), modulo Singapore itself
having no real PortWatch coverage in this harvest (honestly flagged via
`probability_is_real_data=False`, not silently defaulted). Hazard rates
cached at process scope (`functools.lru_cache`) since building them needs
real per-port file I/O and `opt.api` calls this once per idle vessel.

**Done -- `opt/portfolio.py` (the PS's actual stated objective -- "move from
multiple single spot contracts to short/medium-term multiple-voyage
contracts," 16 tests).** Optimizes the mix of spot / period-TC / COA coverage
against a real forecast-driven cost and variance per channel, plus a real
Poisson hazard-model stockout penalty (reusing the same framework as the
repositioning rewrite, applied to vessel *sourcing* speed instead of cargo
availability). **Deliberately not auto-wired into `run_optimizer`,** unlike
the other three additions -- unlike those, its key parameters
(`plant_burden_cover_days`, `stockout_cost_usd`, real sourcing speed) are
genuine SAIL business facts with no honest default derivable from public
data; giving them a silent default would produce a recommendation that reads
as real advice while reflecting an arbitrary made-up number. Exposed as a
separate, explicit, opt-in function (`opt.api.run_portfolio_analysis`)
instead. **Solved via grid search over the coverage simplex, not a closed-form
optimizer** -- a deliberate lesson carried over from the `stopping.py` bug
above: a well-behaved, bounded 3-variable objective is cheap to search
exhaustively at fine resolution, which is robust by construction rather than
merely well-tested. **A real, non-obvious three-way interaction found during
testing:** because variance is quadratic in the coverage weights, blending
spot into a COA-heavy mix can produce *lower* total variance than pure COA
alone (textbook diversification) -- meaning raw expected cost is genuinely
non-monotonic in risk aversion here (unlike the simpler two-channel P2
Almgren-Chriss frontier). Confirmed this is a real property of the model, not
a bug, by hand-verifying the objective at the specific points in question;
the test suite pins the property rather than asserting a false monotonicity.

**Done -- `opt/voyage.py` scoped fix (real per-port bunker prices, audit
defect 19, 2 tests).** `Port.bunker_price_usd` was defined with a real,
distinct value for every one of the 15 ports (Paradip $610/t through
Singapore $540/t) and never read anywhere -- fuel cost used one blended
global constant regardless of where the vessel actually bunkers. Fixed at
all three fuel-cost sites (laden leg, initial ballast, inter-cargo ballast)
using the port each leg *departs from*, a real, consistent, documented
convention. Confirmed live: two otherwise-identical voyages differing only in
ballast-origin bunker price now show strictly different profit. **The fuller
P3 ask for this file -- speed as a CP-SAT decision variable, arc-flow
restructuring for scale -- was deliberately scoped out**, documented in the
module's own docstring: this is the single most heavily-tested, most central
module in the optimizer (it's what the live demo's voyage schedule actually
runs on), and the explicit instruction for this pass was to avoid exactly the
kind of risk that rewrite carries. Flagged as real, separate future work, not
silently dropped.

**Integration summary.** `fleet_mix`, `risk_assessment`, and `stopping_result`
are new optional fields on `OptimizerRecommendation`, computed automatically
inside `run_optimizer` alongside the existing lock/wait + voyage schedule
(which are unchanged); all three degrade to `None` gracefully if the P1/P2
data they need isn't present on disk, so the core recommendation never breaks
because an enrichment layer couldn't compute. `review_trigger` is now real
(driven by `risk_assessment`) rather than the old hardcoded string.
`portfolio.py` is available but not automatic, for the reasons above.
`FleetConfiguration`/`FleetMixFrontier`/`RiskAlert`/`RiskAssessment`/
`StoppingResult`/`PortfolioMix` all moved into `opt/types.py` (matching the
existing `VoyageAssignment`/`RepositioningAction` pattern) specifically to
avoid a circular import between `opt.types` and the modules that produce
these -- the same move already made for the P2 tonnage/impact types.

**Testing discipline.** Every new numerical method here was validated the
same way P2's Almgren-Chriss execution scheduler was: against real,
checkable mathematical properties (optimal-stopping dominance, hazard-model
monotonicity in the search window, portfolio variance bounds) rather than
hardcoded expected outputs, since none of these are forecasts with a
historical answer to check against -- they're decision rules, and the
correctness bar for a decision rule is "does it satisfy the properties an
optimal one must have." Three real, substantive bugs were found and fixed
this way before they could reach a demo: the LSMC boundary blow-up, the
basin-vs-port hazard-rate granularity mistake, and the fleetmix port-spec
near-misses -- none would have been caught by a test that only checked "does
it run without crashing."

**Full suite: 371 passed, 2 skipped at the end of P3 (up from 305 passed + 1
skipped at the end of P2 -- +66 tests across all six pieces), green
throughout with no regressions at any checkpoint.** (See the session's own
commit history for the exact count after each individual piece landed --
checkpointed deliberately, not just verified once at the end, per the "no
huge breaks" instruction; intermediate checkpoints were 348 passed after
fleetmix+risk+stopping, 353 after the repositioning.py rewrite, then 371 with
portfolio.py and the voyage.py bunker-price fix on top.)

**What P3 deliberately does not claim.** No claim that `opt.stopping`'s LSMC
exercise boundary is production-ready to replace the simple ceiling rule --
it's real, tested, and additive, with retirement of the simpler rule flagged
as explicit future work once it has more runway to be checked against real
decisions. No claim that repositioning's hazard-rate coverage is complete --
4 of 15 `PortEnum` members (including Singapore, the plan's own named example)
have no real PortWatch coverage in this harvest and get an honestly-flagged
neutral default. No claim that `portfolio.py`'s COA cost/variance parameters
are fitted to anything -- documented assumptions, stated as such, overridable
by a caller with real figures. No claim that `voyage.py`'s fuller rewrite
(speed optimization, CP-SAT scale restructuring) is done -- explicitly scoped
out as separate future work given its risk profile.

**Next, not started:** P4 (backend/frontend product layer) and the two
explicitly-deferred pieces above (retiring `opt.ceiling` in favour of
`opt.stopping` once validated further; the full `opt.voyage` speed/scale
rewrite).

---

## Post-P3 feature fixes (user-requested, going toward P4 -- 2026-08-25)

After P3 landed, a plain-language walkthrough of all 14 features surfaced
real product-design gaps in several of them. The user is working through
these one at a time before P4 starts; each lands with its own tests and a
full-suite run, same discipline as P0-P3.

**Done -- LOCK/WAIT no longer takes a vessel class as input (the audit's own
gap: "origin/dest port seems to be missing").** Previously `run_optimizer`
required a caller-supplied `target_class: VesselClass` and derived route
basis only opportunistically from `inputs.parcels[0]` if present -- a
charterer would have had to already know which class they wanted priced,
and route-awareness was accidental, not guaranteed. Now the real inputs
(cargo tonnage, origin, destination, contract length, today's quote) drive
everything:
- `opt.ceiling.select_vessel_class_for_cargo(cargo_volume_dwt)` -- the
  smallest class whose representative capacity (`opt.types.CLASS_REFERENCE_DWT`,
  matching `opt.fleetmix`'s own per-class figures) covers the lot, clamped to
  Capesize above that.
- `opt.network.route_family_for_origin(origin_port)` -- derives the same
  `RouteFamily` label a caller previously had to already know and set
  manually on `CargoParcel`, from the origin port alone (every family here is
  EC-India-anchored; an EC-India origin means a coastal `INTRA_EC_INDIA` move).
- `opt.ceiling.lock_or_wait_for_cargo(...)` -- the new entry point, taking
  exactly those five real inputs plus the forecast/basis data a charterer
  would never type by hand. It derives class + route basis and calls the
  original, unchanged `lock_or_wait` underneath, so all of that function's
  existing tested math is untouched -- this is a wrapper, not a rewrite of
  the pricing logic itself.
- `run_optimizer(inputs, as_of=None)` -- `target_class` is gone from the
  signature entirely. The class is derived once, from the first parcel's
  tonnage, and reused consistently everywhere it was already implicitly
  assumed to be shared (Monte Carlo savings, risk assessment, the LSMC
  exercise boundary) -- these were always meant to describe the same
  decision as lock/wait, so deriving the class in one place instead of
  trusting the caller to pass a consistent one is a correctness fix, not
  scope creep. `run_optimizer` now raises a clear `ValueError` if called with
  no parcels, instead of silently losing route-awareness.
- `LockWaitResult` and `OptimizerRecommendation` both gained `origin_port`,
  `dest_port`, `cargo_volume_dwt` fields (optional on the former, since
  `opt.backtest`'s historical class-level replay still calls the original
  `lock_or_wait` directly and has no single cargo lot to attach); the
  route now shows up in `format_recommendation_text`'s output too, not just
  internally.
- `compute_ceiling`, `lock_or_wait`, and `opt.backtest`'s use of them are
  completely unchanged -- `opt.fleetmix`, `opt.stopping`, `opt.repositioning`,
  and `opt.portfolio` all call `compute_ceiling` directly with a class they
  genuinely need to iterate over or already know from context, which is a
  different (correct) use case from the top-level lock/wait decision.
  `run_portfolio_analysis` (feature 8, explicitly not in scope for this
  round) still takes `target_class` unchanged.

Verified both real demo scripts (`run_live_scenario.py`, `run_blackbox_scenario.py`)
derive the exact same class their hardcoded `target_class` used to specify,
from the cargo tonnage each scenario already had -- this fix is behaviour-
preserving for every existing realistic scenario, and only changes what
happens when origin/route actually matters. New tests: `tests/opt/test_network.py`
(route-family derivation, all 15 real ports) and additions to
`tests/opt/test_ceiling.py` (class derivation at every real class boundary;
`lock_or_wait_for_cargo` cross-checked to reproduce `lock_or_wait` exactly
given the same derived inputs; a hand-verified case where two different real
origins for the same cargo/quote flip LOCK to WAIT purely from the route
basis -- the concrete defect this fix closes).

**Done -- LSMC exercise boundary fused into the production LOCK/WAIT decision
(feature 7 into feature 1), after finding and fixing three real defects by
testing it properly first.** `opt.stopping` was P3-additive only (computed
but never used for `lock_action`/`ceiling_usd_per_day`); asked to test it
properly and fuse if ready. Three real bugs surfaced, none reachable by any
pre-existing test (all of which only ever used risk_tolerance=0/basis=None):
1. The simulated price process was calibrated against the raw class-level
   forecast while the strike it was priced against was basis-adjusted --
   fixed via `opt.ceiling.route_adjusted_fans`, applied before calibration.
2. `risk_tolerance` was silently dropped (`compute_ceiling` always called
   with 0.0 inside `solve_exercise_boundary`) -- threaded through properly;
   `expected_spot_blended` replaces `expected_spot_p50` as the strike
   (identical at risk_tolerance=0, so no regression).
3. The reported boundary was wildly unstable day-to-day and seed-to-seed
   (day-1 boundary swung ~$4,000-$18,000/day for identical real inputs) --
   root cause: the per-day linear continuation-value regression is a
   genuinely weak fit far from maturity (the linear-basis simplification's
   known cost), so OLS's slope has real sampling variance, occasionally
   landing the closed-form solve's denominator near zero (the exact P3-era
   instability, just not previously reachable). A ridge-regularized *second*
   fit is now used only to derive the reported boundary; tried ridge-
   regularizing the actual policy fit first and it broke a dominance test
   (systematically triggered premature exercise) -- reverted, kept the
   unbiased OLS driving `should_exercise`/`cash_flow`/`option_value`
   untouched, added the ridge fit as report-only. `_boundary_by_crossing`
   also generalized to return the data-bounded extremal value instead of
   `None` on no-sign-change (mathematically: exercise/continuation are exact
   linear functions of price here, so "no crossing in range" means the true
   root is outside the range the paths actually populated, not that there
   isn't one).

`opt.stopping.solve_lock_or_wait` is the new fused entry point: calls
`opt.ceiling.lock_or_wait_for_cargo` for the base decision (unchanged), swaps
in the exercise boundary's day-0 value as the operative ceiling/action when
the LSMC solve has enough data, falls back to the plain rule otherwise.
`opt.ceiling.compute_ceiling`/`lock_or_wait` themselves are completely
untouched -- fleetmix/repositioning/portfolio still call `compute_ceiling`
directly for their own needs. `run_optimizer` no longer computes
`stopping_result` as a separate, later step; it comes from the same fused
call. 46 new/changed tests across `test_ceiling.py`/`test_stopping.py`,
including exact hand-verified regression tests for all three bugs (a
basis-scaling identity, a risk-tolerance-vs-strike check, and a bounded-jump
+ cross-seed-stability check pinned to the specific regime that broke).

**Done -- explainability layer, extended from opt.risk's existing pattern to
every other ML/decision output ("we don't want this to act as a simple
blackbox model").** New `opt/explain.py`: deterministic, template-based,
real-numbers-only (never a separately-generated narrative -- nothing here
computes a new number, it only narrates values the rest of `opt` already
produced). New `OptimizerRecommendation.explanations` field
(`RecommendationExplanations`: one `Explanation` -- summary, factors, method
-- per lock/wait, each voyage assignment, each repositioning call, savings,
and fleet-mix). `format_recommendation_text` now prints a "Why/Method" block
under each section. Real finding while building this: several of these
explanations already existed as real, computed values one layer down and
were being discarded before reaching the caller -- `opt.repositioning`'s
`RepositionOption` already carried the real hazard probability and both
ports' scores, `opt.monte_carlo`'s `SavingsDistribution` already carried
`prob_positive_savings`/`best_case_p90_savings`, `opt.fleetmix`'s rejected
configurations already carried their real `infeasible_reason`s -- all were
computed, then dropped before construction. Fixed by adding real fields
(`RepositioningAction` +4, `OptimizerRecommendation` +2 savings fields,
`FleetMixFrontier` +`rejected_configurations`, all backward compatible or
the only construction site updated) rather than re-deriving anything.
15 new tests in `test_explain.py`, each asserting the explanation actually
contains the exact real number it claims to explain, not just that
something was returned.

**Full suite: 439 passed, 2 skipped** (up from 405 at the end of the LOCK/WAIT
cargo-input fix), zero regressions, checkpointed after each of the two
changes independently. Both real demo scripts re-verified end to end on real
data after each change.

**PS sanity check (2026-08-25), against `docs/00_og_problem_statement.md` /
`docs/01_problem_statement.md`), requested by the user.** Confirmed solid:
all 4 origin regions (AU/US/MZ/ID; Russia deliberately dropped, documented
already), all 7 named EC-India destination ports, all 4 vessel classes,
seasonal features (`ml/features/calendar.py` -- monsoon, cyclone season,
Indian fiscal quarter, Chinese New Year window), historical rates by
class x route (now genuinely route-adjusted end to end per the earlier fix
this round), and the "cargo + O/D + duration in, comprehensive forecast +
recommendations out" framing the PS explicitly asks for. Three real gaps
found and reported to the user rather than silently patched under time
pressure:
1. **Macro/commodity indicators** ("global economic indicators, commodity
   price trends") -- confirmed genuinely absent (no coal/iron-ore/Brent/FX
   series anywhere in `src/ml`), despite being a named P1 data-layer goal.
   A real, multi-step gap (source data, engineer features, retrain,
   re-validate) -- flagged as a big hole, not attempted this round.
2. **Port congestion feeds a static literal, not real data** -- confirmed
   `expected_wait_days` is only ever the hardcoded per-port `PortEnum`
   value; `opt.risk`'s real, dynamic PortWatch-based congestion z-score
   exists but was never connected to it. More tractable than (1) (no new
   data needed) but touches `opt.voyage`, this project's most
   heavily-guarded module -- flagged, not attempted this round.
3. **Origin ports are missing LOA/beam data** -- confirmed only the 7
   EC-India ports have `max_loa_m`/`max_beam_m` populated; all 8 foreign
   origin ports (Newcastle/Gladstone/Richards_Bay/Beira/Muara_Pantai/
   Balikpapan/Hampton_Roads/Singapore) have `None` for both, so
   `_vessel_can_call`'s LOA/beam check (added in P0) is currently vacuous
   for every real loading port -- a direct, named PS ask ("similar data for
   the loading ports in Australia, the US, Mozambique, and Indonesia").
   Zero code-logic risk to fix (the check already handles populated data
   correctly; this is pure missing data), but needs real, verified port-
   authority figures rather than guessed ones -- flagged rather than
   fabricated under time pressure, offered as a focused next task.

**Done (2026-08-26) -- gap 3 closed: real LOA/beam data sourced and added for
all 8 origin ports.** Asked to pick the easiest of the three gaps and
integrate it; picked this one -- zero logic risk (the check already existed
and already handles populated data correctly; the earlier `None` was the
only actual gap), unlike gap 2 (touches `opt.voyage`) or gap 1 (needs new
data + retraining). Sourced real, cited figures per port (port authority /
terminal operator specifications, via web search): Newcastle ("Newcastlemax"
class, 300m/50m), Gladstone (port max, 315m/55m), Richards Bay (RBCT
Capesize berths 301-306 + published beam tolerance, 350m/47.5m), Beira
(published port max, 200m/34m, draft matches the existing 8.0m exactly),
Muara_Pantai (real recorded max at this open-water anchorage, 289m; beam
inferred at 45.0m -- standard Capesize beam, since no published figure
exists, called out as inferred rather than sourced), Balikpapan (coal
terminal's own spec, 250m/43m), Hampton_Roads (beam from Lamberts Point's
published spec, 53.3m; LOA inferred at 290.0m -- standard Capesize, matching
Dominion Terminal's published 178,000 dwt capacity), Singapore (no single
published port-wide figure for this multi-terminal hub; sized generously
above Capesize, 340m/60m, consistent with how the port's own existing
300,000 dwt / 20.0m draft are already modeled as effectively unconstrained).
Each port's code comment states plainly which figures are directly sourced
vs. inferred from a sourced companion figure.

**Real, deliberate scope boundary.** While sourcing this, several existing
draft/dwt values looked conservative next to what was found (Newcastle's
existing 85,000 dwt cap vs. a real ~232,000 dwt "Newcastlemax" capacity;
Muara_Pantai's existing 80,000 dwt vs. a real recorded 176,944 dwt at that
anchorage) -- left untouched. Changing an *existing* value carries real risk
(other logic/tests may already depend on it); adding a previously-absent
field does not, since `_vessel_can_call` already treated `None` as
"unconstrained" by design. Flagged here as a real, separate, unverified
discrepancy for later -- not silently fixed under a different task's cover,
and not blocking this one either.

**Verified safe before and after landing.** Checked analytically first: DWT
already excludes Capesize (dwt=180,000) at all four smaller newly-fixed
ports (Beira 30k, Muara_Pantai 80k, Balikpapan 75k, Hampton_Roads 100k caps),
so the new LOA/beam data was very unlikely to flip any *existing* feasibility
conclusion -- confirmed live (`enumerate_fleet_mix` at Balikpapan: Capesize
was already rejected on DWT before LOA/beam is even reached). Full suite
caught exactly the one place this assumption was checkable and wrong:
`test_voyage.py::test_no_loa_limit_at_origin_ports_allows_any_length`
asserted the *old* gap directly (a 999m-LOA vessel must be allowed at
Richards Bay because "origin ports have no LOA/beam on record") -- true
before this fix, false after. Replaced with two tests pinning the new
reality: a 999m vessel is now genuinely rejected there (real LOA cited in
the reason string), and a realistically-sized Supramax still clears
Richards Bay's real limits comfortably. Both real demo scripts re-verified
unchanged end to end (all real vessels/cargo in both scenarios sit
comfortably inside every new limit). **Full suite: 440 passed, 2 skipped**
(439 + 1 net: the removed test replaced by two new ones).

**Done (2026-08-26) -- gap 2 closed: dynamic, real-data-driven port
congestion now feeds `opt.voyage`/`opt.repositioning`, replacing the static
literal they were reading before.** New `opt/congestion.py`:
`dynamic_wait_days(port) -> (value, is_real_data)` scales the port's
existing static `expected_wait_days` baseline by how much busier its real,
recent IMF PortWatch daily dry-bulk call count is versus its own longer-run
average (14-day recent window vs. a 60-day baseline ending right before it,
matching `opt.risk`'s own congestion-window convention) -- a genuinely
different question than `opt.risk.port_congestion_alert`'s z-score ("is
this unusual" vs. "how many days will a ship actually wait"), reusing the
exact same real data. Clamped to a 0.5x-3.0x multiplier so a handful of
noisy recent days can't produce an implausible number; falls back to the
unmodified static baseline wherever real data doesn't support a dynamic
estimate, and can never raise (feeds `opt.voyage`'s CP-SAT objective
directly). Verified live across all 15 real ports before wiring anything in:
11 get a real dynamic adjustment, 4 correctly fall back (no tonnage-field
coverage), every adjustment is a small, sane deviation from its baseline
(e.g. Newcastle 5.00 -> 4.64 days, Beira 3.00 -> 4.13) -- no clamping even
hit on real, current data.

**Two call sites swapped, both one-line changes, matching the plan's own
"the load-bearing cost logic doesn't change, only the number that feeds it
does" scoping.** `opt.voyage`'s origin-port queue-wait cost term
(`c.origin_port.value.expected_wait_days` -> `dynamic_wait_days(c.origin_port)`)
and `opt.repositioning`'s candidate-port wait cost
(`port.value.expected_wait_days` -> `dynamic_wait_days(port)`) -- the CP-SAT
variable/constraint structure in `opt.voyage` is completely untouched, only
the Python-level scalar feeding into it changed. Destination-side congestion
still isn't modeled as a separate cost anywhere (neither was it before this
change) -- a real, separate, pre-existing scope boundary, not something this
fix silently claims to close.

**A real, non-obvious circular-import trap found and fixed while wiring
this in.** `opt.congestion` needs `opt.repositioning`'s real
PortEnum-to-tonnage-label mapping (the same one `cargo_probability_within_window`
already uses) to know which real CSV backs which port; `opt.repositioning`
needs `opt.congestion`'s `dynamic_wait_days` for its own wait cost -- a
direct cycle if the mapping stayed where it was. Fixed by moving
`PORT_TO_TONNAGE_LABEL` (renamed from `_PORT_TO_TONNAGE_LABEL`, now genuinely
shared, not module-private) to `opt.network` -- the same dependency-free
reference-data module `route_family_for_origin`/`ORIGIN_PORT_ROUTE_FAMILY`
already live in, for the identical reason. Verified with a direct import
smoke test before running anything else.

**Verified safe before and after landing, same discipline as gap 3.**
Checked that `opt.repositioning.recommend_repositioning`'s own
`_real_wait_days` reader can't raise `PortIndexMissingError` from
`tonnage.basins.port_csv_path` (it's a pure path-join, never raises that --
confirmed by reading the source rather than copying `opt.risk`'s own
apparently-defensive-but-unreachable guard against it) before deciding what
`dynamic_wait_days`'s own try/except needed to actually catch. Full suite
caught exactly one place a hardcoded value depended on the old static
number: `test_repositioning_logic` asserted an exact `wait_cost_usd` derived
from Paradip's static 3.5-day baseline (and, pre-existing and unrelated to
this fix, had stale comments describing a basis adjustment the code never
actually applies) -- fixed by deriving the expected cost from
`dynamic_wait_days` itself inside the test, so it stays correct as real
PortWatch data updates rather than needing a new hardcoded number today that
would just go stale again later. Both real demo scripts re-verified end to
end (profit changed by low hundreds of dollars, consistent with a small
real congestion adjustment, not a break). **Full suite: 453 passed, 2
skipped** (440 + 13 new tests in `test_congestion.py`, zero regressions).

**What's left of the three PS gaps.** Gaps 2 and 3 are both done. Gap 1
(macro/commodity indicators absent from the ML features) remains open --
needs real data sourcing plus model retraining and re-validation, out of
scope for a single pass; not attempted.

---

## Final lap: quote() front door + FastAPI backend (2026-08-26)

**Scope.** Everything up to this point was real computation with no easy
way to call it -- a caller needed to hand-assemble an `OptimizerInputs`
(real forecast fans, real per-class TC quotes, a built `CargoParcel`, a
fleet of `Vessel`s), ~150 lines of setup per `run_live_scenario.py`'s own
scenario builders. Per the user's explicit final-lap instruction ("make
this callable... no new modeling"), this pass built exactly the callable
surface: a real `quote()` front door, a thin FastAPI `backend/`, and $/MT +
confidence% + congestion-label output polish -- deliberately excluding
macro/commodity retraining (gap 1 above), left out per instruction. Landed
in dependency order, full suite re-run after each piece, same discipline as
every prior phase.

**Done -- `ml/live_forecast.py` (NEW, promoted from `run_live_scenario.py`,
9 new tests).** `run_live_scenario.py`'s `_build_feature_table`/
`forecast_all_classes` were the one place in this codebase that actually
turns real Baltic/PortWatch history on disk into a real forecast via the
trained XGBoost models -- proven correct by that demo, but private,
copy-pastable script functions, not an importable module. Moved verbatim
(zero behaviour change -- both real demo scripts re-verified end to end,
byte-identical recommendation text before/after), parameterized by
`master_path` for testability, with a new `latest_available_date()` helper
(the same "use the most recent real date on disk as 'today'" pattern every
demo script already used, now real and reusable). `run_live_scenario.py`
now imports from here instead of carrying its own copy.

**Done -- `opt/quote.py` (NEW, the front-door itself, 12 new tests).**
`quote(cargo_volume_dwt, origin_port, dest_port, laycan_start, laycan_end,
contract_term_days=30, ...)` -- cargo/route/laycan/contract-type in, one
`QuoteResult` out, no `OptimizerInputs` assembly required. Deliberately a
thin wrapper, not a new decision engine: loads real forecasts/quotes via
`ml.live_forecast`, builds the smallest honest `OptimizerInputs` (one
parcel; no vessel unless the caller supplies one -- confirmed
`opt.voyage.schedule_voyages` already degrades gracefully to "no
assignments" on an empty fleet, so lock/wait + fleet-mix + risk run exactly
as they do today), and calls the already-tested `opt.api.run_optimizer`
unchanged. **Real design finding, not a bug:** an optional `vessels` param
was added for a caller who does have a specific ship in hand, but the first
version silently priced that vessel's cargo at `revenue_usd=0.0` -- a real
CP-SAT-fed vessel with real port clearance still never got assigned,
because zero revenue is never profitable. Caught by a test asserting a
known-feasible vessel (identical to `run_live_scenario.py` scenario_a)
should get scheduled. Fixed by adding an explicit optional `revenue_usd`
param (revenue is a real business fact -- what SAIL actually captures from
this cargo -- that no market forecast can supply, same reasoning as
`opt.portfolio`'s `stockout_cost_usd`); the honest default stays 0.0
(never fabricates a profit to force an assignment), and a second test now
pins that *without* a real revenue figure the vessel correctly shows up
idle with a real repositioning recommendation instead of a fabricated
assignment.

**Done -- `opt/present.py` (NEW, output polish, 17 new tests).** Three
pure, real-numbers-only conversions, none of which compute a new decision:
- `usd_per_day_to_usd_per_mt` -- a TC $/day rate restated as $/MT (real
  transit days for the actual route, via a new `estimate_transit_days`
  wrapping `opt.geography.distance_nm` at an assumed 13kn) / cargo tonnage.
  A real, previously-missing gap: "spot" contracts are quoted $/MT in real
  practice; this system had only ever produced $/day. None when the route
  isn't in the real distance matrix -- never fabricates a number.
- `forecast_confidence` -- per-horizon direction ("up"/"down"/"flat") +
  confidence%, derived from the fan's own p10/p50/p90 via the same
  piecewise-lognormal-from-quantiles calibration `opt.stopping` already
  uses (`_Z90=1.2816`), applied to one horizon instead of a whole path --
  P(the real forecast distribution agrees with its own p50-vs-today
  direction), a real computed number, not a hardcoded confidence.
- `congestion_label` -- buckets `opt.congestion.dynamic_wait_days`'s real
  multiplier-on-baseline estimate into LOW/MODERATE/HIGH, carrying its
  `is_real_data` flag through rather than presenting a static-baseline
  fallback as a live read.

**Done -- `opt/types.py` extensions (`RateHorizon`, `PortCheck`,
`QuoteResult`).** `QuoteResult` wraps a full `OptimizerRecommendation`
(carried whole as `full_recommendation` -- nothing it computes is
duplicated or re-derived) with the display polish above, plus
`origin_port_check`/`dest_port_check` -- **both ends**, the PS's own named
"port infra constraints... for both origin and destination" ask, not just
the origin-side cost term `opt.voyage` already had. Defined after
`OptimizerRecommendation` in the file (not before) after hitting a real
pydantic forward-reference resolution error the first time -- with
`from __future__ import annotations`, a class referencing a not-yet-defined
sibling class earlier in the same module fails at import time; moving it
below fixed it cleanly.

**Real circular-import found and fixed while wiring `opt/__init__.py`.**
`opt.quote` depends on `ml.live_forecast`, which itself imports
`opt.types` -- re-exporting `quote()` from `opt/__init__.py` made
`ml.live_forecast`'s own import of `opt.types` re-enter
`opt/__init__.py` while `ml.live_forecast` was still mid-import,
raising `ImportError: cannot import name 'forecast_all_classes' from
partially initialized module`. Fixed by *not* re-exporting `opt.quote`
from the package `__init__` (documented inline why, so it isn't
"fixed" back into a cycle later) -- callers import it directly
(`from opt.quote import quote`), exactly how `backend/main.py` and every
test already do.

**Done -- `backend/` (NEW top-level package, the FastAPI wrapper, 19 new
tests across `test_backend.py` + `test_serialize.py`).** Matches
`docs/plan.md`'s own original target architecture (Part 3: "backend/
FastAPI service"). Deliberately thin -- `backend/main.py` validates HTTP
input and calls `opt.quote.quote()`; no decision logic lives here.
`POST /quote`, `GET /ports`, `GET /health`; free interactive docs at
`/docs` (FastAPI auto-generates from the Pydantic request schema).
Verified against both `TestClient` (in the suite) and a real
`uvicorn`-served process hit with real `curl` requests (not just the
in-process client) before considering this done.

**Real serialization problem found and fixed: `backend/serialize.py`.**
`opt.types`'s `PortEnum` fields are a plain `Enum` whose *value* is a
`Port` pydantic model -- by default, `QuoteResult.model_dump(mode="json")`
embeds the entire `Port` object (id, max_loa_m, ...) everywhere a port
appears, with no symbolic code (`"NEWCASTLE_AU"`) a frontend could send
back on its next request. `OptimizerRecommendation` carries `PortEnum`
fields many levels deep (voyage assignments' `dest_port`, repositioning's
`current_port`/`recommended_port`, fleet-mix's `transshipment_hub`) --
hand-patching every path would be error-prone and silently stale the
moment a new `PortEnum` field is added anywhere in `opt.types`. Fixed
generically instead: `quote_result_to_json` walks the already-dumped JSON
structure and replaces *any* dict matching a `Port` model's exact shape
with that port's real `PortEnum` member name, recursively, everywhere --
correct for every current and future PortEnum field without per-field
maintenance. Verified live through the full nested structure (voyage
assignments, repositioning actions) via both the real server and the test
suite.

**Environment note, not a code issue.** `scikit-learn` and `searoute` --
both real `pyproject.toml` dependencies used by `tonnage.supplycurve` and
`opt.geography`/`tonnage.basins` respectively -- were missing from this
machine's global Python (no uv/venv on this machine, see the environment
notes), causing 4 pre-existing test files to fail collection and 2
`tonnage.basins` tests to fail outright. Both installed via `pip install`;
full suite went from erroring-on-collection to clean. `fastapi`, `uvicorn`
(runtime) and `httpx` (dev, required by FastAPI's `TestClient`) added to
`pyproject.toml` for the same reason -- they were already present on this
machine but hadn't been declared. `pyproject.toml`'s `pythonpath` extended
to `["src", "."]` so `tests/backend`'s `import backend` works even under a
bare `pytest` invocation, not just the documented `python -m pytest`.

**Verification.** Both real demo scripts (`run_live_scenario.py`,
`run_blackbox_scenario.py`) re-run end to end after the `ml.live_forecast`
promotion and again after every subsequent change -- byte-identical
recommendation text where nothing should have changed, sane real numbers
throughout `quote()`'s new $/MT/confidence%/congestion-label output. `ruff
check` clean across every new/changed file (8 auto-fixable issues found and
fixed -- import sorting, one unused import, redundant `noqa` comments; the
5 remaining repo-wide ruff findings are in pre-existing files this session
never touched). **Full suite: 504 passed, 2 skipped** (453 + 51 new tests:
9 in `test_live_forecast.py`, 17 in `test_present.py`, 13 in
`test_quote.py`, 12 across `tests/backend/`), zero regressions anywhere.

**What this final lap deliberately does not claim.** No real basis
calibration table exists yet (`opt.quote` passes `basis={}`, exactly like
both real demo scripts always have) -- gap 1's sibling, P1 defect 12 from
Part 1, still open; `quote()` is honest about this (`route_adjusted=False`)
rather than fabricating basis numbers under this session's time budget. No
claim that the $/MT conversion is a real voyage costing -- it ignores port
time, demurrage, and ballast positioning, all deliberately (documented in
`opt.present`'s own docstring) since `opt.voyage`'s real CP-SAT schedule
already prices those properly once an actual vessel is on hand. No claim
that `forecast_confidence`'s confidence% is a validated, backtested
probability -- it's a real, honestly-derived statistic from the fan's own
quantiles, not a claim of calibration accuracy beyond what a 3-point
lognormal fit can support (same epistemic status as `opt.stopping`'s own
lognormal calibration it reuses the convention from). The backend has no
auth, rate limiting, or persistence -- explicitly out of scope for a
hackathon backend meant to be built on top of locally; CORS is wide open
for the same reason, called out in the module docstring as a real thing to
tighten before any real deployment.

**Backend is now handoff-ready.** `opt.quote.quote()` is the one real
Python entry point (cargo/route/laycan/contract-type in, one clean,
fully-explained `QuoteResult` out); `backend/main.py` is the one real HTTP
entry point (`POST /quote`, self-documenting at `/docs`) a frontend
teammate can build against without reading any of `src/opt`'s internals.
Everything computed earlier in this project (LOCK/WAIT fused with the LSMC
exercise boundary, vessel-type optimization with real rejection reasons,
port constraints at both ends, real risk flags, real repositioning hazard
rates, full explainability) is reachable through it. Gap 1 (macro/
commodity indicators) and real basis calibration remain the two known,
previously-flagged, deliberately-deferred gaps -- both explicitly
retraining/new-data work, out of scope per the user's own instruction for
this session.
