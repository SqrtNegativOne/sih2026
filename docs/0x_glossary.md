# Glossary — plain language

**Voyage charter / spot**: hire a ship for ONE trip, pay $/tonne (or lumpsum), repeat forever. Owner bears fuel/port costs. This is the current state per problem statement.

**TC (Time Charter)**: rent the whole ship for a period (1–12 months) at fixed $/day. You (charterer) direct where it sails; owner covers crew/maintenance; you pay fuel + port dues. This is the target state. Key freedom: under TC YOU choose ports/routes mid-contract; under voyage charter they're fixed by contract.

**TCE (Time Charter Equivalent)**: a conversion formula that expresses ANY voyage's economics (usually **profit**) as a $/day number: (voyage revenue − fuel − port costs) ÷ days occupied. Exists so a $/tonne trip can be compared against TC daily-hire quotes. Baltic routes publish either $/t voyage assessments or TCE $/day assessments — same trade, two units.

**BDI/BCI/BPI/BSI/BHSI**: daily index values. The four sub-indices track the four vessel classes via weighted route averages; BDI composites three of them (40/30/30).

**MAPE**: Mean Absolute Percentage Error. Common accuracy metric; misleading for volatile series (huge % errors near low values). We prefer directional hit-rate + pinball loss instead.

**Decision value**: dollars saved/earned by acting on the model's advice vs a naive policy (e.g., "always lock on Jan 1"). The honest KPI for a forecasting product whose consumer makes money decisions.

**Random walk (financial series)**: a series whose best guess for tomorrow ≈ today ± noise; day-to-day direction is nearly unpredictable. Freight rates behave close to this short-term → nobody can promise low MAPE; accuracy claims should be reframed (see decision value).

**FFA (Forward Freight Agreement)**: cash-settled derivative on a Baltic index/route. Its market price = the market's collective forecast of future freight. If you can see FFA curves, you get everyone else's forecast for free.

**Laycan**: Laydays/Cancelling window — earliest/latest dates a ship may present itself to load. In the optimizer, laycans become time windows.

**COA (Contract of Affiliation/Affreightment)**: promise to move X tonnes over Y months in N voyages — the multi-voyage obligation your optimizer schedules.

**Ballast leg**: sailing empty to the next pickup. Ballast days earn nothing and cost fuel — minimizing them = the "avoid idle" requirement.

**Lookup vs forecast**: today's rates and all history are LOOKUP-able (published data). Future rates exist NOWHERE — not on any site or API. Every statement about them comes from a forecaster (our ML model) or from other people's priced opinion (FFAs). The optimizer looks up past+present; only the model speaks about the future. p_up is not a separate prediction — it's a summary read off the forecast fan.

**Coverage (contract)**: how far ahead freight is secured. NOT delivery time. A single voyage takes weeks; a period TC gives e.g. 6 months of coverage (~N voyages). "Locking" = buying coverage at a fixed $/day before the market moves.

**Period / Term**: the length of a time charter (broker speak: "short period" = up to ~12 months). Our parameter: `contract_term_months`, chosen from a menu (1/3/6/12).

**Planning horizon**: how far ahead the optimizer looks (`planning_horizon_days`). Must be >= longest contract term offered, since signing today spends money across the whole term. Distinct from contract term.

**Rolling horizon**: decisions are re-made continuously (weekly/daily) with updated forecasts — not once at contract start. Four recurring move types: lock-or-wait a new TC, schedule voyages for TC'd vessels, spot-fix overflow cargo, reposition idle vessels.

**Congestion**: ships queued at anchorage waiting for a berth. Queue length × service rate = expected delay; feeds both alerts and optimizer time windows.

**Stocks (two meanings — don't confuse!)**
1. Stock MARKETS (equities): NOT modeled in this project. Equities appear only as optional features in some papers.
2. INVENTORY stocks: coal piles at power plants / ports. These DO matter: low plant stocks → urgent imports → more vessels → rate pressure + congestion. Enter as demand-pressure features, not as separate prediction targets.

**Bunkers**: ship fuel. Price per tonne is a key voyage cost; VLSFO = Very Low Sulfur Fuel Oil is the standard.