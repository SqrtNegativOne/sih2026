import { Info, TriangleAlert } from 'lucide-react'
import type { ReactNode } from 'react'
import { TermText } from '@/components/desk/term'
import { Tooltip } from '@/components/ui/tooltip'
import { useExplainMode } from '@/lib/explain'
import { cn } from '@/lib/utils'

interface PanelProps {
  title: string
  /** Small dim text shown next to the title, e.g. a count or subject. */
  meta?: ReactNode
  /** One-line description of what the panel shows, surfaced on the ⓘ hover. */
  hint?: string
  /**
   * The panel's plain-English Layer 2: what question it answers and what to do
   * when the number is bad. Rendered as a visible strip under the header
   * whenever explain mode is on (see `lib/explain.ts`) — not on hover, because
   * the reader who needs this does not know there is anything to hover over.
   *
   * Write it as one or two full sentences addressed to a chartering manager
   * who has never used this desk, and make the second half ACTIONABLE: "…if
   * the margin is under 0.3 m, ask the agent for a fresh survey before
   * fixing", not "…indicates operational fragility". A restatement of the
   * title is worse than nothing; it teaches the reader that these lines are
   * decorative and they stop reading the ones that are not.
   */
  soWhat?: string
  /** Right-aligned controls in the header strip. */
  actions?: ReactNode
  className?: string
  /** Remove body padding when the panel holds a full-bleed table. */
  flush?: boolean
  children: ReactNode
  id?: string
}

/**
 * The single container primitive for the desk: a 1px-bordered white box with a
 * tonal header strip. Hierarchy comes from borders and tone, plus one hairline
 * shadow so a white panel separates from the grey ground it sits on — never
 * from lift on hover.
 */
export function Panel({
  title,
  meta,
  hint,
  soWhat,
  actions,
  className,
  flush,
  children,
  id,
}: PanelProps) {
  const explain = useExplainMode()
  return (
    <section
      id={id}
      className={cn(
        // No h-full here on purpose (see the F-54 note in docs/12_fix_changelog.md):
        // it used to live in this base class, so every Panel greedily filled
        // 100% of its containing block's height. That's correct ONLY when a
        // caller wraps Panel in an explicitly sized box (VoyageDeskPage's own
        // h-[Npx] row divs) -- on the five secondary pages, panels sit in
        // plain top-to-bottom flex-col flow instead, where the FIRST panel's
        // height:100% resolved against the whole page's real height and
        // swallowed it, squashing every panel stacked after it (a real
        // results panel, fully rendered with real data) to ~2px -- invisible,
        // not absent. Panel now sizes to its own content by default; callers
        // that genuinely need it to fill a fixed-height box opt in with
        // `className="h-full"` explicitly (see the desk/* components that do).
        'flex min-h-0 flex-col overflow-hidden rounded-lg border border-border bg-surface shadow-panel',
        // The top-edge highlight. One inset hairline is what separates "a
        // raised surface catching light from above" from "a rectangle of a
        // slightly different grey" -- on a dark ground a cast shadow does
        // nothing, so this is the whole depth cue.
        'ring-1 ring-inset ring-(--panel-edge)',
        'transition-colors duration-200 hover:border-[color-mix(in_oklab,var(--border)_60%,var(--foreground))]',
        className,
      )}
    >
      {/* h-7 (28px), one step of the 4px grid taller than the old h-6: the
          title, the ⓘ, the meta text and an action button all sat inside 24px
          before, which left the button's own 20px box with 2px of air and made
          every header read as cramped against the 8px body padding below it. */}
      <header className="flex h-7 shrink-0 items-center justify-between gap-2 border-b border-border bg-surface-2 px-2">
        <div className="flex min-w-0 items-center gap-2">
          {/* A 2px accent stub before the title. Costs nothing, and it gives
              every header a fixed optical starting point, so a column of
              panels reads as one set rather than as separate boxes that happen
              to be stacked. */}
          <span aria-hidden="true" className="h-3 w-0.5 shrink-0 rounded-full bg-primary/70" />
          <h2 className="truncate text-caption font-bold uppercase tracking-[0.04em] text-primary">
            {title}
          </h2>
          {/* A real tooltip, not `title`. Every panel's one-line "what
              question does this answer" lived behind the native attribute,
              which waits ~1s, renders in OS chrome, never opens on keyboard
              focus and never opens on touch -- so the ⓘ read as decorative and
              the best explanatory text on the desk was effectively invisible. */}
          {hint && (
            <Tooltip
              content={hint}
              className="shrink-0 text-muted-foreground transition-colors hover:text-primary"
            >
              <Info className="h-3 w-3" aria-hidden="true" />
              <span className="sr-only">What this panel shows</span>
            </Tooltip>
          )}
          {meta != null && (
            <span className="truncate text-micro uppercase tracking-wide text-muted-foreground">
              {meta}
            </span>
          )}
        </div>
        {actions != null && <div className="flex shrink-0 items-center gap-1">{actions}</div>}
      </header>
      {/* Layer 2. Sits between the header and the content, inside the panel's
          own border, so it reads as this panel's explanation rather than as
          page copy that happens to be nearby. `shrink-0` keeps it whole when
          the panel is in one of the desk's fixed-height rows -- the body below
          already scrolls, so the cost of this line is that content scrolls
          slightly sooner, never that anything is clipped away. */}
      {explain && soWhat && (
        <p className="shrink-0 border-b border-border bg-surface-2/60 px-2 py-1.5 text-caption leading-snug text-muted-foreground">
          {/* Run it through the glossary too: these sentences are written to
              avoid jargon, but a few terms (laycan, demurrage, ballast) have
              no plain-English substitute that is still accurate. */}
          <TermText text={soWhat} />
        </p>
      )}
      <div className={cn('min-h-0 flex-1', flush ? 'overflow-auto' : 'overflow-auto p-2')}>
        {children}
      </div>
    </section>
  )
}

/**
 * The "nothing to show yet" state. `title` says what is missing; the optional
 * `hint` says how to supply it. Both are required reading for a user who has
 * just clicked something and got a blank panel back — a bare em-dash tells
 * them nothing about whether the desk is broken or simply waiting on input.
 */
export function PanelEmpty({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="panel-state">
      <p className="panel-state-title">{title}</p>
      {hint && <p className="panel-state-hint">{hint}</p>}
    </div>
  )
}

/**
 * The error state. Always shows the real message the API or the solver
 * returned rather than a generic apology, and offers a retry when the caller
 * has something to retry.
 */
export function PanelError({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="panel-state">
      <TriangleAlert className="h-4 w-4 text-risk" aria-hidden="true" />
      <p className="max-w-[52ch] text-body leading-relaxed text-risk">{message}</p>
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="rounded-sm border border-risk/40 bg-risk-soft px-2 py-1 text-caption font-semibold uppercase tracking-wide text-risk transition-colors hover:bg-risk/15"
        >
          Try again
        </button>
      )}
    </div>
  )
}

/**
 * The page-level equivalent of the three states above, for the five secondary
 * screens (Portfolio, Fragility, Tonnage Field, Port Twin, Ledger) whose
 * results occupy the whole area below their control panel rather than sitting
 * inside a Panel.
 *
 * Each of those pages previously hand-wrote its own `text-sm text-risk` div
 * for errors and its own centred sentence for "nothing run yet", so the same
 * three moments looked different on all five — and, more seriously, none of
 * them rendered anything at all while a multi-second real solve was in
 * flight: clicking "Run analysis" left the page visually unchanged, which is
 * indistinguishable from a dead button. That was the user-visible complaint
 * behind F-54 and it is worth never reproducing.
 */
export function PageState({
  tone = 'idle',
  title,
  hint,
  action,
}: {
  tone?: 'idle' | 'busy' | 'error'
  title: string
  hint?: string
  action?: ReactNode
}) {
  return (
    <div className="flex flex-1 items-center justify-center p-6">
      <div className="flex max-w-md flex-col items-center gap-2 text-center">
        {tone === 'error' && <TriangleAlert className="h-5 w-5 text-risk" aria-hidden="true" />}
        {tone === 'busy' && (
          <div className="flex w-full max-w-xs flex-col gap-2" aria-hidden="true">
            <div className="skeleton h-2.5 w-full" />
            <div className="skeleton h-2.5 w-4/5" />
            <div className="skeleton h-2.5 w-2/3" />
          </div>
        )}
        <p
          className={cn(
            'text-lead font-semibold',
            tone === 'error' ? 'text-risk' : 'text-foreground',
          )}
          role={tone === 'busy' ? 'status' : undefined}
          aria-live={tone === 'busy' ? 'polite' : undefined}
        >
          {title}
        </p>
        {hint && (
          <p className="text-caption leading-relaxed text-muted-foreground">{hint}</p>
        )}
        {action}
      </div>
    </div>
  )
}

/**
 * The loading state. Skeleton bars take the shape of the rows that will
 * replace them, so the panel does not jump when real data lands — a spinner
 * centred in the box would resize the content area twice instead of once.
 */
export function PanelLoading({ rows = 4, label = 'Loading' }: { rows?: number; label?: string }) {
  return (
    <div className="flex flex-col gap-2 p-2" role="status" aria-live="polite" aria-busy="true">
      <span className="sr-only">{label}</span>
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="flex items-center gap-2">
          <div className="skeleton h-3 flex-1" style={{ opacity: 1 - i * 0.12 }} />
          <div className="skeleton h-3 w-12 shrink-0" style={{ opacity: 1 - i * 0.12 }} />
        </div>
      ))}
    </div>
  )
}
