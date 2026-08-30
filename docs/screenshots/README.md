# Screenshots

Real captures from the running system (backend on :8000, frontend on :5173),
taken 2026-08-30. Every number visible in these is real output from real data
on disk -- none are mockups.

| File | What it shows |
|---|---|
| `01_voyage_desk_verdict_and_route.png` | The Voyage Desk's top half: the LOCK/WAIT verdict with its own reasoning, the rate-forecast fan, the live risk feed, and the route map -- all in one frame. A real Hampton Roads -> Paradip quote. |
| `02_voyage_desk_lower_panels.png` | The lower half: fleet-mix frontier with real rejection reasons, port constraints, landed cost with per-component provenance, and the Exposure row (carbon rating, chokepoint fracture, satellite anchorage census). |
| `03_sentinel1_paradip_cfar_detections.png` | The real Sentinel-1 SAR scene over Paradip anchorage (2026-08-28), log-scaled, with red circles at each of the 9 real CFAR detections. The Paradip coastline and Mahanadi delta are clearly visible. **Caveat:** this run used no land mask, so 3 of the 9 circles sit at the coastline and may be port infrastructure rather than anchored vessels -- see `anchorage.detect.apply_land_mask`'s own docstring. |

Note on `02`: the Anchorage Census panel shows a real result (9 vessels,
LOW CONFIDENCE, "observed 2 days ago") because a real scene had been
processed. The LOW CONFIDENCE badge is correct and automatic -- that scene
has genuinely high background clutter.
