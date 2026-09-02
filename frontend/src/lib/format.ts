/**
 * Currency and number presentation.
 *
 * Every figure this desk holds is computed in USD, because that is the
 * currency dry-bulk freight is quoted and settled in. Rupee display is a
 * PRESENTATION conversion applied at render time using the real FRED DEXINUS
 * USD/INR observation the backend serves from `/fx` — never a stored second
 * copy of a number, and never a hardcoded rate. If no real observation covers
 * the pricing date, `/fx` returns null and the desk stays in dollars rather
 * than inventing a conversion.
 *
 * Indian digit grouping is not cosmetic. SAIL is an Indian PSU, and 3,29,17,550
 * is how that figure is written and ₹3.29 crore is how it is said; rendering
 * ₹32,917,550 would be a currency swap wearing international clothes.
 */

export type Currency = 'USD' | 'INR'

/** Live conversion context. `inrPerUsd` is null until /fx answers, or when no
 *  real observation covers the date — in which case callers stay in USD. */
export interface MoneyContext {
  currency: Currency
  inrPerUsd: number | null
}

export const USD_ONLY: MoneyContext = { currency: 'USD', inrPerUsd: null }

/** True when a rupee request can actually be honoured with a real rate. */
export function canShowInr(ctx: MoneyContext): boolean {
  return ctx.currency === 'INR' && ctx.inrPerUsd != null && ctx.inrPerUsd > 0
}

/**
 * Indian digit grouping: last three digits, then pairs.
 * 32917550 -> "3,29,17,550".
 */
export function groupIndian(value: number, digits = 0): string {
  const neg = value < 0
  const abs = Math.abs(value)
  const fixed = abs.toFixed(digits)
  const [whole, frac] = fixed.split('.')
  let out: string
  if (whole.length <= 3) {
    out = whole
  } else {
    const last3 = whole.slice(-3)
    const rest = whole.slice(0, -3)
    out = `${rest.replace(/\B(?=(\d{2})+(?!\d))/g, ',')},${last3}`
  }
  return `${neg ? '-' : ''}${out}${frac ? `.${frac}` : ''}`
}

/**
 * Compact rupees on the Indian scale: crore (10^7) and lakh (10^5).
 * Mirrors formatUsdCompact's thresholds so the two read as the same shape.
 */
export function formatInrCompact(inr: number): string {
  const abs = Math.abs(inr)
  const sign = inr < 0 ? '-' : ''
  if (abs >= 10_000_000) return `${sign}₹${(abs / 10_000_000).toFixed(2)} crore`
  if (abs >= 100_000) return `${sign}₹${(abs / 100_000).toFixed(2)} lakh`
  return `${sign}₹${groupIndian(Math.round(abs))}`
}

export function formatInr(inr: number): string {
  return `₹${groupIndian(Math.round(inr))}`
}

/** Money in whichever currency the desk is set to, from a USD figure. */
export function money(usd: number, ctx: MoneyContext): string {
  return canShowInr(ctx) ? formatInr(usd * ctx.inrPerUsd!) : formatUsd(usd)
}

/** Compact money in whichever currency the desk is set to. */
export function moneyCompact(usd: number, ctx: MoneyContext): string {
  return canShowInr(ctx) ? formatInrCompact(usd * ctx.inrPerUsd!) : formatUsdCompact(usd)
}

export function formatUsdPerDay(value: number): string {
  return `$${Math.round(value).toLocaleString('en-US')}/day`
}

export function formatUsd(value: number): string {
  return `$${Math.round(value).toLocaleString('en-US')}`
}

/** Compact money for KPI tiles: $1.11M, $152.7K, $940. */
export function formatUsdCompact(value: number): string {
  const abs = Math.abs(value)
  const sign = value < 0 ? '-' : ''
  if (abs >= 1_000_000) return `${sign}$${(abs / 1_000_000).toFixed(2)}M`
  if (abs >= 10_000) return `${sign}$${(abs / 1_000).toFixed(1)}K`
  return `${sign}$${Math.round(abs).toLocaleString('en-US')}`
}

export function formatNumber(value: number, digits = 0): string {
  return value.toLocaleString('en-US', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
}

export function formatPct(fraction: number): string {
  return `${Math.round(fraction * 100)}%`
}

export function addDays(isoDate: string, days: number): Date {
  const d = new Date(`${isoDate}T00:00:00Z`)
  d.setUTCDate(d.getUTCDate() + days)
  return d
}

export function formatShortDate(d: Date): string {
  return d.toLocaleDateString('en-US', { month: 'short', day: '2-digit', timeZone: 'UTC' })
}

export function formatIsoShort(isoDate: string): string {
  return formatShortDate(new Date(`${isoDate}T00:00:00Z`))
}

/** "NEWCASTLE_AU" / "Newcastle_AU" -> "Newcastle AU" for display. */
export function prettyPort(name: string): string {
  return name.replace(/_/g, ' ')
}

/** 4.3: "observed 8 days ago" from a real ISO datetime -- the mandatory
 * staleness caveat for any sparse, non-live signal (e.g. a Sentinel-1
 * anchorage census). Uses the reader's real wall-clock `now`, not a
 * server-supplied one -- deliberately: a stale age is the whole point, and
 * computing it client-side means it keeps advancing on a page left open. */
export function formatRelativeAge(isoDatetime: string): string {
  const then = new Date(isoDatetime).getTime()
  const now = Date.now()
  const diffMs = now - then
  if (!Number.isFinite(diffMs)) return 'unknown age'
  if (diffMs < 0) return 'in the future'
  const hours = diffMs / (1000 * 60 * 60)
  if (hours < 1) return 'observed less than an hour ago'
  if (hours < 24) return `observed ${Math.round(hours)} hour${Math.round(hours) === 1 ? '' : 's'} ago`
  const days = Math.round(hours / 24)
  return `observed ${days} day${days === 1 ? '' : 's'} ago`
}
