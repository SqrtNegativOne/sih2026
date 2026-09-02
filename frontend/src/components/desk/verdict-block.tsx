import { motion, useReducedMotion } from 'motion/react'
import { transition } from '@/lib/motion'
import { ExplanationBlock } from '@/components/desk/explanation'
import { Term } from '@/components/desk/term'
import { Figure, FigureGroup } from '@/components/desk/figure'
import { addDays, formatShortDate } from '@/lib/format'
import type { QuoteResult } from '@/lib/types'
import { cn } from '@/lib/utils'
import { useMoney } from '@/lib/money-context'

/**
 * The headline. LOCK or WAIT is the loudest thing on the desk, on a full-bleed
 * semantic fill, and directly under it the explicit money comparison between
 * locking today and waiting for the projected trough.
 */
export function VerdictBlock({ quote }: { quote: QuoteResult }) {
  const reduced = useReducedMotion()
  const { money, moneyCompact } = useMoney()
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
    <div
      className={cn(
        'flex h-full flex-col overflow-hidden rounded-lg border bg-surface shadow-panel',
        'ring-1 ring-inset ring-(--panel-edge)',
        // The verdict panel is the only one on the desk that takes its border
        // from its own answer. A LOCK reads green-edged and a WAIT amber-edged
        // from across the room, before a single figure has been read -- which
        // is the whole job of this panel.
        isLock ? 'border-go/40' : 'border-wait/40',
      )}
    >
      {/*
        The single dominant element on the desk. Everything else on this
        screen is caption-to-body sized; this is the one display-scale figure,
        on the one full-bleed semantic fill, so a five-second glance lands
        here first and reads the answer before reading any evidence.
      */}
      <div
        className={cn(
          'relative flex items-center justify-between gap-3 overflow-hidden px-3 py-2',
          // Paired foreground token, not text-white: on the dark theme --go
          // and --wait are LIGHT inks, and white on them put the loudest
          // element on the desk at roughly 2:1.
          isLock ? 'bg-go text-go-fg' : 'bg-wait text-wait-fg',
        )}
      >
        {/* A soft sheen across the fill. Pure decoration, and the only
            decoration on the desk -- it exists so the one element that carries
            the answer looks lit rather than printed. Kept under 10% so it can
            never affect the contrast of the word sitting on it. */}
        <span
          aria-hidden="true"
          className="pointer-events-none absolute inset-0 bg-linear-to-br from-white/12 via-transparent to-black/10"
        />
        <div className="relative">
          <div className="text-micro font-semibold uppercase tracking-[0.18em] opacity-75">
            Verdict
          </div>
          <motion.div
            key={quote.lock_action}
            initial={reduced ? false : { opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            transition={reduced ? { duration: 0 } : transition.deliberate}
            className="mt-0.5 font-mono text-display font-extrabold tracking-tight"
          >
            {quote.lock_action}
          </motion.div>
        </div>
        <div className="text-right">
          <div className="text-caption font-bold uppercase tracking-wide">
            {quote.target_vessel_class}
          </div>
          <div className="mt-0.5 text-micro opacity-75">{term}-day charter</div>
        </div>
      </div>

      {/* Lock vs wait, in money */}
      <div className="flex-1 overflow-auto p-2">
        <FigureGroup>
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
            {/* The three rates roll to their new values rather than snapping.
                A quote replaces every figure on this screen at once, and a
                hard swap gives no signal about which numbers moved -- the
                delta between "lock today" and "wait for trough" is the whole
                comparison, and motion makes it legible peripherally while the
                eye is still on the verdict. Grouped so the digit columns time
                together instead of shuffling independently. */}
            <tr className={isLock ? 'bg-go-soft' : undefined}>
              <td className="font-semibold">Lock today</td>
              <td className="desk-num text-right">
                <Figure value={lockRate} kind="usd" />
              </td>
              <td className="desk-num text-right font-semibold">
                <Figure value={lockTerm} kind="usdCompact" />
              </td>
              <td className="text-right text-body text-muted-foreground">now</td>
            </tr>
            <tr className={!isLock ? 'bg-wait-soft' : undefined}>
              <td className="font-semibold">Wait for trough</td>
              <td className="desk-num text-right">
                {waitRate != null ? <Figure value={waitRate} kind="usd" /> : '—'}
              </td>
              <td className="desk-num text-right font-semibold">
                {waitTerm != null ? <Figure value={waitTerm} kind="usdCompact" /> : '—'}
              </td>
              <td className="text-right text-body text-muted-foreground">{windowText}</td>
            </tr>
            <tr>
              <td className="font-semibold">
                <Term term="ceiling">Ceiling</Term>
              </td>
              <td className="desk-num text-right text-muted-foreground">
                <Figure value={quote.ceiling_usd_per_day} kind="usd" />
              </td>
              <td
                className={cn(
                  'text-right text-body font-semibold',
                  lockRate <= quote.ceiling_usd_per_day ? 'text-go' : 'text-risk',
                )}
              >
                {lockRate <= quote.ceiling_usd_per_day ? 'today under ceiling' : 'today over ceiling'}
              </td>
              <td className="text-right text-body text-muted-foreground">walk-away line</td>
            </tr>
          </tbody>
        </table>
        </FigureGroup>

        <div className="mt-2 space-y-1 border-t border-border pt-2">
          <div className="stat-row">
            <span className="stat-label">
              {edgeFavorsLock ? 'Locking beats always-spot by, expected' : 'Staying spot beats locking by, expected'}
            </span>
            <span className={cn('stat-value font-semibold', edgeFavorsLock ? 'text-go' : 'text-risk')}>
              {money(Math.abs(edge))}
            </span>
          </div>
          <div className="stat-row">
            <span className="stat-label">Range across the term (P10 to P90)</span>
            <span className="stat-value text-caption text-muted-foreground">
              {moneyCompact(p10Total)} to {moneyCompact(p90Total)}
            </span>
          </div>
          <div className="stat-row">
            <span className="stat-label">Probability locking beats spot</span>
            <span className="flex items-center gap-2">
              {/* The bar is a second, redundant encoding of the number beside
                  it -- never the only one -- so it reads at a glance without
                  being the thing a colour-blind user has to rely on. */}
              <span
                role="img"
                aria-label={`${confidence} percent`}
                className="h-1.5 w-20 overflow-hidden rounded-full bg-border"
              >
                <span
                  className={cn(
                    'block h-full rounded-full transition-[width] duration-300',
                    edgeFavorsLock ? 'bg-go' : 'bg-risk',
                  )}
                  style={{ width: `${confidence}%` }}
                />
              </span>
              <span className="stat-value w-8 text-right font-semibold">{confidence}%</span>
            </span>
          </div>

          {/*
            The two caveats below qualify the numbers above rather than adding
            new ones, so they are grouped into one tinted block instead of
            running as loose paragraphs between the figures -- same words,
            same visibility, but the eye can now skip the prose and come back
            to it, which it could not when it was interleaved.
          */}
          {optionValue != null && optionValue > 0 && (
            <p className="mt-2 rounded-sm border-l-2 border-market/50 bg-market-soft/50 px-2 py-2 text-caption leading-relaxed text-muted-foreground">
              The <strong className="font-semibold text-foreground">{quote.lock_action}</strong>{' '}
              verdict above already prices in the value of keeping the right to wait and lock later
              instead ({money(optionValue)}/day) — that's why it can differ from the simple
              always-spot comparison above, which doesn't account for that option.
            </p>
          )}
          {/* 2.4: a real per-basin cyclone climatology + live 7-day marine
           * forecast delay taxes the WAIT branch of the fused decision above
           * (opt.stopping.solve_lock_or_wait) -- shown only when it actually
           * moved the number, using the backend's own plain-English sentence
           * rather than re-deriving one here. */}
          {quote.transit_buffer != null && quote.transit_buffer.expected_delay_days > 0 && (
            <p className="rounded-sm border-l-2 border-wait/50 bg-wait-soft/60 px-2 py-2 text-caption leading-relaxed text-muted-foreground">
              <span className="font-semibold text-foreground">Weather buffer: </span>
              +{quote.transit_buffer.expected_delay_days.toFixed(1)} expected delay day(s) priced
              into the WAIT comparison above. {quote.transit_buffer.explanation}
            </p>
          )}
          <p className="mt-2 border-t border-border/60 pt-2 text-caption leading-relaxed text-muted-foreground">
            <span className="font-semibold text-foreground">Re-check: </span>
            {rec.review_trigger.schedule.toLowerCase()}, or immediately if{' '}
            {rec.review_trigger.conditions.join(', or if ')}.
          </p>
          {/* F-37: the backend already builds a real, plain-English "why"
           * for this exact verdict (opt.explain.build_explanations) --
           * previously computed on every quote and never rendered
           * anywhere. Collapsed by default so it doesn't compete with the
           * headline numbers above; one click away instead of hidden. */}
          {/* Both of these are backend-written rationales that already ride on
              every quote. `lock_wait` was the only one of the five ever
              rendered; `savings` explains the expected-savings figure a few
              rows above and was being fetched and discarded. */}
          <ExplanationBlock explanation={quote.explanations.lock_wait} label="Why this verdict" />
          <ExplanationBlock
            explanation={quote.explanations.savings}
            label="How the savings were estimated"
          />
        </div>
      </div>
    </div>
  )
}
