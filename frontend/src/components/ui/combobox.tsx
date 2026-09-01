import { Check, ChevronsUpDown } from 'lucide-react'
import { useId, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { cn } from '@/lib/utils'

export interface ComboOption {
  value: string
  label: string
  hint?: string
}

interface ComboboxProps {
  value: string
  onChange: (value: string) => void
  options: ComboOption[]
  placeholder?: string
  /** Allow a value that is not in `options` (used for free-form cargo type). */
  allowFreeText?: boolean
  disabled?: boolean
}

/**
 * Typeahead combobox: type to filter, Up/Down to move, Enter to pick, Esc to
 * close.
 *
 * The dropdown list is portaled to <body> and positioned with `fixed`
 * coordinates computed from the input's own real screen position, not
 * rendered inline where the input sits in the DOM. Every Panel's content
 * area is `overflow-auto` (so long tables/lists scroll inside their own box
 * instead of pushing the page) -- an inline `absolute` dropdown counts as
 * overflowing content there too, so opening one inside a short panel with
 * many options clipped the list and forced the *panel* to grow a scrollbar
 * just to reach options near the bottom, instead of the list floating
 * cleanly above everything the way a dropdown should. Portaling escapes
 * that ancestor entirely; `fixed` (not `absolute`) means its position is
 * relative to the viewport, correct regardless of which ancestor -- a
 * scrollable panel, the fixed-position quote drawer -- it's opened from.
 */
export function Combobox({
  value,
  onChange,
  options,
  placeholder,
  allowFreeText = false,
  disabled = false,
}: ComboboxProps) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [active, setActive] = useState(0)
  const [coords, setCoords] = useState<{ top: number; left: number; width: number } | null>(null)
  const rootRef = useRef<HTMLDivElement>(null)
  const anchorRef = useRef<HTMLDivElement>(null)
  const listId = useId()

  const selected = options.find((o) => o.value === value)
  const shown = open ? query : (selected?.label ?? (allowFreeText ? value : ''))

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return options
    return options.filter(
      (o) => o.label.toLowerCase().includes(q) || o.value.toLowerCase().includes(q),
    )
  }, [options, query])

  // Reposition whenever the dropdown opens, and keep tracking while it's
  // open -- the anchor can move if an ancestor panel scrolls or the window
  // resizes, and `fixed` positioning doesn't follow that on its own.
  useLayoutEffect(() => {
    if (!open) return
    function reposition() {
      const rect = anchorRef.current?.getBoundingClientRect()
      if (rect) setCoords({ top: rect.bottom + 4, left: rect.left, width: rect.width })
    }
    reposition()
    window.addEventListener('scroll', reposition, true)
    window.addEventListener('resize', reposition)
    return () => {
      window.removeEventListener('scroll', reposition, true)
      window.removeEventListener('resize', reposition)
    }
  }, [open])

  function commit(next: string) {
    onChange(next)
    setOpen(false)
    setQuery('')
  }

  function onKeyDown(e: React.KeyboardEvent) {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setOpen(true)
      setActive((a) => Math.min(a + 1, filtered.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActive((a) => Math.max(a - 1, 0))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      if (filtered[active]) commit(filtered[active].value)
      else if (allowFreeText && query.trim()) commit(query.trim())
    } else if (e.key === 'Escape') {
      setOpen(false)
      setQuery('')
    }
  }

  return (
    <div
      ref={rootRef}
      className="relative"
      onBlur={(e) => {
        if (!rootRef.current?.contains(e.relatedTarget as Node)) {
          if (allowFreeText && query.trim()) onChange(query.trim())
          setOpen(false)
          setQuery('')
        }
      }}
    >
      <div ref={anchorRef} className="relative">
        <input
          type="text"
          role="combobox"
          aria-expanded={open}
          aria-controls={listId}
          disabled={disabled}
          value={shown}
          placeholder={placeholder}
          onFocus={() => setOpen(true)}
          onChange={(e) => {
            setQuery(e.target.value)
            setOpen(true)
            setActive(0)
          }}
          onKeyDown={onKeyDown}
          className="h-7 w-full cursor-pointer rounded-sm border border-input bg-surface px-2 pr-7 text-lead text-foreground transition-colors placeholder:text-muted-foreground/70 hover:border-muted-foreground/60 focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/40 disabled:cursor-not-allowed disabled:bg-surface-2 disabled:text-muted-foreground"
        />
        <ChevronsUpDown
          aria-hidden="true"
          className="pointer-events-none absolute right-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground"
        />
      </div>

      {open &&
        coords &&
        createPortal(
          <ul
            id={listId}
            style={{ position: 'fixed', top: coords.top, left: coords.left, width: coords.width }}
            className="z-[60] max-h-56 overflow-auto rounded-md border border-border bg-surface py-1 shadow-raised"
          >
            {filtered.length === 0 && (
              <li className="px-2 py-2 text-body text-muted-foreground">
                {allowFreeText ? 'Press Enter to use as typed' : 'No match'}
              </li>
            )}
            {filtered.map((o, i) => (
              <li key={o.value}>
                <button
                  type="button"
                  tabIndex={-1}
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => commit(o.value)}
                  onMouseEnter={() => setActive(i)}
                  className={cn(
                    'flex w-full items-center gap-2 px-2 py-2 text-left text-lead',
                    i === active ? 'bg-accent text-accent-foreground' : 'text-foreground',
                  )}
                >
                  <Check
                    className={cn('h-3.5 w-3.5', o.value === value ? 'opacity-100 text-primary' : 'opacity-0')}
                  />
                  <span className="flex-1 truncate">{o.label}</span>
                  {o.hint && (
                    <span className="desk-num text-caption text-muted-foreground">{o.hint}</span>
                  )}
                </button>
              </li>
            ))}
          </ul>,
          document.body,
        )}
    </div>
  )
}
