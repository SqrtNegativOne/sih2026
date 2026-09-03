import { Minus, TrendingDown, TrendingUp } from 'lucide-react'
import { motion, useReducedMotion } from 'motion/react'
import { DURATION, drawPath, transition } from '@/lib/motion'
import { Panel } from '@/components/desk/panel'
import { Badge } from '@/components/ui/badge'
import { Tooltip } from '@/components/ui/tooltip'
import { PERCENTILE } from '@/lib/vocabulary'
import { useElementSize } from '@/hooks/use-element-size'
import { formatNumber } from '@/lib/format'
import type { RateHorizon, RouteEvidence } from '@/lib/types'

const ROUTE_EVIDENCE_LABEL: Record<RouteEvidence, string> = {
  OBSERVED: 'Route-validated',
  MODELLED: 'Route-modelled (thin evidence)',
  ROUTE_RATE_BASIS_UNAVAILABLE: 'Class-only',
}

function RouteEvidenceBadge({ evidence }: { evidence: RouteEvidence }) {
  return (
    <Tooltip
      content={
        evidence === 'ROUTE_RATE_BASIS_UNAVAILABLE'
          ? 'No real route-level rate evidence clears the bar for this origin, so this is priced on the class benchmark rather than on this specific route.'
          : evidence === 'MODELLED'
            ? 'Real route-level rate evidence exists for this origin but is a thin sample, so the adjustment is modelled and carries a wide uncertainty.'
            : 'Enough real route-level rate observations exist for this origin to validate the adjustment against the class benchmark.'
      }
    >
      <Badge variant={evidence === 'OBSERVED' ? 'secondary' : 'outline'} className="text-micro">
        {ROUTE_EVIDENCE_LABEL[evidence]}
      </Badge>
    </Tooltip>
  )
}

function Direction({ dir }: { dir: RateHorizon['direction'] }) {
  if (dir === 'up') return <TrendingUp className="h-3.5 w-3.5 text-risk" />
  if (dir === 'down') return <TrendingDown className="h-3.5 w-3.5 text-go" />
  return <Minus className="h-3.5 w-3.5 text-muted-foreground" />
}

const H = 116
const PAD = { t: 10, r: 10, b: 16, l: 44 }

function FanChart({ rows, todayQuote }: { rows: RateHorizon[]; todayQuote: number }) {
  const [box, ref] = useElementSize<HTMLDivElement>()
  const reduced = useReducedMotion()
  const w = Math.max(box.width, 240)

  if (rows.length === 0) return null
  const sorted = [...rows].sort((a, b) => a.horizon_days - b.horizon_days)

  // Identifies THIS dataset. Used as the motion key so the draw-in replays
  // only when a genuinely new forecast arrives -- not on every re-render, and
  // not when the container merely resizes.
  const signature = sorted.map((r) => `${r.horizon_days}:${Math.round(r.p50_usd_per_day)}`).join('|')

  const lo = Math.min(todayQuote, ...sorted.map((r) => r.p10_usd_per_day))
  const hi = Math.max(todayQuote, ...sorted.map((r) => r.p90_usd_per_day))
  const span = hi - lo || 1
  const yMin = lo - span * 0.12
  const yMax = hi + span * 0.12

  const maxDay = sorted[sorted.length - 1].horizon_days
  const x = (day: number) =>
    PAD.l + (Math.sqrt(day) / Math.sqrt(maxDay)) * (w - PAD.l - PAD.r)
  const y = (v: number) => PAD.t + (1 - (v - yMin) / (yMax - yMin)) * (H - PAD.t - PAD.b)

  const band =
    sorted.map((r) => `${x(r.horizon_days).toFixed(1)},${y(r.p90_usd_per_day).toFixed(1)}`).join(' ') +
    ' ' +
    [...sorted]
      .reverse()
      .map((r) => `${x(r.horizon_days).toFixed(1)},${y(r.p10_usd_per_day).toFixed(1)}`)
      .join(' ')

  const p50Line = sorted
    .map((r, i) => `${i === 0 ? 'M' : 'L'}${x(r.horizon_days).toFixed(1)} ${y(r.p50_usd_per_day).toFixed(1)}`)
    .join(' ')

  const yToday = y(todayQuote)

  return (
    <div ref={ref} className="w-full">
      <svg width={w} height={H} className="block">
        {/* today's quote reference */}
        <line
          x1={PAD.l}
          x2={w - PAD.r}
          y1={yToday}
          y2={yToday}
          stroke="var(--foreground)"
          strokeWidth={1}
          strokeDasharray="3 3"
          opacity={0.5}
        />
        <text x={PAD.l - 4} y={yToday + 3} textAnchor="end" className="fill-muted-foreground text-micro">
          today
        </text>

        {/*
          The fan draws itself in when a quote lands: the uncertainty band
          fades up from nothing, then the expected-case line traces left to
          right. `key` is the dataset signature, so the animation replays when
          a NEW forecast arrives and not on every re-render -- an unkeyed
          motion element re-runs on each parent render, which turns a chart
          into a strobe as soon as anything else on the page changes.

          `opacity` is expressed only in the variant, never alongside a `style`
          -- see the note on fadeBand in lib/motion for why.
        */}
        <motion.polygon
          key={`band-${signature}`}
          points={band}
          fill="var(--market)"
          initial={reduced ? { opacity: 0.16 } : { opacity: 0 }}
          animate={{ opacity: 0.16 }}
          transition={reduced ? { duration: 0 } : { ...transition.slow, delay: 0.05 }}
        />
        <motion.path
          key={`p50-${signature}`}
          d={p50Line}
          fill="none"
          stroke="var(--market)"
          strokeWidth={1.75}
          strokeLinejoin="round"
          pathLength={1}
          initial={reduced ? false : 'hidden'}
          animate="shown"
          variants={drawPath}
        />
        {sorted.map((r, i) => (
          <g key={r.horizon_days}>
            <motion.circle
              cx={x(r.horizon_days)}
              cy={y(r.p50_usd_per_day)}
              r={2.5}
              fill="var(--market)"
              initial={reduced ? false : { opacity: 0 }}
              animate={{ opacity: 1 }}
              transition={
                reduced
                  ? { duration: 0 }
                  : { ...transition.fast, delay: 0.2 + i * (DURATION.slow / Math.max(1, sorted.length)) }
              }
            />
            <text
              x={x(r.horizon_days)}
              y={H - 4}
              textAnchor="middle"
              className="fill-muted-foreground text-micro"
            >
              {r.horizon_days}d
            </text>
          </g>
        ))}
        {/* y bounds */}
        <text x={PAD.l - 4} y={y(yMax) + 8} textAnchor="end" className="fill-muted-foreground text-micro">
          ${formatNumber(Math.round(yMax / 100) * 100)}
        </text>
        <text x={PAD.l - 4} y={y(yMin)} textAnchor="end" className="fill-muted-foreground text-micro">
          ${formatNumber(Math.round(yMin / 100) * 100)}
        </text>
      </svg>
    </div>
  )
}

/**
 * What the route evidence actually means for the number above it.
 *
 * The fault register's own words on the previous treatment: *"To be fair: the
 * app discloses this with a 'Class-only' badge. But a badge is not a feature."*
 * It was right. A small grey outline chip, with its only explanation in a
 * native `title` that never opens on keyboard focus and never appears on
 * touch, is not a disclosure anyone reads.
 *
 * The class-only case says the consequence in figures rather than in jargon,
 * because the consequence is genuinely surprising: measured on this data, a
 * cargo lifting from Newcastle (5,669 nm) and one from Hampton Roads
 * (9,913 nm) return a ceiling identical to four decimal places. Someone
 * comparing origins deserves to know that before they act on it, not after.
 */
function RouteEvidenceNote({
  evidence,
  adjustment,
}: {
  evidence: RouteEvidence
  adjustment: number | null
}) {
  if (evidence === 'ROUTE_RATE_BASIS_UNAVAILABLE') {
    return (
      <p className="panel-note border-wait/40 text-wait-on-soft">
        <span className="font-semibold">This price is for the vessel class, not for this route.</span>{' '}
        No published route-level rate for this origin clears the evidence bar, so the forecast falls
        back to the class benchmark — which means two origins on different continents can return the
        same number. Use it to time the market, not to choose between load ports; the landed-cost
        panel is where distance and fuel actually differ.
      </p>
    )
  }
  const pct = adjustment == null ? null : `${adjustment >= 0 ? '+' : ''}${(adjustment * 100).toFixed(1)}%`
  return (
    <p className="panel-note border-go/40">
      <span className="font-semibold">
        Adjusted for this route{pct ? ` by ${pct}` : ''}.
      </span>{' '}
      {evidence === 'MODELLED'
        ? 'Built from real published route-level rates for this origin, but a thin sample — the adjustment is modelled and its uncertainty is deliberately wide.'
        : 'Built from enough real published route-level rates for this origin to validate the adjustment against the class benchmark.'}
    </p>
  )
}

export function RateForecastTable({
  rows,
  todayQuote,
}: {
  rows: RateHorizon[]
  todayQuote: number
}) {
  const routeEvidence = rows[0]?.route_evidence ?? 'ROUTE_RATE_BASIS_UNAVAILABLE'
  return (
    <Panel
      className="h-full"
      id="forecast"
      title="Rate Forecast"
      hint="What the model expects this vessel class to cost per day, 7 / 30 / 90 days out. The shaded band is the low-to-high range, the line is the expected case, and the dashed line is today's rate. Dir compares the expected case against today; Conf is how strongly the model agrees on that direction."
      meta="TC $/day"
      actions={<RouteEvidenceBadge evidence={routeEvidence} />}
      flush
    >
      <div className="border-b border-border px-2 pb-1 pt-2">
        <FanChart rows={rows} todayQuote={todayQuote} />
      </div>
      <RouteEvidenceNote evidence={routeEvidence} adjustment={rows[0]?.route_adjustment ?? null} />
      <table className="desk-table">
        <thead>
          <tr>
            <th>Horizon</th>
            {/* "p10 / p50 / p90" is statistics shorthand, not a shipping
                term. The percentile stays in the tooltip for anyone who reads
                it natively -- "Low" alone is less precise than what they had --
                but it is no longer the first thing to decode. */}
            <th className="cursor-help text-right" title={PERCENTILE.p10.definition}>
              {PERCENTILE.p10.label}
            </th>
            <th className="cursor-help text-right" title={PERCENTILE.p50.definition}>
              {PERCENTILE.p50.label}
            </th>
            <th className="cursor-help text-right" title={PERCENTILE.p90.definition}>
              {PERCENTILE.p90.label}
            </th>
            {/* Was "$/mt (class÷transit)" -- the parenthetical was the
                formula, which belongs in the explanation, not the header. */}
            <th
              className="cursor-help text-right"
              title="Freight per tonne, derived by dividing the class-wide day rate by the transit days -- a unit conversion, not itself a route-specific quote unless the badge above says route-validated. A different figure from the Fleet Mix panel's $/mt (the actual chosen vessel configuration) and the Landed Cost panel's Freight row (one component of a fuller delivered-cost breakdown)."
            >
              $/tonne
            </th>
            <th className="text-center">Dir</th>
            <th
              className="text-right"
              title="Confidence in the direction shown (Dir), not in the forecast overall -- by construction this is always >=50%, since Dir always names whichever direction the forecast favours."
            >
              Conf
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.horizon_days}>
              <td className="font-semibold">{r.horizon_days}d</td>
              <td className="desk-num text-right text-muted-foreground">
                ${formatNumber(r.p10_usd_per_day)}
              </td>
              <td className="desk-num text-right font-semibold">${formatNumber(r.p50_usd_per_day)}</td>
              <td className="desk-num text-right text-muted-foreground">
                ${formatNumber(r.p90_usd_per_day)}
              </td>
              <td className="desk-num text-right">
                {r.p50_usd_per_mt != null ? `$${r.p50_usd_per_mt.toFixed(2)}` : '—'}
              </td>
              <td>
                <div className="flex justify-center">
                  <Direction dir={r.direction} />
                </div>
              </td>
              <td className="text-right">
                <div className="ml-auto flex w-16 items-center gap-1">
                  <div className="h-1 flex-1 overflow-hidden rounded-full bg-muted">
                    <div
                      className="h-full rounded-full bg-market"
                      style={{ width: `${r.confidence_pct}%` }}
                    />
                  </div>
                  <span className="desk-num text-caption text-muted-foreground">
                    {Math.round(r.confidence_pct)}
                  </span>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Panel>
  )
}
