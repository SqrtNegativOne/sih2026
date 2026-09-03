import { useEffect, useMemo, useState } from 'react'
import { PageState, Panel } from '@/components/desk/panel'
import { StatRow } from '@/components/desk/stat'
import { Badge } from '@/components/ui/badge'
import { Combobox, type ComboOption } from '@/components/ui/combobox'
import { Field } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import {
  fetchPortBerths,
  fetchPortCalls,
  fetchPortReality,
  fetchPortWaits,
} from '@/lib/api'
import { formatIsoShort, formatNumber, prettyPort } from '@/lib/format'
import type {
  PortBerthsResponse,
  PortCallsResponse,
  PortCode,
  PortListing,
  PortRealityReport,
  PortWaitsResponse,
  WaitIntervalKind,
} from '@/lib/types'
import { cn } from '@/lib/utils'

const VERDICT_TONE: Record<PortRealityReport['verdict'], 'go' | 'risk' | 'wait'> = {
  FEASIBLE: 'go',
  INFEASIBLE: 'risk',
  CANNOT_VERIFY: 'wait',
}

const WAIT_INTERVAL_LABEL: Record<WaitIntervalKind, string> = {
  ARRIVAL_TO_READY: 'Arrival → Ready',
  READY_TO_BERTH: 'Ready → Berth',
  ARRIVAL_TO_BERTH: 'Arrival → Berth',
  BERTH_TO_SAIL: 'Berth → Sail',
}

function VerdictBadge({ verdict }: { verdict: PortRealityReport['verdict'] }) {
  const tone = VERDICT_TONE[verdict]
  return (
    <span
      className={cn(
        'inline-flex items-center gap-2 rounded-sm px-2 py-1 font-mono text-lead font-extrabold uppercase tracking-wide',
        // Paired foreground per fill -- the dark theme's semantics are light
        // inks, so `text-white` would sink this badge into its own background.
        tone === 'go' && 'bg-go text-go-fg',
        tone === 'risk' && 'bg-risk text-risk-fg',
        tone === 'wait' && 'bg-wait text-wait-fg',
      )}
    >
      {verdict.replace('_', ' ')}
    </span>
  )
}

function SourceQualityBadge({ quality }: { quality: string | null }) {
  if (!quality) return null
  const isWeak = quality === 'PUBLIC_AGGREGATOR'
  return (
    <Badge variant={isWeak ? 'destructive' : 'secondary'} className="text-micro">
      {quality.replace(/_/g, ' ')}
    </Badge>
  )
}

/** Hours -> a compact "Xd Yh" or "Yh" string. */
function fmtHours(h: number | null): string {
  if (h == null) return '—'
  const days = Math.floor(h / 24)
  const rem = Math.round(h % 24)
  if (days === 0) return `${rem}h`
  return `${days}d ${rem}h`
}

export function PortTwinPage({ ports }: { ports: PortListing[] }) {
  const options: ComboOption[] = useMemo(
    () => ports.map((p) => ({ value: p.code, label: prettyPort(p.name), hint: p.code })),
    [ports],
  )

  const [port, setPort] = useState<string>('')
  const [vesselDwt, setVesselDwt] = useState('75000')
  const [draftM, setDraftM] = useState('14.5')
  const [loaM, setLoaM] = useState('225')
  const [beamM, setBeamM] = useState('32')
  const [vesselClass, setVesselClass] = useState('Panamax')
  const [isLaden, setIsLaden] = useState(true)
  const [commodity, setCommodity] = useState('Dry Bulk')

  const [reality, setReality] = useState<PortRealityReport | null>(null)
  const [berths, setBerths] = useState<PortBerthsResponse | null>(null)
  const [calls, setCalls] = useState<PortCallsResponse | null>(null)
  const [waits, setWaits] = useState<PortWaitsResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!port) return
    let cancelled = false
    setLoading(true)
    setError(null)
    Promise.all([
      fetchPortReality(port as PortCode, {
        vesselDwt: Number(vesselDwt) || 0,
        draftM: Number(draftM) || 0,
        loaM: Number(loaM) || 0,
        beamM: Number(beamM) || 0,
        vesselClass,
        vesselIsLaden: isLaden,
        commodity,
      }),
      fetchPortBerths(port as PortCode),
      fetchPortCalls(port as PortCode, { limit: 15 }),
      fetchPortWaits(port as PortCode),
    ])
      .then(([r, b, c, w]) => {
        if (cancelled) return
        setReality(r)
        setBerths(b)
        setCalls(c)
        setWaits(w)
      })
      .catch((err: unknown) => {
        if (cancelled) return
        setError(err instanceof Error ? err.message : 'Failed to load port reality.')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [port, vesselDwt, draftM, loaM, beamM, vesselClass, isLaden, commodity])

  return (
    <div className="flex h-full flex-col gap-2 overflow-hidden p-2" id="port-twin">
      {/* Query bar */}
      <Panel title="Port Twin"
        soWhat={'What a port can actually take, from its own published rules and its recorded ship calls. Check it before promising an owner a berth — a ship that cannot enter is not a cheaper ship.'} meta="Real berth constraints, tide rules, and empirical wait/handling data, per port">
        <div className="flex flex-wrap items-end gap-2 p-1">
          <Field label="Port" className="w-56">
            <Combobox
              value={port}
              onChange={setPort}
              options={options}
              placeholder="Select a port…"
              label="Port to inspect"
            />
          </Field>
          {/* F-41 fix: this feeds vesselDwt (the ship's own deadweight,
           * checked against the port's max DWT) into fetchPortReality --
           * it was labelled "Cargo DWT", which is backwards on two counts:
           * it's the vessel's figure, not the cargo's, and DWT is a ship
           * capacity concept in the first place. */}
          <LabeledInput label="Vessel DWT" value={vesselDwt} onChange={setVesselDwt} width="w-24" />
          <LabeledInput label="Draft (m)" value={draftM} onChange={setDraftM} width="w-20" />
          <LabeledInput label="LOA (m)" value={loaM} onChange={setLoaM} width="w-20" />
          <LabeledInput label="Beam (m)" value={beamM} onChange={setBeamM} width="w-20" />
          <Field label="Class">
            <select
              className="h-7 w-28 rounded-sm border border-input bg-background px-2 text-body"
              value={vesselClass}
              onChange={(e) => setVesselClass(e.target.value)}
            >
              {['Handysize', 'Supramax', 'Panamax', 'Capesize'].map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          </Field>
          {/* Deliberately NOT a <Field>: this holds a <button>, and a <label>
              around a button replaces the button's own text as its accessible
              name -- the toggle would announce as "State" and never say
              whether it is currently laden or in ballast. */}
          <div className="flex flex-col gap-0.5">
            <span className="stat-label" aria-hidden="true">
              State
            </span>
            {/* A two-state toggle, not a command: it says which state is
                currently selected and switches on click, so it carries
                aria-pressed rather than reading as a button that "does"
                something. */}
            <button
              type="button"
              onClick={() => setIsLaden((v) => !v)}
              aria-pressed={isLaden}
              aria-label={`Vessel state: currently ${isLaden ? 'laden' : 'in ballast'}. Activate to switch.`}
              className={cn(
                'h-7 w-20 cursor-pointer rounded-sm border text-body font-semibold transition-colors',
                'focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-ring',
                isLaden
                  ? 'border-go bg-go-soft text-go hover:bg-go/15'
                  : 'border-border bg-surface-2 text-muted-foreground hover:border-muted-foreground/60',
              )}
            >
              {isLaden ? 'Laden' : 'Ballast'}
            </button>
          </div>
          <LabeledInput label="Commodity" value={commodity} onChange={setCommodity} width="w-32" />
        </div>
      </Panel>

      {!port && !error && (
        <PageState
          title="No port selected"
          hint="Pick a port above to inspect its real berth constraints, tide status, and empirical waiting-time record."
        />
      )}

      {port && loading && !error && (
        <PageState
          tone="busy"
          title={`Reading ${prettyPort(port)}…`}
          hint="Fetching real berth geometry, tide authority rules, port calls and the empirical wait distribution."
        />
      )}

      {error && <PageState tone="error" title="Could not load this port" hint={error} />}

      {port && reality && (
        <div className="grid min-h-0 flex-1 auto-rows-min grid-cols-1 content-start gap-2 overflow-auto lg:grid-cols-3">
          {/* Verdict + constraint */}
          <Panel
            title="Feasibility Verdict"
        soWhat={'Whether this specific ship can work at this specific port. A fail is a hard stop, not a caution: change the ship, the load, or the port.'}
            meta={reality.as_of}
            className="lg:col-span-1"
            actions={<SourceQualityBadge quality={reality.source_quality} />}
          >
            <div className="flex flex-col gap-2 p-1">
              <VerdictBadge verdict={reality.verdict} />
              {reality.reason && <p className="text-body text-muted-foreground">{reality.reason}</p>}
              <div className="border-t border-border pt-2">
                <StatRow
                  label="Limit source"
                  value={
                    reality.limit_source === 'REGISTER'
                      ? 'published berth register'
                      : reality.limit_source === 'PORTENUM_FALLBACK'
                        ? 'general port reference (no berth-specific register)'
                        : 'none on record'
                  }
                />
                <StatRow label="Binding berth" value={reality.berth_id ?? '—'} />
                <StatRow
                  label="Draft margin"
                  value={reality.margin_draft_m != null ? `${formatNumber(reality.margin_draft_m, 2)} m` : 'untested'}
                  tone={reality.margin_draft_m != null && reality.margin_draft_m < 0 ? 'risk' : 'plain'}
                />
                <StatRow
                  label="LOA margin"
                  value={reality.margin_loa_m != null ? `${formatNumber(reality.margin_loa_m, 1)} m` : 'untested'}
                />
                <StatRow
                  label="Beam margin"
                  value={reality.margin_beam_m != null ? `${formatNumber(reality.margin_beam_m, 1)} m` : 'untested'}
                />
                <StatRow label="Confidence" value={`${Math.round(reality.confidence * 100)}%`} />
              </div>
              {reality.untested_checks.length > 0 && (
                <div className="border-t border-border pt-2">
                  <span className="stat-label">Untested checks</span>
                  <ul className="mt-0.5 list-disc space-y-0.5 pl-3.5 text-caption text-muted-foreground">
                    {reality.untested_checks.map((c) => (
                      <li key={c}>{c}</li>
                    ))}
                  </ul>
                </div>
              )}
              {reality.binding_constraint && (
                <div className="border-t border-border pt-2 text-caption text-muted-foreground">
                  Source: {reality.binding_constraint.source_doc_id}
                  {reality.binding_constraint.source_url && (
                    <>
                      {' · '}
                      <a
                        href={reality.binding_constraint.source_url}
                        target="_blank"
                        rel="noreferrer"
                        className="text-market underline"
                      >
                        document
                      </a>
                    </>
                  )}
                  {reality.binding_constraint.doc_internal_date && (
                    <> · dated {formatIsoShort(reality.binding_constraint.doc_internal_date)}</>
                  )}
                </div>
              )}
            </div>
          </Panel>

          {/* Tide */}
          <Panel title="Tide Assessment"
        soWhat={'Whether the ship needs a high tide to enter or leave, and on whose rule. If it does, the ship can only move in a window each day — build that into the laycan rather than discovering it at the berth.'} meta={reality.tide.authority ?? 'no tide data'}>
            <div className="flex flex-col gap-2 p-1">
              <Badge
                variant={
                  reality.tide.impact === 'NONE'
                    ? 'secondary'
                    : reality.tide.impact === 'BLOCKING'
                      ? 'destructive'
                      : 'outline'
                }
                className={cn(reality.tide.impact === 'CONDITIONAL' && 'border-wait text-wait')}
              >
                {reality.tide.impact}
              </Badge>
              {reality.tide.rule_text && (
                <p className="text-body text-muted-foreground">"{reality.tide.rule_text}"</p>
              )}
              {reality.tide.allowance_m != null && (
                <StatRow label="Published allowance" value={`${reality.tide.allowance_m} m`} />
              )}
              {reality.tide.source_is_current === false && (
                <p className="text-caption text-risk">Source document is superseded — not current.</p>
              )}
              {reality.tide.reason && <p className="text-caption text-muted-foreground">{reality.tide.reason}</p>}
              {reality.tide.authority === null && (
                <p className="text-body text-muted-foreground">No tide constraint on record for this berth.</p>
              )}
            </div>
          </Panel>

          {/* Observed vs declared */}
          <Panel title="Observed Envelope"
        soWhat={'The largest ships that have genuinely called here, from real port records. If the ship you are considering is bigger than anything in this list, treat the paper limit with suspicion and ask the agent.'} meta={`${reality.observed_envelope.n_calls} real calls`}>
            <div className="flex flex-col gap-1 p-1">
              <StatRow label="Max observed draft" value={fmtOrDash(reality.observed_envelope.max_draft_m, 'm')} />
              <StatRow label="Max observed LOA" value={fmtOrDash(reality.observed_envelope.max_loa_m, 'm')} />
              <StatRow label="Max observed beam" value={fmtOrDash(reality.observed_envelope.max_beam_m, 'm')} />
              {reality.declared_vs_observed_conflicts.length > 0 ? (
                <div className="mt-1 border-t border-border pt-2">
                  <span className="stat-label text-risk">Declared vs observed conflicts</span>
                  {reality.declared_vs_observed_conflicts.map((c) => (
                    <p key={c.dimension} className="mt-0.5 text-caption text-risk">
                      {c.note}
                    </p>
                  ))}
                </div>
              ) : (
                <p className="mt-1 text-caption text-muted-foreground">
                  No conflicts — every observed call falls within the declared limits.
                </p>
              )}
            </div>
          </Panel>

          {/* Wait distributions */}
          <Panel
            title="Empirical Wait Distribution"
        soWhat={'How long ships have actually waited here, not how long the port says they should. Use the P90, not the average, when you are deciding how much demurrage risk to accept.'}
            meta={waits ? (waits.status === 'OK' ? 'real data' : 'baseline only') : undefined}
            className="lg:col-span-2"
          >
            {waits && (
              <table className="desk-table w-full">
                <thead>
                  <tr>
                    <th>Interval</th>
                    <th className="text-right">n</th>
                    <th className="text-right">P50</th>
                    <th className="text-right">P75</th>
                    <th className="text-right">P90</th>
                  </tr>
                </thead>
                <tbody>
                  {(Object.keys(WAIT_INTERVAL_LABEL) as WaitIntervalKind[]).map((k) => {
                    const d = waits.distributions[k]
                    return (
                      <tr key={k}>
                        <td>{WAIT_INTERVAL_LABEL[k]}</td>
                        <td className="desk-num text-right">{d.n}</td>
                        {d.is_sufficient ? (
                          <>
                            <td className="desk-num text-right">{fmtHours(d.p50_hours)}</td>
                            <td className="desk-num text-right">{fmtHours(d.p75_hours)}</td>
                            <td className="desk-num text-right font-semibold">{fmtHours(d.p90_hours)}</td>
                          </>
                        ) : (
                          <td colSpan={3} className="text-right text-caption text-muted-foreground">
                            insufficient sample (n={d.n}) — falls back to static baseline
                          </td>
                        )}
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            )}
            {waits?.portwatch_mapping_status === 'PROXY' && (
              <p className="p-1 text-caption text-wait">⚠ {waits.portwatch_mapping_note}</p>
            )}
          </Panel>

          {/* Handling */}
          <Panel title="Handling Productivity"
        soWhat={'How fast this port actually loads or discharges. A slow port turns a cheap freight rate into an expensive voyage — check this before choosing the port on rate alone.'}>
            {reality.handling?.is_sufficient ? (
              <div className="flex flex-col gap-1 p-1">
                <StatRow label="Norm (median)" value={`${formatNumber(reality.handling.norm_tpd_median ?? 0)} t/d`} />
                <StatRow
                  label="Actual (median)"
                  value={`${formatNumber(reality.handling.actual_tpd_median ?? 0)} t/d`}
                />
                <StatRow
                  label="Actual / Norm"
                  value={`${formatNumber((reality.handling.actual_over_norm_ratio ?? 0) * 100)}%`}
                  tone={(reality.handling.actual_over_norm_ratio ?? 1) < 0.6 ? 'risk' : 'plain'}
                />
              </div>
            ) : (
              <p className="p-2 text-body text-muted-foreground">
                Insufficient real handling data (n={reality.handling?.n ?? 0}) — no fabricated rate shown.
              </p>
            )}
          </Panel>

          {/* Berth register */}
          <Panel
            title="Berth Register"
        soWhat={'The individual berths, with what each can take. If the port passes overall but the one berth that handles your cargo does not, you still have a problem.'}
            meta={berths ? `${berths.status}${berths.berths.length ? ` · ${berths.berths.length} berths` : ''}` : undefined}
            className="lg:col-span-3"
            flush
          >
            {berths?.status === 'NOT_AVAILABLE' ? (
              <p className="p-2 text-body text-muted-foreground">{berths.reason}</p>
            ) : (
              <table className="desk-table w-full">
                <thead>
                  <tr>
                    <th>Berth</th>
                    <th>Status</th>
                    <th className="text-right">Draft (m)</th>
                    <th className="text-right">LOA (m)</th>
                    <th className="text-right">Beam (m)</th>
                    <th>Commodity</th>
                    <th>Function</th>
                    <th>Source</th>
                  </tr>
                </thead>
                <tbody>
                  {berths?.berths.map((b) => (
                    <tr key={b.berth_id}>
                      <td className="font-semibold">{b.berth_id}</td>
                      <td>
                        <Badge variant={b.limit_status === 'PUBLISHED' ? 'secondary' : 'outline'} className="text-micro">
                          {b.limit_status}
                        </Badge>
                        {!b.is_published_constraint_berth && (
                          <span className="ml-1 text-micro text-muted-foreground">observed-only</span>
                        )}
                      </td>
                      <td className="desk-num text-right">{fmtOrDash(b.permissible_draft_m)}</td>
                      <td className="desk-num text-right">{fmtOrDash(b.max_loa_m)}</td>
                      <td className="desk-num text-right">{fmtOrDash(b.max_beam_m)}</td>
                      <td className="text-caption">{b.commodity_class ?? '—'}</td>
                      <td className="text-caption text-muted-foreground">{b.berth_function ?? '—'}</td>
                      <td className="text-caption text-muted-foreground">{b.source_doc_id}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Panel>

          {/* Recent real vessel calls */}
          <Panel
            title="Recent Vessel Calls"
        soWhat={'The ships that have been here lately, as evidence behind everything above. If this list is short or old, the port\'s numbers on this screen rest on thin ground.'}
            meta={calls ? (calls.status === 'OK' ? `${calls.total} real rows` : 'no ingested history') : undefined}
            className="lg:col-span-3"
            flush
          >
            {calls?.status === 'NOT_AVAILABLE' ? (
              <p className="p-2 text-body text-muted-foreground">
                No fact_port_call history has been ingested for this port yet.
              </p>
            ) : (
              <table className="desk-table w-full">
                <thead>
                  <tr>
                    <th>Vessel</th>
                    <th>Berth</th>
                    <th className="text-right">Draft</th>
                    <th>Cargo</th>
                    <th>D/L</th>
                    <th>Arrival</th>
                    <th>Berthed</th>
                  </tr>
                </thead>
                <tbody>
                  {calls?.rows.map((r, i) => (
                    <tr key={i}>
                      <td className="font-semibold">{r.vessel_name ?? '—'}</td>
                      <td>{r.berth_or_point ?? '—'}</td>
                      <td className="desk-num text-right">{fmtOrDash(r.arrival_draft_m)}</td>
                      <td className="max-w-40 truncate text-caption" title={r.cargo_raw ?? undefined}>
                        {r.cargo_raw ?? '—'}
                      </td>
                      <td>{r.load_discharge ?? '—'}</td>
                      <td className="text-caption text-muted-foreground">{r.arrival_ts?.slice(0, 16) ?? '—'}</td>
                      <td className="text-caption text-muted-foreground">{r.berth_ts?.slice(0, 16) ?? '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Panel>
        </div>
      )}
    </div>
  )
}

function fmtOrDash(v: number | null, suffix = ''): string {
  return v == null ? '—' : `${formatNumber(v, 1)}${suffix ? ` ${suffix}` : ''}`
}

function LabeledInput({
  label,
  value,
  onChange,
  width,
}: {
  label: string
  value: string
  onChange: (v: string) => void
  width: string
}) {
  return (
    <Field label={label}>
      <Input
        className={cn('h-7 text-body', width)}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
    </Field>
  )
}
