import type { DeskView } from '@/App'
import type { QuoteRequest } from '@/lib/types'

/**
 * URL state: which screen you are on, and which quote you are looking at.
 *
 * The review's finding was plain — "no router, no URL state — a refresh loses
 * the quote". Two costs follow from that, and the second is the expensive one:
 *
 *   1. A reload drops you back on an empty desk, and on a screen where a quote
 *      takes real seconds to solve that is a genuine loss, not an annoyance.
 *   2. There is no way to send someone a decision. "Look at the Paradip
 *      quote" is not a thing you can say in an email, which is exactly what a
 *      chartering manager needs to do with an answer they intend to act on.
 *
 * Deliberately NOT react-router. This app has seven screens selected by one
 * `view` state variable and no nested routes, no route params, no loaders and
 * no code that wants a `<Link>`; adding a router would mean restructuring
 * every view swap in App.tsx to buy features none of them use. The hash is
 * enough, it is one dependency-free module, and — the reason that matters here
 * — an app with no hash in the URL behaves exactly as it did before, so
 * nothing that already works can be broken by this.
 *
 * Format:
 *
 *   #/portfolio                          the Portfolio screen, no quote
 *   #/desk?o=NEWCASTLE_AU&d=PARADIP&     the desk, showing a specific quote
 *     t=75000&ls=2026-09-17&le=2026-09-24
 *     &c=Thermal+Coal&term=30&risk=0
 *
 * The quote is carried as the REQUEST, not the answer. A link therefore always
 * re-solves against today's data rather than replaying a stale result — if the
 * market has moved since the link was sent, the recipient sees the moved
 * market, which is the honest behaviour for a decision tool. It also keeps
 * links short and means no result ever has to be serialised into a URL.
 *
 * Vessels and `as_of` are not encoded. A vessel spec is eight fields and would
 * dominate the URL, and `as_of` is what makes a link stale — a shared link
 * reproduces the cargo, and prices it against current data.
 */

const VIEWS: readonly DeskView[] = [
  'desk',
  'season-plan',
  'port-twin',
  'tonnage-field',
  'fragility',
  'ledger',
  'portfolio',
]

export interface DeepLink {
  view: DeskView
  /** Present only when the URL carried a complete, well-formed quote. */
  quote: QuoteRequest | null
}

function isView(value: string): value is DeskView {
  return (VIEWS as readonly string[]).includes(value)
}

/**
 * Read the current URL. Never throws and never partially applies: a hash that
 * is malformed, truncated by an email client, or from an older version of this
 * format degrades to "the desk, no quote" rather than to a half-filled form.
 */
export function readDeepLink(hash: string = window.location.hash): DeepLink {
  const raw = hash.replace(/^#\/?/, '')
  if (!raw) return { view: 'desk', quote: null }

  const [path, search = ''] = raw.split('?')
  const view = isView(path) ? path : 'desk'
  const params = new URLSearchParams(search)

  const origin = params.get('o')
  const dest = params.get('d')
  const tonnes = Number(params.get('t'))
  const laycanStart = params.get('ls')
  const laycanEnd = params.get('le')
  const commodity = params.get('c')
  const contractTermDays = Number(params.get('term'))
  const riskTolerance = Number(params.get('risk'))

  // Every field the solver needs, or nothing. QuoteRequest has eight members
  // and there is no defensible default for any of them here: silently
  // supplying "Thermal Coal" or a 30-day term to a link that did not carry
  // one would mean the recipient is shown a priced answer to a question
  // nobody asked. Either the URL describes a whole cargo or it describes no
  // cargo, and the recipient lands on the ordinary empty desk.
  const complete =
    origin != null &&
    dest != null &&
    laycanStart != null &&
    laycanEnd != null &&
    commodity != null &&
    Number.isFinite(tonnes) &&
    tonnes > 0 &&
    Number.isFinite(contractTermDays) &&
    contractTermDays > 0 &&
    Number.isFinite(riskTolerance)
  if (!complete) return { view, quote: null }

  return {
    view,
    quote: {
      cargo_volume_dwt: tonnes,
      origin_port: origin as QuoteRequest['origin_port'],
      dest_port: dest as QuoteRequest['dest_port'],
      laycan_start: laycanStart,
      laycan_end: laycanEnd,
      contract_term_days: contractTermDays,
      commodity,
      risk_tolerance: riskTolerance,
    },
  }
}

/** Build the hash for a view and, optionally, the quote on screen. */
export function buildDeepLink(view: DeskView, quote: QuoteRequest | null): string {
  if (!quote) return `#/${view}`
  const params = new URLSearchParams({
    o: quote.origin_port,
    d: quote.dest_port,
    t: String(quote.cargo_volume_dwt),
    ls: quote.laycan_start,
    le: quote.laycan_end,
    c: quote.commodity,
    term: String(quote.contract_term_days),
    risk: String(quote.risk_tolerance),
  })
  return `#/${view}?${params.toString()}`
}

/**
 * Write the hash without adding a history entry.
 *
 * `replaceState`, not `pushState`: the desk's view swaps are not navigations a
 * reader wants to step back through one at a time, and pushing would make the
 * browser Back button undo a panel change instead of leaving the app — which
 * is the behaviour people actually complain about in single-page apps. Back
 * still leaves the desk, and the URL still reflects where you are, which is
 * what makes it copyable.
 */
export function writeDeepLink(view: DeskView, quote: QuoteRequest | null): void {
  const next = buildDeepLink(view, quote)
  if (window.location.hash === next) return
  try {
    window.history.replaceState(null, '', next)
  } catch {
    // Some embedded/sandboxed contexts refuse history writes. The app is
    // fully functional without the URL reflecting its state, so this is a
    // lost convenience, never an error worth showing anyone.
  }
}

/** The absolute URL to share for what is currently on screen. */
export function shareableUrl(view: DeskView, quote: QuoteRequest | null): string {
  return `${window.location.origin}${window.location.pathname}${buildDeepLink(view, quote)}`
}
