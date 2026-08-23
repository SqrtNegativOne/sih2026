# Sources for baltic_routes.csv

Primary (fetched 2026-08-22):
- https://help.veson.com/vesselsvalue/baltic-timeseries-definitions
  - Full official definitions + TC average weighting formulas:
    C5TC = C8_14*0.25 + C9_14*0.125 + C10_14*0.25 + C14*0.25 + C16*0.125
    P5TC = P1A_82*0.25 + P2A_82*0.10 + P3A_82*0.25 + P4_82*0.10 + P6_82*0.30
    S10TC(_58) = S1B*.05 + S1C*.05 + S2*.20 + S3*.15 + S4A*.075 + S4B*.10 + S5*.05 + S8*.15 + S9*.075 + S10*.10
    HS7TC = HS1..HS4 *.125 each + HS5*.20 + HS6*.20 + HS7*.10
  - Note: P5_82 assessed but NOT in P5TC formula; BDI = 40% C5TC + 30% P5TC + 30% S10TC (Handysize excluded since Mar 2018).
- https://www.bmti-report.com/baltic-capesize-index-bci/ (older _03/_14 route vintages, C-route history)

Open item: S15_63 definition (new Supramax route in S11TC) - not yet documented.

## Index time-series sources (pilot validated 2026-08-22)

- https://www.handybulk.com/baltic-dry-index
  - Daily BDI + BCI/BPI/BSI/BHSI index values AND per-class average TC earnings ($/day) in parseable prose.
  - Monthly archive pages back to 2016 (with gaps): /baltic-dry-index/{year}/{month}/
  - Weekly broker reports embedded with route-level commentary (C3, C5 $/t levels) and anecdotal fixtures incl. India-relevant ones.
  - Pilot file: raw/pilot_index_levels.csv (14 trading days parsed).
- Cross-validation: HandyBulk values match investing.com .com BADI exactly on overlapping dates (Aug 3-20, 2026). Regional investing.com mirrors are stale/unreliable - do not use.
- Route-level weekly numbers: Signal Group "weekly dry market monitor" articles republished on HSN (e.g. 'Panamax Freight view - Indonesia Thermal Coal to India') carry C3/C17/P7/P8/S4A/HS4/C5/P3A/P5/S2/S10/HS5-7 weekly values + Indian Ocean ballaster counts.

