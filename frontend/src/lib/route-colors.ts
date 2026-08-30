import type { SolverRoute } from './types'

/**
 * Assign each drawable (non-rejected) route an evenly spaced OKLCH hue at a
 * fixed lightness and chroma, so any number of routes stays mutually
 * distinguishable and colour-blind-legible. Rejected routes are not coloured
 * here -- the map draws them in the fixed risk red.
 */
export function assignRouteColors(routes: SolverRoute[]): Map<string, string> {
  const drawable = routes.filter((r) => r.status !== 'rejected')
  const n = Math.max(drawable.length, 1)
  const colors = new Map<string, string>()
  drawable.forEach((route, i) => {
    const hue = Math.round((i * 360) / n + 18) % 360
    colors.set(route.id, `oklch(0.60 0.17 ${hue})`)
  })
  return colors
}
