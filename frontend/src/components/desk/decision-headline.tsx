import { motion, useReducedMotion } from 'motion/react'
import { Figure } from '@/components/desk/figure'
import { addDays, formatShortDate, prettyPort } from '@/lib/format'
import { transition } from '@/lib/motion'
import type { PortListing, QuoteResult } from '@/lib/types'
import { cn } from '@/lib/utils'
import { useMoney } from '@/lib/money-context'

/**
 * The answer, as a sentence, above everything else.
 *
 * The desk was twelve panels of equal visual weight. Every figure on it was
 * correct and none of them said what to *do* -- a reader had to assemble the
 * verdict word, the gap to the walk-away line, the entry window and the
 * expected edge from four separate boxes before the screen meant anything.
 *
 * This states it once, in one line, in the words someone would use out loud,
 * with the two numbers that carry the decision set at display size beside it.
 * Nothing here is new data and nothing is rounded differently -- it is the same
 * `lock_action`, `ceiling_usd_per_day`, `today_quote_usd_per_day` and
 * `expected_savings_usd_total` the panels below show, composed into a claim.
 */
export function DecisionHeadline({
  quote,
  ports,
}: {
  quote: QuoteResult
  ports: PortListing[]
}) {
  const reduced = useReducedMotion()
  const { money } = useMoney()
  const isLock = quote.lock_action === 'LOCK'
  const portName = (c: string) => prettyPort(ports.find((p) => p.code === c)?.name ?? c)

  const gap = Math.abs(quote.today_quote_usd_per_day - quote.ceiling_usd_per_day)
  const start = quote.optimal_entry_window_start_day
  const end = quote.optimal_entry_window_end_day
  const hasWindow = start != null && end != null

  return (
    <motion.section
      aria-label="Decision"
      initial={reduced ? false : { opacity: 0, y: -4 }}
      animate={{ opacity: 1, y: 0 }}
      transition={reduced ? { duration: 0 } : transition.base}
      className={cn(
        'relative overflow-hidden rounded-lg border bg-surface px-4 py-3 shadow-panel',
        'ring-1 ring-inset ring-(--panel-edge)',
        isLock ? 'border-go/35' : 'border-wait/35',
      )}
    >
      {/* A wash in the verdict's own colour, so the strip is identifiable
          before it is read. Under 8% -- it never competes with the text. */}
      <span
        aria-hidden="true"
        className={cn(
          'pointer-events-none absolute inset-0',
          isLock
            ? 'bg-linear-to-r from-go/8 via-transparent to-transparent'
            : 'bg-linear-to-r from-wait/8 via-transparent to-transparent',
        )}
      />

      <div className="relative flex flex-wrap items-center justify-between gap-x-6 gap-y-3">
        <div className="min-w-0 flex-1">
          <div className="text-micro font-semibold uppercase tracking-[0.18em] text-muted-foreground">
            {portName(quote.origin_port)} → {portName(quote.dest_port)} ·{' '}
            {quote.target_vessel_class}
          </div>
          <h1 className="mt-1 text-figure font-semibold leading-snug text-foreground">
            {isLock ? (
              <>
                <span className="font-bold text-go">Lock this charter now.</span> Today&apos;s{' '}
                {money(quote.today_quote_usd_per_day)}/day is inside the walk-away line, and
                waiting is not expected to beat it.
              </>
            ) : (
              <>
                <span className="font-bold text-wait">Wait before fixing.</span> Today&apos;s{' '}
                {money(quote.today_quote_usd_per_day)}/day is {money(gap)}/day above the
                walk-away line
                {hasWindow ? (
                  <>
                    , and the model expects the better entry between{' '}
                    {formatShortDate(addDays(quote.as_of, start))} and{' '}
                    {formatShortDate(addDays(quote.as_of, end))}.
                  </>
                ) : (
                  '.'
                )}
              </>
            )}
          </h1>
        </div>

        {/* The two figures that carry the decision, at display size. */}
        <div className="flex shrink-0 items-start gap-6">
          <Stat
            label="Walk-away line"
            value={<Figure value={quote.ceiling_usd_per_day} kind="usd" />}
            sub="per day"
          />
          <Stat
            label={quote.expected_savings_usd_total > 0 ? 'Expected edge' : 'Expected cost'}
            value={
              <Figure value={Math.abs(quote.expected_savings_usd_total)} kind="usd" />
            }
            sub={`over ${quote.contract_term_days} days`}
            tone={quote.expected_savings_usd_total > 0 ? 'go' : 'risk'}
          />
          <Stat
            label="Confidence"
            value={
              <Figure value={quote.prob_savings_positive} kind="percent" digits={0} />
            }
            sub="locking beats spot"
          />
        </div>
      </div>
    </motion.section>
  )
}

function Stat({
  label,
  value,
  sub,
  tone,
}: {
  label: string
  value: React.ReactNode
  sub: string
  tone?: 'go' | 'risk'
}) {
  return (
    <div className="text-right">
      <div className="text-micro font-semibold uppercase tracking-wide text-muted-foreground">
        {label}
      </div>
      <div
        className={cn(
          'desk-num mt-0.5 text-figure-lg font-bold leading-none',
          tone === 'go' ? 'text-go' : tone === 'risk' ? 'text-risk' : 'text-foreground',
        )}
      >
        {value}
      </div>
      <div className="mt-1 text-micro text-muted-foreground">{sub}</div>
    </div>
  )
}
