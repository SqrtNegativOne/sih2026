Problem statement: build a forecasting model that tells a logistic manager when to charter a ship, what size ship to use, and how to avoid it sitting idle, for bulk cargo (like coal) coming into India's East Coast.

- Current state: spot contracts. Hire a ship per single voyage at whatever the market rate is that day, repeated over and over.
- Target state: period contracts: lock a ship into a short/medium-term deal convering multiple voyages, at a rate fixed in advance.

Fixed parameters:
- Cargo: bulk.
- Origin countries: Australia, US, Mozambique, Indonesia. (maybe Russia; only mentioned once; dropped by accident?)
- Destination ports: Paradip, Vizag, Gangavaram, Gopalpur, Dhamra, Sagar-Sandheads, Haldia (only these 7 named; could be more?)
- Vessel classes: Handysize, Supramax, Panamax, Capesize (exact)
- Port constraints: draft, LOA, beam, berthing limits, cargo handling rates, cargo handling capacities, maximum permissible vessel size, turnaround time. (exact)

Data needed for the model:
- Historical freight rates, broken down by vessel size × route
- Macro indicators: global economic indicators, commodity price trends
- Seasonal demand/supply patterns
- Real-time port congestion, both origin and destination
- Port infra specs for all the ports above (same draft/LOA/beam/handling data)

Modeling approach: ML/AI, time-series forecasting and regression.

Required outputs: given cargo parcels/volumne (provide parcel-level specificity?), origin-destination pair, desired contract duration, return:
- *multiple* voyages, with details:
	- best timing to lock in a short/mid-term charter
	- recommended vessel type
	- early alerts for market volatility, port congestions, or other disruptions
	- forecast slow periods, suggest repositioning, alternate cargo, or alternate employment opportunities to cut deadheading

- cool UI dashboard, "high degree of accuracy"
- deadboat/demise charters outside the scope?