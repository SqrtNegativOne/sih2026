import { useEffect, useRef, useState } from 'react'
import { QuoteDrawer } from '@/components/desk/quote-drawer'
import { IconRail } from '@/components/shell/icon-rail'
import { TopBar } from '@/components/shell/top-bar'
import { ApiRequestError, fetchChokepoints, fetchMeta, fetchPorts, streamQuote } from '@/lib/api'
import type {
  ChokepointReference,
  ProgressStage,
  PortListing,
  QuoteEnvelope,
  QuoteRequest,
  VesselInput,
} from '@/lib/types'
import { FragilityPage } from '@/pages/fragility-page'
import { LedgerPage } from '@/pages/ledger-page'
import { PortfolioPage } from '@/pages/portfolio-page'
import { PortTwinPage } from '@/pages/port-twin-page'
import { TonnageFieldPage } from '@/pages/tonnage-field-page'
import { VoyageDeskPage } from '@/pages/voyage-desk-page'

export type DeskView = 'desk' | 'port-twin' | 'tonnage-field' | 'fragility' | 'ledger' | 'portfolio'

const VIEW_LABEL: Partial<Record<DeskView, string>> = {
  'port-twin': 'Port Twin',
  'tonnage-field': 'Tonnage Field',
  fragility: 'Fragility',
  ledger: 'Ledger',
  portfolio: 'Portfolio',
}

function App() {
  const [view, setView] = useState<DeskView>('desk')
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
  const [stages, setStages] = useState<ProgressStage[]>([])
  const [solving, setSolving] = useState(false)
  const [quoteError, setQuoteError] = useState<string | null>(null)
  const [drawerOpen, setDrawerOpen] = useState(true)
  const runId = useRef(0)

  useEffect(() => {
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
  }, [])

  function handleSubmit(req: QuoteRequest) {
    const id = ++runId.current
    setSolving(true)
    setStages([])
    setQuoteError(null)
    setDrawerOpen(false)
    setLastVessels(req.vessels ?? [])

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

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <TopBar onNewQuote={() => setDrawerOpen(true)} />
      <div className="flex min-h-0 flex-1">
        <IconRail active={VIEW_LABEL[view]} onSelectView={setView} />
        <main className="min-w-0 flex-1 overflow-y-auto p-1.5">
          {view === 'port-twin' ? (
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
              vessels={lastVessels}
            />
          )}
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
    </div>
  )
}

export default App
