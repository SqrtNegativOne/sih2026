import { Bell, CircleHelp, Settings, Ship } from 'lucide-react'
import { useEffect, useState } from 'react'
import { AccountMenu } from '@/components/shell/account-menu'
import { fetchAlerts } from '@/lib/api'
import { ThemeToggle } from '@/components/shell/theme-toggle'

const SECTIONS = [
  { id: 'forecast', label: 'Forecast' },
  { id: 'fleet', label: 'Fleet' },
  { id: 'ports', label: 'Ports' },
  { id: 'risk', label: 'Risk' },
  { id: 'map', label: 'Map' },
]

interface TopBarProps {
  onNewQuote: () => void
  onOpenSettings: () => void
  onOpenHelp: () => void
  onOpenAccounts: () => void
  onOpenAlerts: () => void
  /** Bumped by the shell whenever something might have changed the count --
   *  closing the alerts drawer, for instance. The bell polls slowly on its
   *  own; this is for the cases where waiting would look broken. */
  alertsRefreshKey: number
}

/** A live icon control on the navy bar. Every one of these does something —
 *  there is no disabled variant, because the top bar no longer carries any
 *  control that is not implemented. */
function IconButton({
  label,
  onClick,
  icon: Icon,
}: {
  label: string
  onClick: () => void
  icon: typeof Settings
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={label}
      aria-label={label}
      className="inline-flex h-6 w-6 cursor-pointer items-center justify-center rounded-sm text-navbar-muted transition-colors hover:bg-white/10 hover:text-navbar-foreground focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-white"
    >
      <Icon className="h-4 w-4" aria-hidden="true" />
    </button>
  )
}

function scrollTo(id: string) {
  document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

export function TopBar({
  onNewQuote,
  onOpenSettings,
  onOpenHelp,
  onOpenAccounts,
  onOpenAlerts,
  alertsRefreshKey,
}: TopBarProps) {
  // The bell shows a real count or it shows nothing. It polls at a slow,
  // deliberate cadence: the backend evaluates on its own interval and the
  // market data behind it only changes when a harvester runs, so a fast poll
  // would be asking a question whose answer cannot have changed.
  const [unread, setUnread] = useState(0)
  useEffect(() => {
    let cancelled = false
    const load = () => {
      fetchAlerts()
        .then((a) => {
          if (!cancelled) setUnread(a.unread)
        })
        // Silent: an unreachable backend already surfaces everywhere else,
        // and a bell that renders an error is worse than one that renders
        // nothing.
        .catch(() => undefined)
    }
    load()
    const id = window.setInterval(load, 120_000)
    return () => {
      cancelled = true
      window.clearInterval(id)
    }
  }, [alertsRefreshKey])

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
        {/*
          F-35 disabled Search / Notifications / Settings / Help with honest
          "not implemented" tooltips, which was the right call at the time: a
          live-looking control that silently does nothing reads as broken.

          But a control that announces its own absence is still a control that
          announces its own absence, and four of them across the top bar make a
          finished product look like a prototype. So each one is now resolved
          rather than labelled:

            Settings  -> built (lib/settings + SettingsDrawer)
            Help      -> built (HelpDrawer: what the desk does, the glossary,
                         and how it treats its own numbers)
            Search    -> REMOVED. There is no cross-entity search to run: ports
                         are one click away on Port Twin, and a box that only
                         filters a 16-row list is furniture.
            Bell      -> REMOVED. Alerts need somewhere to persist and someone
                         to notify; both arrive with the account system, and
                         until then the icon promises a capability that does
                         not exist anywhere in the stack.

          Nothing in the top bar says "not implemented" any more, because
          nothing in it is unimplemented.
        */}
        <button
          type="button"
          onClick={onNewQuote}
          // Was a white pill with --primary text. That reads well against the
          // light theme's navy bar, but --primary on dark is a light blue
          // (#4d9fff) and the same pill measured 2.72:1 -- the screen's main
          // call to action, under the AA floor. The primary fill carries its
          // own paired foreground token in both themes, so the pairing cannot
          // drift like that again.
          className="cursor-pointer rounded-sm bg-primary px-2 py-1 text-lead font-semibold text-primary-foreground transition-colors hover:bg-primary/90 focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-white"
        >
          New Quote
        </button>
        <button
          type="button"
          onClick={onOpenAlerts}
          aria-label={
            unread > 0
              ? `Alerts, ${unread} unread`
              : 'Alerts — nothing new'
          }
          className="relative inline-flex h-6 w-6 cursor-pointer items-center justify-center rounded-sm text-navbar-muted transition-colors hover:bg-white/10 hover:text-navbar-foreground focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-white"
        >
          <Bell className="h-4 w-4" aria-hidden="true" />
          {unread > 0 && (
            <span
              aria-hidden="true"
              className="absolute -right-0.5 -top-0.5 flex h-3.5 min-w-3.5 items-center justify-center rounded-full bg-risk px-1 text-[9px] font-bold leading-none text-risk-fg"
            >
              {unread > 9 ? '9+' : unread}
            </span>
          )}
        </button>
        <AccountMenu onOpenAccounts={onOpenAccounts} />
        <ThemeToggle onDark />
        <IconButton label="Settings" onClick={onOpenSettings} icon={Settings} />
        <IconButton label="Help" onClick={onOpenHelp} icon={CircleHelp} />
      </div>
    </header>
  )
}
