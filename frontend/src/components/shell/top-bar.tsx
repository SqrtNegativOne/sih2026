import { Bell, CircleHelp, Search, Settings, Ship } from 'lucide-react'

const SECTIONS = [
  { id: 'forecast', label: 'Forecast' },
  { id: 'fleet', label: 'Fleet' },
  { id: 'ports', label: 'Ports' },
  { id: 'risk', label: 'Risk' },
  { id: 'map', label: 'Map' },
]

interface TopBarProps {
  onNewQuote: () => void
}

function scrollTo(id: string) {
  document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

export function TopBar({ onNewQuote }: TopBarProps) {
  return (
    // relative z-50: same fix as icon-rail.tsx -- without an explicit
    // z-index this static header sat behind the New Quote drawer's `fixed
    // z-40` backdrop whenever the drawer was open (including on first load),
    // silently swallowing clicks on the Forecast/Fleet/Ports/Risk/Map links.
    // Tied at z-50 with the drawer's own panel, DOM order (the drawer
    // renders after this header) keeps the panel itself on top where it
    // actually overlaps the header's right edge -- New Quote/search/etc.
    // stay correctly unclickable while the drawer covers them.
    <header className="relative z-50 flex h-10 shrink-0 items-center justify-between bg-navbar pl-3 pr-3 text-navbar-foreground">
      <div className="flex h-full items-center gap-5">
        <div className="flex items-center gap-2 text-figure font-extrabold tracking-tight">
          <Ship className="h-4.5 w-4.5" strokeWidth={2} />
          CHARTERING
        </div>
        <nav className="hidden h-full items-end gap-4 md:flex">
          {SECTIONS.map((s, i) => (
            <button
              key={s.id}
              type="button"
              onClick={() => scrollTo(s.id)}
              className={
                'pb-2 text-lead font-medium transition-colors ' +
                (i === 0
                  ? 'border-b-2 border-navbar-foreground text-navbar-foreground'
                  : 'text-navbar-muted hover:text-navbar-foreground')
              }
            >
              {s.label}
            </button>
          ))}
        </nav>
      </div>

      <div className="flex items-center gap-2">
        {/* F-35: search/notifications/settings have no backend behind them
         * at all -- rendering them as live-looking controls that silently
         * do nothing on click read as broken, not "coming soon". Disabled
         * with an honest tooltip, the same treatment the icon rail already
         * gives TC In/TC Out for the identical reason. */}
        {/* Measured at a 1:1 contrast ratio before this change: a half-opacity
            muted foreground on a half-opacity white pill, over navy, came out
            genuinely unreadable rather than merely quiet. A disabled control
            is exempt from the AA floor, but "not implemented" is the whole
            point of showing it — if the label cannot be read, the control just
            looks broken. Now it sits on the navbar's own tone with its real
            muted colour. */}
        <div className="relative hidden sm:block" title="Search — not implemented">
          <Search
            aria-hidden="true"
            className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-navbar-muted"
          />
          <input
            type="text"
            disabled
            aria-label="Search — not implemented"
            placeholder="Search — not implemented"
            className="w-44 cursor-not-allowed rounded-sm border border-white/15 bg-white/5 py-1 pl-7 pr-2 text-lead text-navbar-muted placeholder:text-navbar-muted focus:outline-none"
          />
        </div>
        <button
          type="button"
          onClick={onNewQuote}
          className="rounded bg-white px-2 py-1 text-lead font-semibold text-primary transition-colors hover:bg-white/90"
        >
          New Quote
        </button>
        <button
          type="button"
          disabled
          title="Notifications — not implemented"
          className="cursor-not-allowed rounded p-1 text-navbar-muted/40"
        >
          <Bell className="h-4 w-4" />
        </button>
        <button
          type="button"
          disabled
          title="Settings — not implemented"
          className="cursor-not-allowed rounded p-1 text-navbar-muted/40"
        >
          <Settings className="h-4 w-4" />
        </button>
        <button
          type="button"
          disabled
          title="Help — not implemented"
          className="cursor-not-allowed rounded p-1 text-navbar-muted/40"
        >
          <CircleHelp className="h-4 w-4" />
        </button>
      </div>
    </header>
  )
}
