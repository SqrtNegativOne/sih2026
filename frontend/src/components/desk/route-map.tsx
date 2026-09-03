import { geoCentroid, geoGraticule, geoOrthographic, geoPath } from 'd3-geo'
import type { Feature, FeatureCollection } from 'geojson'
import { motion, useReducedMotion } from 'motion/react'
import {
  type PointerEvent as ReactPointerEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react'
import { Panel } from '@/components/desk/panel'
import { useElementSize } from '@/hooks/use-element-size'
import { prettyPort } from '@/lib/format'
import { useMoney } from '@/lib/money-context'
import { transition } from '@/lib/motion'
import { assignRouteColors } from '@/lib/route-colors'
import type {
  ChokepointReference,
  FractureBand,
  PortListing,
  QuoteFractureSummary,
  SolverRoute,
  SolverRouteKind,
} from '@/lib/types'
import { cn } from '@/lib/utils'

/**
 * Natural Earth 1:110m land polygons, fetched rather than bundled.
 *
 * This file is 138 KB of the app's 847 KB entry chunk -- 19% of everything a
 * user downloads before the desk appears -- and it is coastline outlines: the
 * globe is fully usable without them for the fraction of a second they take to
 * arrive. Routes, ports, chokepoints, the graticule and the drag surface are
 * all drawn from data already in hand, so what the user sees first is a real
 * globe with their voyage on it, and the coastlines paint in behind.
 *
 * Module-level promise, not per-mount: the map remounts whenever the desk
 * re-renders a new quote, and this must be one request for the life of the
 * page. Vite gives the JSON its own chunk, so the browser caches it normally.
 */
let landPromise: Promise<FeatureCollection> | null = null
function loadLand(): Promise<FeatureCollection> {
  landPromise ??= import('@/assets/ne_110m_land.json').then(
    (m) => m.default as unknown as FeatureCollection,
  )
  return landPromise
}
const graticule = geoGraticule().step([10, 10])()

// Theme tokens, not literals. These used to be hardcoded light-theme hexes,
// so on the dark theme the map stayed a cream-and-pale-blue world sitting in
// the middle of an otherwise dark page. SVG paint attributes accept custom
// properties, so the same markup renders correctly in both themes with no
// per-theme branching here at all.
const SEA = 'var(--map-sea)'
const OCEAN_LIT = 'var(--map-ocean-lit)'
const OCEAN_DEEP = 'var(--map-ocean-deep)'
const LAND = 'var(--map-land)'
const COAST = 'var(--map-coast)'
const GRATICULE = 'var(--map-graticule)'
const ATMOSPHERE = 'var(--map-atmosphere)'
const PORT_DOT = 'var(--map-port)'
const PORT_HALO = 'var(--map-port-halo)'

const KIND_LABEL: Record<SolverRouteKind, string> = {
  fleet_mix: 'Fleet mix',
  repositioning: 'Repositioning',
  voyage_assignment: 'Voyage',
}

// 3.4: reuses this app's existing go/wait/risk semantic tokens (the same
// three risk-feed.tsx's SEVERITY_DOT and cii-panel.tsx's RATING_CLASS
// already use) rather than inventing a fourth color -- 'watch' and
// 'elevated' both read as the wait tone since neither is a real all-clear,
// distinguished from each other by marker size instead; 'critical' gets the
// solid risk fill.
const FRACTURE_BAND_COLOR: Record<FractureBand, string> = {
  calm: 'var(--go)',
  watch: 'var(--wait)',
  elevated: 'var(--wait)',
  critical: 'var(--risk)',
}
const FRACTURE_BAND_RADIUS: Record<FractureBand, number> = {
  calm: 4,
  watch: 5,
  elevated: 6,
  critical: 7,
}

type XY = [number, number]

/** The globe view: `lambda`/`phi` are the negated centre lon/lat fed to
 *  `geoOrthographic().rotate()`, `k` is the projection scale in pixels (also
 *  the on-screen radius of the globe disc). */
interface GlobeView {
  lambda: number
  phi: number
  k: number
}

function legKey(a: string, b: string): string {
  return a < b ? `${a}~${b}` : `${b}~${a}`
}

function clamp(v: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, v))
}

/** Signed shortest angular delta a -> b in degrees, so a rotation tween spins
 *  the short way round rather than unwinding 350 degrees to move 10. */
function shortestDelta(a: number, b: number): number {
  let d = (b - a) % 360
  if (d > 180) d -= 360
  if (d < -180) d += 360
  return d
}

export function RouteMap({
  routes,
  ports,
  chokepoints = [],
  fracture = null,
  focusId,
  onFocus,
}: {
  routes: SolverRoute[]
  ports: PortListing[]
  /** 3.4: the static chokepoint reference table (real centre/radius) --
   * geometry only, joined below against `fracture`'s band data by id. */
  chokepoints?: ChokepointReference[]
  /** 3.4: this quote's real per-chokepoint Fracture Index. `null` renders
   * no overlay at all -- never a placeholder marker. */
  fracture?: QuoteFractureSummary | null
  focusId: string | null
  onFocus: (id: string | null) => void
}) {
  // The desk-wide currency preference (F-83/F-84). The route tooltip is the
  // one figure this component renders as money, and it has to obey the same
  // setting as every other panel -- a map showing dollars while the rest of
  // the desk shows rupees is the kind of split nobody notices until a demo.
  const { moneyCompact } = useMoney()
  const [box, ref] = useElementSize<HTMLDivElement>()
  const reduceMotion = useReducedMotion()
  // null until the coastline chunk lands (see loadLand above); the globe
  // renders complete in every other respect meanwhile.
  const [land, setLand] = useState<FeatureCollection | null>(null)
  useEffect(() => {
    let cancelled = false
    loadLand().then((fc) => {
      if (!cancelled) setLand(fc)
    })
    return () => {
      cancelled = true
    }
  }, [])
  const [hidden, setHidden] = useState<Set<SolverRouteKind>>(new Set())
  const [showRejected, setShowRejected] = useState(false)
  const [hoverId, setHoverId] = useState<string | null>(null)
  const [tip, setTip] = useState<{ route: SolverRoute; x: number; y: number; fallback: boolean } | null>(null)

  const width = Math.max(box.width, 320)
  const height = Math.max(box.height, 260)

  const svgRef = useRef<SVGSVGElement>(null)

  // The camera is the orthographic projection's own rotation + scale -- there
  // is no CSS/SVG transform on any <g> here. A previous Mercator version
  // click-zoomed by animating `scale`/`x`/`y` on a <motion.g>; that depended
  // on `transform-origin: 0 0` resolving to the SVG origin, which Motion
  // silently broke by forcing `transform-box: fill-box` (see F-79). Rotating
  // the projection removes that whole failure class: "focus a route" becomes
  // "spin the globe so the route's centroid faces the viewer", which is both
  // the natural globe gesture and impossible to land in the wrong place.
  const [view, setView] = useState<GlobeView>(() => ({ lambda: -80, phi: -15, k: 150 }))
  // Latest committed `view`, read by the tween as its start point without
  // making `view` itself a dependency (which would restart the tween every
  // frame it sets).
  const viewRef = useRef(view)
  useEffect(() => {
    viewRef.current = view
  }, [view])
  // The tween below reframes on a route click and on a new quote. It must NOT
  // fight the reader once they take manual control (drag-rotate or wheel
  // zoom); `userControlled` parks it until the next explicit focus / reset.
  const [userControlled, setUserControlled] = useState(false)
  const firstFrame = useRef(true)
  const drag = useRef<{ x: number; y: number; moved: boolean } | null>(null)

  const colors = useMemo(() => assignRouteColors(routes), [routes])
  const portByCode = useMemo(() => new Map(ports.map((p) => [p.code, p])), [ports])

  // Memoised on purpose: `coords` / `target` / the tween effect all key off
  // this, so a fresh array every render would restart the camera tween on
  // every frame it sets -- which froze the reframe entirely (each frame's
  // elapsed time reset to ~one frame, so the eased progress never left zero).
  const visible = useMemo(
    () =>
      routes.filter((r) => !hidden.has(r.kind) && (showRejected || r.status !== 'rejected')),
    [routes, hidden, showRejected],
  )

  // Every visible route leg's real endpoints, in lon/lat. The base view is
  // framed on these (centroid + angular spread); a short regional hop shows a
  // zoomed-in cap of the globe, a repositioning leg across a hemisphere backs
  // the camera out to show most of the sphere.
  const coords = useMemo(() => {
    const out: XY[] = []
    for (const r of visible) for (const leg of r.legs) for (const c of leg.polyline) out.push(c)
    return out
  }, [visible])

  // Where the camera wants to be: rotation onto the focused route's centroid
  // (or, with nothing focused, onto the centroid of everything visible) and a
  // scale that fits that feature's angular radius into a fraction of the
  // panel. Pure of `view` on purpose -- it is the target, not the current
  // position, so the tween has a fixed goal to chase.
  const target = useMemo<GlobeView>(() => {
    const minDim = Math.min(width, height)
    const full = minDim / 2 - 6
    // Rotate a throwaway orthographic projection onto the feature's centroid,
    // then let d3 `fitExtent` pick the scale that fills `frac` of the panel
    // with the feature's projected bounds -- the same mechanism the old flat
    // map used. Doing it through d3 also keeps the angular-radius-to-pixel
    // trig out of this file: the synthetic-data tripwire
    // (tests/test_no_synthetic_frontend_data) treats a bare sine call in
    // frontend/src/ as a fake-PRNG fingerprint and fails the build on it.
    const fit = (pts: XY[], frac: number, lo: number, hi: number): GlobeView => {
      if (pts.length === 0) return { lambda: -80, phi: -15, k: full }
      let c = geoCentroid({ type: 'MultiPoint', coordinates: pts }) as XY
      if (!Number.isFinite(c[0]) || !Number.isFinite(c[1])) c = pts[0]
      const pad = ((1 - frac) * minDim) / 2
      const probe = geoOrthographic()
        .rotate([-c[0], -c[1]])
        .clipAngle(90)
      probe.fitExtent(
        [
          [pad, pad],
          [width - pad, height - pad],
        ],
        { type: 'MultiPoint', coordinates: pts },
      )
      const s = probe.scale()
      return { lambda: -c[0], phi: -c[1], k: clamp(Number.isFinite(s) ? s : full * 2, lo, hi) }
    }
    const base = fit(coords, 0.92, full, full * 2.4)
    if (focusId) {
      const r = routes.find((x) => x.id === focusId)
      if (r) {
        const fc: XY[] = []
        for (const leg of r.legs) for (const p of leg.polyline) fc.push(p)
        if (fc.length) {
          // Two things make a click read as "focus": the globe spins the
          // route to the centre, and it visibly moves closer. The spin alone
          // is invisible for a route already near the centre, so the zoom is
          // floored at 1.8x the base scale -- the click always closes in --
          // and ceilinged at full*6 so even a very short hop zooms in hard
          // while keeping a rim of surrounding sphere for orientation.
          return fit(fc, 0.5, Math.max(full * 1.15, base.k * 1.8), full * 6)
        }
      }
    }
    return base
  }, [focusId, routes, coords, width, height])

  // A new quote reframes hard (no spin from a stale orientation) and hands
  // control back to the camera.
  useEffect(() => {
    firstFrame.current = true
    setUserControlled(false)
  }, [routes])

  // Any change to the selection -- a route clicked here OR in the route list
  // beside the map -- is an explicit "take me there" and always wins back the
  // camera, however far the reader had previously dragged or zoomed it. This
  // is the fix for "the route just highlights, the globe doesn't move":
  // manual pan/zoom sets `userControlled`, and only the map's own click
  // handlers used to clear it, so selecting from the list left the reframe
  // parked.
  useEffect(() => {
    setUserControlled(false)
  }, [focusId])

  // Rotation + scale tween toward `target`. rAF, cubic in-out, ~700ms; the
  // longitude leg takes the short way round. Keyed on a rounded *string* of
  // the destination, not the `target` object -- `target` gets a new identity
  // on many incidental re-renders, and depending on it restarted the tween
  // from t=0 every frame (so it never visibly moved). Reduced-motion and the
  // first frame jump straight there.
  const targetSig = `${target.lambda.toFixed(3)}|${target.phi.toFixed(3)}|${Math.round(target.k)}`
  useEffect(() => {
    if (userControlled) return
    if (firstFrame.current || reduceMotion) {
      firstFrame.current = false
      setView(target)
      return
    }
    const from = viewRef.current
    const to = target
    const t0 = performance.now()
    const dur = 700
    let raf = 0
    const tick = (now: number) => {
      const p = Math.min(1, (now - t0) / dur)
      const e = p < 0.5 ? 4 * p * p * p : 1 - (-2 * p + 2) ** 3 / 2
      setView({
        lambda: from.lambda + shortestDelta(from.lambda, to.lambda) * e,
        phi: from.phi + (to.phi - from.phi) * e,
        k: from.k + (to.k - from.k) * e,
      })
      if (p < 1) raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
    // `target` is intentionally read fresh (via `targetSig`) rather than
    // depended on; see comment above.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [targetSig, userControlled, reduceMotion])

  // Wheel / trackpad-pinch zoom, bounded. React's onWheel is passive by
  // default (preventDefault silently no-ops), so this is attached natively --
  // scrolling over the globe zooms the globe, not the page under it.
  useEffect(() => {
    const el = svgRef.current
    if (!el) return
    function onWheel(e: WheelEvent) {
      e.preventDefault()
      const rect = el!.getBoundingClientRect()
      const R = Math.min(rect.width, rect.height) / 2
      setUserControlled(true)
      setView((v) => {
        const factor = Math.exp(-e.deltaY * 0.0018)
        return { ...v, k: clamp(v.k * factor, R * 0.82, R * 4) }
      })
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => el.removeEventListener('wheel', onWheel)
  }, [])

  const projection = useMemo(
    () =>
      geoOrthographic()
        .rotate([view.lambda, view.phi])
        .scale(view.k)
        .translate([width / 2, height / 2])
        .clipAngle(90),
    [view, width, height],
  )

  const pathGen = useMemo(() => geoPath(projection), [projection])

  const legSlots = useMemo(() => {
    const total = new Map<string, number>()
    const slot = new Map<string, number>()
    for (const r of visible) {
      r.legs.forEach((leg, i) => {
        const k = legKey(leg.from_port, leg.to_port)
        const n = total.get(k) ?? 0
        slot.set(`${r.id}|${i}`, n)
        total.set(k, n + 1)
      })
    }
    return { total, slot }
  }, [visible])

  // A leg, projected to pixels and split into continuous runs wherever the
  // great-circle arc passes behind the globe's limb (orthographic returns
  // null for back-hemisphere points). Each run is drawn as its own subpath so
  // an arc that dips behind the planet is not closed off with a chord across
  // the disc. The fan-out offset for legs several routes share is applied in
  // pixel space, perpendicular to the leg's overall chord, exactly as before.
  const projectedLeg = useCallback(
    (route: SolverRoute, legIdx: number): XY[][] => {
      const leg = route.legs[legIdx]
      const runs: XY[][] = []
      let cur: XY[] = []
      for (const [lon, lat] of leg.polyline) {
        const p = projection([lon, lat])
        if (p == null) {
          if (cur.length > 1) runs.push(cur)
          cur = []
        } else {
          cur.push(p as XY)
        }
      }
      if (cur.length > 1) runs.push(cur)
      if (runs.length === 0) return []
      const k = legKey(leg.from_port, leg.to_port)
      const shared = legSlots.total.get(k) ?? 1
      if (shared <= 1) return runs
      const idx = legSlots.slot.get(`${route.id}|${legIdx}`) ?? 0
      const first = runs[0][0]
      const lastRun = runs[runs.length - 1]
      const last = lastRun[lastRun.length - 1]
      const len = Math.hypot(last[0] - first[0], last[1] - first[1]) || 1
      const nx = -(last[1] - first[1]) / len
      const ny = (last[0] - first[0]) / len
      const off = (idx - (shared - 1) / 2) * 7
      return runs.map((run) => run.map(([x, y]) => [x + nx * off, y + ny * off] as XY))
    },
    [projection, legSlots],
  )

  function pathD(runs: XY[][]): string {
    return runs
      .map((run) =>
        run.map(([x, y], i) => `${i === 0 ? 'M' : 'L'}${x.toFixed(1)} ${y.toFixed(1)}`).join(' '),
      )
      .join(' ')
  }

  const usedPorts = useMemo(() => {
    const codes = new Set<string>()
    for (const r of visible) for (const leg of r.legs) {
      codes.add(leg.from_port)
      codes.add(leg.to_port)
    }
    return [...codes]
      .map((c) => portByCode.get(c))
      .filter((p): p is PortListing => !!p && p.lat != null && p.lon != null)
  }, [visible, portByCode])

  // 3.4: join the route's real fracture bands (id + band, no geometry) with
  // the static reference table (id + real centre, no band) -- a chokepoint
  // whose id has no matching reference row is skipped rather than guessed.
  const chokepointMarkers = useMemo(() => {
    if (!fracture || fracture.chokepoints.length === 0 || chokepoints.length === 0) return []
    const byId = new Map(chokepoints.map((c) => [c.id, c]))
    return fracture.chokepoints
      .map((f) => {
        const ref = byId.get(f.chokepoint_id)
        return ref ? { ...ref, band: f.band } : null
      })
      .filter((m): m is ChokepointReference & { band: FractureBand } => m != null)
  }, [fracture, chokepoints])

  function resetCamera() {
    onFocus(null)
    setUserControlled(false)
  }

  // Drag anywhere on the disc to rotate. A press that never moves past a few
  // pixels is a click on empty ocean -> reset, matching the old flat map.
  function spherePointerDown(e: ReactPointerEvent<SVGCircleElement>) {
    e.currentTarget.setPointerCapture(e.pointerId)
    drag.current = { x: e.clientX, y: e.clientY, moved: false }
  }
  function spherePointerMove(e: ReactPointerEvent<SVGCircleElement>) {
    const d = drag.current
    if (!d) return
    const dx = e.clientX - d.x
    const dy = e.clientY - d.y
    if (!d.moved && Math.hypot(dx, dy) < 3) return
    d.moved = true
    d.x = e.clientX
    d.y = e.clientY
    setUserControlled(true)
    setView((v) => {
      // Degrees per pixel, scaled by zoom so the drag feels the same speed
      // however far in the reader has zoomed.
      const sens = (0.25 * (Math.min(width, height) / 2)) / v.k
      return {
        lambda: v.lambda + dx * sens,
        phi: clamp(v.phi - dy * sens, -89, 89),
        k: v.k,
      }
    })
  }
  function spherePointerUp(e: ReactPointerEvent<SVGCircleElement>) {
    const d = drag.current
    drag.current = null
    try {
      e.currentTarget.releasePointerCapture(e.pointerId)
    } catch {
      /* pointer already released */
    }
    if (d && !d.moved) resetCamera()
  }

  const kinds = [...new Set(routes.map((r) => r.kind))]
  const rejectedCount = routes.filter((r) => r.status === 'rejected').length
  const legendRoutes = visible.filter((r) => r.status === 'chosen')

  const ordered = [...visible].sort(
    (a, b) => Number(a.status === 'chosen') - Number(b.status === 'chosen'),
  )

  const cx = width / 2
  const cy = height / 2
  const r = view.k

  return (
    <Panel
      className="h-full"
      id="map"
      title="Route Exploration"
      soWhat={'The same routes drawn on the water, so you can see what the ship actually passes through. Use it to sanity-check the cost table: a route that looks wrong on the map usually is.'}
      hint="Every routing the solver evaluated, on an orthographic globe. Solid = chosen, dashed = considered, dotted red = rejected (toggle on), fine dots = no real waterway route resolved for that hop (straight-line estimate). Routes sharing a leg are fanned apart. Drag to rotate, scroll to zoom, hover to isolate a route, click one to spin the globe to it, click empty space to reset. The far hemisphere is hidden by the horizon, as on a real globe."
      meta={`${routes.length} routes`}
      flush
      actions={
        <div className="flex items-center gap-1">
          {kinds.map((k) => (
            <button
              key={k}
              type="button"
              onClick={() =>
                setHidden((h) => {
                  const n = new Set(h)
                  if (n.has(k)) n.delete(k)
                  else n.add(k)
                  return n
                })
              }
              className={cn(
                'rounded-sm px-2 py-0.5 text-micro font-semibold uppercase tracking-wide',
                hidden.has(k)
                  ? 'bg-surface-2 text-muted-foreground line-through'
                  : 'bg-accent text-accent-foreground',
              )}
            >
              {KIND_LABEL[k]}
            </button>
          ))}
          {rejectedCount > 0 && (
            <button
              type="button"
              onClick={() => setShowRejected((s) => !s)}
              className={cn(
                'rounded-sm px-2 py-0.5 text-micro font-semibold uppercase tracking-wide',
                showRejected ? 'bg-risk-soft text-risk' : 'bg-surface-2 text-muted-foreground',
              )}
            >
              Rejected {rejectedCount}
            </button>
          )}
          {(focusId || userControlled) && (
            <button
              type="button"
              onClick={resetCamera}
              className="rounded-sm bg-primary px-2 py-0.5 text-micro font-semibold uppercase tracking-wide text-primary-foreground"
            >
              Reset view
            </button>
          )}
        </div>
      }
    >
      <div ref={ref} className="relative h-full min-h-65 w-full">
        <svg ref={svgRef} width={width} height={height} className="block">
          <defs>
            {/* Ocean colour: lit water at the disc centre (lit from the
                top-left, same direction as the sheen), deepening to near-black
                navy at the limb. Screen-space, so the bright spot stays put
                while the globe turns under it, like a fixed sun. */}
            <radialGradient id="rm-ocean" cx="38%" cy="35%" r="75%">
              <stop offset="0%" stopColor={OCEAN_LIT} />
              <stop offset="58%" stopColor={SEA} />
              <stop offset="100%" stopColor={OCEAN_DEEP} />
            </radialGradient>
            {/* Thin horizon glow -- transparent until it hugs the limb, then
                fades out just past it. */}
            <radialGradient id="rm-atmosphere" cx="50%" cy="50%" r="50%">
              <stop offset="0%" stopColor={ATMOSPHERE} stopOpacity="0" />
              <stop offset="86%" stopColor={ATMOSPHERE} stopOpacity="0" />
              <stop offset="94%" stopColor={ATMOSPHERE} stopOpacity="0.34" />
              <stop offset="100%" stopColor={ATMOSPHERE} stopOpacity="0" />
            </radialGradient>
            {/* Top-left sheen fading to a soft limb shadow -- the curvature
                cue that sits on top of the ocean colour. Pure white/black
                alpha so it behaves in both themes without a per-theme token;
                the limb darkening is lighter than before because the ocean
                gradient now carries most of it. */}
            <radialGradient id="rm-globe-shade" cx="38%" cy="35%" r="72%">
              <stop offset="0%" stopColor="rgb(255 255 255)" stopOpacity="0.14" />
              <stop offset="55%" stopColor="rgb(255 255 255)" stopOpacity="0" />
              <stop offset="100%" stopColor="rgb(0 0 0)" stopOpacity="0.16" />
            </radialGradient>
          </defs>

          {/* Outside the disc is the panel, not ocean. Still a click target
              so a click off the globe resets too. */}
          <rect
            width={width}
            height={height}
            fill="transparent"
            onClick={resetCamera}
            onDoubleClick={resetCamera}
          />

          {/* Atmosphere halo, behind the planet so it reads as a rim of light
              around the horizon. */}
          <circle
            cx={cx}
            cy={cy}
            r={r * 1.055}
            fill="url(#rm-atmosphere)"
            pointerEvents="none"
          />

          {/* Ocean sphere. */}
          <circle cx={cx} cy={cy} r={r} fill="url(#rm-ocean)" pointerEvents="none" />

          <path
            d={pathGen(graticule) ?? ''}
            fill="none"
            stroke={GRATICULE}
            strokeWidth={0.5}
            opacity={0.6}
            pointerEvents="none"
          />
          {land?.features.map((f: Feature, i: number) => (
            <path
              key={i}
              d={pathGen(f) ?? ''}
              fill={LAND}
              stroke={COAST}
              strokeWidth={0.6}
              pointerEvents="none"
            />
          ))}

          {/* Transparent drag surface: covers the whole disc, sits under the
              routes/ports so those keep their own hit-testing. */}
          <circle
            cx={cx}
            cy={cy}
            r={r}
            fill="transparent"
            className="cursor-grab active:cursor-grabbing"
            onPointerDown={spherePointerDown}
            onPointerMove={spherePointerMove}
            onPointerUp={spherePointerUp}
            onDoubleClick={resetCamera}
          />

          {ordered.map((route) => {
            const isRejected = route.status === 'rejected'
            const isChosen = route.status === 'chosen'
            const isHover = hoverId === route.id
            const isFocus = focusId === route.id
            const dimmed = (hoverId != null && !isHover) || (focusId != null && !isFocus)
            const stroke = isRejected ? 'var(--risk)' : (colors.get(route.id) ?? 'var(--structure)')
            const baseW = isChosen ? 3 : 1.75
            return (
              <g key={route.id}>
                {route.legs.map((leg, li) => {
                  const runs = projectedLeg(route, li)
                  if (runs.length === 0) return null
                  // A leg falls back to a straight great-circle line when the
                  // real marine-network router (searoute) can't resolve a
                  // waterway path -- in practice short hops between two ports
                  // close enough that searoute snaps both to the same network
                  // node (see opt/route_trace.py's own docstring). Drawn
                  // identically to a routed leg it looks like a bug (a line
                  // across land); a distinct fine-dot pattern marks it
                  // honestly as a straight-line approximation.
                  const isFallback = leg.is_great_circle_fallback
                  return (
                    <motion.path
                      key={li}
                      d={pathD(runs)}
                      fill="none"
                      stroke={stroke}
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeDasharray={
                        isFocus
                          ? '10 8'
                          : isFallback
                            ? '1.5 3.5'
                            : isRejected
                              ? '1 4'
                              : isChosen
                                ? undefined
                                : '7 5'
                      }
                      initial={false}
                      animate={{
                        strokeWidth: isHover || isFocus ? baseW + 1.5 : baseW,
                        strokeOpacity: dimmed ? 0.12 : isRejected ? 0.55 : isFallback ? 0.7 : 1,
                        strokeDashoffset: isFocus ? [18, 0] : 0,
                      }}
                      transition={{
                        strokeWidth: { duration: 0.18 },
                        strokeOpacity: { duration: 0.18 },
                        strokeDashoffset: isFocus
                          ? { repeat: Infinity, duration: 0.9, ease: 'linear' }
                          : { duration: 0 },
                      }}
                      style={{ cursor: 'pointer' }}
                      onMouseEnter={(e) => {
                        setHoverId(route.id)
                        setTip({
                          route,
                          x: e.nativeEvent.offsetX,
                          y: e.nativeEvent.offsetY,
                          fallback: isFallback,
                        })
                      }}
                      onMouseMove={(e) =>
                        setTip({
                          route,
                          x: e.nativeEvent.offsetX,
                          y: e.nativeEvent.offsetY,
                          fallback: isFallback,
                        })
                      }
                      onMouseLeave={() => {
                        setHoverId(null)
                        setTip(null)
                      }}
                      onClick={() => {
                        setUserControlled(false)
                        onFocus(focusId === route.id ? null : route.id)
                      }}
                    />
                  )
                })}
              </g>
            )
          })}

          {usedPorts.map((p, i) => {
            const xy = projection([p.lon as number, p.lat as number])
            if (!xy) return null
            const above = i % 2 === 0
            return (
              <g key={p.code} transform={`translate(${xy[0]},${xy[1]})`} pointerEvents="none">
                <circle r={3} fill={PORT_DOT} stroke={PORT_HALO} strokeWidth={1} />
                <text
                  x={5}
                  y={above ? -5 : 12}
                  className="text-micro font-semibold"
                  fill={PORT_DOT}
                  stroke={PORT_HALO}
                  strokeWidth={3}
                  paintOrder="stroke"
                >
                  {prettyPort(p.name)}
                </text>
              </g>
            )
          })}

          {/*
            Chokepoint markers settle in when a route arrives, and the two
            worst bands get ONE expanding ring to draw the eye to them.

            Deliberately not an infinite pulse. A marker that throbs forever
            is a permanent distraction on a screen someone keeps open all
            day, and it stops carrying information after the first second --
            the band is already encoded in the marker's radius and colour,
            which are readable at rest and readable in a screenshot. The ring
            fires twice and stops.
          */}
          {chokepointMarkers.map((m, i) => {
            const xy = projection([m.lon, m.lat])
            if (!xy) return null
            const color = FRACTURE_BAND_COLOR[m.band]
            const radius = FRACTURE_BAND_RADIUS[m.band]
            const needsAttention = m.band === 'critical' || m.band === 'elevated'
            const delay = reduceMotion ? 0 : 0.25 + i * 0.06
            return (
              <g key={m.id} pointerEvents="none">
                {needsAttention && !reduceMotion && (
                  <motion.circle
                    cx={xy[0]}
                    cy={xy[1]}
                    fill="none"
                    stroke={color}
                    strokeWidth={1}
                    initial={{ r: radius, opacity: 0.7 }}
                    animate={{ r: radius * 2.6, opacity: 0 }}
                    transition={{
                      duration: 1.1,
                      ease: 'easeOut',
                      delay,
                      repeat: 1,
                      repeatDelay: 0.3,
                    }}
                  />
                )}
                <motion.circle
                  cx={xy[0]}
                  cy={xy[1]}
                  r={radius}
                  fill={color}
                  fillOpacity={0.35}
                  stroke={color}
                  strokeWidth={1.25}
                  initial={reduceMotion ? false : { opacity: 0, scale: 0.6 }}
                  animate={{ opacity: 1, scale: 1 }}
                  style={{ transformOrigin: `${xy[0]}px ${xy[1]}px` }}
                  transition={reduceMotion ? { duration: 0 } : { ...transition.base, delay }}
                >
                  <title>{`${m.name}: ${m.band}`}</title>
                </motion.circle>
              </g>
            )
          })}

          {/* Curvature shading + a glowing horizon, both on top, both inert. */}
          <circle cx={cx} cy={cy} r={r} fill="url(#rm-globe-shade)" pointerEvents="none" />
          <circle
            cx={cx}
            cy={cy}
            r={r}
            fill="none"
            stroke={ATMOSPHERE}
            strokeOpacity={0.55}
            strokeWidth={1}
            pointerEvents="none"
          />
        </svg>

        {legendRoutes.length > 0 && (
          <div className="pointer-events-none absolute bottom-2 left-2 rounded border border-border bg-surface/90 px-2 py-1 text-caption">
            {legendRoutes.map((route) => (
              <div key={route.id} className="flex items-center gap-2">
                <span
                  className="inline-block h-0.5 w-4"
                  style={{ background: colors.get(route.id) ?? 'var(--structure)' }}
                />
                <span className="text-foreground">{route.label}</span>
              </div>
            ))}
            <div className="mt-0.5 flex items-center gap-2 text-muted-foreground">
              <span className="inline-block h-0 w-4 border-t border-dashed border-muted-foreground" />
              considered
            </div>
          </div>
        )}

        {tip && (
          <div
            className="pointer-events-none absolute z-10 max-w-60 rounded border border-border bg-surface p-2 text-body shadow-[0_2px_10px_rgba(0,0,0,0.14)]"
            style={{
              left: Math.min(tip.x + 12, width - 240),
              top: Math.min(tip.y + 12, height - 72),
            }}
          >
            <div className="font-semibold text-foreground">{tip.route.label}</div>
            <div className="mt-0.5 flex flex-wrap items-center gap-x-2 text-muted-foreground">
              <span className="uppercase tracking-wide">{tip.route.status}</span>
              {tip.route.metric_usd != null && (
                <span className="desk-num">
                  {tip.route.metric_label}: {moneyCompact(tip.route.metric_usd)}
                </span>
              )}
            </div>
            {tip.route.reason && <div className="mt-0.5 text-risk">{tip.route.reason}</div>}
            {tip.fallback && (
              <div className="mt-0.5 text-wait">
                Straight-line estimate on this leg -- no real waterway route resolved for this hop.
              </div>
            )}
          </div>
        )}
      </div>
    </Panel>
  )
}
