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
        // h-full: every caller wraps Panel in a sized box (h-[Npx] or a grid/
        // flex track) expecting it to fill that height -- but a plain sized
        // *block* wrapper (the common case, e.g. VoyageDeskPage's row divs)
        // does not stretch a block child to fill it the way a flex/grid
        // parent would, so without this Panel silently shrank to its own
        // content height instead. Usually invisible because a table/chart's
        // content happens to be close to the intended height anyway; on
        // RouteMap (whose only content is an SVG with a 260px floor, deep
        // below its 424px wrapper) the gap was large enough to make the map
        // look broken -- "suddenly so small" was this bug, not a map change.
        'flex h-full min-h-0 flex-col overflow-hidden rounded-[3px] border border-border bg-surface',
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
