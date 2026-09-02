import type {
  AnchorageCalibrationResponse,
  AnchorageCensus,
  AnchoragePortCode,
  ApiError,
  BackhaulResponse,
  ChokepointReference,
  FractureResponse,
  FragilityReport,
  FragilityVariable,
  LandedCostBreakdown,
  LandedCostRequest,
  LedgerLiveResponse,
  LedgerPerformanceResponse,
  LedgerReplayResponse,
  Meta,
  PortBerthsResponse,
  PortCallsResponse,
  PortCode,
  PortListing,
  PortfolioResponse,
  PortRealityReport,
  PortWaitsResponse,
  ProgressStage,
  QuoteEnvelope,
  QuoteRequest,
  TonnageFieldForwardResponse,
  TonnageFieldResponse,
  TonnageFieldValidationResponse,
  VesselClass,
  VesselInput,
} from './types'

// F-39: '/api' only resolves in `npm run dev`, where vite.config.ts proxies
// it to the real backend -- a `npm run build` bundle has no dev server to do
// that, so every request 404s in production unless something in front of
// the static files (nginx, etc.) proxies /api itself. VITE_API_BASE_URL lets
// a production deploy point straight at the backend's real origin instead
// (set it in `frontend/.env.production` or the build environment); omitted,
// this is unchanged '/api' -- the exact dev behaviour today.
const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? '/api'

export class ApiRequestError extends Error {
  status: number

  constructor(status: number, detail: string) {
    super(detail)
    this.status = status
  }
}

async function parseOrThrow<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const body = (await res.json().catch(() => null)) as ApiError | null
    throw new ApiRequestError(res.status, body?.detail ?? res.statusText)
  }
  return res.json() as Promise<T>
}

export async function fetchMeta(): Promise<Meta> {
  return parseOrThrow<Meta>(await fetch(`${BASE_URL}/meta`))
}

export async function fetchPorts(): Promise<PortListing[]> {
  return parseOrThrow<PortListing[]>(await fetch(`${BASE_URL}/ports`))
}

// ---------------------------------------------------------------------------
// P2 -- M4 Berth Reality Engine / Port Twin
// ---------------------------------------------------------------------------

export interface PortRealityParams {
  vesselDwt: number
  draftM: number
  loaM: number
  beamM: number
  vesselClass?: string
  vesselIsLaden?: boolean
  commodity?: string
  asOf?: string
}

export async function fetchPortReality(code: PortCode, p: PortRealityParams): Promise<PortRealityReport> {
  const qs = new URLSearchParams({
    vessel_dwt: String(p.vesselDwt),
    draft_m: String(p.draftM),
    loa_m: String(p.loaM),
    beam_m: String(p.beamM),
  })
  if (p.vesselClass) qs.set('vessel_class', p.vesselClass)
  if (p.vesselIsLaden !== undefined) qs.set('vessel_is_laden', String(p.vesselIsLaden))
  if (p.commodity) qs.set('commodity', p.commodity)
  if (p.asOf) qs.set('as_of', p.asOf)
  return parseOrThrow<PortRealityReport>(await fetch(`${BASE_URL}/ports/${code}/reality?${qs}`))
}

export async function fetchPortBerths(code: PortCode): Promise<PortBerthsResponse> {
  return parseOrThrow<PortBerthsResponse>(await fetch(`${BASE_URL}/ports/${code}/berths`))
}

export async function fetchPortCalls(
  code: PortCode,
  opts: { limit?: number; offset?: number } = {},
): Promise<PortCallsResponse> {
  const qs = new URLSearchParams()
  if (opts.limit !== undefined) qs.set('limit', String(opts.limit))
  if (opts.offset !== undefined) qs.set('offset', String(opts.offset))
  return parseOrThrow<PortCallsResponse>(await fetch(`${BASE_URL}/ports/${code}/calls?${qs}`))
}

export async function fetchPortWaits(code: PortCode): Promise<PortWaitsResponse> {
  return parseOrThrow<PortWaitsResponse>(await fetch(`${BASE_URL}/ports/${code}/waits`))
}

// ---------------------------------------------------------------------------
// P3 -- M1 Tonnage Field
// ---------------------------------------------------------------------------

export async function fetchTonnageField(asOf?: string): Promise<TonnageFieldResponse> {
  const qs = asOf ? `?${new URLSearchParams({ as_of: asOf })}` : ''
  return parseOrThrow<TonnageFieldResponse>(await fetch(`${BASE_URL}/tonnage-field${qs}`))
}

export async function fetchTonnageFieldForward(horizon = 90): Promise<TonnageFieldForwardResponse> {
  const qs = new URLSearchParams({ horizon: String(horizon) })
  return parseOrThrow<TonnageFieldForwardResponse>(await fetch(`${BASE_URL}/tonnage-field/forward?${qs}`))
}

/** Slow on the first call after backend start (~45s real -- an XGBoost A/B
 * ablation, not a demo delay). Warm calls are fast; callers should show a
 * "computing validation" state rather than a generic spinner so a first-time
 * user understands the wait. */
export async function fetchTonnageFieldValidation(): Promise<TonnageFieldValidationResponse> {
  return parseOrThrow<TonnageFieldValidationResponse>(await fetch(`${BASE_URL}/tonnage-field/validation`))
}

export async function fetchQuote(req: QuoteRequest): Promise<QuoteEnvelope> {
  const res = await fetch(`${BASE_URL}/quote`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  })
  return parseOrThrow<QuoteEnvelope>(res)
}

interface StreamHandlers {
  onStage: (stage: ProgressStage) => void
  onResult: (envelope: QuoteEnvelope) => void
  onError: (err: ApiRequestError) => void
}

/**
 * POST /quote/stream and dispatch its Server-Sent Events. `stage` events feed
 * the live progress checklist; a single `result` event carries the envelope;
 * an `error` event (or a transport failure) becomes an ApiRequestError.
 */
export async function streamQuote(req: QuoteRequest, handlers: StreamHandlers): Promise<void> {
  let res: Response
  try {
    res = await fetch(`${BASE_URL}/quote/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(req),
    })
  } catch {
    handlers.onError(new ApiRequestError(0, 'Could not reach the optimizer. Is the backend running?'))
    return
  }

  if (!res.ok || !res.body) {
    const body = (await res.json().catch(() => null)) as ApiError | null
    handlers.onError(new ApiRequestError(res.status, body?.detail ?? res.statusText))
    return
  }

  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader()
  let buffer = ''

  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += value

    let sep: number
    while ((sep = buffer.indexOf('\n\n')) !== -1) {
      const block = buffer.slice(0, sep)
      buffer = buffer.slice(sep + 2)

      let event = 'message'
      const dataLines: string[] = []
      for (const line of block.split('\n')) {
        if (line.startsWith('event: ')) event = line.slice(7).trim()
        else if (line.startsWith('data: ')) dataLines.push(line.slice(6))
      }
      if (dataLines.length === 0) continue
      const payload = JSON.parse(dataLines.join('\n'))

      if (event === 'stage') handlers.onStage(payload as ProgressStage)
      else if (event === 'result') handlers.onResult(payload as QuoteEnvelope)
      else if (event === 'error') {
        handlers.onError(new ApiRequestError(payload.status_code ?? 500, payload.detail ?? 'Solve failed.'))
      }
    }
  }
}

// ---------------------------------------------------------------------------
// P5 -- Decision Fragility (DF)
// ---------------------------------------------------------------------------

export interface FragilityRequest {
  cargoVolumeDwt: number
  originPort: PortCode
  destPort: PortCode
  laycanStart: string
  laycanEnd: string
  contractTermDays?: number
  commodity?: string
  asOf?: string
  riskTolerance?: number
  variables?: FragilityVariable[]
}

export async function fetchFragility(req: FragilityRequest): Promise<FragilityReport> {
  const res = await fetch(`${BASE_URL}/fragility`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      cargo_volume_dwt: req.cargoVolumeDwt,
      origin_port: req.originPort,
      dest_port: req.destPort,
      laycan_start: req.laycanStart,
      laycan_end: req.laycanEnd,
      contract_term_days: req.contractTermDays,
      commodity: req.commodity,
      as_of: req.asOf,
      risk_tolerance: req.riskTolerance,
      variables: req.variables,
    }),
  })
  return parseOrThrow<FragilityReport>(res)
}

// ---------------------------------------------------------------------------
// P5 -- Live Decision Ledger + Historical Model Replay
// ---------------------------------------------------------------------------

export async function fetchLedgerLive(opts: { from?: string; to?: string } = {}): Promise<LedgerLiveResponse> {
  const qs = new URLSearchParams()
  if (opts.from) qs.set('date_from', opts.from)
  if (opts.to) qs.set('date_to', opts.to)
  return parseOrThrow<LedgerLiveResponse>(await fetch(`${BASE_URL}/ledger/live?${qs}`))
}

export async function fetchLedgerPerformance(opts: { from?: string; to?: string } = {}): Promise<LedgerPerformanceResponse> {
  const qs = new URLSearchParams()
  if (opts.from) qs.set('date_from', opts.from)
  if (opts.to) qs.set('date_to', opts.to)
  return parseOrThrow<LedgerPerformanceResponse>(await fetch(`${BASE_URL}/ledger/live/performance?${qs}`))
}

export async function postLedgerOutcome(req: {
  entryId: string
  realizedRateUsdPerDay: number
  realizedAtDate: string
  note?: string
}): Promise<void> {
  const res = await fetch(`${BASE_URL}/ledger/outcome`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      entry_id: req.entryId,
      realized_rate_usd_per_day: req.realizedRateUsdPerDay,
      realized_at_date: req.realizedAtDate,
      note: req.note,
    }),
  })
  await parseOrThrow<unknown>(res)
}

/** F-38: clears the Live Decision Ledger entirely (all entries and
 * outcomes) -- see backend's DELETE /ledger/live docstring for why this is
 * safe (all-or-nothing, never a selective per-entry deletion). */
export async function resetLedgerLive(): Promise<{ cleared: boolean; records_removed: number }> {
  const res = await fetch(`${BASE_URL}/ledger/live`, { method: 'DELETE' })
  return parseOrThrow<{ cleared: boolean; records_removed: number }>(res)
}

/** Slow on the first call after backend start (~real minutes -- PSO
 * calibration plus a genuine backtest, not a demo delay). Warm calls are
 * fast; callers should show a "computing replay" state, not a generic
 * spinner, so a first-time user understands the wait. */
export async function fetchLedgerReplay(): Promise<LedgerReplayResponse> {
  return parseOrThrow<LedgerReplayResponse>(await fetch(`${BASE_URL}/ledger/replay`))
}

// ---------------------------------------------------------------------------
// P6 -- landed cost + backhaul opportunity (commercial upgrades)
// ---------------------------------------------------------------------------

export async function fetchLandedCost(req: LandedCostRequest): Promise<LandedCostBreakdown> {
  const res = await fetch(`${BASE_URL}/landed-cost`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  })
  return parseOrThrow<LandedCostBreakdown>(res)
}

/** Omitting `candidateLoadPorts` sweeps every other real port -- a real,
 * measured multi-second operation (see opt.backhaul's own module docstring
 * on why): show a "scoring…" state, not a snappy-spinner assumption. */
export async function fetchBackhaul(req: {
  vessel: VesselInput
  dischargePort: PortCode
  candidateLoadPorts?: PortCode[]
  assumedWindowDays?: number
}): Promise<BackhaulResponse> {
  const res = await fetch(`${BASE_URL}/backhaul`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      vessel: req.vessel,
      discharge_port: req.dischargePort,
      candidate_load_ports: req.candidateLoadPorts,
      assumed_window_days: req.assumedWindowDays,
    }),
  })
  return parseOrThrow<BackhaulResponse>(res)
}

/** F-16: opt.portfolio's spot/period-TC/COA coverage mix -- the PS's own
 * stated objective. plantBurdenCoverDays/stockoutCostUsd/spotSourcingHazardRate
 * are real business facts this system cannot infer -- required, no default. */
export async function fetchPortfolio(req: {
  vesselClass: VesselClass
  contractTermDays: number
  plantBurdenCoverDays: number
  stockoutCostUsd: number
  spotSourcingHazardRatePerDay: number
  riskAversionK?: number
  originPort?: PortCode
  destPort?: PortCode
  asOf?: string
}): Promise<PortfolioResponse> {
  const res = await fetch(`${BASE_URL}/portfolio`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      vessel_class: req.vesselClass,
      contract_term_days: req.contractTermDays,
      plant_burden_cover_days: req.plantBurdenCoverDays,
      stockout_cost_usd: req.stockoutCostUsd,
      spot_sourcing_hazard_rate_per_day: req.spotSourcingHazardRatePerDay,
      risk_aversion_k: req.riskAversionK,
      origin_port: req.originPort,
      dest_port: req.destPort,
      as_of: req.asOf,
    }),
  })
  return parseOrThrow<PortfolioResponse>(res)
}

// ---------------------------------------------------------------------------
// 4.3 -- Anchorage vessel census (Sentinel-1 SAR spot check) + calibration
// ---------------------------------------------------------------------------

/** null means "no Sentinel-1 scene has been processed for this port yet" --
 * the real, honest 404 case GET /anchorage/{port}/census documents, not an
 * error. Any OTHER failure (network, 5xx) still throws ApiRequestError, same
 * as every other fetch* helper here. */
export async function fetchAnchorageCensus(port: AnchoragePortCode): Promise<AnchorageCensus | null> {
  const res = await fetch(`${BASE_URL}/anchorage/${port}/census`)
  if (res.status === 404) return null
  return parseOrThrow<AnchorageCensus>(res)
}

export async function fetchAnchorageCalibration(): Promise<AnchorageCalibrationResponse> {
  return parseOrThrow<AnchorageCalibrationResponse>(await fetch(`${BASE_URL}/anchorage/calibration`))
}

/** GET /anchorage/{port}/overlay.png -- the real SAR crop with a ring drawn
 * at every detection actually counted in the current census. Returns a
 * plain URL (not fetched here) so an <img> can load/cache/retry it itself;
 * the URL 404s honestly when no overlay has been rendered for the port's
 * current scene yet -- callers should handle that via the <img>'s own
 * onError, same as any other image, not treat it as a thrown ApiRequestError. */
export function anchorageOverlayUrl(port: AnchoragePortCode): string {
  return `${BASE_URL}/anchorage/${port}/overlay.png`
}

// ---------------------------------------------------------------------------
// 3.4 -- Chokepoint Fracture Index + Joint War Committee war-risk premium
// ---------------------------------------------------------------------------

/** Static reference table (id, real name, real centre, radius) -- changes
 * only when opt.chokepoints itself is edited, so a caller can fetch this
 * once and reuse it (e.g. to place markers for every fracture response
 * afterward) rather than refetching per quote. */
export async function fetchChokepoints(): Promise<ChokepointReference[]> {
  return parseOrThrow<ChokepointReference[]>(await fetch(`${BASE_URL}/chokepoints`))
}

export async function fetchFracture(req: {
  originPort: PortCode
  destPort: PortCode
  asOf?: string
  hullValueUsd?: number
}): Promise<FractureResponse> {
  const res = await fetch(`${BASE_URL}/fracture`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      origin_port: req.originPort,
      dest_port: req.destPort,
      as_of: req.asOf,
      hull_value_usd: req.hullValueUsd,
    }),
  })
  return parseOrThrow<FractureResponse>(res)
}

// ---------------------------------------------------------------------------
// FX -- the real USD/INR rate, for desk-wide rupee display.
// ---------------------------------------------------------------------------

export interface FxRate {
  /** Null when no real observation covers the date -- the desk then stays in
   *  dollars rather than inventing a conversion. */
  inr_per_usd: number | null
  as_of: string | null
  provenance: string | null
  reason: string
}

export async function fetchFxRate(): Promise<FxRate> {
  const res = await fetch(`${BASE_URL}/fx`)
  return parseOrThrow<FxRate>(res)
}
