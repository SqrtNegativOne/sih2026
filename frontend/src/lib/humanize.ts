/**
 * Rewrites backend-authored "why this is missing" strings into plain English.
 *
 * These arrive from the API carrying raw field names and endpoint paths:
 *
 *   "opex_usd_per_day is not available at the /quote level -- see POST /landed-cost."
 *   "no handling_rate_usd_per_mt supplied -- no repo-derived $/MT handling
 *    tariff exists (opt.network.Port.handling_rate_tph is a throughput rate,
 *    not a price)"
 *
 * Every one of them is *correct* and worth reading — they are the honesty layer
 * that says which parts of a landed cost are real. But `opex_usd_per_day` and
 * `POST /landed-cost` are implementation vocabulary, and a charterer has no
 * reason to decode either.
 *
 * The backend is frozen, so this is presentation-only, and it is deliberately
 * conservative:
 *  - The rewrite must preserve the caveat exactly. Nothing is softened, and no
 *    "unavailable" becomes "unknown" or disappears. Where the original says a
 *    thing is never assumed, the rewrite says so too.
 *  - Anything not explicitly matched is passed through VERBATIM, so a future
 *    backend message can never be silently swallowed or half-translated.
 *  - Callers keep the original string as a tooltip, so the exact wording the
 *    API returned stays reachable for anyone auditing a number.
 */

interface Rule {
  /** Matched against the raw reason, case-insensitively. */
  match: RegExp
  /** The plain-English replacement. Must not weaken the original claim. */
  text: string
}

const RULES: Rule[] = [
  {
    match: /opex_usd_per_day is not available at the \/quote level/i,
    text:
      'A daily operating cost is needed to price waiting time, and a quote does not carry one. Enter it in the assumptions below to include this.',
  },
  {
    match: /no handling_rate_usd_per_mt supplied/i,
    text:
      'No handling tariff supplied. There is no per-tonne handling price on record to fall back on — the port data holds a throughput rate (tonnes per hour), which is a speed, not a price.',
  },
  {
    match: /no demurrage_usd_per_day\/laytime_allowance_days supplied/i,
    text:
      'No demurrage rate or laytime allowance supplied. These are contractual terms agreed per fixture, so they are never assumed.',
  },
  {
    match: /no origin_port supplied/i,
    text:
      'No origin port supplied, so the route — and therefore which war-risk listed areas it enters — cannot be resolved.',
  },
  { match: /^no commodity supplied$/i, text: 'No commodity supplied.' },
  { match: /convert_to_inr not requested/i, text: 'Not converted to rupees.' },
]

/**
 * Returns the plain-English form of a backend reason, or the original string
 * unchanged when nothing matches.
 */
export function humanizeReason(reason: string | null | undefined): string | null {
  if (!reason) return null
  const hit = RULES.find((r) => r.match.test(reason))
  return hit ? hit.text : reason
}

/** True when `humanizeReason` actually rewrote the string — callers use this
 *  to decide whether showing the original in a tooltip adds anything. */
export function wasRewritten(reason: string | null | undefined): boolean {
  if (!reason) return false
  return RULES.some((r) => r.match.test(reason))
}

/**
 * Landed-cost component keys, as the API names them, mapped to the labels the
 * rows in that panel already use — so "excludes: handling_cost, war_risk"
 * becomes "excludes: handling, war risk" and refers to the same words the
 * reader just looked at. Unknown keys pass through with underscores stripped
 * rather than being dropped.
 */
const COMPONENT_LABEL: Record<string, string> = {
  freight: 'freight',
  wait_cost: 'wait / delay',
  handling_cost: 'handling',
  demurrage_cost: 'demurrage',
  war_risk: 'war risk',
  commodity_price: 'commodity price',
}

export function componentLabel(key: string): string {
  return COMPONENT_LABEL[key] ?? key.replace(/_/g, ' ')
}
