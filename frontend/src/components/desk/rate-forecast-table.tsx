import { Minus, TrendingDown, TrendingUp } from 'lucide-react'
import { Panel } from '@/components/desk/panel'
import { Badge } from '@/components/ui/badge'
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
    <Badge
      variant={evidence === 'OBSERVED' ? 'secondary' : evidence === 'MODELLED' ? 'outline' : 'outline'}
      className="text-micro"
      title={
        evidence === 'ROUTE_RATE_BASIS_UNAVAILABLE'
          ? 'No real route-level rate evidence clears the bar for this origin -- priced on the class benchmark, not this specific route.'
          : undefined
      }
    >
      {ROUTE_EVIDENCE_LABEL[evidence]}
    </Badge>
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
  const w = Math.max(box.width, 240)

  if (rows.length === 0) return null
  const sorted = [...rows].sort((a, b) => a.horizon_days - b.horizon_days)

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

        <polygon points={band} fill="var(--market)" opacity={0.16} />
        <path d={p50Line} fill="none" stroke="var(--market)" strokeWidth={1.75} />
        {sorted.map((r) => (
          <g key={r.horizon_days}>
            <circle cx={x(r.horizon_days)} cy={y(r.p50_usd_per_day)} r={2.5} fill="var(--market)" />
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
      hint="Model-forecast time-charter rate by horizon. Shaded band is the p10–p90 range, line is p50, dashed line is today's quote. Dir compares p50 to today; Conf is model agreement."
      meta="TC $/day"
      actions={<RouteEvidenceBadge evidence={routeEvidence} />}
      flush
    >
      <div className="border-b border-border px-2 pb-1 pt-2">
        <FanChart rows={rows} todayQuote={todayQuote} />
      </div>
      <table className="desk-table">
        <thead>
          <tr>
            <th>Horizon</th>
            <th className="text-right">p10</th>
            <th className="text-right">p50</th>
            <th className="text-right">p90</th>
            <th className="text-right" title="class rate ÷ transit days -- a unit conversion, not itself a route-specific quote unless the badge above says Route-validated/Route-modelled. A different figure from the Fleet Mix panel's $/mt (the actual chosen vessel configuration) and the Landed Cost panel's Freight row (one component of a fuller delivered-cost breakdown).">
              $/mt (class÷transit)
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
