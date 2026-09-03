import { lazy, Suspense, useEffect, useRef, useState } from 'react'
import { QuoteDrawer } from '@/components/desk/quote-drawer'
import { AccountsDrawer } from '@/components/shell/accounts-drawer'
import { AlertsDrawer } from '@/components/shell/alerts-drawer'
import { HelpDrawer } from '@/components/shell/help-drawer'
import { IconRail } from '@/components/shell/icon-rail'
import { SettingsDrawer } from '@/components/shell/settings-drawer'
import { SignIn } from '@/components/shell/sign-in'
import { TopBar } from '@/components/shell/top-bar'
import { AuthProvider, useAuth } from '@/lib/auth-context'
import { readDeepLink, writeDeepLink } from '@/lib/deep-link'
import { loadSettings, type DeskSettings } from '@/lib/settings'
import { MoneyProvider } from '@/lib/money-context'
import type { MoneyContext } from '@/lib/format'
import {
  ApiRequestError,
  fetchChokepoints,
  fetchFxRate,
  fetchMeta,
  fetchPorts,
  streamQuote,
} from '@/lib/api'
import type {
  ChokepointReference,
  ProgressStage,
  PortListing,
  QuoteEnvelope,
  QuoteRequest,
  VesselInput,
} from '@/lib/types'
// The Voyage Desk is what loads on arrival, so it stays a static import --
// code-splitting the landing view only moves its cost into a second round
// trip. The six secondary screens are `lazy` instead: the build was one
// 847 KB chunk, of which these pages and the charting they pull in are the
// bulk, and a user who never opens Season Plan should not download its
// scheduler view to look at a quote.
import { VoyageDeskPage } from '@/pages/voyage-desk-page'

const FragilityPage = lazy(() =>
  import('@/pages/fragility-page').then((m) => ({ default: m.FragilityPage })),
)
const LedgerPage = lazy(() =>
  import('@/pages/ledger-page').then((m) => ({ default: m.LedgerPage })),
)
const PortfolioPage = lazy(() =>
  import('@/pages/portfolio-page').then((m) => ({ default: m.PortfolioPage })),
)
const PortTwinPage = lazy(() =>
  import('@/pages/port-twin-page').then((m) => ({ default: m.PortTwinPage })),
)
const SeasonPlanPage = lazy(() =>
  import('@/pages/season-plan-page').then((m) => ({ default: m.SeasonPlanPage })),
)
const TonnageFieldPage = lazy(() =>
  import('@/pages/tonnage-field-page').then((m) => ({ default: m.TonnageFieldPage })),
)

/** Shown for the fraction of a second a secondary screen's chunk is in
 *  flight. It says "fetching the screen", not "computing" -- this desk has
 *  several genuinely slow computations and the two must not look alike. */
function ViewLoading() {
  return (
    <div className="flex h-full items-center justify-center p-8 text-lead text-muted-foreground">
      Loading this screen…
    </div>
  )
}

export type DeskView =
  | 'desk'
  | 'season-plan'
  | 'port-twin'
  | 'tonnage-field'
  | 'fragility'
  | 'ledger'
  | 'portfolio'

const VIEW_LABEL: Partial<Record<DeskView, string>> = {
  // The desk can finally name itself here. It used to be left out on purpose
  // (F-34): the rail carried six scroll-to-anchor links into the desk and
  // nothing tracked which section was actually in view, so highlighting any
  // one of them would have been a guess. The rail now has exactly one honest
  // desk row, so "you are on the Voyage Desk" is a true statement again.
  desk: 'Voyage Desk',
  'season-plan': 'Season Plan',
  'port-twin': 'Port Twin',
  'tonnage-field': 'Tonnage Field',
  fragility: 'Fragility',
  ledger: 'Ledger',
  portfolio: 'Portfolio',
}

function Shell() {
  const auth = useAuth()
  // The URL as it was when the app opened, read exactly once.
  //
  // This must be captured rather than re-read later, and the reason is a bug
  // this had on the first attempt: the effect that keeps the URL in sync runs
  // on mount with no quote yet loaded, so it rewrites the hash to a bare
  // `#/desk` -- and by the time the port list has arrived and the auto-run
  // effect is ready to act, the cargo it was supposed to solve has been erased
  // from the address bar by this app's own housekeeping. Reading at mount and
  // holding the value makes the two effects independent of each other's
  // ordering.
  const [initialLink] = useState(() => readDeepLink())
  // Initialised from the URL, not hardcoded to the desk -- a link to
  // #/portfolio has to land on Portfolio, and it has to do so on the FIRST
  // render rather than by flashing the desk and then swapping.
  const [view, setView] = useState<DeskView>(initialLink.view)
  const [ports, setPorts] = useState<PortListing[]>([])
  const [portsError, setPortsError] = useState<string | null>(null)
  // 3.4: the static chokepoint reference table (real id/name/centre/radius)
  // -- cheap and cacheable, same "fetch once at app start" treatment as
  // `ports` above, since it only changes when opt.chokepoints itself is
  // edited. Failure is non-fatal: the map overlay simply has nothing to
  // join a quote's fracture bands against, same as an empty `ports`.
  const [chokepoints, setChokepoints] = useState<ChokepointReference[]>([])
  const [latestDate, setLatestDate] = useState<string | null>(null)
  const [envelope, setEnvelope] = useState<QuoteEnvelope | null>(null)
  // P6: retained so VoyageDeskPage can offer a real backhaul sweep for the
  // vessel(s) actually quoted -- QuoteResult never echoes the full vessel
  // specs back (only vessel_id inside assignments/repositioning), so this
  // is the one place the real draft/beam/LOA/DWT the user typed still exists.
  const [lastVessels, setLastVessels] = useState<VesselInput[]>([])
  // The request behind whatever is on screen. Kept so the URL can describe the
  // current quote, and so a reload or a shared link re-solves the same cargo.
  const [lastRequest, setLastRequest] = useState<QuoteRequest | null>(null)
  const [stages, setStages] = useState<ProgressStage[]>([])
  const [solving, setSolving] = useState(false)
  const [quoteError, setQuoteError] = useState<string | null>(null)
  // F-53: this used to default to `true` -- the drawer, and its
  // full-viewport backdrop, auto-opened on every fresh load. That backdrop
  // is exactly what F-48/F-52 had to chase down and patch with z-index
  // fixes on the nav rail, top bar, and <main> in turn -- three separate
  // fixes for the same root cause, and still the first thing a user hits
  // on load. Defaulting closed removes the bug class at its source instead
  // of relying on every future page/panel remembering to out-z-index a
  // backdrop it may not even know exists: the empty desk state already has
  // its own explicit "New Charter Quote" button for a user who wants to
  // open it.
  const [drawerOpen, setDrawerOpen] = useState(false)
  // Settings and Help replace two of the four "not implemented" controls the
  // top bar used to carry. Both default closed, for the same F-53 reason the
  // quote drawer does: a modal that mounts open puts a click-eating backdrop
  // over the whole app before the user has done anything.
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [helpOpen, setHelpOpen] = useState(false)
  const [accountsOpen, setAccountsOpen] = useState(false)
  const [alertsOpen, setAlertsOpen] = useState(false)
  // Bumped when the alerts drawer closes, so the bell's count reflects what
  // was just read rather than waiting for its next slow poll.
  const [alertsRefreshKey, setAlertsRefreshKey] = useState(0)
  const [settings, setSettings] = useState<DeskSettings>(() => loadSettings())
  // The real FRED USD/INR observation, fetched once. Null until it answers,
  // and null forever if no real observation covers the pricing date -- in
  // which case `money()` keeps returning dollars rather than inventing a
  // conversion, whatever the currency preference says.
  const [inrPerUsd, setInrPerUsd] = useState<number | null>(null)
  const [fxAsOf, setFxAsOf] = useState<string | null>(null)
  const runId = useRef(0)

  // Only once the desk is actually reachable. These four fired on mount
  // unconditionally, which on a closed deployment meant four guaranteed 401s
  // in the console behind the sign-in screen -- before the app even knew
  // whether anyone was signed in. Harmless in effect and awful in practice:
  // real failures become impossible to spot in a console that always has
  // errors in it, and a user's first impression of the product is a wall of
  // red. `admitted` is false while /auth/status is still in flight, so the
  // requests wait for the answer rather than racing it.
  const admitted = !auth.loading && !auth.unreachable && (!auth.status?.enforced || !!auth.user)

  useEffect(() => {
    if (!admitted) return
    fetchPorts()
      .then(setPorts)
      .catch((err: unknown) => {
        setPortsError(err instanceof Error ? err.message : 'Failed to load ports.')
      })
    fetchMeta()
      .then((m) => setLatestDate(m.latest_data_date))
      .catch(() => setLatestDate(null))
    fetchChokepoints()
      .then(setChokepoints)
      .catch(() => setChokepoints([]))
    fetchFxRate()
      .then((f) => {
        setInrPerUsd(f.inr_per_usd)
        setFxAsOf(f.as_of)
      })
      .catch(() => setInrPerUsd(null))
  }, [admitted])

  function handleSubmit(req: QuoteRequest) {
    const id = ++runId.current
    setSolving(true)
    setStages([])
    setQuoteError(null)
    setDrawerOpen(false)
    setLastVessels(req.vessels ?? [])
    setLastRequest(req)

    void streamQuote(req, {
      onStage: (stage) => {
        if (id === runId.current) setStages((prev) => [...prev, stage])
      },
      onResult: (env) => {
        if (id !== runId.current) return
        setEnvelope(env)
        setSolving(false)
      },
      onError: (err: ApiRequestError) => {
        if (id !== runId.current) return
        setQuoteError(err.message)
        setEnvelope(null)
        setSolving(false)
      },
    })
  }

  // A shared link, or a reload of one, re-solves its cargo once the app is
  // admitted and the port list has arrived.
  //
  // The guard matters more than it looks. `admitted` and `ports` both settle
  // asynchronously and this effect depends on them, so without a ref it would
  // re-submit the same quote every time either changed -- the desk would
  // appear to work, and would be solving the same cargo two or three times
  // over on every cold load. One link, one solve.
  const deepLinkRan = useRef(false)
  useEffect(() => {
    if (deepLinkRan.current || !admitted || ports.length === 0) return
    deepLinkRan.current = true
    if (!initialLink.quote) return
    setDrawerOpen(false)
    handleSubmit(initialLink.quote)
    // handleSubmit is redeclared each render and is not a dependency worth
    // stabilising for a one-shot effect that is already ref-guarded.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [admitted, ports.length, initialLink])

  // Someone pasting a link into a tab that is ALREADY open.
  //
  // Changing only the fragment does not reload the document, so without this
  // the link that works perfectly from an email does nothing at all when
  // pasted into the address bar of a desk the reader already has in front of
  // them -- and "the link is broken" is what they will report, reasonably.
  //
  // Safe to honour in full, quote included: `writeDeepLink` uses
  // `replaceState`, which by specification does NOT fire `hashchange`, so this
  // handler only ever sees a navigation a person actually performed.
  useEffect(() => {
    function onHashChange() {
      const link = readDeepLink()
      setView(link.view)
      if (link.quote) {
        setDrawerOpen(false)
        handleSubmit(link.quote)
      }
    }
    window.addEventListener('hashchange', onHashChange)
    return () => window.removeEventListener('hashchange', onHashChange)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Keep the URL describing what is on screen. `replaceState` only, so the
  // Back button still leaves the app rather than stepping backwards through
  // panel changes -- see lib/deep-link.ts.
  //
  // Held back until the incoming link has been consumed above, so the address
  // bar never briefly loses the cargo the user is waiting on -- a URL that
  // blinks from a full quote to `#/desk` and back reads as the link having
  // failed, right at the moment the recipient is deciding whether to trust it.
  useEffect(() => {
    if (!deepLinkRan.current && initialLink.quote) return
    writeDeepLink(view, lastRequest)
  }, [view, lastRequest, initialLink])

  /**
   * The worked example: a real lane, submitted through the ordinary path.
   *
   * Newcastle to Paradip is the trade the problem statement itself names, and
   * every figure it produces is computed exactly as any other quote's is --
   * this fills the request and submits it, it does not return a stored
   * answer. The laycan is set a fortnight out from the real last day of
   * market data rather than from the wall clock, for the same reason the
   * quote form resyncs to it (F-02): pricing from a date the dataset does not
   * reach is not a demo, it is a wrong answer.
   */
  function handleRunExample() {
    const anchor = latestDate ?? new Date().toISOString().slice(0, 10)
    const start = new Date(`${anchor}T00:00:00Z`)
    start.setUTCDate(start.getUTCDate() + 14)
    const end = new Date(start)
    end.setUTCDate(end.getUTCDate() + 7)
    handleSubmit({
      cargo_volume_dwt: 75_000,
      origin_port: 'NEWCASTLE_AU',
      dest_port: 'PARADIP',
      laycan_start: start.toISOString().slice(0, 10),
      laycan_end: end.toISOString().slice(0, 10),
      contract_term_days: 30,
      commodity: 'Thermal Coal',
      as_of: anchor,
      risk_tolerance: 0,
    })
  }

  const moneyCtx: MoneyContext = {
    currency: settings.currency,
    inrPerUsd,
  }

  // Every hook above runs unconditionally; the gates below are the last thing
  // before render, so switching between the sign-in screen and the desk never
  // changes the hook order.
  if (auth.loading) {
    // Deliberately blank rather than a spinner. This resolves in one local
    // request, and the only thing worse than a brief blank frame is a closed
    // desk that flashes its contents before deciding to ask for a password.
    return <div className="h-full bg-background" aria-busy="true" />
  }

  if (auth.unreachable) {
    // A backend that is down is not an unauthenticated user. Showing a
    // sign-in form here would send someone to type credentials at a server
    // that cannot answer.
    return (
      <div className="flex h-full items-center justify-center bg-background p-6">
        <div className="max-w-sm space-y-2 text-center">
          <p className="text-lead font-semibold text-risk">Cannot reach the desk</p>
          <p className="text-caption leading-relaxed text-muted-foreground">
            The backend is not answering. Start it with{' '}
            <span className="desk-num">uv run uvicorn backend.main:app --reload</span> and reload
            this page.
          </p>
        </div>
      </div>
    )
  }

  if (auth.status?.enforced && !auth.user) {
    return (
      <div className="h-full bg-background">
        <SignIn />
      </div>
    )
  }

  return (
    <MoneyProvider value={moneyCtx}>
    <div className="flex h-full flex-col overflow-hidden">
      {/* First focusable thing in the document. Without it, reaching the
          desk's primary action by keyboard took a measured 19 tab presses --
          the top bar's five section links plus the rail's thirteen module
          buttons all precede <main> in DOM order. */}
      <a href="#desk-main" className="skip-link">
        Skip to the desk
      </a>
      <TopBar
        dataThrough={latestDate}
        onNewQuote={() => setDrawerOpen(true)}
        onOpenSettings={() => setSettingsOpen(true)}
        onOpenHelp={() => setHelpOpen(true)}
        onOpenAccounts={() => setAccountsOpen(true)}
        onOpenAlerts={() => setAlertsOpen(true)}
        alertsRefreshKey={alertsRefreshKey}
      />
      <div className="flex min-h-0 flex-1">
        <IconRail active={VIEW_LABEL[view]} onSelectView={setView} />
        {/* F-52: the icon-rail/top-bar z-50 fix (F-48) only restored the
            NAV controls' own clickability through the drawer's backdrop --
            it never fixed the actual PAGE CONTENT here, which had no
            z-index either. drawerOpen defaults to true on first load, so a
            user who navigated straight to Portfolio/Fragility/Tonnage
            Field/Port Twin via the (now-clickable) rail still landed on a
            page whose own controls -- Run analysis, Run sweep, any button
            in here -- were silently covered by the same backdrop and did
            nothing when clicked. Confirmed live via elementFromPoint at the
            real Portfolio "Run analysis" button's screen coordinates: it
            resolved to the backdrop div, not the button. Same z-50 fix,
            same reasoning: DOM order (the drawer renders after this <main>)
            keeps the drawer panel itself on top where it actually overlaps
            this element's right edge. */}
        <main
          id="desk-main"
          tabIndex={-1}
          className="relative z-50 min-w-0 flex-1 overflow-y-auto p-2 focus:outline-none"
        >
          <Suspense fallback={<ViewLoading />}>
          {view === 'season-plan' ? (
            <SeasonPlanPage ports={ports} latestDate={latestDate} />
          ) : view === 'port-twin' ? (
            <PortTwinPage ports={ports} />
          ) : view === 'tonnage-field' ? (
            <TonnageFieldPage />
          ) : view === 'fragility' ? (
            <FragilityPage ports={ports} />
          ) : view === 'ledger' ? (
            <LedgerPage />
          ) : view === 'portfolio' ? (
            <PortfolioPage ports={ports} />
          ) : (
            <VoyageDeskPage
              envelope={envelope}
              ports={ports}
              chokepoints={chokepoints}
              stages={stages}
              solving={solving}
              error={quoteError}
              onNewQuote={() => setDrawerOpen(true)}
              onRunExample={handleRunExample}
              vessels={lastVessels}
            />
          )}
          </Suspense>
        </main>
      </div>
      <QuoteDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        ports={ports}
        portsError={portsError}
        latestDate={latestDate}
        submitting={solving}
        onSubmit={handleSubmit}
      />
      <SettingsDrawer
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        settings={settings}
        onChange={setSettings}
        fx={{ inrPerUsd, asOf: fxAsOf }}
      />
      <HelpDrawer open={helpOpen} onClose={() => setHelpOpen(false)} />
      <AccountsDrawer open={accountsOpen} onClose={() => setAccountsOpen(false)} />
      <AlertsDrawer
        open={alertsOpen}
        onClose={() => {
          setAlertsOpen(false)
          setAlertsRefreshKey((k) => k + 1)
        }}
      />
    </div>
    </MoneyProvider>
  )
}

/** AuthProvider wraps the shell rather than living inside it, so the shell
 *  can read the session with a hook and still decide, before rendering
 *  anything, whether this deployment wants a sign-in first. */
function App() {
  return (
    <AuthProvider>
      <Shell />
    </AuthProvider>
  )
}

export default App
