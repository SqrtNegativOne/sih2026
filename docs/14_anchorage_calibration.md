# Anchorage satellite vs. PortWatch calibration (4.3)

Generated 2026-08-30 by `anchorage.calibrate.calibrate_all_ports()`.

This is a spot-check comparison between real Sentinel-1-derived vessel counts (`anchorage.detect`, MODEL_DERIVED) and PortWatch's real daily dry-bulk call counts (OBSERVED). It is evidence, not a replacement for the live PortWatch-derived congestion signal `opt.congestion`/`opt.risk` already use -- Sentinel-1's real revisit cadence (roughly every 4-11 days per port, see `data_builders.harvest_sentinel1`'s own PULL_NOTES.md) is far too sparse to drive a live quote.

A correlation is reported only when a port has at least 10 real paired observations (`MIN_N_FOR_CORRELATION`) -- see `anchorage.calibrate`'s own module docstring for why fewer than that is not a real finding.

## Results

| Port | n | Date range | Spearman r | Pearson r | Mean abs. diff | Finding |
|---|---|---|---|---|---|---|
| PARADIP | 0 | n/a | not reported | not reported | n/a | n=0: no census's real acquired_at date matched a real PortWatch date for this port. No correlation or difference can be reported. |
| VISAKHAPATNAM | 0 | n/a | not reported | not reported | n/a | n=0: no census's real acquired_at date matched a real PortWatch date for this port. No correlation or difference can be reported. |
| NEWCASTLE_AU | 0 | n/a | not reported | not reported | n/a | n=0: no census's real acquired_at date matched a real PortWatch date for this port. No correlation or difference can be reported. |
| HAY_POINT_AU | 0 | n/a | not reported | not reported | n/a | n=0: no census's real acquired_at date matched a real PortWatch date for this port. No correlation or difference can be reported. |
| RICHARDS_BAY_ZA | 0 | n/a | not reported | not reported | n/a | n=0: no census's real acquired_at date matched a real PortWatch date for this port. No correlation or difference can be reported. |

## Reading this table

A port with `n=0` means no real Sentinel-1 scene has been processed into a real `AnchorageCensus` for it yet -- not a failed comparison, an absent one. This module never reports a correlation below `MIN_N_FOR_CORRELATION` real paired observations, regardless of how strong or weak the few available points might look.
