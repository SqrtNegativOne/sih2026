import { useMemo, useState } from 'react'
import { PageState, Panel } from '@/components/desk/panel'
import { SolveProgress } from '@/components/desk/solve-progress'
import { StatRow } from '@/components/desk/stat'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Combobox, type ComboOption } from '@/components/ui/combobox'
import { Field } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { streamFragility } from '@/lib/api'
import { formatNumber, prettyPort } from '@/lib/format'
import type {
  FlipPoint,
  FragilityCategory,
  FragilityReport,
  FragilityVariable,
  PortCode,
  ProgressStage,
  PortListing,
} from '@/lib/types'
import { cn } from '@/lib/utils'

/** Quick vs full. The backend has always accepted a partial sweep and the
 *  client has always typed the parameter; nothing ever sent it, so every user
 *  paid the full eight-variable search. */
type SweepScope = 'quick' | 'full'

/** The three that actually move a verdict: how much cargo, whether the ship
 *  can physically call, and how much room the laycan leaves. */
const QUICK_VARIABLES: FragilityVariable[] = [
  'cargo_volume_dwt',
  'permissible_draft_m',
  'laycan_width_days',
]

/** "Searching cargo_volume_dwt" -> "Searching cargo volume". Falls back to
 *  the engine's own wording for any stage that is not a variable search
 *  (the baseline step), so a new stage added upstream still reads sensibly
 *  here without this needing to know about it. */
function prettyStage(label: string): string {
  const m = label.match(/^Searching (\w+)$/)
  if (!m) return label
  const pretty = VARIABLE_LABEL[m[1]]
  return pretty ? `Searching ${pretty.toLowerCase()}` : label
}

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
  // The sweep silently treated any non-numeric or non-positive text here as
  // 0 (`Number(cargo) || 0` in runSweep) and submitted it anyway -- a typo
  // paid a round trip to the backend for a 422 the client could have caught
  // instantly. This field is a plain text `Input` rather than the quote
  // drawer's native `type="number" min` control (no `<form>` wraps this page
  // to make HTML5 constraint validation apply even if it were), so the check
  // has to be explicit.
  const cargoTonnes = Number(cargo)
  const cargoValid = Number.isFinite(cargoTonnes) && cargoTonnes > 0
  const [scope, setScope] = useState<SweepScope>('quick')
  const [stages, setStages] = useState<ProgressStage[]>([])
  const today = new Date()
  const inDays = (n: number) => new Date(today.getTime() + n * 86_400_000).toISOString().slice(0, 10)
  const [laycanStart, setLaycanStart] = useState(inDays(21))
  const [laycanEnd, setLaycanEnd] = useState(inDays(35))

  const [report, setReport] = useState<FragilityReport | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [elapsedMs, setElapsedMs] = useState<number | null>(null)

  function runSweep(scope: SweepScope = 'quick') {
    // Guards the retry button too (PageState's "Try again" below calls this
    // directly, bypassing the disabled state on the two sweep buttons) --
    // belt-and-braces against submitting a cargo figure that was never valid.
    if (!origin || !dest || !cargoValid) return
    setLoading(true)
    setScope(scope)
    setError(null)
    setStages([])
    const t0 = performance.now()
    // Streamed, not fetched. The sweep takes real seconds because every point
    // is a genuine re-solve; the engine has always announced each variable as
    // it starts and finishes, and this is the first caller to listen. The
    // checklist that appears is the same component the quote solve uses, fed
    // by the same event shape.
    void streamFragility(
      {
        cargoVolumeDwt: cargoTonnes,
        originPort: origin as PortCode,
        destPort: dest as PortCode,
        laycanStart,
        laycanEnd,
        // The backend has always accepted a partial sweep and the client has
        // always typed it; nothing ever sent it, so every user paid the full
        // eight-variable search whether they needed it or not. The three below
        // are the ones that actually move a verdict in practice -- cargo size,
        // the draft limit that decides whether the ship can call at all, and
        // how much room the laycan leaves.
        variables: scope === 'quick' ? QUICK_VARIABLES : undefined,
      },
      {
        // The engine names its stages by the variable id it is searching
        // ("Searching cargo_volume_dwt"), which is exactly the register the
        // review flagged as stopping a reader dead. VARIABLE_LABEL already
        // holds the plain-English name for every one of them, so the label is
        // rewritten here for display; the `key` -- which is what the
        // checklist matches start events to done events on -- is untouched.
        onStage: (stage) => setStages((prev) => [...prev, { ...stage, label: prettyStage(stage.label) }]),
        onResult: (r) => {
          setReport(r)
          setElapsedMs(performance.now() - t0)
          setLoading(false)
        },
        onError: (err) => {
          setError(err.message)
          setLoading(false)
        },
      },
    )
  }

  const ranked = useMemo(() => {
    if (!report) return []
    return [...report.findings].sort((a, b) => a.fragility_rank - b.fragility_rank)
  }, [report])

  return (
    <div className="flex h-full flex-col gap-2 overflow-hidden p-2" id="fragility">
      <Panel title="Decision Fragility"
        soWhat={'How close this recommendation is to flipping to a different answer. A decision that survives a big change in your assumptions is safe to act on; one that flips on a small change needs a second opinion before you fix.'} meta="How far is this recommendation from changing?">
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
            <Input
              type="number"
              min={1}
              aria-invalid={!cargoValid}
              className={cn(
                'h-7 w-24 text-body',
                !cargoValid && 'border-risk focus:border-risk focus:ring-risk/40',
              )}
              value={cargo}
              onChange={(e) => setCargo(e.target.value)}
            />
          </Field>
          <Field label="Laycan start">
            <Input type="date" className="h-7 w-36 text-body" value={laycanStart} onChange={(e) => setLaycanStart(e.target.value)} />
          </Field>
          <Field label="Laycan end">
            <Input type="date" className="h-7 w-36 text-body" value={laycanEnd} onChange={(e) => setLaycanEnd(e.target.value)} />
          </Field>
          {/* Two scopes, both real, with their honest costs on the label.
              The quick sweep is not a lesser answer -- it is the same search
              over the three variables that move a verdict in practice. */}
          <Button
            variant="primary"
            size="md"
            onClick={() => runSweep('quick')}
            disabled={!origin || !dest || !cargoValid || loading}
          >
            {loading && scope === 'quick' ? 'Sweeping…' : 'Quick sweep · 3 variables'}
          </Button>
          <Button
            size="md"
            onClick={() => runSweep('full')}
            disabled={!origin || !dest || !cargoValid || loading}
          >
            {loading && scope === 'full' ? 'Sweeping…' : 'Full sweep · all 8'}
          </Button>
          {/* Why the button is dim, said in the open rather than in a native
              `title` — which most platforms suppress entirely on a disabled
              element, leaving a dead-looking control and no reason for it. */}
          {!origin || !dest ? (
            <span className="self-center text-caption text-muted-foreground">
              Pick an origin and a destination port first.
            </span>
          ) : !cargoValid ? (
            <span className="self-center text-caption text-risk">
              Cargo tonnes needs to be a positive number.
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
          // Retry whichever scope actually failed, not a default one -- a user
          // who asked for the full sweep should not silently get the quick one.
          action={
            <Button variant="danger" size="sm" onClick={() => runSweep(scope)}>
              Try again
            </Button>
          }
        />
      )}

      {/* The wait, itemised. This used to be a single "Sweeping the decision
          boundary…" card that sat there for up to twenty seconds with nothing
          moving -- which is indistinguishable from a hang, and reads as one.
          The engine names each variable as it starts and finishes, so the
          honest thing to show is that list ticking off. The cost is the
          selling point here: every line is a real re-solve, and the reader
          can now watch it being spent rather than being asked to take it on
          trust. */}
      {loading && !error && (
        <div className="flex min-h-0 flex-1 flex-col gap-2 overflow-auto">
          <PageState
            tone="busy"
            title={
              scope === 'quick'
                ? 'Sweeping 3 variables…'
                : 'Sweeping all 8 variables…'
            }
            hint="Re-solving the recommendation across perturbed inputs to find where the verdict flips. Every evaluation below is a real solve, not a lookup."
          />
          {/* No `pipeline` here on purpose -- the engine skips variables
              whose flip point is meaningless for these inputs, so the rows
              are whatever it actually announces. */}
          {stages.length > 0 && <SolveProgress stages={stages} title="Sweeping" />}
        </div>
      )}

      {!report && !loading && !error && (
        <PageState
          title="No sweep run yet"
          hint="Pick an origin and a destination above, then run a sweep to see how far the inputs can move before this recommendation changes."
        />
      )}

      {report && (
        <div className="grid min-h-0 flex-1 auto-rows-min grid-cols-1 content-start gap-2 overflow-auto lg:grid-cols-3">
          <Panel title="Current Decision"
        soWhat={'The recommendation being stress-tested here, so you can see what is being pushed on. Change the cargo or route above and re-run to test a different one.'} className="lg:col-span-1">
            <div className="flex flex-col gap-1 p-1">
              <StatRow label="Lock action" value={report.current_decision.lock_action ?? '—'} />
              <StatRow label="Vessel class" value={report.current_decision.target_vessel_class ?? '—'} />
              <StatRow label="Envelope status" value={report.current_decision.envelope_status ?? '—'} />
              <StatRow label="Fleet mix" value={prettyConfigId(report.current_decision.chosen_config_id)} />
            </div>
          </Panel>

          <Panel title="Sweep Cost"
        soWhat={'How long the test took and how much work it did. It is slow because every point is a genuine re-solve of the whole quote, not a lookup — if you only need the headline, use the quick sweep.'} className="lg:col-span-2">
            <div className="flex flex-col gap-1 p-1">
              <StatRow label="Total evaluations" value={report.evaluations_used} />
              <StatRow label="Findings" value={report.findings.length} />
              <StatRow
                label="Fragile / Stable / Unavailable"
                value={`${ranked.filter((f) => f.fragility_category === 'FRAGILE').length} / ${ranked.filter((f) => f.fragility_category === 'STABLE').length} / ${ranked.filter((f) => f.fragility_category === 'UNAVAILABLE').length}`}
              />
            </div>
          </Panel>

          <Panel title="Findings"
        soWhat={'Each row says how far one input can move before the answer changes. Anything marked FRAGILE is a number worth confirming with the agent or the owner before you commit — the recommendation rests on it.'} meta="fragile leads" className="lg:col-span-3" flush>
            <div>
              {ranked.map((fp) => (
                <FindingRow key={fp.variable} fp={fp} />
              ))}
            </div>
          </Panel>

          <Panel title="Limitations"
        soWhat={'What this test does not cover. Read it before quoting a result in a meeting: an input that was never swept has not been shown to be safe, only left untested.'} className="lg:col-span-3">
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
