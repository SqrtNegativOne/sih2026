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

// Severity was previously carried by the dot's colour alone, which is exactly
// the encoding a red/green-blind reader cannot resolve -- and these three
// tones (primary blue, wait amber, risk red) are the ones that most need
// telling apart. The word now travels with the dot everywhere.
const SEVERITY_LABEL: Record<RiskSeverity, string> = {
  info: 'Info',
  warning: 'Warning',
  critical: 'Critical',
}

const SEVERITY_TEXT: Record<RiskSeverity, string> = {
  info: 'text-primary',
  warning: 'text-wait',
  critical: 'text-risk',
}

// 2.4: "Cyclone season" (a bare Oct-Dec calendar check) renamed now that
// opt.risk.cyclone_season_alert is a real per-basin, per-ISO-week strike
// climatology lookup keyed off the actual ports on the quote, not a
// calendar rule that fired the same way regardless of where the cargo was.
const MONITORED = ['Rate regime', 'Port congestion', 'Chokepoint transits', 'Cyclone climatology']

function AlertRow({ a }: { a: RiskAlert }) {
  return (
    <li className="flex gap-2 border-b border-border/70 px-2 py-2 transition-colors last:border-b-0 hover:bg-surface-2">
      <span
        className={cn('mt-2 h-2 w-2 shrink-0 rounded-full', SEVERITY_DOT[a.severity])}
        aria-hidden="true"
      />
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline justify-between gap-2">
          <span className="min-w-0 truncate text-caption font-bold uppercase tracking-wide text-muted-foreground">
            <span className={SEVERITY_TEXT[a.severity]}>{SEVERITY_LABEL[a.severity]}</span>
            {/* A rule, not a pipe character: a literal "|" is text as far as a
                screen reader and a contrast check are concerned (it measured
                1.48:1 in border grey), while this is purely decorative and
                announces nothing. */}
            <span
              aria-hidden="true"
              className="mx-1.5 inline-block h-2 w-px translate-y-px bg-border"
            />
            {a.category.replace(/_/g, ' ')} · {a.subject}
          </span>
          {/* Was a bare "-2.22 / -1.75" with nothing saying which number was
              which -- two signed figures separated by a slash read as a range
              or a fraction before they read as observed-vs-threshold. */}
          <span
            className="desk-chip desk-chip-neutral"
            title={`Observed ${formatNumber(a.metric_value, 2)} against an alert threshold of ${formatNumber(a.threshold, 2)}`}
          >
            {formatNumber(a.metric_value, 2)}
            <span className="font-sans font-normal normal-case text-muted-foreground">vs</span>
            {formatNumber(a.threshold, 2)}
          </span>
        </div>
        <p className="mt-0.5 text-body leading-relaxed text-foreground">{a.message}</p>
      </div>
    </li>
  )
}

export function RiskFeed({ assessment }: { assessment: RiskAssessment }) {
  const alerts = assessment.alerts
  return (
    <Panel
      className="h-full"
      id="risk"
      title="Risk Feed"
      soWhat={'The things that could make this quote wrong, listed worst first. Anything flagged here should be checked with the agent or the owner before you fix — the model has priced the voyage, not the surprise.'}
      hint="Real-data early warnings: unusual rate-regime shifts, port congestion spikes, chokepoint traffic drops (Suez, Hormuz, Malacca, Bab-el-Mandeb, Cape), and real per-basin, per-week cyclone strike climatology for the ports on this quote. Each alert shows its metric vs threshold."
      meta={`${alerts.length} alert${alerts.length === 1 ? '' : 's'} · ${assessment.as_of}`}
      flush
    >
      {alerts.length === 0 ? (
        <div className="p-2">
          <div className="flex items-center gap-2 rounded-sm border border-go/30 bg-go-soft px-2 py-2 text-lead font-semibold text-go">
            <ShieldCheck className="h-4 w-4 shrink-0" aria-hidden="true" />
            No disruption signals
          </div>
          {/* Naming what was checked matters as much as the all-clear: an
              empty feed with nothing beside it is indistinguishable from a
              feed that failed to load. */}
          <div className="mt-2 grid grid-cols-2 gap-1">
            {MONITORED.map((m) => (
              <div
                key={m}
                className="flex items-center gap-2 rounded-sm bg-surface-2 px-2 py-1 text-caption text-muted-foreground"
              >
                <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-go" aria-hidden="true" />
                {m}
              </div>
            ))}
          </div>
          <p className="mt-2 text-caption leading-relaxed text-muted-foreground">
            All four signals are within their real-data thresholds as of {assessment.as_of}.
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
