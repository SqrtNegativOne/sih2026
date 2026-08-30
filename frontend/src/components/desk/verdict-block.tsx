import { useState } from 'react'
import { ChevronDown, ChevronUp } from 'lucide-react'
import { addDays, formatShortDate, formatUsd, formatUsdCompact } from '@/lib/format'
import type { QuoteResult } from '@/lib/types'
import { cn } from '@/lib/utils'

/**
 * The headline. LOCK or WAIT is the loudest thing on the desk, on a full-bleed
 * semantic fill, and directly under it the explicit money comparison between
 * locking today and waiting for the projected trough.
 */
export function VerdictBlock({ quote }: { quote: QuoteResult }) {
  const [showWhy, setShowWhy] = useState(false)
  const isLock = quote.lock_action === 'LOCK'
  const term = quote.contract_term_days
  const rec = quote.full_recommendation

  const lockRate = quote.today_quote_usd_per_day
  const lockTerm = lockRate * term
  const waitRate = rec.optimal_entry_window_p50_usd
  const waitTerm = waitRate != null ? waitRate * term : null

  const windowText =
    quote.optimal_entry_window_start_day != null && quote.optimal_entry_window_end_day != null
      ? `${formatShortDate(addDays(quote.as_of, quote.optimal_entry_window_start_day))} to ${formatShortDate(
          addDays(quote.as_of, quote.optimal_entry_window_end_day),
        )}`
      : 'no clear trough'

  // F-06 fix: this row used to key its caption/colour off `isLock` (the
  // LSMC-fused verdict above), not off the actual sign of `edge` -- so
  // whenever the two disagreed (a real, correct case: the option value of
  // waiting can flip the verdict to WAIT even while the naive always-spot
  // comparison still favours locking) the caption stated the opposite of
  // what the number meant. Verified live: WAIT + edge=+$7,570 rendered as
  // "Waiting avoids an expected loss of $7,570". Keying off edge's own
  // sign makes the caption correct regardless of which way the verdict
  // above went; the note below explains the two can differ, instead of
  // leaving that silently implied.
  const edge = quote.expected_savings_usd_total
  const edgeFavorsLock = edge > 0
  const confidence = Math.round(quote.prob_savings_positive * 100)
  const optionValue = rec.stopping_result?.option_value_usd_per_day ?? null
  // F-37: p10/p90 savings and the review trigger were already computed by
  // the backend on every quote and typed in the frontend, but never
  // rendered anywhere -- the desk showed only the single expected-savings
  // number, dropping the real downside/upside range and the "when should
  // I look at this again" answer the engine already has.
  const p10Total = rec.p10_savings_usd_per_day * term
  const p90Total = rec.p90_savings_usd_per_day * term

  return (
    <div className="flex h-full flex-col overflow-hidden rounded-[3px] border border-border bg-surface">
      {/* Verdict fill */}
      <div
        className={cn(
          'flex items-center justify-between gap-3 px-2.5 py-1.5 text-white',
          isLock ? 'bg-go' : 'bg-wait',
        )}
      >
        <div>
          <div className="text-[9px] font-semibold uppercase tracking-[0.15em] opacity-90">
            Verdict
          </div>
          <div className="font-mono text-[34px] font-extrabold leading-none tracking-tight">
            {quote.lock_action}
          </div>
        </div>
        <div className="text-right text-[10px] leading-tight">
          <div className="font-semibold uppercase tracking-wide">{quote.target_vessel_class}</div>
          <div className="opacity-90">{term} day charter</div>
        </div>
      </div>

      {/* Lock vs wait, in money */}
      <div className="flex-1 overflow-auto p-1.5">
        <table className="desk-table">
          <thead>
            <tr>
              <th>Option</th>
              <th className="text-right">Rate $/day</th>
              <th className="text-right">Cost over term</th>
              <th className="text-right">Timing</th>
            </tr>
          </thead>
          <tbody>
            <tr className={isLock ? 'bg-go-soft' : undefined}>
              <td className="font-semibold">Lock today</td>
              <td className="desk-num text-right">{formatUsd(lockRate)}</td>
              <td className="desk-num text-right font-semibold">{formatUsdCompact(lockTerm)}</td>
              <td className="text-right text-[11px] text-muted-foreground">now</td>
            </tr>
            <tr className={!isLock ? 'bg-wait-soft' : undefined}>
              <td className="font-semibold">Wait for trough</td>
              <td className="desk-num text-right">
                {waitRate != null ? formatUsd(waitRate) : '—'}
              </td>
              <td className="desk-num text-right font-semibold">
                {waitTerm != null ? formatUsdCompact(waitTerm) : '—'}
              </td>
              <td className="text-right text-[11px] text-muted-foreground">{windowText}</td>
            </tr>
            <tr>
              <td className="font-semibold">Ceiling</td>
              <td className="desk-num text-right text-muted-foreground">
                {formatUsd(quote.ceiling_usd_per_day)}
              </td>
              <td
                className={cn(
                  'text-right text-[11px] font-semibold',
                  lockRate <= quote.ceiling_usd_per_day ? 'text-go' : 'text-risk',
                )}
              >
                {lockRate <= quote.ceiling_usd_per_day ? 'today under ceiling' : 'today over ceiling'}
              </td>
              <td className="text-right text-[11px] text-muted-foreground">walk-away line</td>
            </tr>
          </tbody>
        </table>

        <div className="mt-1.5 space-y-0.5 border-t border-border pt-1.5">
          <div className="stat-row">
            <span className="stat-label">
              {edgeFavorsLock ? 'Locking beats always-spot by, expected' : 'Staying spot beats locking by, expected'}
            </span>
            <span className={cn('stat-value font-semibold', edgeFavorsLock ? 'text-go' : 'text-risk')}>
              {formatUsd(Math.abs(edge))}
            </span>
          </div>
          <div className="stat-row">
            <span className="stat-label">Range across the term (P10 to P90)</span>
            <span className="stat-value text-[10px] text-muted-foreground">
              {formatUsdCompact(p10Total)} to {formatUsdCompact(p90Total)}
            </span>
          </div>
          <div className="stat-row">
            <span className="stat-label">Probability locking beats spot</span>
            <span className="flex items-center gap-2">
              <span className="h-1.5 w-20 overflow-hidden rounded-full bg-muted">
                <span
                  className={cn('block h-full rounded-full', edgeFavorsLock ? 'bg-go' : 'bg-risk')}
                  style={{ width: `${confidence}%` }}
                />
              </span>
              <span className="stat-value">{confidence}%</span>
            </span>
          </div>
          {optionValue != null && optionValue > 0 && (
            <p className="pt-1 text-[10px] leading-snug text-muted-foreground">
              The <strong className="text-foreground">{quote.lock_action}</strong> verdict above
              already prices in the value of keeping the right to wait and lock later instead
              ({formatUsd(optionValue)}/day) — that's why it can differ from the simple
              always-spot comparison above, which doesn't account for that option.
            </p>
          )}
          {/* 2.4: a real per-basin cyclone climatology + live 7-day marine
           * forecast delay taxes the WAIT branch of the fused decision above
           * (opt.stopping.solve_lock_or_wait) -- shown only when it actually
           * moved the number, using the backend's own plain-English sentence
           * rather than re-deriving one here. */}
          {quote.transit_buffer != null && quote.transit_buffer.expected_delay_days > 0 && (
            <p className="pt-1 text-[10px] leading-snug text-muted-foreground">
              <span className="font-semibold text-foreground">Weather buffer: </span>
              +{quote.transit_buffer.expected_delay_days.toFixed(1)} expected delay day(s) priced
              into the WAIT comparison above. {quote.transit_buffer.explanation}
            </p>
          )}
          <p className="border-t border-border/60 pt-1 text-[10px] leading-snug text-muted-foreground">
            <span className="font-semibold text-foreground">Re-check: </span>
            {rec.review_trigger.schedule.toLowerCase()}, or immediately if{' '}
            {rec.review_trigger.conditions.join(', or if ')}.
          </p>
          {/* F-37: the backend already builds a real, plain-English "why"
           * for this exact verdict (opt.explain.build_explanations) --
           * previously computed on every quote and never rendered
           * anywhere. Collapsed by default so it doesn't compete with the
           * headline numbers above; one click away instead of hidden. */}
          <button
            type="button"
            onClick={() => setShowWhy((v) => !v)}
            className="flex w-full items-center gap-1 pt-1 text-[10px] font-semibold uppercase tracking-wide text-market"
          >
            {showWhy ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
            Why this verdict
          </button>
          {showWhy && (
            <ul className="list-inside list-disc space-y-0.5 pb-0.5 text-[10px] leading-snug text-muted-foreground">
              {quote.explanations.lock_wait.factors.map((f, i) => (
                <li key={i}>{f}</li>
              ))}
              <li className="italic">{quote.explanations.lock_wait.method}</li>
            </ul>
          )}
        </div>
      </div>
    </div>
  )
}
