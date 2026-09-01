import { geoGraticule, geoMercator, geoPath } from 'd3-geo'
import type { Feature, FeatureCollection } from 'geojson'
import { motion } from 'motion/react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import landRaw from '@/assets/ne_110m_land.json'
import { Panel } from '@/components/desk/panel'
import { useElementSize } from '@/hooks/use-element-size'
import { formatUsdCompact, prettyPort } from '@/lib/format'
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

const land = landRaw as unknown as FeatureCollection
const graticule = geoGraticule().step([10, 10])()

const SEA = '#e4edf6'
const LAND = '#f0ede3'
const COAST = '#c4cdd8'

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

function legKey(a: string, b: string): string {
  return a < b ? `${a}~${b}` : `${b}~${a}`
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
  const [box, ref] = useElementSize<HTMLDivElement>()
  const [hidden, setHidden] = useState<Set<SolverRouteKind>>(new Set())
  const [showRejected, setShowRejected] = useState(false)
  const [hoverId, setHoverId] = useState<string | null>(null)
  const [tip, setTip] = useState<{ route: SolverRoute; x: number; y: number; fallback: boolean } | null>(null)

  const width = Math.max(box.width, 320)
  const height = Math.max(box.height, 260)

  // User-driven scroll/pinch zoom, centred on the cursor -- independent of
  // the click-to-focus `camera` transform below (that one re-frames on a
  // route click; this one is the reader's own free zoom/pan, wheel or
  // touchpad pinch, the interaction every map-like view is expected to
  // have). React's onWheel is attached passive by default, which silently
  // blocks preventDefault -- attached natively instead so scrolling over
  // the map reliably zooms the map, not the page underneath it.
  const svgRef = useRef<SVGSVGElement>(null)
  const [userTf, setUserTf] = useState({ k: 1, x: 0, y: 0 })

  useEffect(() => {
    const el = svgRef.current
    if (!el) return
    function onWheel(e: WheelEvent) {
      e.preventDefault()
      const rect = el!.getBoundingClientRect()
      const mx = e.clientX - rect.left
      const my = e.clientY - rect.top
      setUserTf((prev) => {
        const factor = Math.exp(-e.deltaY * 0.0018)
        const k = Math.min(10, Math.max(0.6, prev.k * factor))
        const dataX = (mx - prev.x) / prev.k
        const dataY = (my - prev.y) / prev.k
        return { k, x: mx - dataX * k, y: my - dataY * k }
      })
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => el.removeEventListener('wheel', onWheel)
  }, [])

  const colors = useMemo(() => assignRouteColors(routes), [routes])
  const portByCode = useMemo(() => new Map(ports.map((p) => [p.code, p])), [ports])

  const visible = routes.filter(
    (r) => !hidden.has(r.kind) && (showRejected || r.status !== 'rejected'),
  )

  // F-36 fix: this used to also add EVERY port in the whole system
  // (`ports`, the full API port list) to the bounding-box points below,
  // regardless of whether that port appears in any visible route -- since
  // the real port list spans Hampton Roads (76W) to Newcastle (152E),
  // every quote's map rendered zoomed out to roughly half the globe, even
  // for a short regional hop. The polylines already carry the real
  // endpoints of every visible leg (a real port-to-port route's own first/
  // last coordinates), so bounding just those is both sufficient and
  // correctly scoped to what's actually on screen; the box shrinks to a
  // small regional view for a short route and only spans further when the
  // real route legs do.
  const coords = useMemo(() => {
    const out: XY[] = []
    for (const r of visible) for (const leg of r.legs) for (const c of leg.polyline) out.push(c)
    return out
  }, [visible])

  const projection = useMemo(() => {
    const proj = geoMercator()
    if (coords.length >= 2) {
      let minLon = 180
      let minLat = 90
      let maxLon = -180
      let maxLat = -90
      for (const [lon, lat] of coords) {
        minLon = Math.min(minLon, lon)
        maxLon = Math.max(maxLon, lon)
        minLat = Math.min(minLat, lat)
        maxLat = Math.max(maxLat, lat)
      }
      const mLon = Math.max((maxLon - minLon) * 0.26, 6)
      const mLat = Math.max((maxLat - minLat) * 0.12, 4)
      const region = {
        type: 'Polygon' as const,
        coordinates: [
          [
            [minLon - mLon, minLat - mLat],
            [maxLon + mLon, minLat - mLat],
            [maxLon + mLon, maxLat + mLat],
            [minLon - mLon, maxLat + mLat],
            [minLon - mLon, minLat - mLat],
          ],
        ],
      }
      proj.fitExtent(
        [
          [20, 20],
          [width - 20, height - 20],
        ],
        region,
      )
      // fitExtent fills the limiting dimension; on a wide panel that leaves the
      // whole hemisphere visible. Zoom in (bounded) so the region actually
      // dominates, then recentre on its middle.
      const cLon = (minLon + maxLon) / 2
      const cLat = (minLat + maxLat) / 2
      const w0 = proj([minLon - mLon, cLat])
      const w1 = proj([maxLon + mLon, cLat])
      if (w0 && w1) {
        const regionPx = Math.abs(w1[0] - w0[0]) || 1
        const boost = Math.max(1, Math.min((width * 0.94) / regionPx, 1.35))
        proj.scale(proj.scale() * boost)
        const c = proj([cLon, cLat])
        const t = proj.translate()
        if (c) proj.translate([t[0] + width / 2 - c[0], t[1] + height / 2 - c[1]])
      }
    } else {
      proj.fitSize([width, height], land)
    }
    return proj
  }, [coords, width, height])

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

  const projectedLeg = useCallback(
    (route: SolverRoute, legIdx: number): XY[] => {
      const proj = route.legs[legIdx].polyline
        .map(([lon, lat]) => projection([lon, lat]))
        .filter((xy): xy is XY => xy != null)
      if (proj.length < 2) return proj
      const k = legKey(route.legs[legIdx].from_port, route.legs[legIdx].to_port)
      const shared = legSlots.total.get(k) ?? 1
      const idx = legSlots.slot.get(`${route.id}|${legIdx}`) ?? 0
      if (shared <= 1) return proj
      const [ax, ay] = proj[0]
      const [bx, by] = proj[proj.length - 1]
      const len = Math.hypot(bx - ax, by - ay) || 1
      const nx = -(by - ay) / len
      const ny = (bx - ax) / len
      const off = (idx - (shared - 1) / 2) * 7
      return proj.map(([x, y]) => [x + nx * off, y + ny * off] as XY)
    },
    [projection, legSlots],
  )

  function pathD(pts: XY[]): string {
    return pts.map(([x, y], i) => `${i === 0 ? 'M' : 'L'}${x.toFixed(1)} ${y.toFixed(1)}`).join(' ')
  }

  // Camera zoom toward the focused route.
  const camera = useMemo(() => {
    if (!focusId) return { scale: 1, x: 0, y: 0 }
    const route = routes.find((r) => r.id === focusId)
    if (!route) return { scale: 1, x: 0, y: 0 }
    let minX = Infinity
    let minY = Infinity
    let maxX = -Infinity
    let maxY = -Infinity
    route.legs.forEach((_, i) => {
      for (const [x, y] of projectedLeg(route, i)) {
        minX = Math.min(minX, x)
        minY = Math.min(minY, y)
        maxX = Math.max(maxX, x)
        maxY = Math.max(maxY, y)
      }
    })
    if (!isFinite(minX)) return { scale: 1, x: 0, y: 0 }
    const cx = (minX + maxX) / 2
    const cy = (minY + maxY) / 2
    const bw = Math.max(maxX - minX, 40)
    const bh = Math.max(maxY - minY, 40)
    const k = Math.min(3.2, Math.max(1.4, Math.min((width * 0.7) / bw, (height * 0.7) / bh)))
    // Translate so the focused route's own centroid (cx, cy) lands on the
    // panel's actual centre (width/2, height/2) post-scale -- the previous
    // `cx - k * cx` / `cy - k * cy` anchored the route at its OWN pre-zoom
    // pixel position instead, which only looked centred for a route that
    // already happened to sit near the panel's middle; a route toward an
    // edge (routes fanned out to differing regions on the map) zoomed in
    // correctly but panned to the wrong spot, appearing to click-zoom "to
    // the wrong place."
    return { scale: k, x: width / 2 - k * cx, y: height / 2 - k * cy }
  }, [focusId, routes, projectedLeg, width, height])

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

  const kinds = [...new Set(routes.map((r) => r.kind))]
  const rejectedCount = routes.filter((r) => r.status === 'rejected').length
  const legendRoutes = visible.filter((r) => r.status === 'chosen')

  const ordered = [...visible].sort(
    (a, b) => Number(a.status === 'chosen') - Number(b.status === 'chosen'),
  )

  return (
    <Panel
      className="h-full"
      id="map"
      title="Route Exploration"
      hint="Every routing the solver evaluated. Solid = chosen, dashed = considered, dotted red = rejected (toggle on), fine dots = no real waterway route resolved for that hop (straight-line estimate). Routes sharing a leg are fanned apart. Hover to isolate a route; click one to zoom to it, click empty water to reset."
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
                'rounded-sm px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wide',
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
                'rounded-sm px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wide',
                showRejected ? 'bg-risk-soft text-risk' : 'bg-surface-2 text-muted-foreground',
              )}
            >
              Rejected {rejectedCount}
            </button>
          )}
          {(focusId || userTf.k !== 1) && (
            <button
              type="button"
              onClick={() => {
                onFocus(null)
                setUserTf({ k: 1, x: 0, y: 0 })
              }}
              className="rounded-sm bg-primary px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wide text-primary-foreground"
            >
              Reset zoom
            </button>
          )}
        </div>
      }
    >
      <div ref={ref} className="relative h-full min-h-[260px] w-full">
        <svg ref={svgRef} width={width} height={height} className="block cursor-grab">
          <rect
            width={width}
            height={height}
            fill={SEA}
            onClick={() => onFocus(null)}
            onDoubleClick={() => setUserTf({ k: 1, x: 0, y: 0 })}
          />
          <g transform={`translate(${userTf.x} ${userTf.y}) scale(${userTf.k})`}>
          <motion.g
            animate={{ scale: camera.scale, x: camera.x, y: camera.y }}
            transition={{ type: 'spring', stiffness: 180, damping: 26 }}
            style={{ transformOrigin: '0px 0px' }}
          >
            <path
              d={pathGen(graticule) ?? ''}
              fill="none"
              stroke={COAST}
              strokeWidth={0.5}
              opacity={0.6}
            />
            {land.features.map((f: Feature, i: number) => (
              <path key={i} d={pathGen(f) ?? ''} fill={LAND} stroke={COAST} strokeWidth={0.6} />
            ))}

            {ordered.map((r) => {
              const isRejected = r.status === 'rejected'
              const isChosen = r.status === 'chosen'
              const isHover = hoverId === r.id
              const isFocus = focusId === r.id
              const dimmed =
                (hoverId != null && !isHover) || (focusId != null && !isFocus)
              const stroke = isRejected ? 'var(--risk)' : (colors.get(r.id) ?? 'var(--structure)')
              const baseW = isChosen ? 3 : 1.75
              return (
                <g key={r.id}>
                  {r.legs.map((leg, li) => {
                    const pts = projectedLeg(r, li)
                    if (pts.length < 2) return null
                    // A leg falls back to a straight great-circle line when
                    // the real marine-network router (searoute) can't
                    // resolve a waterway path -- in practice this is short
                    // hops between two ports close enough that searoute
                    // snaps both to the same network node (see
                    // opt/route_trace.py's own docstring). Drawn identically
                    // to a real routed leg it looks like a bug (a line
                    // cutting across land); a distinct fine-dot pattern
                    // marks it honestly as a straight-line approximation.
                    const isFallback = leg.is_great_circle_fallback
                    return (
                      <motion.path
                        key={li}
                        d={pathD(pts)}
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
                          setHoverId(r.id)
                          setTip({ route: r, x: e.nativeEvent.offsetX, y: e.nativeEvent.offsetY, fallback: isFallback })
                        }}
                        onMouseMove={(e) =>
                          setTip({ route: r, x: e.nativeEvent.offsetX, y: e.nativeEvent.offsetY, fallback: isFallback })
                        }
                        onMouseLeave={() => {
                          setHoverId(null)
                          setTip(null)
                        }}
                        onClick={() => onFocus(focusId === r.id ? null : r.id)}
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
                <g key={p.code} transform={`translate(${xy[0]},${xy[1]})`}>
                  <circle r={3} fill="#1c2b3a" stroke="#fff" strokeWidth={1} />
                  <text
                    x={5}
                    y={above ? -5 : 12}
                    className="text-[9px] font-semibold"
                    fill="#1c2b3a"
                    stroke="#fff"
                    strokeWidth={3}
                    paintOrder="stroke"
                  >
                    {prettyPort(p.name)}
                  </text>
                </g>
              )
            })}

            {chokepointMarkers.map((m) => {
              const xy = projection([m.lon, m.lat])
              if (!xy) return null
              return (
                <circle
                  key={m.id}
                  cx={xy[0]}
                  cy={xy[1]}
                  r={FRACTURE_BAND_RADIUS[m.band]}
                  fill={FRACTURE_BAND_COLOR[m.band]}
                  fillOpacity={0.35}
                  stroke={FRACTURE_BAND_COLOR[m.band]}
                  strokeWidth={1.25}
                >
                  <title>{`${m.name}: ${m.band}`}</title>
                </circle>
              )
            })}
          </motion.g>
          </g>
        </svg>

        {legendRoutes.length > 0 && (
          <div className="pointer-events-none absolute bottom-2 left-2 rounded border border-border bg-surface/90 px-2 py-1 text-[10px]">
            {legendRoutes.map((r) => (
              <div key={r.id} className="flex items-center gap-1.5">
                <span
                  className="inline-block h-0.5 w-4"
                  style={{ background: colors.get(r.id) ?? 'var(--structure)' }}
                />
                <span className="text-foreground">{r.label}</span>
              </div>
            ))}
            <div className="mt-0.5 flex items-center gap-1.5 text-muted-foreground">
              <span className="inline-block h-0 w-4 border-t border-dashed border-muted-foreground" />
              considered
            </div>
          </div>
        )}

        {tip && (
          <div
            className="pointer-events-none absolute z-10 max-w-[240px] rounded border border-border bg-surface p-1.5 text-[11px] shadow-[0_2px_10px_rgba(0,0,0,0.14)]"
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
                  {tip.route.metric_label}: {formatUsdCompact(tip.route.metric_usd)}
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
