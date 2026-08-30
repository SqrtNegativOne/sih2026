import { ShieldCheck } from 'lucide-react'
import { Panel } from '@/components/desk/panel'
import type { RiskAlert, RiskAssessment, RiskSeverity } from '@/lib/types'
import { formatNumber } from '@/lib/format'
import { cn } from '@/lib/utils'

const SEVERITY_DOT: Record<RiskSeverity, string> = {
  info: 'bg-primary',
  warning: 'bg-wait',
  critical: 'bg-risk',
}

// 2.4: "Cyclone season" (a bare Oct-Dec calendar check) renamed now that
// opt.risk.cyclone_season_alert is a real per-basin, per-ISO-week strike
// climatology lookup keyed off the actual ports on the quote, not a
// calendar rule that fired the same way regardless of where the cargo was.
const MONITORED = ['Rate regime', 'Port congestion', 'Chokepoint transits', 'Cyclone climatology']

function AlertRow({ a }: { a: RiskAlert }) {
  return (
    <li className="flex gap-2 border-b border-border/70 px-2 py-1.5 last:border-b-0">
      <span className={cn('mt-1 h-2 w-2 shrink-0 rounded-full', SEVERITY_DOT[a.severity])} />
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline justify-between gap-2">
          <span className="text-[10px] font-bold uppercase tracking-wide text-muted-foreground">
            {a.category.replace(/_/g, ' ')} · {a.subject}
          </span>
          <span className="desk-num text-[10px] text-muted-foreground">
            {formatNumber(a.metric_value, 2)} / {formatNumber(a.threshold, 2)}
          </span>
        </div>
        <p className="text-[12px] leading-snug text-foreground">{a.message}</p>
      </div>
    </li>
  )
}

export function RiskFeed({ assessment }: { assessment: RiskAssessment }) {
  const alerts = assessment.alerts
  return (
    <Panel
      id="risk"
      title="Risk Feed"
      hint="Real-data early warnings: unusual rate-regime shifts, port congestion spikes, chokepoint traffic drops (Suez, Hormuz, Malacca, Bab-el-Mandeb, Cape), and real per-basin, per-week cyclone strike climatology for the ports on this quote. Each alert shows its metric vs threshold."
      meta={`${alerts.length} alert${alerts.length === 1 ? '' : 's'} · ${assessment.as_of}`}
      flush
    >
      {alerts.length === 0 ? (
        <div className="p-2">
          <div className="flex items-center gap-1.5 text-[12px] font-semibold text-go">
            <ShieldCheck className="h-4 w-4" />
            No disruption signals
          </div>
          <div className="mt-2 grid grid-cols-2 gap-1">
            {MONITORED.map((m) => (
              <div
                key={m}
                className="flex items-center gap-1.5 rounded bg-go-soft px-1.5 py-1 text-[10px] text-go"
              >
                <span className="h-1.5 w-1.5 rounded-full bg-go" />
                {m}
              </div>
            ))}
          </div>
          <p className="mt-2 text-[10px] text-muted-foreground">
            All signals within their real-data thresholds as of {assessment.as_of}.
          </p>
        </div>
      ) : (
        <ul>
          {alerts.map((a, i) => (
            <AlertRow key={`${a.category}-${a.subject}-${i}`} a={a} />
          ))}
        </ul>
      )}
    </Panel>
  )
}
