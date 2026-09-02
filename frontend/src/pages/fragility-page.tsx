import { useMemo, useState } from 'react'
import { PageState, Panel } from '@/components/desk/panel'
import { StatRow } from '@/components/desk/stat'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Combobox, type ComboOption } from '@/components/ui/combobox'
import { Field } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { fetchFragility } from '@/lib/api'
import { formatNumber, prettyPort } from '@/lib/format'
import type { FlipPoint, FragilityCategory, FragilityReport, PortCode, PortListing } from '@/lib/types'
import { cn } from '@/lib/utils'

const VARIABLE_LABEL: Record<string, string> = {
  cargo_volume_dwt: 'Cargo volume',
  origin_wait_days: 'Origin wait days',
  dest_wait_days: 'Destination wait days',
  vessel_draft_m: 'Vessel draft',
  permissible_draft_m: 'Permissible draft',
  laycan_width_days: 'Laycan width',
  risk_tolerance: 'Risk tolerance',
  contract_term_days: 'Contract term',
}

// F-31: these two chips used to print the raw backend enum values verbatim
// -- "T1 CLOSED_FORM" / "ASSUMPTION" and similar -- which read as debug
// output, not a chartering answer. Both enums (fragility.models.Tier /
// Provenance) describe HOW a finding was checked and WHERE its starting
// value came from; the labels below say that in plain English, with the
// original technical meaning kept as the hover title so nothing is lost,
// just not shown by default.
const TIER_LABEL: Record<string, { label: string; hint: string }> = {
  TIER1_CLOSED_FORM: { label: 'instant check', hint: 'Answered directly, no solver needed.' },
  TIER2_FLEET_MIX: { label: 're-priced fleet mix', hint: 'Re-ran the fleet-mix frontier to check this.' },
  TIER3_FULL_QUOTE: { label: 'full re-solve', hint: 'Re-ran the complete quote pipeline to check this.' },
}
const PROVENANCE_LABEL: Record<string, { label: string; hint: string }> = {
  USER_INPUT: { label: 'your input', hint: 'The value you typed into the quote form.' },
  DERIVED: { label: 'derived', hint: 'Computed from your other inputs, not typed directly.' },
  OBSERVED: { label: 'real data', hint: 'Read from current, real port/market data.' },
  ASSUMPTION: { label: 'assumed', hint: 'A static fallback -- no current real data available.' },
}

/** "Supramax:2:D" -> "2x Supramax" (+ " via transshipment" for "...:T") --
 * chosen_config_id is a compact internal signature (fragility.tiers.
 * config_id), not meant for display as-is; this is presentation only, the
 * underlying string is untouched. */
function prettyConfigId(id: string | null): string {
  if (!id) return '—'
  const [vesselClass, nVessels, mode] = id.split(':')
  if (!vesselClass || !nVessels) return id
  return `${nVessels}x ${vesselClass}${mode === 'T' ? ' via transshipment' : ''}`
}

function CategoryBadge({ category }: { category: FragilityCategory }) {
  return (
    <Badge
      variant={category === 'FRAGILE' ? 'destructive' : category === 'STABLE' ? 'secondary' : 'outline'}
      className="text-micro"
    >
      {category}
    </Badge>
  )
}

/** The natural-language summary shapes the P5 spec names explicitly --
 * derived from real FlipPoint fields (absolute_delta, flip_value), never a
 * separate hardcoded string. */
function summarize(fp: FlipPoint): string {
  if (fp.unavailable_reason) return fp.unavailable_reason
  if (!fp.flip_found) {
    return `No flip found across the searched range (${formatNumber(fp.range_searched_low ?? 0, 2)} to ${formatNumber(fp.range_searched_high ?? 0, 2)} ${fp.unit}).`
  }
  const label = VARIABLE_LABEL[fp.variable] ?? fp.variable
  const changed = fp.changed_components.join(', ') || 'the recommendation'
  const dir = fp.direction === 'increase' ? '+' : '−'
  const deltaStr = `${dir}${formatNumber(Math.abs(fp.absolute_delta ?? 0), fp.unit === 'dwt' ? 0 : 2)} ${fp.unit}`

  // "Recommendation = Panamax, but +7,500 t flips to Capesize." -- a class
  // change is common enough (cargo_volume_dwt, sometimes contract_term_days)
  // to deserve its own reading, built from the real before/after signature.
  if (
    fp.changed_components.includes('target_vessel_class') &&
    fp.base_signature?.target_vessel_class &&
    fp.flipped_signature?.target_vessel_class
  ) {
    return `Recommendation = ${fp.base_signature.target_vessel_class}, but ${deltaStr} flips to ${fp.flipped_signature.target_vessel_class}.`
  }

  if (fp.variable === 'permissible_draft_m' || fp.variable === 'vessel_draft_m') {
    const margin = Math.abs(fp.absolute_delta ?? 0)
    return `${label} margin = ${formatNumber(margin, 2)} m before ${changed} flips — ${margin < 0.3 ? 'operationally fragile' : 'a real margin exists'}.`
  }
  return `${label}: ${deltaStr} flips ${changed} (${fp.flip_value != null ? formatNumber(fp.flip_value, 2) : '?'} ${fp.unit}).`
}

function BerthTruthChips({ fp }: { fp: FlipPoint }) {
  const ctx = fp.berth_truth_context
  if (!ctx) return null
  const chips: string[] = []
  if (ctx.draft_status) chips.push(`draft: ${ctx.draft_status}${ctx.draft_source ? ` (${ctx.draft_source})` : ''}`)
  if (ctx.tide_impact && ctx.tide_impact !== 'NONE') chips.push(`tide: ${ctx.tide_impact} (${ctx.tide_authority})`)
  else if (ctx.tide_authority) chips.push(`tide rule on record, does not bind here`)
  if (ctx.empirical_wait_sample_n != null) {
    chips.push(
      ctx.empirical_wait_is_sufficient
        ? `empirical wait P50/P90: ${formatNumber(ctx.empirical_wait_p50_hours ?? 0, 1)}h / ${formatNumber(ctx.empirical_wait_p90_hours ?? 0, 1)}h (n=${ctx.empirical_wait_sample_n})`
        : `empirical wait: insufficient sample (n=${ctx.empirical_wait_sample_n})`,
    )
  }
  if (chips.length === 0) return null
  return (
    <div className="mt-1 flex flex-wrap gap-1">
      {chips.map((c) => (
        <span key={c} className="rounded-sm border border-border px-1 py-px font-mono text-micro text-muted-foreground">
          {c}
        </span>
      ))}
    </div>
  )
}

function FindingRow({ fp }: { fp: FlipPoint }) {
  return (
    <div className="border-b border-border p-2 last:border-b-0">
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="text-body font-semibold">{VARIABLE_LABEL[fp.variable] ?? fp.variable}</span>
          <CategoryBadge category={fp.fragility_category} />
          <span
            className="text-micro uppercase tracking-wide text-muted-foreground"
            title={TIER_LABEL[fp.tier]?.hint}
          >
            {TIER_LABEL[fp.tier]?.label ?? fp.tier}
          </span>
          <span
            className="text-micro uppercase tracking-wide text-muted-foreground"
            title={PROVENANCE_LABEL[fp.provenance]?.hint}
          >
            {PROVENANCE_LABEL[fp.provenance]?.label ?? fp.provenance}
          </span>
        </div>
        {fp.fragility_score != null && (
          <span className="desk-num text-caption text-muted-foreground">score {formatNumber(fp.fragility_score, 1)}</span>
        )}
      </div>
      <p className={cn('mt-0.5 text-body', fp.fragility_category === 'FRAGILE' ? 'text-risk' : 'text-foreground')}>
        {summarize(fp)}
      </p>
      <BerthTruthChips fp={fp} />
    </div>
  )
}

export function FragilityPage({ ports }: { ports: PortListing[] }) {
  const options: ComboOption[] = useMemo(
    () => ports.map((p) => ({ value: p.code, label: prettyPort(p.name), hint: p.code })),
    [ports],
  )

  const [origin, setOrigin] = useState('')
  const [dest, setDest] = useState('')
  const [cargo, setCargo] = useState('75000')
  const today = new Date()
  const inDays = (n: number) => new Date(today.getTime() + n * 86_400_000).toISOString().slice(0, 10)
  const [laycanStart, setLaycanStart] = useState(inDays(21))
  const [laycanEnd, setLaycanEnd] = useState(inDays(35))

  const [report, setReport] = useState<FragilityReport | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [elapsedMs, setElapsedMs] = useState<number | null>(null)

  function runSweep() {
    if (!origin || !dest) return
    setLoading(true)
    setError(null)
    const t0 = performance.now()
    fetchFragility({
      cargoVolumeDwt: Number(cargo) || 0,
      originPort: origin as PortCode,
      destPort: dest as PortCode,
      laycanStart,
      laycanEnd,
    })
      .then((r) => {
        setReport(r)
        setElapsedMs(performance.now() - t0)
      })
      .catch((err: unknown) => setError(err instanceof Error ? err.message : 'Fragility sweep failed.'))
      .finally(() => setLoading(false))
  }

  const ranked = useMemo(() => {
    if (!report) return []
    return [...report.findings].sort((a, b) => a.fragility_rank - b.fragility_rank)
  }, [report])

  return (
    <div className="flex h-full flex-col gap-2 overflow-hidden p-2" id="fragility">
      <Panel title="Decision Fragility" meta="How far is this recommendation from changing?">
        <div className="flex flex-wrap items-end gap-2 p-1">
          <Field label="Origin" className="w-48">
            <Combobox
              value={origin}
              onChange={setOrigin}
              options={options}
              placeholder="Select…"
              label="Origin port"
            />
          </Field>
          <Field label="Destination" className="w-48">
            <Combobox
              value={dest}
              onChange={setDest}
              options={options}
              placeholder="Select…"
              label="Destination port"
            />
          </Field>
          <Field label="Cargo tonnes">
            <Input className="h-7 w-24 text-body" value={cargo} onChange={(e) => setCargo(e.target.value)} />
          </Field>
          <Field label="Laycan start">
            <Input type="date" className="h-7 w-36 text-body" value={laycanStart} onChange={(e) => setLaycanStart(e.target.value)} />
          </Field>
          <Field label="Laycan end">
            <Input type="date" className="h-7 w-36 text-body" value={laycanEnd} onChange={(e) => setLaycanEnd(e.target.value)} />
          </Field>
          <Button variant="primary" size="md" onClick={runSweep} disabled={!origin || !dest || loading}>
            {loading ? 'Sweeping…' : report ? 'Re-run sweep' : 'Run sweep'}
          </Button>
          {/* Why the button is dim, said in the open rather than in a native
              `title` — which most platforms suppress entirely on a disabled
              element, leaving a dead-looking control and no reason for it. */}
          {!origin || !dest ? (
            <span className="self-center text-caption text-muted-foreground">
              Pick an origin and a destination port first.
            </span>
          ) : null}
          {elapsedMs != null && !loading && (
            <span className="stat-label">{(elapsedMs / 1000).toFixed(1)}s, {report?.evaluations_used} evaluations</span>
          )}
        </div>
      </Panel>

      {error && (
        <PageState
          tone="error"
          title="The fragility sweep failed"
          hint={error}
          action={
            <Button variant="danger" size="sm" onClick={runSweep}>
              Try again
            </Button>
          }
        />
      )}

      {loading && !error && (
        <PageState
          tone="busy"
          title="Sweeping the decision boundary…"
          hint="Re-solving the recommendation across perturbed inputs to find where the verdict flips. Every evaluation is a real solve, so this takes a few seconds."
        />
      )}

      {!report && !loading && !error && (
        <PageState
          title="No sweep run yet"
          hint="Pick an origin and a destination above, then run a sweep to see how far the inputs can move before this recommendation changes."
        />
      )}

      {report && (
        <div className="grid min-h-0 flex-1 auto-rows-min grid-cols-1 content-start gap-2 overflow-auto lg:grid-cols-3">
          <Panel title="Current Decision" className="lg:col-span-1">
            <div className="flex flex-col gap-1 p-1">
              <StatRow label="Lock action" value={report.current_decision.lock_action ?? '—'} />
              <StatRow label="Vessel class" value={report.current_decision.target_vessel_class ?? '—'} />
              <StatRow label="Envelope status" value={report.current_decision.envelope_status ?? '—'} />
              <StatRow label="Fleet mix" value={prettyConfigId(report.current_decision.chosen_config_id)} />
            </div>
          </Panel>

          <Panel title="Sweep Cost" className="lg:col-span-2">
            <div className="flex flex-col gap-1 p-1">
              <StatRow label="Total evaluations" value={report.evaluations_used} />
              <StatRow label="Findings" value={report.findings.length} />
              <StatRow
                label="Fragile / Stable / Unavailable"
                value={`${ranked.filter((f) => f.fragility_category === 'FRAGILE').length} / ${ranked.filter((f) => f.fragility_category === 'STABLE').length} / ${ranked.filter((f) => f.fragility_category === 'UNAVAILABLE').length}`}
              />
            </div>
          </Panel>

          <Panel title="Findings" meta="fragile leads" className="lg:col-span-3" flush>
            <div>
              {ranked.map((fp) => (
                <FindingRow key={fp.variable} fp={fp} />
              ))}
            </div>
          </Panel>

          <Panel title="Limitations" className="lg:col-span-3">
            <ul className="list-disc space-y-1 p-1 pl-4 text-caption text-muted-foreground">
              {report.limitations.map((l) => (
                <li key={l}>{l}</li>
              ))}
            </ul>
          </Panel>
        </div>
      )}
    </div>
  )
}
