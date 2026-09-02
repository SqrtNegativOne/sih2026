import { useCallback, useEffect, useId, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { cn } from '@/lib/utils'

/**
 * A real tooltip, replacing the native `title` attribute.
 *
 * The desk's best explanatory writing was hidden behind `title`: every panel's
 * one-line "what question does this answer", every column's caveat, every
 * provenance definition. The native tooltip is a poor host for it — it waits
 * about a second before appearing, renders in the OS chrome style with no
 * relation to the product, truncates long strings on some platforms, never
 * appears on keyboard focus, and never appears at all on touch. In practice
 * the ⓘ icons read as decorative, which is exactly the complaint.
 *
 * This one opens fast, on hover AND on focus, is dismissable with Escape,
 * takes the desk's own surface tokens, and is portaled to <body> with fixed
 * coordinates so it escapes the `overflow: auto` on every Panel body — the
 * same problem, and the same fix, as the Combobox dropdown.
 */
export function Tooltip({
  content,
  children,
  className,
  side = 'bottom',
}: {
  content: React.ReactNode
  children: React.ReactNode
  className?: string
  side?: 'top' | 'bottom'
}) {
  const [open, setOpen] = useState(false)
  const [coords, setCoords] = useState<{ top: number; left: number } | null>(null)
  const anchorRef = useRef<HTMLSpanElement>(null)
  const id = useId()

  const place = useCallback(() => {
    const el = anchorRef.current
    if (!el) return
    const r = el.getBoundingClientRect()
    const MAX_W = 320
    // Keep the panel inside the viewport horizontally; a tooltip on a
    // right-edge panel would otherwise render half off-screen.
    const left = Math.min(Math.max(8, r.left + r.width / 2 - MAX_W / 2), window.innerWidth - MAX_W - 8)
    const top = side === 'top' ? r.top - 8 : r.bottom + 8
    setCoords({ top, left })
  }, [side])

  useEffect(() => {
    if (!open) return
    place()
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    window.addEventListener('scroll', place, true)
    window.addEventListener('resize', place)
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('scroll', place, true)
      window.removeEventListener('resize', place)
      window.removeEventListener('keydown', onKey)
    }
  }, [open, place])

  return (
    <>
      <span
        ref={anchorRef}
        tabIndex={0}
        aria-describedby={open ? id : undefined}
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onClick={() => setOpen((v) => !v)}
        className={cn(
          'inline-flex cursor-help items-center rounded-sm',
          'focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-ring',
          className,
        )}
      >
        {children}
      </span>
      {open &&
        coords &&
        createPortal(
          <div
            id={id}
            role="tooltip"
            style={{
              position: 'fixed',
              top: coords.top,
              left: coords.left,
              maxWidth: 320,
              transform: side === 'top' ? 'translateY(-100%)' : undefined,
            }}
            className="z-[80] rounded-md border border-border bg-popover px-3 py-2 text-caption leading-relaxed text-popover-foreground shadow-raised"
          >
            {content}
          </div>,
          document.body,
        )}
    </>
  )
}
