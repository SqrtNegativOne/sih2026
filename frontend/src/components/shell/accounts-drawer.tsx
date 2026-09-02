import { UserPlus, X } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Field } from '@/components/ui/field'
import { createUser, listUsers, updateUser } from '@/lib/api'
import { useAuth } from '@/lib/auth-context'
import type { DeskRole, DeskUser } from '@/lib/types'
import { cn } from '@/lib/utils'

/**
 * Account management. Administrators only, in both modes.
 *
 * "This deployment is open" is a statement about the desk — anyone may price a
 * cargo — and never about the account system itself. The backend enforces that
 * regardless of `DESK_REQUIRE_AUTH`, because a username is half of a
 * credential; this drawer is simply the interface to a door that was already
 * locked.
 */

const inputCls =
  'h-8 w-full rounded-sm border border-input bg-surface px-2 text-body text-foreground ' +
  'transition-colors placeholder:text-muted-foreground/70 hover:border-muted-foreground/60 ' +
  'focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/40'

const ROLE_ORDER: DeskRole[] = ['viewer', 'chartering_manager', 'admin']

export function AccountsDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { status, user: me, refresh } = useAuth()
  const [users, setUsers] = useState<DeskUser[]>([])
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const [newUsername, setNewUsername] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [newDisplay, setNewDisplay] = useState('')
  const [newRole, setNewRole] = useState<DeskRole>('viewer')

  const reload = useCallback(async () => {
    try {
      setUsers(await listUsers())
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load accounts.')
    }
  }, [])

  useEffect(() => {
    if (open) void reload()
  }, [open, reload])

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!open) return null

  const roleHelp = new Map((status?.roles ?? []).map((r) => [r.value, r.description]))

  async function act(fn: () => Promise<unknown>) {
    setBusy(true)
    setError(null)
    try {
      await fn()
      await reload()
      // A change to your own account (a demotion, say) changes what the rest
      // of the desk may show you.
      await refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'That change was refused.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <div className="fixed inset-0 z-40 bg-black/40" onClick={onClose} aria-hidden="true" />
      <aside
        role="dialog"
        aria-modal="true"
        aria-label="Accounts"
        className="fixed right-0 top-0 z-50 flex h-full w-full max-w-lg flex-col border-l border-border bg-surface shadow-raised"
      >
        <header className="flex h-10 shrink-0 items-center justify-between border-b border-border px-3">
          <h2 className="text-lead font-bold tracking-tight text-foreground">Accounts</h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close accounts"
            className="inline-flex h-6 w-6 cursor-pointer items-center justify-center rounded-sm text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
          >
            <X className="h-4 w-4" aria-hidden="true" />
          </button>
        </header>

        <div className="min-h-0 flex-1 space-y-4 overflow-auto p-3">
          {error && (
            <p role="alert" className="panel-note border-risk/40 text-risk">
              {error}
            </p>
          )}

          {!status?.enforced && (
            <p className="panel-note">
              This deployment does not require a sign-in, so these accounts control attribution
              rather than access — outcomes recorded by a signed-in user carry their name. Set{' '}
              <span className="desk-num">DESK_REQUIRE_AUTH=1</span> on the backend to require one.
            </p>
          )}

          <section className="space-y-2">
            <h3 className="stat-label">Existing accounts</h3>
            <div className="overflow-x-auto">
              <table className="desk-table">
                <thead>
                  <tr>
                    <th>Name</th>
                    <th>Role</th>
                    <th>Last signed in</th>
                    <th>Active</th>
                  </tr>
                </thead>
                <tbody>
                  {users.map((u) => (
                    <tr key={u.user_id}>
                      <td>
                        <div className="font-semibold text-foreground">{u.display_name}</div>
                        <div className="desk-num text-micro text-muted-foreground">{u.username}</div>
                      </td>
                      <td>
                        <select
                          value={u.role}
                          disabled={busy}
                          aria-label={`Role for ${u.username}`}
                          onChange={(e) =>
                            void act(() => updateUser(u.user_id, { role: e.target.value as DeskRole }))
                          }
                          className="h-7 rounded-sm border border-input bg-surface px-1 text-caption"
                        >
                          {ROLE_ORDER.map((r) => (
                            <option key={r} value={r} title={roleHelp.get(r)}>
                              {r.replace(/_/g, ' ')}
                            </option>
                          ))}
                        </select>
                      </td>
                      <td className="desk-num text-caption text-muted-foreground">
                        {u.last_login_at ? u.last_login_at.slice(0, 10) : 'never'}
                      </td>
                      <td>
                        <Button
                          size="xs"
                          variant={u.is_active ? 'action' : 'danger'}
                          disabled={busy || u.user_id === me?.user_id}
                          title={
                            u.user_id === me?.user_id
                              ? 'You cannot disable the account you are signed in with.'
                              : undefined
                          }
                          onClick={() =>
                            void act(() => updateUser(u.user_id, { is_active: !u.is_active }))
                          }
                        >
                          {u.is_active ? 'Active' : 'Disabled'}
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="text-micro leading-relaxed text-muted-foreground">
              The last active administrator cannot be disabled or demoted — that would leave this
              deployment with no way to create accounts or restore access. Disabling an account
              also ends its live sessions immediately, rather than at the next expiry.
            </p>
          </section>

          <section className="space-y-2 border-t border-border pt-3">
            <h3 className="stat-label">Add an account</h3>
            <div className="grid gap-2 sm:grid-cols-2">
              <Field label="Username">
                <input
                  value={newUsername}
                  onChange={(e) => setNewUsername(e.target.value)}
                  autoComplete="off"
                  className={inputCls}
                />
              </Field>
              <Field label="Display name">
                <input
                  value={newDisplay}
                  onChange={(e) => setNewDisplay(e.target.value)}
                  autoComplete="off"
                  className={inputCls}
                />
              </Field>
              <Field label="Password" hint="At least 12 characters. Length is the only rule.">
                <input
                  type="password"
                  value={newPassword}
                  onChange={(e) => setNewPassword(e.target.value)}
                  autoComplete="new-password"
                  className={inputCls}
                />
              </Field>
              <Field label="Role" hint={roleHelp.get(newRole)}>
                <select
                  value={newRole}
                  onChange={(e) => setNewRole(e.target.value as DeskRole)}
                  className={cn(inputCls, 'cursor-pointer')}
                >
                  {ROLE_ORDER.map((r) => (
                    <option key={r} value={r}>
                      {r.replace(/_/g, ' ')}
                    </option>
                  ))}
                </select>
              </Field>
            </div>
            <p className="text-micro leading-relaxed text-muted-foreground">
              {roleHelp.get(newRole)}
            </p>
            <Button
              variant="primary"
              size="md"
              disabled={busy || !newUsername.trim() || newPassword.length < 12}
              onClick={() =>
                void act(async () => {
                  await createUser({
                    username: newUsername.trim(),
                    password: newPassword,
                    display_name: newDisplay.trim(),
                    role: newRole,
                  })
                  setNewUsername('')
                  setNewPassword('')
                  setNewDisplay('')
                  setNewRole('viewer')
                })
              }
            >
              <UserPlus className="h-3.5 w-3.5" aria-hidden="true" />
              Create account
            </Button>
          </section>
        </div>
      </aside>
    </>
  )
}
