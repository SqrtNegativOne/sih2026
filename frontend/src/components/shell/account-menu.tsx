import { Tooltip } from '@/components/ui/tooltip'
import { LogOut, ShieldCheck, User as UserIcon } from 'lucide-react'
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { logout } from '@/lib/api'
import { useAuth } from '@/lib/auth-context'
import type { DeskRole } from '@/lib/types'
import { cn } from '@/lib/utils'

/**
 * Who is signed in, on the navy bar, with the way out.
 *
 * On an open deployment with nobody signed in this says so plainly rather than
 * showing a padlock. "Open desk" is a real mode — a fresh clone has to run end
 * to end with no setup — and an interface that implies protection it does not
 * have is worse than one that admits the door is open.
 */

const ROLE_LABEL: Record<DeskRole, string> = {
  viewer: 'Viewer',
  chartering_manager: 'Chartering manager',
  admin: 'Administrator',
}

export function AccountMenu({ onOpenAccounts }: { onOpenAccounts: () => void }) {
  const { user, status, refresh } = useAuth()
  const [open, setOpen] = useState(false)
  const [coords, setCoords] = useState<{ top: number; right: number } | null>(null)
  const rootRef = useRef<HTMLDivElement>(null)
  const buttonRef = useRef<HTMLButtonElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)

  // Portaled to <body> with fixed coordinates, not rendered inline under the
  // button. Inline, this dropdown was invisible to the mouse: the top bar is
  // `relative z-50` and <main> is also `relative z-50` but comes AFTER it in
  // DOM order, so <main> paints over anything inside the header no matter
  // what z-index the dropdown itself carries -- a child cannot escape its
  // ancestor's stacking context. The menu rendered, looked right, and every
  // click on it hit the page behind. That is the same root cause as F-48 and
  // F-52, and the same fix the Combobox and Tooltip already use.
  useLayoutEffect(() => {
    if (!open) return
    function place() {
      const r = buttonRef.current?.getBoundingClientRect()
      if (r) setCoords({ top: r.bottom + 4, right: window.innerWidth - r.right })
    }
    place()
    window.addEventListener('resize', place)
    window.addEventListener('scroll', place, true)
    return () => {
      window.removeEventListener('resize', place)
      window.removeEventListener('scroll', place, true)
    }
  }, [open])

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    const onClick = (e: MouseEvent) => {
      const t = e.target as Node
      // The menu now lives outside this subtree, so it needs its own check.
      if (!rootRef.current?.contains(t) && !menuRef.current?.contains(t)) setOpen(false)
    }
    window.addEventListener('keydown', onKey)
    window.addEventListener('mousedown', onClick)
    return () => {
      window.removeEventListener('keydown', onKey)
      window.removeEventListener('mousedown', onClick)
    }
  }, [open])

  if (!user) {
    // Open deployment, nobody signed in. Nothing to sign out of, and no
    // sign-in form to offer either — on an open desk the server accepts the
    // request regardless, so a login prompt here would suggest that signing
    // in unlocks something. What it actually does is attribute your ledger
    // entries to you, which is what the label says.
    return (
      // A real Tooltip: "Open desk" without its explanation reads as a
      // button, and the one thing this chip has to convey -- that the desk is
      // deliberately unauthenticated and how to change that -- was sitting
      // behind an attribute that never opens on keyboard focus or on touch.
      <Tooltip content="This deployment does not require a sign-in. Set DESK_REQUIRE_AUTH on the backend to close it.">
        <span className="hidden items-center gap-1 rounded-sm px-1.5 py-0.5 text-caption text-navbar-muted sm:inline-flex">
          <UserIcon className="h-3.5 w-3.5" aria-hidden="true" />
          Open desk
        </span>
      </Tooltip>
    )
  }

  return (
    <div ref={rootRef} className="relative">
      <button
        ref={buttonRef}
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-haspopup="menu"
        // Explicit, because the visible name is `hidden sm:inline` and the
        // monogram beside it is aria-hidden: below the sm breakpoint the
        // button would otherwise have no accessible name at all, which is how
        // it read in a real narrow-viewport run.
        aria-label={`Account: ${user.display_name || user.username}, ${ROLE_LABEL[user.role]}`}
        className="flex cursor-pointer items-center gap-1.5 rounded-sm px-1.5 py-0.5 text-caption text-navbar-foreground transition-colors hover:bg-white/10 focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-white"
      >
        <span
          aria-hidden="true"
          className="flex h-5 w-5 items-center justify-center rounded-full bg-white/15 text-micro font-bold"
        >
          {initials(user.display_name || user.username)}
        </span>
        <span className="hidden sm:inline">{user.display_name || user.username}</span>
      </button>

      {open &&
        coords &&
        createPortal(
          <div
            ref={menuRef}
            role="menu"
            style={{ position: 'fixed', top: coords.top, right: coords.right }}
            className="z-80 w-60 rounded-md border border-border bg-surface p-1 shadow-raised"
          >
          <div className="border-b border-border px-2 py-2">
            <p className="text-body font-semibold text-foreground">
              {user.display_name || user.username}
            </p>
            <p className="desk-num text-micro text-muted-foreground">{user.username}</p>
            <p className="mt-1 flex items-center gap-1 text-caption text-primary">
              <ShieldCheck className="h-3 w-3" aria-hidden="true" />
              {ROLE_LABEL[user.role]}
            </p>
            {!status?.enforced && (
              <p className="mt-1 text-micro leading-relaxed text-muted-foreground">
                This desk does not require a sign-in. You are signed in anyway, so outcomes you
                record carry your name.
              </p>
            )}
          </div>

          {user.role === 'admin' && (
            <MenuItem
              onClick={() => {
                setOpen(false)
                onOpenAccounts()
              }}
            >
              Manage accounts
            </MenuItem>
          )}

          <MenuItem
            onClick={async () => {
              setOpen(false)
              await logout()
              await refresh()
            }}
            icon={LogOut}
          >
            Sign out
          </MenuItem>
          </div>,
          document.body,
        )}
    </div>
  )
}

function MenuItem({
  children,
  onClick,
  icon: Icon,
}: {
  children: React.ReactNode
  onClick: () => void | Promise<void>
  icon?: typeof LogOut
}) {
  return (
    <button
      type="button"
      role="menuitem"
      onClick={() => void onClick()}
      className={cn(
        'flex w-full cursor-pointer items-center gap-2 rounded-sm px-2 py-1.5 text-left text-body',
        'text-foreground transition-colors hover:bg-accent hover:text-accent-foreground',
        'focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-ring',
      )}
    >
      {Icon && <Icon className="h-3.5 w-3.5" aria-hidden="true" />}
      {children}
    </button>
  )
}

/** First letters of the first two words, so a two-word display name reads as
 *  a monogram and a single username still shows something. */
function initials(name: string): string {
  const parts = name.trim().split(/[\s._-]+/).filter(Boolean)
  return (parts[0]?.[0] ?? '?').toUpperCase() + (parts[1]?.[0] ?? '').toUpperCase()
}
