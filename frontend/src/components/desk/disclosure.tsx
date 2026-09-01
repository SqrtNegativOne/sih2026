import { ChevronRight } from 'lucide-react'
import { useId, useState, type ReactNode } from 'react'
import { cn } from '@/lib/utils'

/**
 * The desk's single progressive-disclosure control.
 *
 * The reasoning behind a verdict is the most valuable text on the page and
 * also the longest, so it cannot sit inline competing with the numbers it
 * explains — but it must stay one click away, not buried. Every "show the
 * evidence" affordance on the desk routes through this one component so the
 * gesture is identical wherever a user meets it: same chevron, same rotation,
 * same colour, same keyboard and screen-reader wiring.
 *
 * Collapsed by default on purpose. A panel that opens with its own footnotes
 * expanded pushes its headline figure off the visible area of a fixed-height
 * row, which is the same class of bug as F-54 — content present, not seen.
 */
export function Disclosure({
  label,
  children,
  className,
  defaultOpen = false,
}: {
  label: string
  children: ReactNode
  className?: string
  defaultOpen?: boolean
}) {
  const [open, setOpen] = useState(defaultOpen)
  const id = useId()

  return (
    <div className={className}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-controls={id}
        className="group flex w-full items-center gap-1 rounded-sm py-1 text-caption font-semibold uppercase tracking-wide text-market transition-colors hover:text-primary"
      >
        <ChevronRight
          aria-hidden="true"
          className={cn(
            'h-3 w-3 shrink-0 transition-transform duration-150',
            open && 'rotate-90',
          )}
        />
        <span>{label}</span>
      </button>
      {open && (
        <div id={id} className="pb-1">
          {children}
        </div>
      )}
    </div>
  )
}
