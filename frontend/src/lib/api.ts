import type {
  AnchorageCalibrationResponse,
  AnchorageCensus,
  AnchoragePortCode,
  AlertsResponse,
  ApiError,
  AuthStatus,
  BackhaulResponse,
  ChokepointReference,
  DeskRole,
  DeskUser,
  Firing,
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
  SeasonParcelInput,
  SeasonPlanResponse,
  TonnageFieldForwardResponse,
  TonnageFieldResponse,
  TonnageFieldValidationResponse,
  VesselClass,
  VesselInput,
  Watch,
  WatchKind,
} from './types'

// F-39: '/api' only resolves in `npm run dev`, where vite.config.ts proxies
// it to the real backend -- a `npm run build` bundle has no dev server to do
// that, so every request 404s in production unless something in front of
// the static files (nginx, etc.) proxies /api itself. VITE_API_BASE_URL lets
// a production deploy point straight at the backend's real origin instead
// (set it in `frontend/.env.production` or the build environment); omitted,
// this is unchanged '/api' -- the exact dev behaviour today.
const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? '/api'

/**
 * Every request goes through here so the session cookie is sent consistently.
 *
 * `credentials` defaults to 'same-origin', which is already correct for the
 * normal setup — vite proxies /api to the backend, so the browser sees one
 * origin. It is NOT correct when VITE_API_BASE_URL points at another origin:
 * there the cookie is simply omitted and every call comes back 401 on a
 * closed desk, with nothing in the console to say why. Deciding it once, from
 * the base URL, means no call site can get it wrong or forget.
 *
 * A cross-origin deployment also needs the server to name that origin in
 * DESK_CORS_ORIGINS — the backend enables credentialed CORS only for an
 * explicit list, because a wildcard plus credentials lets any site on the
 * internet make authenticated calls with a logged-in user's cookie.
 */
const CREDENTIALS: RequestCredentials = /^https?:\/\//.test(BASE_URL)
  ? 'include'
  : 'same-origin'

function api(path: string, init?: RequestInit): Promise<Response> {
  return fetch(`${BASE_URL}${path}`, { ...init, credentials: CREDENTIALS })
}

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
  return parseOrThrow<Meta>(await api(`/meta`))
}

export async function fetchPorts(): Promise<PortListing[]> {
  return parseOrThrow<PortListing[]>(await api(`/ports`))
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
  return parseOrThrow<PortRealityReport>(await api(`/ports/${code}/reality?${qs}`))
}

export async function fetchPortBerths(code: PortCode): Promise<PortBerthsResponse> {
  return parseOrThrow<PortBerthsResponse>(await api(`/ports/${code}/berths`))
}

export async function fetchPortCalls(
  code: PortCode,
  opts: { limit?: number; offset?: number } = {},
): Promise<PortCallsResponse> {
  const qs = new URLSearchParams()
  if (opts.limit !== undefined) qs.set('limit', String(opts.limit))
  if (opts.offset !== undefined) qs.set('offset', String(opts.offset))
  return parseOrThrow<PortCallsResponse>(await api(`/ports/${code}/calls?${qs}`))
}

export async function fetchPortWaits(code: PortCode): Promise<PortWaitsResponse> {
  return parseOrThrow<PortWaitsResponse>(await api(`/ports/${code}/waits`))
}

// ---------------------------------------------------------------------------
// P3 -- M1 Tonnage Field
// ---------------------------------------------------------------------------

export async function fetchTonnageField(asOf?: string): Promise<TonnageFieldResponse> {
  const qs = asOf ? `?${new URLSearchParams({ as_of: asOf })}` : ''
  return parseOrThrow<TonnageFieldResponse>(await api(`/tonnage-field${qs}`))
}

export async function fetchTonnageFieldForward(horizon = 90): Promise<TonnageFieldForwardResponse> {
  const qs = new URLSearchParams({ horizon: String(horizon) })
  return parseOrThrow<TonnageFieldForwardResponse>(await api(`/tonnage-field/forward?${qs}`))
}

/** Slow on the first call after backend start (~45s real -- an XGBoost A/B
 * ablation, not a demo delay). Warm calls are fast; callers should show a
 * "computing validation" state rather than a generic spinner so a first-time
 * user understands the wait. */
export async function fetchTonnageFieldValidation(): Promise<TonnageFieldValidationResponse> {
  return parseOrThrow<TonnageFieldValidationResponse>(await api(`/tonnage-field/validation`))
}

export async function fetchQuote(req: QuoteRequest): Promise<QuoteEnvelope> {
  const res = await api(`/quote`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  })
  return parseOrThrow<QuoteEnvelope>(res)
}

interface StreamHandlers<TResult> {
  onStage: (stage: ProgressStage) => void
  onResult: (result: TResult) => void
  onError: (err: ApiRequestError) => void
}

/**
 * The shared Server-Sent Events reader.
 *
 * Two endpoints stream now -- `/quote/stream` and `/fragility/stream` -- and
 * they emit byte-identical event shapes on purpose, so this is one parser
 * rather than two that have to be kept in step. The result payload is the only
 * thing that differs, so it is the only thing the type parameter carries.
 */
async function streamSse<TResult>(
  path: string,
  body: unknown,
  handlers: StreamHandlers<TResult>,
  unreachableMessage: string,
): Promise<void> {
  let res: Response
  try {
    res = await api(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
  } catch {
    handlers.onError(new ApiRequestError(0, unreachableMessage))
    return
  }

  if (!res.ok || !res.body) {
    const err = (await res.json().catch(() => null)) as ApiError | null
    handlers.onError(new ApiRequestError(res.status, err?.detail ?? res.statusText))
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
      else if (event === 'result') handlers.onResult(payload as TResult)
      else if (event === 'error') {
        handlers.onError(new ApiRequestError(payload.status_code ?? 500, payload.detail ?? 'Solve failed.'))
      }
    }
  }
}

/**
 * POST /quote/stream and dispatch its Server-Sent Events. `stage` events feed
 * the live progress checklist; a single `result` event carries the envelope;
 * an `error` event (or a transport failure) becomes an ApiRequestError.
 */
export function streamQuote(req: QuoteRequest, handlers: StreamHandlers<QuoteEnvelope>): Promise<void> {
  return streamSse('/quote/stream', req, handlers, 'Could not reach the optimizer. Is the backend running?')
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

/** The wire shape both fragility endpoints take. */
function fragilityBody(req: FragilityRequest): Record<string, unknown> {
  return {
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
  }
}

/**
 * The streaming sweep. Same report as `fetchFragility`, but the caller sees
 * each variable's search start and finish as it happens -- which for a
 * genuinely multi-second computation is the difference between "working" and
 * "hung".
 */
export function streamFragility(
  req: FragilityRequest,
  handlers: StreamHandlers<FragilityReport>,
): Promise<void> {
  return streamSse(
    '/fragility/stream',
    fragilityBody(req),
    handlers,
    'Could not reach the fragility engine. Is the backend running?',
  )
}

export async function fetchFragility(req: FragilityRequest): Promise<FragilityReport> {
  const res = await api(`/fragility`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(fragilityBody(req)),
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
  return parseOrThrow<LedgerLiveResponse>(await api(`/ledger/live?${qs}`))
}

export async function fetchLedgerPerformance(opts: { from?: string; to?: string } = {}): Promise<LedgerPerformanceResponse> {
  const qs = new URLSearchParams()
  if (opts.from) qs.set('date_from', opts.from)
  if (opts.to) qs.set('date_to', opts.to)
  return parseOrThrow<LedgerPerformanceResponse>(await api(`/ledger/live/performance?${qs}`))
}

export async function postLedgerOutcome(req: {
  entryId: string
  realizedRateUsdPerDay: number
  realizedAtDate: string
  note?: string
}): Promise<void> {
  const res = await api(`/ledger/outcome`, {
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
  const res = await api(`/ledger/live`, { method: 'DELETE' })
  return parseOrThrow<{ cleared: boolean; records_removed: number }>(res)
}

/** Slow on the first call after backend start (~real minutes -- PSO
 * calibration plus a genuine backtest, not a demo delay). Warm calls are
 * fast; callers should show a "computing replay" state, not a generic
 * spinner, so a first-time user understands the wait. */
export async function fetchLedgerReplay(): Promise<LedgerReplayResponse> {
  return parseOrThrow<LedgerReplayResponse>(await api(`/ledger/replay`))
}

// ---------------------------------------------------------------------------
// P6 -- landed cost + backhaul opportunity (commercial upgrades)
// ---------------------------------------------------------------------------

export async function fetchLandedCost(req: LandedCostRequest): Promise<LandedCostBreakdown> {
  const res = await api(`/landed-cost`, {
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
  const res = await api(`/backhaul`, {
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
  const res = await api(`/portfolio`, {
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
  const res = await api(`/anchorage/${port}/census`)
  if (res.status === 404) return null
  return parseOrThrow<AnchorageCensus>(res)
}

export async function fetchAnchorageCalibration(): Promise<AnchorageCalibrationResponse> {
  return parseOrThrow<AnchorageCalibrationResponse>(await api(`/anchorage/calibration`))
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
  return parseOrThrow<ChokepointReference[]>(await api(`/chokepoints`))
}

export async function fetchFracture(req: {
  originPort: PortCode
  destPort: PortCode
  asOf?: string
  hullValueUsd?: number
}): Promise<FractureResponse> {
  const res = await api(`/fracture`, {
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
  const res = await api(`/fx`)
  return parseOrThrow<FxRate>(res)
}

// ---------------------------------------------------------------------------
// Season plan -- the multi-voyage schedule.
// ---------------------------------------------------------------------------

export async function fetchSeasonPlan(req: {
  parcels: SeasonParcelInput[]
  vessels: VesselInput[]
  as_of?: string
  contract_term_days?: number
}): Promise<SeasonPlanResponse> {
  const res = await api(`/season-plan`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  })
  return parseOrThrow<SeasonPlanResponse>(res)
}

// ---------------------------------------------------------------------------
// Accounts, roles and sessions
// ---------------------------------------------------------------------------

export async function fetchAuthStatus(): Promise<AuthStatus> {
  return parseOrThrow<AuthStatus>(await api(`/auth/status`))
}

/** Create the very first account (an admin) and sign in as it. Only works on
 *  an empty deployment; the endpoint closes permanently once used. */
export async function bootstrapAdmin(body: {
  username: string
  password: string
  display_name?: string
}): Promise<{ user: DeskUser }> {
  return parseOrThrow(
    await api(`/auth/bootstrap`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  )
}

export async function login(username: string, password: string): Promise<{ user: DeskUser }> {
  return parseOrThrow(
    await api(`/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    }),
  )
}

export async function logout(): Promise<void> {
  await api(`/auth/logout`, { method: 'POST' })
}

export async function listUsers(): Promise<DeskUser[]> {
  return parseOrThrow<DeskUser[]>(await api(`/auth/users`))
}

export async function createUser(body: {
  username: string
  password: string
  display_name?: string
  role: DeskRole
}): Promise<{ user: DeskUser }> {
  return parseOrThrow(
    await api(`/auth/users`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  )
}

export async function updateUser(
  userId: string,
  body: { role?: DeskRole; is_active?: boolean; password?: string },
): Promise<{ user: DeskUser }> {
  return parseOrThrow(
    await api(`/auth/users/${userId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  )
}

// ---------------------------------------------------------------------------
// Standing alerts
// ---------------------------------------------------------------------------

export async function fetchAlerts(): Promise<AlertsResponse> {
  return parseOrThrow<AlertsResponse>(await api(`/alerts`))
}

export async function createWatch(body: {
  kind: WatchKind
  label: string
  vessel_class?: VesselClass | null
  threshold_usd_per_day?: number | null
  direction?: 'above' | 'below' | null
  move_pct?: number | null
  window_days?: number | null
  overdue_days?: number | null
}): Promise<{ watch: Watch }> {
  return parseOrThrow(
    await api(`/alerts/watches`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  )
}

export async function setWatchActive(watchId: string, isActive: boolean): Promise<{ watch: Watch }> {
  return parseOrThrow(
    await api(`/alerts/watches/${watchId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ is_active: isActive }),
    }),
  )
}

export async function deleteWatch(watchId: string): Promise<void> {
  await api(`/alerts/watches/${watchId}`, { method: 'DELETE' })
}

export async function markAlertsRead(): Promise<void> {
  await api(`/alerts/read`, { method: 'POST' })
}

/** Evaluate every active watch now rather than waiting for the loop. Exists
 *  so the feature is demonstrable without waiting fifteen minutes. */
export async function evaluateAlerts(): Promise<{ fired: Firing[]; unread: number }> {
  return parseOrThrow(await api(`/alerts/evaluate`, { method: 'POST' }))
}
