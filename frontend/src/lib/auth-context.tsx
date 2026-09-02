import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import { fetchAuthStatus } from '@/lib/api'
import type { AuthStatus, DeskRole, DeskUser } from '@/lib/types'

/**
 * Who is signed in, and whether this deployment requires anyone to be.
 *
 * Two facts, not one. `enforced` is a property of the deployment and
 * `user` is a property of the browser, and the desk reads very differently
 * depending on the pair:
 *
 * - enforced, no user      → show the sign-in screen; nothing else is reachable
 * - enforced, user         → normal desk, gated by role
 * - open, user             → normal desk, and actions are still attributed
 * - open, no user          → normal desk, and it SAYS it is open
 *
 * That last case is the one worth being careful about. An open deployment is a
 * real, deliberate mode — a fresh clone has to run end to end with no setup —
 * but the interface must not imply protection it does not have. So there is no
 * padlock, no greyed-out button pretending to be role-gated: the desk shows an
 * honest "open desk" state and offers to sign in.
 */

const RANK: Record<DeskRole, number> = {
  viewer: 0,
  chartering_manager: 1,
  admin: 2,
}

export interface AuthApi {
  status: AuthStatus | null
  user: DeskUser | null
  /** True until the first /auth/status answers. The shell must not decide
   *  whether to show a sign-in screen before it knows the mode, or a closed
   *  desk flashes its contents for a frame on every load. */
  loading: boolean
  /** The status call itself failed — the backend is down or unreachable.
   *  Distinct from "not signed in", which is a real answer. */
  unreachable: boolean
  /** Rank comparison, mirroring `auth.models.outranks_or_equals`. On an open
   *  deployment with nobody signed in this is true for every role: the desk
   *  really is open, and disabling a control the server would happily accept
   *  would be a lie told by the UI. */
  can: (minimum: DeskRole) => boolean
  refresh: () => Promise<void>
}

const AuthContext = createContext<AuthApi | null>(null)

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [status, setStatus] = useState<AuthStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [unreachable, setUnreachable] = useState(false)

  const refresh = useCallback(async () => {
    try {
      setStatus(await fetchAuthStatus())
      setUnreachable(false)
    } catch {
      // A backend that is down is not an unauthenticated user. Saying
      // "please sign in" when the server is simply not running sends people
      // to type credentials at a form that cannot possibly work.
      setStatus(null)
      setUnreachable(true)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const value = useMemo<AuthApi>(() => {
    const user = status?.user ?? null
    return {
      status,
      user,
      loading,
      unreachable,
      can: (minimum) => {
        if (!status?.enforced && user === null) return true
        if (user === null) return false
        return RANK[user.role] >= RANK[minimum]
      },
      refresh,
    }
  }, [status, loading, unreachable, refresh])

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthApi {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used inside an AuthProvider')
  return ctx
}
