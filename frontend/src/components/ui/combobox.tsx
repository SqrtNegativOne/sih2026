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
  /**
   * Accessible name for the field. Without one the only name a screen reader
   * can find is the placeholder, so a row of these announces as "Select…"
   * over and over with nothing to say which is the load port and which the
   * discharge port. A visible <label> is not always available — inside a data
   * table the column header is not programmatically associated with an input
   * in the cell — so callers pass the name here.
   */
  label?: string
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
  label,
}: ComboboxProps) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [active, setActive] = useState(0)
  const [coords, setCoords] = useState<{ top: number; left: number; width: number } | null>(null)
  const rootRef = useRef<HTMLDivElement>(null)
  const anchorRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)
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

  // Opening with a value already chosen starts the highlight ON that value
  // rather than at the top of the list -- the common case for this control is
  // correcting a wrong pick, not making a first one -- and scrolls it into
  // view, since a 16-port list is taller than the dropdown.
  useLayoutEffect(() => {
    if (!open) return
    const i = options.findIndex((o) => o.value === value)
    setActive(i >= 0 ? i : 0)
    if (i >= 0) {
      requestAnimationFrame(() => {
        document
          .querySelector(`#${CSS.escape(listId)} [data-index="${i}"]`)
          ?.scrollIntoView({ block: 'nearest' })
      })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

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
      // Only swallow Escape when this list is actually open. The quote and
      // settings drawers close on Escape via a window listener, so without
      // this an open dropdown and its host drawer both closed on one press --
      // you could never dismiss the list and stay in the form. When the list
      // is already closed the event is left alone, so Escape still closes the
      // drawer as it should.
      if (open) {
        e.preventDefault()
        e.stopPropagation()
        setOpen(false)
        setQuery('')
      }
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
        {/*
          `onFocus` used to be the ONLY thing that opened this list, and that
          made a selected combobox a dead end: committing an option closes the
          list but leaves focus in the input, so clicking it again fires no
          focus event and nothing reopens. The only way back to the options was
          to backspace the text until `onChange` fired -- which is exactly what
          a user hit after picking the wrong port.

          Opening is now bound to the click as well, and the chevron is a real
          toggle button rather than a decorative glyph, so there are three
          obvious ways back into the list: click the field, click the chevron,
          or press ArrowDown.
        */}
        <input
          ref={inputRef}
          type="text"
          role="combobox"
          aria-label={label}
          aria-expanded={open}
          aria-controls={listId}
          disabled={disabled}
          value={shown}
          placeholder={placeholder}
          onFocus={() => setOpen(true)}
          onClick={() => setOpen(true)}
          onChange={(e) => {
            setQuery(e.target.value)
            setOpen(true)
            setActive(0)
          }}
          onKeyDown={onKeyDown}
          className="h-7 w-full cursor-pointer rounded-sm border border-input bg-surface px-2 pr-7 text-lead text-foreground transition-colors placeholder:text-muted-foreground/70 hover:border-muted-foreground/60 focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/40 disabled:cursor-not-allowed disabled:bg-surface-2 disabled:text-muted-foreground"
        />
        <button
          type="button"
          tabIndex={-1}
          disabled={disabled}
          aria-label={
            open
              ? `Close the list${label ? ` for ${label}` : ''}`
              : `Show all options${label ? ` for ${label}` : ''}`
          }
          // Keep focus in the input so typing still filters after the toggle.
          onMouseDown={(e) => e.preventDefault()}
          onClick={() => {
            setOpen((o) => !o)
            setQuery('')
            inputRef.current?.focus()
          }}
          className="absolute right-1 top-1/2 flex h-5 w-5 -translate-y-1/2 cursor-pointer items-center justify-center rounded-sm text-muted-foreground transition-colors hover:text-foreground disabled:cursor-not-allowed"
        >
          <ChevronsUpDown className="h-3.5 w-3.5" aria-hidden="true" />
        </button>
      </div>

      {open &&
        coords &&
        createPortal(
          <ul
            id={listId}
            style={{ position: 'fixed', top: coords.top, left: coords.left, width: coords.width }}
            className="z-60 max-h-56 overflow-auto rounded-md border border-border bg-surface py-1 shadow-raised"
          >
            {filtered.length === 0 && (
              <li className="px-2 py-2 text-body text-muted-foreground">
                {allowFreeText ? 'Press Enter to use as typed' : 'No match'}
              </li>
            )}
            {filtered.map((o, i) => (
              <li key={o.value} data-index={i}>
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
