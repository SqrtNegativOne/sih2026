# Team audit & feature guide

Written 2026-08-28, covering the full P1–P7 build. This is the judge/teammate-facing summary:
what exists, what's real vs honestly disclosed as unavailable, how to run it, and how to see
each feature yourself. For the deep design writeup see [`docs/plan.md`](plan.md); for the
original phase-by-phase prompts see [`docs/SONNET_PROMPTS.md`](SONNET_PROMPTS.md).

## Running it

See the root [`README.md`](../README.md) for setup verified from a clean checkout. Short version:

```
python -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\pip install -e .
.venv\Scripts\pip install --group dev
cd frontend && npm install && cd ..
run.bat
```

`run.bat` (or `run.ps1`) starts the backend on `http://127.0.0.1:8000` and the frontend on
`http://localhost:5173` (Vite will pick the next free port if 5173 is busy — check the terminal
window it opens for the real URL). No `uv` required.

## Feature guide — where to see each thing

The app is a five-screen dashboard. The left icon rail switches screens; "TC In"/"TC Out" are
greyed out on purpose (see Limitations).

### 1. Voyage Desk (default screen)
Click **New Charter Quote** (top bar) and fill in a cargo lot — origin, destination, laycan
window, optionally a vessel. Submitting shows:
- **Verdict** — LOCK/WAIT recommendation, with the LSMC option-value reasoning.
- **Rate forecast** — p10/p50/p90 at 7/30/90-day horizons, with a route-evidence badge (almost
  always "route basis unavailable" today — see Limitations, this is honestly disclosed, not
  hidden).
- **Fleet mix**, **port constraints** (both ends), **voyage assignments** (if you added a vessel).
- **Landed Cost panel** — freight/wait/handling/demurrage/commodity, each with a provenance
  badge; type your own handling rate, demurrage terms, or pick a commodity and hit Recompute.
- **Backhaul Opportunity panel** — only active with a vessel attached; click "Score every port"
  for a real (multi-second) sweep of backhaul candidates. Never shows a $/MT credit — every
  result explains why.
- **Route map + route list** — every candidate the solver actually considered.

### 2. Port Twin
Pick a port from the dropdown. Shows the real Berth Reality Engine composition for that port:
feasibility verdict, tide rule (if one binds), empirical wait-time percentiles, empirical
handling productivity, and the raw recent port calls behind it. Paradip has by far the richest
real sample (n≈1200); most other ports honestly show an insufficient-sample state rather than a
guessed number.

### 3. Tonnage Field
Loads automatically (first load takes ~10s — a real reconstruction over ~130 real ports, then
cached). Shows the real physical supply-tightness index by vessel class and basin, and — in the
validation panel (first load ~30s, then cached) — the identification-gate verdict (RELATIVE, not
ABSOLUTE — explained in place) and the real sign-diagnosis findings per class.

### 4. Fragility
Fill in a cargo lot, hit **Run sweep** (takes ~15-20s — a real, disclosed multi-variable
flip-point search, not a caching bug). Shows which inputs would have to move, and by how much,
before the recommendation changes — ranked fragile-first.

### 5. Ledger
Two sections. **Live** — every real recommendation this app has actually made through the UI,
starting empty and filling forward as you use it; enter a realized rate to score one. **Replay**
— a retrospective backtest against historical data, clearly labelled "not decisions this system
actually made" and kept structurally separate from the live numbers. First load of Replay is
genuinely slow (~20 minutes — a real PSO calibration + Monte Carlo backtest); cached after.

## Ship-verification matrix

Every link below was executed against real data this session, not assumed.

| Subsystem | Chain | Verified |
|---|---|---|
| **M1 — Tonnage Field** | Real PortWatch AIS data → `tonnage.stockflow`/`supplycurve` (RELATIVE, gated) → `tonnage.forward` → `GET /tonnage-field*` → Tonnage Field screen | Live: real tightness values, real ablation verdict |
| **M4 — Berth Reality** | `fact_port_call`/BT register → `berth_truth.reality.get_port_reality` → `GET /ports/{code}/*` → Port Twin screen | Live: real n=1210/1211 wait/handling at Paradip, real register data at Dhamra/Gangavaram |
| **DF — Fragility** | Real M4 inputs → `fragility.engine.analyze_fragility` → `POST /fragility` → Fragility screen | Live: real sweep, real ranked findings |
| **Forecast** | Class-aware XGBoost + route-evidence gate → `opt.quote.quote_envelope` → `POST /quote` → rate-forecast table | Live: 3 real horizons; route_evidence always present, never silently missing |
| **Regret ledger** | `opt.ledger` record→outcome→score → `GET/POST /ledger/*` → Ledger screen | Live: full round trip; replay kept structurally separate from live |
| **Commercial** | `opt.backhaul` + `opt.landed_cost` → `/quote`'s `landed_cost` field + `POST /landed-cost`/`/backhaul` → Landed Cost / Backhaul panels | Live: all 5 landed-cost components real when specified; credit always null with a real reason |

## Problem-statement requirement coverage

1. **Freight forecasting by class** — real. **By route** — the mechanism is real and tested, but
   honestly produces no different number yet (every route currently resolves
   `ROUTE_RATE_BASIS_UNAVAILABLE`, verified live).
2. **LOCK/WAIT timing** — real, LSMC-fused into the production decision.
3. **Vessel-type + port constraints, both ends** — real.
4. **Idle/repositioning strategy** — real, per-port Poisson hazard rates.
5. **Risk/early-warning** — real (regime, congestion, chokepoint, cyclone signals).
6. **Macro/commodity context** — real data (World Bank + FRED), used in landed cost. Honestly
   *rejected* as a rate-forecasting feature: all 5 real candidates were A/B tested and none
   cleared the adoption bar (real pinball-loss changes from −31% to +2.5%, none both above the
   +1% threshold and validation-agreeing). A disclosed negative result, not a gap.
7. **Explainability** — real, on every quote.
8. **Country coverage** — all 5 problem-statement-named countries (Australia, US, Mozambique,
   Russia, Indonesia) and all 7 Indian discharge ports are present. No gap.

## Closure report vs. the original audit

| # | Original defect | Status | Evidence |
|---|---|---|---|
| 1 | Route-level forecasting absent | **Partial** | Mechanism real+tested; number still origin-invariant today, honestly labelled |
| 2 | Tonnage Field hash placeholder | **Closed** | Real reconstruction; guard test passing |
| 3 | Moats unreachable over HTTP | **Closed** | All verified live |
| 4 | Optimiser edge ≈ zero | **Partial** | Real measurement: pooled +$0.61/day, Capesize +$2.12/day, others $0.00 — honestly measured |
| 5 | No macro/commodity indicators | **Partial** | Real data, used commercially; honestly ablation-rejected for forecasting |
| 6 | Tonnage scale unvalidated + wrong-signed curve | **Closed** (diagnosis) | Formal RELATIVE gate + full sign diagnosis, all 4 classes |
| 7 | Thin $/day series | **Closed** (mitigation) | Models verified to train on the rich index series, never directly on the thin one |
| 8 | PortWatch estimates treated as observations | **Closed** | Real provenance framework, reaches API + UI |
| 9 | No point-in-time data | **Partial** | Real mechanism, 2 real archived snapshots; can't reconstruct pre-archive history |
| 10 | Launcher assumes `uv` | **Closed** | Rewritten, verified from a clean shell |
| 11 | PortWatch coverage holes | **Confirmed, unchanged** | Exactly 5 ports: Gangavaram, Sagar Sandheads, Gladstone AU, Hampton Roads, Singapore |
| 12 | Berth Reality Engine ~1/3 complete | **Closed** (capability) | Tide + berth register + wait + handling all real and composed |
| 13 | `src/impact/` built but invisible | **Closed** | Marked experimental, zero live references, enforced by a static test |

## Real, measured endpoint timings

| Endpoint | Cold | Warm |
|---|---|---|
| `/health`, `/meta`, `/ports` | — | <0.05s |
| `/ports/{code}/*` | 0.085s | <0.11s |
| `/tonnage-field` | 10.8s | 0.01s |
| `/tonnage-field/validation` | 34.6s (separate cache) | 0.004s |
| `POST /quote` | 1.6s | 0.8s |
| `POST /fragility` | 20.7s | ~17s — real per-sweep cost, not a caching gap |
| `POST /landed-cost` | — | 0.03s |
| `POST /backhaul` (2 candidates) | 5.6s | <3s |
| `GET /ledger/replay` | 22.4 min, real PSO+Monte Carlo | instant (cached) |

## Honest limitations — what you will and won't see

- Route-aware pricing exists as a real, tested mechanism but produces no different number yet —
  every route is evidence-unavailable today.
- The optimizer's measured edge is small and that's the honest number: near-zero pooled, real
  only for Capesize.
- Macro/commodity data doesn't improve the rate forecast (tested, rejected) — it's used for
  landed cost, not rate prediction.
- Empirical wait/handling data is real and rich for Paradip only; every other port is honestly
  gated rather than guessed.
- `POST /fragility` and `GET /ledger/replay` are genuinely slow by design (a real search sweep, a
  real backtest) — both disclosed in the UI, not hidden.
- "TC In"/"TC Out" nav items are real chartering concepts (time-charter in/out contract
  management) this app does not implement — shown disabled rather than as a dead click.
- `src/impact/` (Almgren-Chriss market-impact scheduling) is real code, real tests, but formally
  marked experimental and disconnected — its two upstream inputs (tonnage scale, supply-curve
  slope) were found invalid for this use by the identification-gate/sign-diagnosis work.

## Full test suite

**1038 passed, 2 skipped.** `npm run build` and `npx tsc --noEmit` both clean.
