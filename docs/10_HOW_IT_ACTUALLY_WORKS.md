# How it actually works

A plain-language walkthrough of the running app, screen by screen: what you type, what comes
back, and what the backend actually does to produce it. No file names, no jargon that isn't
explained.

Written 2026-08-29 against the `feature-full-build-p1-through-p7` branch, with the backend and
frontend both running locally and every number below read off a live response.

Companion document: [`11_FAULT_REGISTER.md`](11_FAULT_REGISTER.md) — everything that is wrong,
checked against the problem statement.

---

## 0. What this thing is supposed to be

You have one lot of coal to move from somewhere overseas to an East Coast India port. You want
to know four things:

1. **Should I sign a charter today, or wait?**
2. **What size ship should I use?**
3. **Will that ship physically fit at both ends?**
4. **Is anything about to blow up in my face?**

The app answers those four questions for one cargo lot at a time. Everything else on screen is
supporting evidence for those four answers.

---

## 1. The mental model — read this once and the rest makes sense

### There are two different "todays" and they are not the same day

| | What it is | Value right now |
|---|---|---|
| **Your calendar today** | The real date on your laptop | 2026-08-29 |
| **The market date** ("Price as of") | The most recent day the app has real Baltic freight-market data for | **2026-08-20** |

The freight data is scraped from a public site. The scrape stopped on 20 August. So the app's
whole world ends on 20 August 2026, nine days before your actual today.

**This is why the "Price as of" box maxes out at 20 August.** It isn't predicting the past — it
is standing on 20 August and predicting 7, 30 and 90 days *forward from there*, i.e. out to
27 Aug, 19 Sep and 18 Nov. It's just that "forward from there" now partly overlaps days that
have already happened in real life. Fix the data freshness and this confusion disappears
entirely.

### What the machine-learning model actually predicts

This is the single most misunderstood thing in the project, so, precisely:

The model **does not** predict "the price of shipping coal from Australia to Paradip."

The model predicts **the percentage change in a published market index**, and nothing else.

Here is the whole chain, in order:

```
  1. HISTORY          The Baltic Exchange publishes four daily numbers, one per ship size:
                      BCI (Capesize), BPI (Panamax), BSI (Supramax), BHSI (Handysize).
                      These are index points, not money. We have them back to 2012.

  2. THE MODEL        An XGBoost model looks at the last 63 days of one index (its own lags,
                      its 30/90-day averages and volatility, the calendar — monsoon, Chinese
                      New Year, Indian fiscal quarter — and daily ship-count data at a
                      handful of ports) and outputs THREE numbers per horizon:
                      "in 7 / 30 / 90 days this index will have moved by X%",
                      given as a low guess (p10), a middle guess (p50) and a high guess (p90).

  3. TRANSLATION      Index points are not dollars. Separately, the app knows today's real
                      published time-charter rate in $/day for each ship size
                      (on 2026-08-20: Capesize $40,170, Panamax $18,790,
                      Supramax $20,698, Handysize $15,670).
                      It applies the predicted % change to today's real $/day rate.

  4. THE FAN          You now have, for each ship size, nine numbers:
                      p10/p50/p90 at 7 days, at 30 days, at 90 days. That is "the fan"
                      you see drawn on the Rate Forecast panel.
```

**Everything after step 4 contains no machine learning at all.** The route, the distance, the
fuel, the port limits, the queue times, the vessel scheduling — all of that is ordinary
arithmetic and constraint-solving done on top of the fan.

### What "LOCK" and "WAIT" actually mean

You are choosing between two ways of buying ship capacity:

- **Spot / voyage charter** — hire a ship for one trip, at whatever the market charges that
  week. Repeat forever. This is what SAIL does today.
- **Time charter (TC)** — rent a ship for a fixed period (30 days, 90 days, 6 months) at a
  fixed $/day agreed up front. This is the "lock" the problem statement wants you to move to.

The app computes a **ceiling**: the highest fixed $/day at which locking still beats staying
in the spot market. Then:

```
   today's quoted TC rate  ≤  ceiling   →   LOCK
   today's quoted TC rate  >  ceiling   →   WAIT
```

That is the entire decision. Everything else on the Verdict panel is context for those two
numbers.

### How the ceiling is built (three steps, no magic)

**Step A — blend the forecast over your contract length.**
Your contract term is chopped into three slices and each slice is priced off the nearest
forecast horizon:

| Days of your contract | Priced off |
|---|---|
| days 1–18 | the 7-day forecast |
| days 19–60 | the 30-day forecast |
| days 61+ | the 90-day forecast |

A 30-day contract is therefore 63% 7-day-forecast and 37% 30-day-forecast. A 365-day contract
is mostly the 90-day forecast held flat. That weighted average of the p50s is your expected
spot cost — the naive ceiling.

**Step B — add the value of being allowed to keep waiting.**
"Wait" is not just "don't sign." It's "don't sign *and keep the right to sign tomorrow, or next
week, at a better price*." That right is worth money, exactly like a financial option. The app
prices it with a standard technique (Longstaff–Schwartz least-squares Monte Carlo): it simulates
4,000 possible price paths consistent with the forecast fan, then works backwards day by day
asking "at this price, on this day, is signing better than holding on?"

The output is an **exercise boundary** — a price line for every day of the planning horizon.
Day 0 of that line replaces the naive ceiling. Because holding an option is valuable, the
boundary sits *below* the naive number, which makes the app harder to convince to lock.

You can see this in a live response: for a 55,000 t Newcastle→Paradip lot, the naive ceiling
was **$21,016/day**, the option value of waiting was **$1,538/day**, and the operative ceiling
dropped to **$19,478/day**. Today's rate was $20,698 — above the boundary — so: **WAIT**.

**Step C — compare, and report.**
Today's rate vs. the boundary gives LOCK or WAIT. Separately (and this matters — see the fault
register) a *different*, much cruder simulation estimates "expected savings."

### What the model is blind to

| The model does **not** know | Why it matters |
|---|---|
| Which route you picked | Australia and Mozambique return the **identical** $/day forecast |
| Which port you're going to | Same |
| Your ship's fuel burn, speed, size | Those live in a separate voyage calculator |
| Your laycan dates | Verified: changing the laycan changes nothing in the recommendation |
| Commodity prices, FX, the economy | Tested and rejected — didn't improve the forecast |

---

## 2. The words you will see on screen

| Word on screen | What it means |
|---|---|
| **TC $/day** | Time-charter hire — the daily rent for the whole ship. You still pay fuel and port dues on top. |
| **p10 / p50 / p90** | The model's low / middle / high guess. "p10 = $15,400" means: 10% chance the rate lands below $15,400. |
| **Fan** | The shaded band on the chart between p10 and p90. Wide band = the model is unsure. |
| **Horizon** | How far ahead. Only three exist: 7, 30, 90 days. |
| **Ceiling** | The walk-away price. Pay more than this and locking is worse than staying spot. |
| **Laycan** | Laydays/cancelling — the window in which the ship must show up to load. |
| **Contract term** | How many days the time charter runs for. |
| **DWT** | Deadweight tonnes — how much a ship can carry, including fuel and stores. |
| **Draft** | How deep the ship sits in the water. The binding limit at shallow Indian ports. |
| **LOA / Beam** | Length overall / width. Berth-geometry limits. |
| **Ballast** | Sailing empty. Costs fuel, earns nothing. The thing you want to minimise. |
| **Transshipment / T/S** | Discharge at a deep port, then move cargo onward by smaller ship or barge. |
| **Class-only** *(badge)* | "We priced this on the ship-size benchmark, not on your specific route." It says this on **every** route. |
| **Congestion LOW/MODERATE/HIGH** | Recent ship-arrival counts at that port vs. its own normal. Not a queue length. |
| **Tightness** | A dimensionless index of how fast free ship capacity is being used up. Not a number of ships. |

---

## 3. Screen: the shell (top bar and left rail)

### Top bar

| Control | What it does |
|---|---|
| **CHARTERING** logo | Nothing |
| Forecast / Fleet / Ports / Risk / Map | Scroll-to-section links on the Voyage Desk. "Forecast" is permanently underlined regardless of where you are. |
| **Search box** | **Does nothing.** Not wired to anything. |
| **New Quote** | Opens the charter form. The one working control here. |
| Bell / gear / question mark | **All three do nothing.** |

### Left rail — 12 icons, 4 of which are screens

| Rail item | What it really is |
|---|---|
| Cargoes, Estimates, Fixtures, Market, Matching, Scheduling | **Not screens.** Six anchor links that scroll to six panels on the *same* Voyage Desk page. |
| Port Twin, Tonnage Field, Fragility, Ledger | **Real, separate screens.** |
| TC In, TC Out | **Greyed out. Not implemented.** |

"Estimates" is highlighted as active whenever you are on the Voyage Desk, because the desk has
no rail label of its own. "SCHEDULING" is wider than the 56-pixel rail and is clipped at both
edges.

---

## 4. The "New Charter Quote" form — every field

This drawer is the only place the app takes real input. Here is what each field does, and —
just as importantly — what it does *not* do. Everything marked **verified** was tested against
the live API.

### Cargo volume (DWT) — the most important field on the form

**What you type:** tonnes, e.g. 75000.

**What it does:** it picks the vessel class, by a pure size lookup, and *that class drives the
entire recommendation* — the rate, the ceiling, the LOCK/WAIT verdict, the savings, the ledger
entry.

| You type | Class chosen | Rate used (2026-08-20) |
|---|---|---|
| up to 32,000 | Handysize | $15,670/day |
| 32,001 – 58,000 | Supramax | $20,698/day |
| 58,001 – 82,000 | Panamax | $18,790/day |
| 82,001+ | Capesize | $40,170/day |

**What it does not do:** this lookup never checks whether that ship can actually enter your
ports. A separate panel does check — and frequently disagrees. See fault F-03.

### Cargo type

Free text, or pick from the dropdown. **It has no effect on the price.** It is carried through
and printed back at you. The only place a commodity does real work is the Landed Cost panel's
own "Commodity" dropdown, which offers only iron ore and coal.

### Origin port / Destination port

**What they do:**
- set the sea distance (used for $/MT conversion and voyage time)
- decide which vessel classes physically fit at each end
- select which ports get checked for congestion and berth limits

**What they do NOT do — verified:** they do not change the freight forecast. Same cargo, same
laycan, same date, six different origins:

| Origin | Ceiling $/day | 30-day p50 | Verdict | Transit |
|---|---|---|---|---|
| Newcastle, Australia | 18,141.4554 | 19,078.49 | WAIT | 18.2 d |
| Hampton Roads, USA | 18,141.4554 | 19,078.49 | WAIT | 31.8 d |
| Beira, Mozambique | 18,141.4554 | 19,078.49 | WAIT | 14.1 d |
| Balikpapan, Indonesia | 18,141.4554 | 19,078.49 | WAIT | 8.4 d |
| Vostochny, Russia | 18,141.4554 | 19,078.49 | WAIT | 14.8 d |
| Richards Bay, S. Africa | 18,141.4554 | 19,078.49 | WAIT | 15.0 d |

Byte-identical to four decimal places. Only the sailing time changes, which is a distance
lookup, not a forecast. The "Class-only" badge on the Rate Forecast panel is the app admitting
this.

### Price as of

**What it does:** picks which market day to stand on.

**Constraints nobody tells you about in the box:**
- must be **on or before 2026-08-20** (there is no data after that)
- must be an **actual trading day**. Weekends and holidays fail: 2026-08-15 (Sat) → error,
  2026-08-16 (Sun) → error, 2026-08-20 (Thu) → works.
- the field **defaults to your real today (2026-08-29)**, which always fails. The very first
  quote a new user runs returns a red error. See fault F-02.

### Contract term (days)

**What it does:** sets how the 7/30/90-day forecasts are blended (the table in §1), and
multiplies the per-day savings into a headline total.

**Verified effect** (Newcastle→Paradip, 75,000 t):

| Term | Ceiling | Headline savings figure |
|---|---|---|
| 7 d | $17,897 | −$2,742 |
| 30 d | $18,141 | −$5,872 |
| 90 d | $18,052 | −$26,791 |
| 365 d | $17,786 | −$294,392 |

The ceiling barely moves; the headline total scales almost linearly with the term because it is
just (per-day figure × term).

### Laycan start / Laycan end

**What they do:** almost nothing, unless you also add a vessel.

**Verified:** four completely different laycans, same everything else:

| Laycan | Ceiling | Verdict |
|---|---|---|
| 25–31 Aug 2026 | $18,141 | WAIT |
| 3–10 Sep 2026 | $18,141 | WAIT |
| 1–30 Nov 2026 | $18,141 | WAIT |
| 1–20 Mar 2027 | $18,141 | WAIT |

The laycan is used for exactly two things: (a) a sanity check that it hasn't already closed,
and (b) as a time window in the vessel scheduler — which only runs if you added a vessel.
Otherwise it is inert.

### Risk tolerance slider

**Intended meaning:** 0 = price on the middle forecast (p50); 1 = price on the pessimistic
forecast (p90), i.e. assume rates rise, so lock more readily.

**Verified actual effect:**

| Slider | Ceiling | Verdict |
|---|---|---|
| 0 | $18,141 | WAIT |
| 0.5 | $18,439 | WAIT |
| 1.0 | $18,397 | WAIT |

A 1.6% spread, and it goes *down* between 0.5 and 1.0 — the simulation noise is bigger than the
effect. It never changes the verdict. The Fragility screen independently confirms this: it
searches the full 0.00–1.00 range and reports "no flip found."

### "+ Add vessel" — 11 fields per ship

This is the switch that turns on a whole second half of the app. Without a vessel, the app
answers "what should I do about this cargo." With one, it also answers "can *this ship* do it,
and what does it cost."

| Field | What it is used for |
|---|---|
| Vessel ID | A label. Appears in the assignment and repositioning rows. |
| Class | Which market rate is used for repositioning value and backhaul scoring. |
| Current port | Where the ballast (empty) leg starts. |
| DWT | Checked against each port's max DWT. This is what usually rejects a ship. |
| Draft (m) | Checked against each port's permissible draft. |
| LOA (m), Beam (m) | Checked against berth length/width limits. |
| Speed (kn) | Divides distance into sailing hours. |
| Laden t/day, Ballast t/day | Fuel burn, multiplied by the departure port's bunker price. |
| Available from | **The clock's zero point.** All the hour figures in the assignments table are measured from this date, not from today. |

The form pre-fills a generic Panamax (75,000 dwt, 13.5 m draft, 225 m, 32.2 m beam, 13 kn,
32/27 t/day). Those are placeholder numbers, not your fleet.

### Cargo revenue (USD)

Only appears once you've added a vessel. **Without it, no vessel is ever assigned to any cargo,
because the scheduler maximises profit and profit with zero revenue is always negative.** The
app is being honest here — it refuses to invent what your cargo is worth — but it means the
Voyage Assignments panel stays empty until you type a number that only SAIL knows.

---

## 5. Screen: Voyage Desk — the main screen

### Anatomy

```
 ┌──────────────────────────────────────────────────────────────────────┐
 │ SUMMARY STRIP   route · commodity · cargo · laycan                   │
 ├──────────────────┬────────────────────┬──────────────────────────────┤
 │ VERDICT          │ RATE FORECAST      │ RISK FEED                    │
 │ LOCK or WAIT     │ fan chart + table  │ alerts, or "all clear"       │
 ├──────────────────┴────────┬───────────┴─────────┬────────────────────┤
 │ FLEET MIX FRONTIER        │ PORT CONSTRAINTS    │ VOYAGE ASSIGNMENTS │
 │ which class, and cost     │ load vs discharge   │ (needs a vessel)   │
 ├───────────────────────────┴─────────────────────┴────────────────────┤
 │ LANDED COST                      │ BACKHAUL OPPORTUNITY              │
 ├──────────────────────────────────┴───────────────────────────────────┤
 │ ROUTE EXPLORATION (map)                    │ ROUTES (list)           │
 └────────────────────────────────────────────┴─────────────────────────┘
```

### Panel: Verdict

**Output:** a giant LOCK or WAIT, the class, the term, and a three-row money table.

| Row | What the number is |
|---|---|
| **Lock today** | Today's real published TC rate for the chosen class, × your term. |
| **Wait for trough** | The lowest p50 anywhere in the next 90 days, and the date it lands on. |
| **Ceiling** | The option-adjusted walk-away line from §1 Step B. |

Below that, two lines that need care:

- **"Waiting avoids an expected loss of $X"** — this label is wrong whenever the underlying
  number is positive. In a live example the app printed *"Waiting avoids an expected loss of
  $7,570"* when the number actually meant *"locking today would beat expected spot by $7,570."*
  See fault F-06.

- **"Confidence locking beats spot — 47%"** — **this is not an accuracy figure.** It is the
  fraction of 2,000 simulated futures in which the average spot rate over your contract term
  ends up above today's quoted rate. 47% means "in 47% of simulated futures you'd have been
  better off locking." It has nothing to do with how accurate the model is. It also changes
  every time you press the button, because the simulation is unseeded — the same request four
  times in a row gave 45.7%, 46.0%, 46.6%, 46.8%.

**About the "trough" date:** it is the minimum of the daily p50 curve over the next 90 days, and
it is computed *without reference to your laycan.* In the worked example the app advised waiting
until "Sep 16 to Sep 22" for a cargo whose loading window was "Sep 12 to Sep 19" — advice to
fix after the ship was supposed to have loaded.

### Panel: Rate Forecast

**Output:** the fan chart plus one row per horizon.

| Column | Meaning |
|---|---|
| Horizon | 7d / 30d / 90d |
| p10 / p50 / p90 | Low / middle / high $/day guess for that horizon |
| **$/mt (class÷transit)** | A unit conversion only: (p50 × sailing days) ÷ cargo tonnes. It ignores port time, waiting, ballast and fuel. It is **not** a freight quote. |
| Dir | Arrow: is p50 above or below today's rate |
| Conf | See below |

**"Conf" is not model accuracy either.** It is: given the model's own fan, what's the chance the
rate lands on the same side of today's price as the p50 does. Because of how it's defined it can
never go below 50 — the bar is structurally at least half full, always.

**"Class-only" badge:** the honest admission that no route-specific adjustment was applied.
It says this on every route, always, because the only route-level rate evidence in the whole
dataset is three observations of an Indonesia→East India rate quoted in $/tonne, which is below
the app's own evidence bar.

### Panel: Risk Feed

**Output:** alerts, or a green "no disruption signals" panel listing four monitored channels.

Four real checks run:

1. **Rate regime** — is today's index move unusual vs. its own last 90 days (|z| > 2)?
2. **Port congestion** — is the ship-arrival count at your two ports unusually high (z > 2)?
3. **Chokepoint transits** — has traffic through Suez / Bab el-Mandeb / Malacca / Hormuz / Cape
   of Good Hope *dropped* unusually (z < −1.75)? A drop means blockage; a spike doesn't.
4. **Cyclone season** — is the date in October–December? Pure calendar, no storm tracking.

In the live run, one alert fired: *"Malacca Strait dry-bulk transits fell to 40/day, z=−2.2 vs
its own 60-day baseline."* That one works correctly.

**Channel 2 does not work.** For 8 of the 12 ports the app can send it, the port is looked up
under the wrong name and silently returns nothing. For the 4 that do resolve, the data file is
read without sorting by date, so it tests a day from **October 2025** as though it were
2026-08-20, against a "60-day baseline" made of scrambled dates from three different years.
See faults F-08 and F-09. The green "all signals within their thresholds" panel is therefore not
trustworthy.

### Panel: Fleet Mix Frontier

**Output:** one row per vessel class, cheapest feasible first, plus a greyed list of the
rejected ones with reasons.

**How it's computed:** for each of the four classes, take a standard representative ship
(Handysize 32,000 t / 9.5 m draft; Supramax 58,000 / 12.5 m; Panamax 82,000 / 14.5 m; Capesize
180,000 / 16.5 m); work out how many you'd need; check that ship against both ports' DWT, draft,
LOA and beam limits; if the destination is too shallow, try routing via a deep hub (Haldia and
Sagar-Sandheads → Dhamra; Gopalpur → Vizag) with a $3.50/tonne lightering surcharge; then price
it as (rate × sailing days × number of ships) + fuel.

**This panel is the app's honest answer to "which ship size" — and it routinely contradicts
the Verdict panel above it.** For 75,000 t Newcastle→Paradip:

- Verdict says: **Panamax**, priced at $18,790/day
- Fleet Mix says: **Panamax ×1 — cannot call destination Paradip: vessel DWT (82,000) exceeds
  port max (75,000)**, and the cheapest feasible answer is **Supramax ×2** at $20,698/day each

Both are on screen at once. See fault F-03.

**"Rel." column** is a reliability grade, A–E, from a simple rule: fewer ships is better,
transshipment is worse. It is a heuristic, not a fitted model.

**Note the two different $/MT numbers on this screen.** The Fleet Mix row says $16.59/MT
(full voyage cost including fuel, for two ships). The Rate Forecast row says $4.45/MT (rate ×
transit ÷ tonnes, one ship, no fuel). The Landed Cost panel says $4.55/MT. Nothing on screen
explains that these are three different quantities.

### Panel: Port Constraints

**Output:** load port vs. discharge port, side by side.

| Row | Where it comes from |
|---|---|
| Max DWT, Draft, LOA, Beam | Hardcoded constants in the app's port table. **Not** from the berth register — no port in the register publishes a max DWT at all. |
| Wait, days | The port's baseline wait, scaled by how busy it's been in the last 14 days vs. the previous 60. Real satellite ship-count data. Paradip: baseline 3.5 → 3.60 days. Newcastle: 5.0 → 4.64 days. |
| Congestion | That same multiplier bucketed: ≤0.8× = LOW, ≥1.3× = HIGH, else MODERATE. |

A `*` next to the wait means it fell back to the static baseline. Note this figure is *not* the
same as the "empirical wait" shown on Port Twin — they are two different estimates of the same
thing, computed from two different data sources, and both appear in the product.

### Panel: Voyage Assignments

**Only populated if you added a vessel *and* a cargo revenue figure.**

**How it's computed:** a constraint solver (Google OR-Tools CP-SAT) maximises
revenue − fuel − idle-time cost − demurrage, subject to: the ship fits at both ports, it arrives
inside the laycan, and the physical sequence works.

**Column meanings — and a warning about the clock:**

| Column | Meaning |
|---|---|
| Arrive | Hours until arrival **at the load port** |
| Wait | Queue time before loading can start |
| Finish | Hours until discharge is complete at the destination |
| Ballast | Empty-sailing hours to reach the load port |
| Profit | Your revenue minus modelled costs |

The panel's tooltip says "times are days from now." **They are not.** Zero on that clock is the
*earliest vessel availability date* you typed, not today. In a live run "Arrive 14.0d" meant
3 September, measured from the vessel's 20 August availability — not 14 days from your actual
today.

Also: the ship's daily running cost used in this optimisation is **hardcoded at $500/day**. A
real Panamax runs $5,000–6,000/day. It is not exposed anywhere in the form.

### Panel: Landed Cost

**Output:** five cost components in $/MT, each with a provenance badge, and a "partial total"
that is explicitly a lower bound.

| Component | Where it comes from |
|---|---|
| Freight | (today's $/day × sailing days) ÷ tonnes. Always available. |
| Wait / delay | Median arrival-to-berth time × your ship's daily cost ÷ tonnes. Blank on the desk because the desk doesn't know your daily cost. |
| Handling | **Blank unless you type a number.** The app has no $/tonne handling tariff for any port. |
| Demurrage | **Blank unless you type both a $/day rate and a laytime allowance.** |
| Commodity price | Real World Bank benchmark price for iron ore or coal, if you pick one. |

The bottom half is a form where you supply your own commercial terms and press Recompute.
**On a normal screen that form is cut off** — the panel is a fixed 260 pixels tall and the
inputs sit below the fold with no visible scrollbar. See fault F-33.

The default state shows "1/5 components real" and a total of about $4.55/MT, which is freight
only.

### Panel: Backhaul Opportunity

**Output:** after your ship discharges here, where could it pick up its next cargo?

**How it's scored:** for every other port, it computes P(a suitable cargo departs that port
within 30 days) using a Poisson hazard rate built from satellite-observed export tonnage,
divided by a typical parcel size for that ship class. Then it zeroes the score if the ship
physically can't call there or the ballast leg is longer than the window.

**The score *is* that probability and nothing else.** No distance penalty, no fuel cost, no rate
difference, no money. A port with satellite coverage scores ~1.0; a port without gets a flat 0.5
placeholder. So the ranking is effectively "ports we happen to have data for, first." The panel
correctly and permanently shows no $/MT credit, because the underlying data has no rate field
in it at all.

### Panels: Route Exploration (map) + Routes (list)

**Output:** every routing the solver looked at, drawn on a world map, plus a clickable list.

Solid line = chosen, dashed = considered, dotted red = rejected. Real sea routes where the
routing library resolves them, great-circle arcs otherwise.

**Why the map is always zoomed out to half the world:** the bounding box it fits is computed
over *every port in the system*, not just the ports on your route. Since the port list spans
Hampton Roads (76°W) to Newcastle (152°E), the map always shows Africa through Australia even
for a short Indonesia→Paradip hop.

---

## 6. Screen: Port Twin

**Input:** pick a port, then a ship's dimensions (the box labelled "Cargo DWT" is actually the
*vessel's* DWT — mislabelled).

**Output:** six panels answering "can this ship use this port, and what will it be like?"

| Panel | What it tells you |
|---|---|
| **Feasibility Verdict** | FEASIBLE / INFEASIBLE / CANNOT_VERIFY, with the margin in metres on each dimension. |
| **Tide Assessment** | Whether a published tide rule binds. |
| **Observed Envelope** | The biggest ship actually seen calling here. |
| **Empirical Wait Distribution** | Real median/75th/90th percentile waiting times, four intervals. |
| **Handling Productivity** | Observed tonnes-per-day vs. the port's own published norm. |
| **Berth Register / Recent Vessel Calls** | The underlying evidence. |

**The honest state of the data behind this screen:**

| Port | Berth register | Real port-call history |
|---|---|---|
| Paradip | **None** — falls back to hardcoded constants | 304 real calls, 6 weeks (13 Jul – 26 Aug 2026) |
| Dhamra | 20 berths, but **0 have a draft figure** (only 13 have a length) | None |
| Gangavaram | 9 berths, draft + length, no beam, no DWT | None |
| Vizag | 29 berths, 28 with draft + length, 8 with beam, no DWT | None |
| Gopalpur, Haldia, Sagar-Sandheads | **None** | None |

So: **no port in the register publishes a maximum DWT**, which is the limit that actually
rejects ships in this app. Every DWT check in the product runs on an unsourced constant. And the
port with all the real arrival data has no berth register at all.

**Three things on this screen are actively misleading:**

1. It shows "Max observed draft **21.5 m**" and, directly underneath, *"No conflicts — every
   observed call falls within the declared limits"* — while the verdict panel next to it says
   the declared limit is **14.3 m**. The conflict check silently skips any port without a berth
   register, which is every port that has observations.
2. That 21.5 m figure comes from **crude oil tankers at an offshore mooring buoy** (the vessel
   *Atherina*, 339.76 m, discharging crude at SPM-3). This is a dry-bulk chartering tool.
3. It reports **Confidence 100%** on a verdict whose own "Limit source" field reads
   `PORTENUM_FALLBACK` and whose "Binding berth" is blank.

**And the wait numbers are inflated.** The 1,224 rows in the store are only **304 distinct port
calls** — 75% are duplicates, because a vessel is re-ingested from every daily PDF it still
appears in (one ship appears 56 times). Ships that wait longer appear in more reports, so they
count more. Deduplicated, the median wait is **54.8 h**; as shipped it reads **73.9 h** — 35%
too high. The 90th percentile is 232 h vs. the displayed 299.7 h.

**Handling Productivity says "insufficient data (n=0)" while the optimiser is using that same
data.** The panel filters by commodity, and commodity is blank on every row, so it finds
nothing. The voyage scheduler doesn't filter, finds 1,211 rows, and derives 280 tonnes/hour —
against the port's published 1,200 t/h. That turns a 2.6-day discharge into an **11.2-day**
discharge inside the schedule, invisibly. See fault F-10.

---

## 7. Screen: Tonnage Field

**Input:** none. It loads on open (~10 s the first time).

**Output:** a "tightness" number per ship class and per ocean basin, a forward projection, and a
hidden validation section.

**What tightness is:** trailing average daily loading tonnage ÷ estimated free ship capacity. The
free-capacity figure is reconstructed from satellite port-call counts at ~130 ports, with ship
class inferred from average parcel size per call.

**What it is not:** a number of ships, or a tonnage you can charter. The orange banner says so.

**Why you can't act on it.** The screen shows four figures — Handysize 0.0123, Supramax 0.0068,
Panamax 0.0051, Capesize 0.0061 — with no scale, no history, no percentile, and no "is this high
or low." Its own Evidence Quality panel reports that when checked against real commercial
ballaster counts, the reconstruction is off by a **median factor of 7.09×**, ranging 0.35× to
25.75×, on 10 comparison points, and states "Absolute scale validated: **NO**."

The Forward Tightness chart projects 90 days by holding the recent trend flat, so it renders as
a **flat horizontal line** (Capesize p50: 0.0061 → 0.0060).

The "Run validation" button runs a 45-second A/B test asking whether adding this signal improves
the freight forecast. Its answer is **no** — so the whole screen is, by the project's own
measurement, disconnected from every recommendation the app makes.

---

## 8. Screen: Fragility

**Input:** origin, destination, cargo size, laycan. Press "Run sweep" (~19 s).

**Output:** for each of eight inputs, how far it would have to move before the recommendation
changes.

**How it works:** it re-runs the decision many times, nudging one input at a time, and binary-
searches for the point where the answer flips. Cheap inputs use a closed-form check; expensive
ones re-run the whole solver. 65 evaluations in the live run.

**Live results for 75,000 t Newcastle→Paradip:**

| Input | Result |
|---|---|
| Permissible draft | **FRAGILE** — only 0.03 m of margin before feasibility flips |
| Cargo volume | **FRAGILE** — +7,500 t flips the class from Panamax to Capesize |
| Vessel draft | Stable — no flip from 0.1 m to 30 m |
| Laycan width | Stable — no flip from 1 to 270 days |
| Risk tolerance | Stable — no flip from 0.00 to 1.00 |
| Contract term | Stable — no flip from 1 to 542 days |
| Origin wait days | **Unavailable — cannot be searched, by design** |
| Destination wait days | **Unavailable — cannot be searched, by design** |

So two of the eight levers can never produce an answer, and four more never flip anything. The
genuinely useful output is the draft margin — 3 centimetres is a real, actionable warning.

**This screen is where the internals leak worst.** The two "unavailable" findings render as
~90-word paragraphs of source code prose naming modules and functions
(`opt.quote.quote()`, `opt.fleetmix.enumerate_fleet_mix`, `opt.stopping.solve_lock_or_wait`,
`dynamic_wait_days`, `berth_truth.empirical`, `DecisionSignature`) plus internal build-phase
labels like `(P5, re-checked after P2/P3/P4)`. The four "stable" findings display
**"score 1,000,000,000,000.0"** — that is an internal sorting sentinel, not a score. The
current decision is summarised as `Config: Supramax:2:D`.

---

## 9. Screen: Ledger

Two sections.

### Live Decision Ledger

**Every quote you run is silently and permanently written here.** It is append-only by design —
there is no delete. During testing, 17 exploratory API calls became 17 identical-looking ledger
rows, all "pending", all un-removable from the UI.

To score an entry you type in what the rate actually turned out to be, later. Until someone does
that, every metric at the top — lock accuracy, mean regret, savings vs. always-lock, savings vs.
always-wait — reads "—". **At demo time this screen is structurally empty of results.** It is a
promise, not evidence.

Note "Lock accuracy" only counts LOCK decisions. If the app says WAIT every time, the metric
stays blank forever.

### Historical Model Replay

A genuine backtest over the frozen historical test period, deliberately kept separate from the
live ledger. It is behind a button because it takes roughly 20 minutes on first run.

Its disclaimer text prints a **source file name** (`run_optimizer_backtest.py`) to the end user.

---

## 10. Where every number on the desk comes from

| Number on screen | Source |
|---|---|
| Today's TC rate ($/day) | Real published time-charter average, scraped, latest 2026-08-20 |
| p10 / p50 / p90 | XGBoost prediction of index % change, applied to the rate above |
| Ceiling | Horizon-blended forecast, then adjusted down by option value (4,000 simulated paths) |
| LOCK / WAIT | today's rate vs. that ceiling |
| Expected savings, Confidence % | A **separate**, cruder 2,000-path simulation. Unseeded — changes every run. |
| Trough window | Minimum of the daily p50 curve over 90 days. Ignores your laycan. |
| $/mt (Rate Forecast) | rate × sailing days ÷ tonnes. Unit conversion only. |
| $/mt (Fleet Mix) | (rate × days × ships) + fuel, ÷ tonnes. A different quantity. |
| Sailing days | Real sea distance from a marine routing graph ÷ 13 kn assumed speed |
| Max DWT / draft / LOA / beam | **Hardcoded constants.** Not from the berth register. |
| Wait, days (desk) | Port baseline × (last 14 days' arrivals ÷ previous 60 days') |
| Empirical wait (Port Twin) | Percentiles of real arrival→berth gaps at Paradip only, inflated ~35% by duplicate rows |
| Congestion LOW/MOD/HIGH | That same multiplier, bucketed |
| Risk alerts | z-scores on index returns, port arrivals, chokepoint transits + a calendar flag |
| Fleet mix cost | (blended forecast rate × sailing days × ships) + fuel at the load port's bunker price |
| Repositioning / backhaul P(cargo) | Poisson hazard from satellite export tonnage ÷ typical parcel size |
| Tightness | Trailing loading tonnage ÷ reconstructed free capacity. Scale unvalidated (median 7× off). |
| Voyage profit | Your typed revenue − fuel − idle cost ($500/day hardcoded) − demurrage |

---

## 11. Things the backend computes and the frontend never shows

These are real outputs, already in the API response, already typed in the frontend's own type
definitions, and rendered **nowhere**:

- **The plain-English explanation of every decision.** Example from a live response:
  > *"Option value of waiting: $1,538/day (the expected benefit of being able to keep watching
  > and lock later instead of deciding only today), from 4,000 simulated market paths. That
  > option value pulls today's threshold down to $19,478/day, below the $21,016/day it would be
  > without accounting for waiting."*

  That is the single best sentence the system produces. It is invisible.
- **The exercise boundary** — the day-by-day price line that *is* the LOCK/WAIT rule.
- **The option value** in dollars.
- **The review trigger** — "re-solve weekly, or immediately if [the specific live alert]".
- **P10 / P90 savings** — the downside and upside range, not just the middle.

The problem statement asks for explainability as a deliverable. The engine produces it. The
screen drops it.
