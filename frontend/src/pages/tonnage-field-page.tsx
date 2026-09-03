import { curveMonotoneX } from '@visx/curve'
import { GridRows } from '@visx/grid'
import { ParentSize } from '@visx/responsive'
import { scaleLinear } from '@visx/scale'
import { AreaClosed, LinePath } from '@visx/shape'
import { useEffect, useMemo, useState } from 'react'
import { PageState, Panel, PanelError, PanelLoading } from '@/components/desk/panel'
import { StatRow } from '@/components/desk/stat'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { ProvenanceChip } from '@/components/desk/term'
import type { ProvenanceKind } from '@/lib/vocabulary'
import { fetchTonnageField, fetchTonnageFieldForward, fetchTonnageFieldValidation } from '@/lib/api'
import { formatNumber } from '@/lib/format'
import type {
  AblationMetricRow,
  SignDiagnosis,
  TightnessForwardPoint,
  TonnageFieldForwardResponse,
  TonnageFieldResponse,
  TonnageFieldValidationResponse,
} from '@/lib/types'
import { cn } from '@/lib/utils'

const CLASS_ORDER = ['Handysize', 'Supramax', 'Panamax', 'Capesize']

/** Every figure on this page is one of these -- shown as a small tag so a
 * reader never mistakes a model-derived signal for an observed fact. P3
 * requirement: "Provenance per figure: OBSERVED / ESTIMATED / MODEL_DERIVED /
 * DECLARED / INFERRED." The union itself now lives in lib/vocabulary alongside
 * the plain-English labels, so the two cannot drift apart. */
type Provenance = ProvenanceKind

/**
 * Was a chip printing the raw enum -- "MODEL_DERIVED" -- which is a field
 * value from src/data_builders/provenance.py, not something a charterer has
 * any reason to decode. The shared ProvenanceChip shows the plain word
 * ("modelled") and keeps both the definition and the original enum in its
 * tooltip, so the credibility signal is louder and the jargon is gone.
 */
function ProvenanceTag({ kind }: { kind: Provenance }) {
  return <ProvenanceChip kind={kind} />
}

function IndexTypeBanner({ data }: { data: TonnageFieldResponse }) {
  if (data.index_type === 'ABSOLUTE') {
    return (
      <div className="border border-go bg-go-soft p-2 text-body text-go">
        <span className="font-bold uppercase tracking-wide">Absolute scale validated.</span>{' '}
        {data.index_type_reasoning}
      </div>
    )
  }
  return (
    <div className="border border-wait bg-wait-soft p-2 text-body text-wait">
      <span className="font-bold uppercase tracking-wide">Relative index -- not absolute tonnage.</span>{' '}
      Every figure below is a{' '}
      <span className="font-semibold">Physical Supply Pressure Index / Tonnage Tightness Index</span> -- a
      dimensionless, within-class-over-time signal. It is not a count of ships or a DWT figure available to
      charter, and is never presented as one on this page.
    </div>
  )
}

function CurrentTightnessPanel({ data }: { data: TonnageFieldResponse }) {
  const byClass = useMemo(
    () =>
      [...data.tightness_by_class].sort(
        (a, b) => CLASS_ORDER.indexOf(a.vessel_class) - CLASS_ORDER.indexOf(b.vessel_class),
      ),
    [data.tightness_by_class],
  )
  return (
    <Panel
      title="Current Tightness"
        soWhat={'Whether ships are scarce or plentiful right now. Tight means owners have the upper hand: expect to pay up, and do not expect to negotiate the rate down by waiting a week.'}
      meta={`as of ${data.as_of}`}
      hint="Trailing export flow / free-tonnage stock, per class, all basins pooled. Higher = capacity being drawn down faster relative to what exists."
      actions={<ProvenanceTag kind="MODEL_DERIVED" />}
    >
      <div className="flex flex-col gap-1 p-1">
        {byClass.map((row) => (
          <StatRow
            key={row.vessel_class}
            label={
              <span className="flex items-center gap-1">
                {row.vessel_class}
                {row.low_confidence && (
                  <Badge variant="outline" className="text-micro">
                    thin sample (n={row.n_obs})
                  </Badge>
                )}
              </span>
            }
            value={formatNumber(row.tightness, 4)}
          />
        ))}
      </div>
    </Panel>
  )
}

function BasinBreakdownPanel({ data }: { data: TonnageFieldResponse }) {
  return (
    <Panel
      title="Basin × Class Breakdown"
        soWhat={'Where the scarcity actually is, by region and ship size. Tightness in one basin does not bind you if your cargo loads in another — check your own row before reacting to the headline.'}
      meta={`${data.tightness_by_basin_class.length} cells`}
      actions={<ProvenanceTag kind="MODEL_DERIVED" />}
      flush
    >
      <table className="desk-table w-full">
        <thead>
          <tr>
            <th>Basin</th>
            <th>Class</th>
            <th className="text-right">Tightness</th>
          </tr>
        </thead>
        <tbody>
          {[...data.tightness_by_basin_class]
            .sort(
              (a, b) =>
                CLASS_ORDER.indexOf(a.vessel_class) - CLASS_ORDER.indexOf(b.vessel_class) ||
                a.basin.localeCompare(b.basin),
            )
            .map((row) => (
              <tr key={`${row.basin}-${row.vessel_class}`}>
                <td className="text-caption text-muted-foreground">{row.basin.replace('_', ' ')}</td>
                <td>{row.vessel_class}</td>
                <td className="desk-num text-right">{formatNumber(row.tightness, 4)}</td>
              </tr>
            ))}
        </tbody>
      </table>
    </Panel>
  )
}

function EvidenceQualityPanel({ data }: { data: TonnageFieldResponse }) {
  const sv = data.evidence_quality.signal_validation
  return (
    <Panel
      title="Evidence Quality"
        soWhat={'How much real data sits behind the tightness reading. A thin sample is a weak signal: if the count here is low, do not let this screen override what your brokers are telling you.'}
      hint="How much of the real port-call universe this reconstruction actually covers, and how it checks against real third-party ballaster counts."
      actions={<ProvenanceTag kind="OBSERVED" />}
    >
      <div className="flex flex-col gap-1 p-1">
        <StatRow label="Ports used" value={data.evidence_quality.n_ports_used} />
        <StatRow label="Ports skipped" value={data.evidence_quality.n_ports_skipped} />
        <StatRow
          label="Clipped fraction"
          value={`${formatNumber(data.evidence_quality.clipped_fraction * 100, 1)}%`}
          tone={data.evidence_quality.clipped_fraction > 0.05 ? 'wait' : 'plain'}
        />
        <div className="mt-1 border-t border-border pt-2">
          <span className="stat-label">vs. real Signal Ocean ballaster counts ({sv.n_points} points)</span>
          <StatRow label="Ratio range" value={`${formatNumber(sv.min_ratio, 2)}x – ${formatNumber(sv.max_ratio, 2)}x`} />
          <StatRow label="Median ratio" value={`${formatNumber(sv.median_ratio, 2)}x`} />
          <StatRow
            label="Absolute scale validated"
            value={sv.absolute_scale_validated ? 'YES' : 'NO'}
            tone={sv.absolute_scale_validated ? 'go' : 'wait'}
          />
        </div>
      </div>
    </Panel>
  )
}

// ---------------------------------------------------------------------------
// Forward tightness band chart -- real p10/p50/p90 fan, real visx primitives.
// ---------------------------------------------------------------------------

function TightnessChart({ points }: { points: TightnessForwardPoint[] }) {
  if (points.length === 0) {
    return <p className="p-2 text-body text-muted-foreground">No forward projection for this class.</p>
  }
  const yMax = Math.max(...points.map((p) => p.p90)) * 1.08
  const yMin = 0
  return (
    <div className="h-40 w-full">
      <ParentSize>
        {({ width, height }) => {
          if (width < 10) return null
          const marginLeft = 44
          const marginBottom = 18
          const marginTop = 4
          const innerW = Math.max(width - marginLeft - 4, 1)
          const innerH = Math.max(height - marginBottom - marginTop, 1)
          const xScale = scaleLinear({ domain: [points[0].horizon_days, points[points.length - 1].horizon_days], range: [0, innerW] })
          const yScale = scaleLinear({ domain: [yMin, yMax], range: [innerH, 0] })
          return (
            <svg width={width} height={height}>
              <g transform={`translate(${marginLeft},${marginTop})`}>
                <GridRows scale={yScale} width={innerW} numTicks={4} stroke="var(--border)" strokeDasharray="2,2" />
                <AreaClosed
                  data={points}
                  x={(d) => xScale(d.horizon_days) ?? 0}
                  y0={(d) => yScale(d.p10) ?? 0}
                  y1={(d) => yScale(d.p90) ?? 0}
                  yScale={yScale}
                  curve={curveMonotoneX}
                  fill="var(--market)"
                  fillOpacity={0.16}
                  stroke="none"
                />
                <LinePath
                  data={points}
                  x={(d) => xScale(d.horizon_days) ?? 0}
                  y={(d) => yScale(d.p50) ?? 0}
                  curve={curveMonotoneX}
                  stroke="var(--market)"
                  strokeWidth={1.75}
                />
                {[yMin, yMax / 2, yMax].map((t) => (
                  <text key={t} x={-6} y={yScale(t)} dy={3} textAnchor="end" fontSize={9} fill="var(--muted-foreground)">
                    {formatNumber(t, 3)}
                  </text>
                ))}
                <text x={0} y={innerH + 14} fontSize={9} fill="var(--muted-foreground)">
                  +{points[0].horizon_days}d
                </text>
                <text x={innerW} y={innerH + 14} textAnchor="end" fontSize={9} fill="var(--muted-foreground)">
                  +{points[points.length - 1].horizon_days}d
                </text>
              </g>
            </svg>
          )
        }}
      </ParentSize>
    </div>
  )
}

function ForwardTightnessPanel({ forward }: { forward: TonnageFieldForwardResponse | null }) {
  const [cls, setCls] = useState('Capesize')
  const points = useMemo(
    () =>
      forward
        ? forward.projections.filter((p) => p.vessel_class === cls).sort((a, b) => a.horizon_days - b.horizon_days)
        : [],
    [forward, cls],
  )
  return (
    <Panel
      title="Forward Tightness"
        soWhat={'Where the model thinks scarcity is heading over the coming weeks. Loosening ahead is an argument for waiting; tightening ahead is an argument for fixing now.'}
      meta={forward ? `p10 / p50 / p90, +${forward.projections.at(-1)?.horizon_days ?? 0}d` : undefined}
      hint="Persistence / random-walk-with-drift extrapolation of the recent trailing trend -- not a forecast model. The band widens as sqrt(horizon), honestly reflecting that nothing beyond recent port activity is known this far out."
      actions={<ProvenanceTag kind="MODEL_DERIVED" />}
    >
      <div className="flex flex-col gap-2 p-1">
        {/* A segmented control, not four loose buttons: one shared border,
            no gaps, and the selected segment filled -- so it reads as "pick
            one of four" at a glance rather than as four independent actions. */}
        <div
          role="group"
          aria-label="Vessel class"
          className="inline-flex overflow-hidden rounded-sm border border-border"
        >
          {CLASS_ORDER.map((c) => (
            <button
              key={c}
              type="button"
              onClick={() => setCls(c)}
              aria-pressed={cls === c}
              className={cn(
                'cursor-pointer border-r border-border px-2 py-1 text-caption font-semibold transition-colors last:border-r-0',
                'focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-ring',
                cls === c
                  ? 'bg-market text-market-fg'
                  : 'bg-surface text-muted-foreground hover:bg-surface-2 hover:text-foreground',
              )}
            >
              {c}
            </button>
          ))}
        </div>
        {forward ? <TightnessChart points={points} /> : <PanelLoading rows={3} label="Loading the forward projection" />}
        {forward && points.length > 0 && (
          <div className="flex justify-between text-caption text-muted-foreground">
            <span>p10: {formatNumber(points[0].p10, 4)} → {formatNumber(points.at(-1)!.p10, 4)}</span>
            <span>p50: {formatNumber(points[0].p50, 4)} → {formatNumber(points.at(-1)!.p50, 4)}</span>
            <span>p90: {formatNumber(points[0].p90, 4)} → {formatNumber(points.at(-1)!.p90, 4)}</span>
          </div>
        )}
      </div>
    </Panel>
  )
}

// ---------------------------------------------------------------------------
// Validation + ablation -- P3 requirement 9: "the ablation result stated
// honestly." Loaded separately: the endpoint's first call trains real models
// (~45s), so this panel gets its own loading state rather than blocking the
// rest of the page.
// ---------------------------------------------------------------------------

function SignDiagnosisRow({ d }: { d: SignDiagnosis }) {
  return (
    <tr>
      <td className="font-semibold">{d.vessel_class}</td>
      <td className="desk-num text-right" style={{ color: d.pearson_r < 0 ? 'var(--risk)' : undefined }}>
        {formatNumber(d.pearson_r, 3)}
      </td>
      <td className="desk-num text-right">{d.n_obs}</td>
      <td className="max-w-96 text-caption text-muted-foreground">{d.explanation}</td>
    </tr>
  )
}

function AblationTable({ rows }: { rows: AblationMetricRow[] }) {
  const testPooled = rows.filter((r) => r.split === 'test' && r.scope === 'POOLED').sort((a, b) => a.h - b.h)
  return (
    <table className="desk-table w-full">
      <thead>
        <tr>
          <th>Horizon</th>
          <th className="text-right">n</th>
          <th className="text-right">Without this signal</th>
          <th className="text-right">With this signal</th>
          <th className="text-right">Change (+ = improved)</th>
          <th className="text-right">Without: dir. hit</th>
          <th className="text-right">With: dir. hit</th>
        </tr>
      </thead>
      <tbody>
        {testPooled.map((r) => {
          const impr = r.pinball_0_5_a === 0 ? 0 : (r.pinball_0_5_a - r.pinball_0_5_b) / Math.abs(r.pinball_0_5_a)
          return (
            <tr key={r.h}>
              <td>{r.h}d</td>
              <td className="desk-num text-right">{r.n}</td>
              <td className="desk-num text-right">{formatNumber(r.pinball_0_5_a, 5)}</td>
              <td className="desk-num text-right">{formatNumber(r.pinball_0_5_b, 5)}</td>
              <td className="desk-num text-right" style={{ color: impr > 0 ? 'var(--go)' : 'var(--risk)' }}>
                {impr >= 0 ? '+' : ''}
                {formatNumber(impr * 100, 2)}%
              </td>
              <td className="desk-num text-right">{formatNumber(r.dir_hit_a * 100, 1)}%</td>
              <td className="desk-num text-right">{formatNumber(r.dir_hit_b * 100, 1)}%</td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

function ValidationPanel() {
  const [validation, setValidation] = useState<TonnageFieldValidationResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [requested, setRequested] = useState(false)

  function load() {
    setRequested(true)
    setLoading(true)
    setError(null)
    fetchTonnageFieldValidation()
      .then(setValidation)
      .catch((err: unknown) => setError(err instanceof Error ? err.message : 'Failed to load validation.'))
      .finally(() => setLoading(false))
  }

  return (
    <Panel
      title="Does this actually improve the forecast?"
        soWhat={'An honest scoreboard of whether adding this signal made the rate forecast better or worse. If it did not help, that is reported here rather than hidden — and you should weight this screen accordingly.'}
      meta="Forecast alone vs. forecast + this page's tightness signal"
      hint="The real test that decides whether this page's tightness signal is actually used in pricing: does adding it measurably improve the freight forecast, out-of-sample, on the real frozen holdout?"
      className="lg:col-span-3"
      actions={<ProvenanceTag kind="MODEL_DERIVED" />}
    >
      {!requested && (
        <div className="flex flex-col items-start gap-2 p-2">
          <p className="text-body leading-relaxed text-muted-foreground">
            Runs a real A/B XGBoost ablation on the first load — about 45 seconds, training six real
            models. Cached after that.
          </p>
          <Button variant="primary" size="md" onClick={load}>
            Run validation
          </Button>
        </div>
      )}
      {loading && (
        <div className="p-2">
          <p className="mb-2 text-body text-muted-foreground" role="status" aria-live="polite">
            Training the A/B models and scoring the real holdout split…
          </p>
          <PanelLoading rows={4} label="Training models" />
        </div>
      )}
      {error && <PanelError message={error} onRetry={load} />}
      {validation && (
        <div className="flex flex-col gap-2 p-1">
          <div
            className={cn(
              'border p-2 text-body',
              validation.ablation.adopt_b ? 'border-go bg-go-soft text-go' : 'border-wait bg-wait-soft text-wait',
            )}
          >
            <span className="font-bold uppercase tracking-wide">
              {validation.ablation.adopt_b ? 'Adopted -- wired into the live forecast' : 'Not adopted -- decision-support only'}
            </span>
            <p className="mt-0.5 text-foreground">{validation.ablation.reasoning}</p>
          </div>

          <AblationTable rows={validation.ablation.rows} />

          <div className="grid grid-cols-1 gap-2 border-t border-border pt-2 md:grid-cols-2">
            <div>
              <span className="stat-label">IV (instrumental variable)</span>
              <StatRow label="Status" value={validation.iv_verdict.status.replace(/_/g, ' ')} />
              <p className="mt-0.5 text-caption text-muted-foreground">{validation.iv_verdict.reason}</p>
            </div>
            <div>
              <span className="stat-label">Kalman / state-space</span>
              <StatRow label="Status" value={validation.kalman_verdict.status.replace(/_/g, ' ')} />
              <p className="mt-0.5 text-caption text-muted-foreground">{validation.kalman_verdict.reason}</p>
            </div>
          </div>

          <div className="border-t border-border pt-2">
            <span className="stat-label">Supply-curve sign diagnosis, per class</span>
            <table className="desk-table mt-1 w-full">
              <thead>
                <tr>
                  <th>Class</th>
                  <th className="text-right">Pearson r</th>
                  <th className="text-right">n</th>
                  <th>Diagnosis</th>
                </tr>
              </thead>
              <tbody>
                {[...validation.sign_diagnoses]
                  .sort((a, b) => CLASS_ORDER.indexOf(a.vessel_class) - CLASS_ORDER.indexOf(b.vessel_class))
                  .map((d) => (
                    <SignDiagnosisRow key={d.vessel_class} d={d} />
                  ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </Panel>
  )
}

export function TonnageFieldPage() {
  const [data, setData] = useState<TonnageFieldResponse | null>(null)
  const [forward, setForward] = useState<TonnageFieldForwardResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    fetchTonnageField()
      .then((d) => {
        if (!cancelled) setData(d)
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Failed to load the Tonnage Field.')
      })
    fetchTonnageFieldForward(90).then((f) => {
      if (!cancelled) setForward(f)
    })
    return () => {
      cancelled = true
    }
  }, [])

  if (error) {
    return (
      <div className="flex h-full flex-col">
        <PageState tone="error" title="Could not load the Tonnage Field" hint={error} />
      </div>
    )
  }
  if (!data) {
    return (
      <div className="flex h-full flex-col">
        <PageState
          tone="busy"
          title="Loading the Tonnage Field…"
          hint="Reading the physical supply-pressure signal computed from real port-call activity."
        />
      </div>
    )
  }

  return (
    <div className="flex h-full flex-col gap-2 overflow-hidden p-2" id="tonnage-field">
      <Panel
        title="Tonnage Field"
        soWhat={'A map of where the world\'s ships are and how tightly they are spoken for. Use it to explain WHY the rate is where it is, before you argue about the rate itself.'}
        meta={`Physical supply-pressure signal · computed ${new Date(data.computed_at).toLocaleString()}${data.stale ? ' · STALE (last-good)' : ''}`}
        actions={
          <>
            {data.stale && (
              <Badge variant="destructive" className="text-micro">
                STALE
              </Badge>
            )}
            <Badge variant={data.index_type === 'RELATIVE' ? 'outline' : 'secondary'} className="text-micro">
              {data.index_type}
            </Badge>
          </>
        }
      >
        <div className="p-1">
          <IndexTypeBanner data={data} />
        </div>
      </Panel>

      <div className="grid min-h-0 flex-1 auto-rows-min grid-cols-1 content-start gap-2 overflow-auto lg:grid-cols-3">
        <CurrentTightnessPanel data={data} />
        <BasinBreakdownPanel data={data} />
        <EvidenceQualityPanel data={data} />
        <div className="lg:col-span-3">
          <ForwardTightnessPanel forward={forward} />
        </div>
        <ValidationPanel />
      </div>
    </div>
  )
}
