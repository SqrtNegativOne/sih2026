import { TermText } from '@/components/desk/term'
import { Tooltip } from '@/components/ui/tooltip'
import { cn } from '@/lib/utils'

/**
 * A labelled form control.
 *
 * The wrapper is a real `<label>`, not a `<div>` with a caption above it. That
 * single element is the whole difference between a control a screen reader
 * announces as "Origin, combo box" and one it announces as nothing at all: a
 * `<label>` wrapping a control associates the two implicitly, whereas an
 * adjacent `<span>` is merely text that happens to sit nearby.
 *
 * This exists as one shared component rather than a copy per page because the
 * `<div>` + caption `<span>` shape had been written out by hand on four
 * screens, and an audit of the running app — walking every view and listing
 * every input, select and textarea with no accessible name from any source
 * (aria-label, aria-labelledby, `label[for]`, or an ancestor `<label>`) —
 * found 300 unnamed controls across them. A helper that is correct once is
 * the only version of this that stays correct.
 *
 * `hint` goes through the desk's own Tooltip rather than a native `title`,
 * for the reason `ui/tooltip.tsx` documents: `title` never opens on keyboard
 * focus and never appears on touch, so a caveat parked there is unreadable to
 * exactly the people who most need it.
 *
 * NOT for wrapping a `<button>`. A button is itself a labelable element, so a
 * `<label>` around one *replaces* the button's own text as its accessible
 * name — a "State" caption over a Laden/Ballast toggle would make the toggle
 * announce as "State" and never say which state it is in.
 */
export function Field({
  label,
  hint,
  className,
  children,
}: {
  label: string
  hint?: string
  className?: string
  children: React.ReactNode
}) {
  return (
    <label className={cn('flex flex-col gap-0.5', className)}>
      {hint ? (
        <Tooltip content={hint} className="self-start">
          <span className="stat-label border-b border-dotted border-muted-foreground/50">
            {label}
          </span>
        </Tooltip>
      ) : (
        <span className="stat-label">
          <TermText text={label} />
        </span>
      )}
      {children}
    </label>
  )
}
