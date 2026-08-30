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
