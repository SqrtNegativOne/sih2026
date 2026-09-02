import { Ship } from 'lucide-react'
import { motion, useReducedMotion } from 'motion/react'
import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { Field } from '@/components/ui/field'
import { bootstrapAdmin, login } from '@/lib/api'
import { useAuth } from '@/lib/auth-context'
import { transition } from '@/lib/motion'
import { cn } from '@/lib/utils'

/**
 * The sign-in screen, and the first-run screen, which are the same screen in
 * two states.
 *
 * On a deployment with no accounts yet this creates the first admin instead of
 * asking for one. There is deliberately no seeded account and no default
 * password anywhere in this system: a well-known first-run credential is the
 * single most reliably exploited thing in self-hosted software, and a desk
 * whose value is an auditable record of who decided what cannot begin life
 * with an account nobody owns.
 *
 * A failed sign-in says the same thing whatever went wrong — unknown username,
 * wrong password, disabled account. The server does not distinguish them
 * either; separating them would hand an attacker free account enumeration, and
 * the person actually locked out is no better served by knowing which of the
 * three it was.
 */

const inputCls =
  'h-9 w-full rounded-sm border border-input bg-surface px-2 text-lead text-foreground ' +
  'transition-colors placeholder:text-muted-foreground/70 hover:border-muted-foreground/60 ' +
  'focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/40'

export function SignIn() {
  const { status, refresh } = useAuth()
  const reduced = useReducedMotion()
  const firstRun = status?.needs_bootstrap ?? false

  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const ready = username.trim() !== '' && password !== '' && !busy

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (!ready) return
    setBusy(true)
    setError(null)
    try {
      if (firstRun) {
        await bootstrapAdmin({
          username: username.trim(),
          password,
          display_name: displayName.trim(),
        })
      } else {
        await login(username.trim(), password)
      }
      await refresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Sign-in failed.')
      setPassword('')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex h-full items-center justify-center p-6">
      <motion.form
        onSubmit={submit}
        initial={reduced ? false : { opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={reduced ? { duration: 0 } : transition.base}
        className="w-full max-w-sm space-y-4 rounded-md border border-border bg-surface p-5 shadow-raised"
      >
        <div className="flex items-center gap-2">
          <Ship className="h-5 w-5 text-primary" aria-hidden="true" />
          <div>
            <h1 className="text-lead font-bold tracking-tight text-foreground">Chartering Desk</h1>
            <p className="text-caption text-muted-foreground">
              {firstRun ? 'Create the first account' : 'Sign in to continue'}
            </p>
          </div>
        </div>

        {firstRun && (
          <p className="panel-note">
            This deployment has no accounts yet. The account you create now is the administrator —
            it can create everyone else. There is no default password anywhere in this system, by
            design.
          </p>
        )}

        <Field label="Username">
          <input
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="username"
            autoFocus
            className={inputCls}
          />
        </Field>

        {firstRun && (
          <Field label="Display name" hint="The name that appears on ledger lines you record. Defaults to the username.">
            <input
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              autoComplete="name"
              className={inputCls}
            />
          </Field>
        )}

        <Field
          label="Password"
          hint={
            firstRun
              ? 'At least 12 characters. Length is the only rule — NIST advises against composition requirements, which push people toward predictable substitutions.'
              : undefined
          }
        >
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete={firstRun ? 'new-password' : 'current-password'}
            className={inputCls}
          />
        </Field>

        {error && (
          <p role="alert" className="text-caption font-semibold text-risk">
            {error}
          </p>
        )}

        <Button type="submit" variant="primary" size="lg" disabled={!ready} className={cn('w-full')}>
          {busy ? 'Working…' : firstRun ? 'Create administrator' : 'Sign in'}
        </Button>
      </motion.form>
    </div>
  )
}
