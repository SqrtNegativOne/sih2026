import { createContext, useContext, useMemo } from 'react'
import {
  USD_ONLY,
  canShowInr,
  money as moneyOf,
  moneyCompact as moneyCompactOf,
  type MoneyContext,
} from '@/lib/format'

/**
 * Desk-wide currency.
 *
 * Every figure in this system is computed in USD, because that is the currency
 * dry-bulk freight is quoted and settled in. Rupee display is therefore a
 * PRESENTATION conversion applied at render time, from the real FRED DEXINUS
 * USD/INR observation `/fx` serves — there is never a second stored copy of a
 * number, and never a hardcoded rate.
 *
 * The honest failure mode is built in: if `/fx` has no real observation
 * covering the pricing date it returns null, `canShowInr` goes false, and
 * every consumer silently stays in dollars. The desk will not invent a
 * conversion to satisfy a preference.
 */

interface MoneyApi extends MoneyContext {
  /** Full precision, in the active currency. */
  money: (usd: number) => string
  /** Compact — $1.24M, or ₹11.9 crore. */
  moneyCompact: (usd: number) => string
  /** True when rupees are actually being shown (preference set AND rate real). */
  showingInr: boolean
}

const Ctx = createContext<MoneyApi>({
  ...USD_ONLY,
  money: (usd) => moneyOf(usd, USD_ONLY),
  moneyCompact: (usd) => moneyCompactOf(usd, USD_ONLY),
  showingInr: false,
})

export function MoneyProvider({
  value,
  children,
}: {
  value: MoneyContext
  children: React.ReactNode
}) {
  const api = useMemo<MoneyApi>(
    () => ({
      ...value,
      money: (usd: number) => moneyOf(usd, value),
      moneyCompact: (usd: number) => moneyCompactOf(usd, value),
      showingInr: canShowInr(value),
    }),
    [value],
  )
  return <Ctx.Provider value={api}>{children}</Ctx.Provider>
}

export function useMoney(): MoneyApi {
  return useContext(Ctx)
}
