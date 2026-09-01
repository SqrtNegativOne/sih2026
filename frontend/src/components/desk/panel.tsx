import { Info } from 'lucide-react'
import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'

interface PanelProps {
  title: string
  /** Small dim text shown next to the title, e.g. a count or subject. */
  meta?: ReactNode
  /** One-line description of what the panel shows, surfaced on the ⓘ hover. */
  hint?: string
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
 * tonal header strip. No shadows — hierarchy comes from borders and tone.
 */
export function Panel({ title, meta, hint, actions, className, flush, children, id }: PanelProps) {
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
        // `className="h-full"` explicitly (see the nine desk/* components
        // that do).
        'flex min-h-0 flex-col overflow-hidden rounded-[3px] border border-border bg-surface',
        className,
      )}
    >
      <header className="flex h-6 shrink-0 items-center justify-between gap-2 border-b border-border bg-surface-2 px-1.5">
        <div className="flex items-center gap-1.5 truncate">
          <span className="truncate text-[10px] font-bold uppercase tracking-[0.03em] text-primary">
            {title}
          </span>
          {hint && (
            <span
              className="shrink-0 cursor-help text-muted-foreground"
              title={hint}
              aria-label={hint}
            >
              <Info className="h-3 w-3" />
            </span>
          )}
          {meta != null && (
            <span className="truncate text-[10px] uppercase tracking-wide text-muted-foreground">
              {meta}
            </span>
          )}
        </div>
        {actions != null && <div className="flex shrink-0 items-center gap-1">{actions}</div>}
      </header>
      <div className={cn('min-h-0 flex-1', flush ? 'overflow-auto' : 'overflow-auto p-1.5')}>
        {children}
      </div>
    </section>
  )
}
