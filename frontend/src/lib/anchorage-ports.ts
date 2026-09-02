import type { AnchoragePortCode, PortCode } from '@/lib/types'

// anchorage.detect's five ports use their own label space (VISAKHAPATNAM,
// RICHARDS_BAY_ZA), not opt.network.PortEnum's (VIZAG, RICHARDS_BAY) --
// same physical ports, different ids (see data_builders.harvest_sentinel1's
// own docstring for why HAY_POINT_AU has no PortEnum entry to map at all).
// This is the one place that correspondence is declared.
//
// Split out of anchorage-panel.tsx: a .tsx file that exports a plain
// function alongside its component breaks React Fast Refresh's HMR
// boundary detection (Vite falls back to a full module invalidation on
// every edit instead of patching the component in place -- visible live in
// this session's own vite dev-server log as repeated "Could not Fast
// Refresh (anchoragePortForQuotePort export is incompatible)" warnings). A
// plain .ts file has no such boundary to break.
export const PORT_CODE_TO_ANCHORAGE_PORT: Partial<Record<PortCode, AnchoragePortCode>> = {
  PARADIP: 'PARADIP',
  VIZAG: 'VISAKHAPATNAM',
  NEWCASTLE_AU: 'NEWCASTLE_AU',
  RICHARDS_BAY: 'RICHARDS_BAY_ZA',
}

/** The covered anchorage port for a quote's origin/dest PortCode, or null
 * when neither this system's port network nor Sentinel-1's five-port
 * coverage overlap for it. */
export function anchoragePortForQuotePort(code: PortCode): AnchoragePortCode | null {
  return PORT_CODE_TO_ANCHORAGE_PORT[code] ?? null
}
