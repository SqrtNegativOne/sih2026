import { Panel } from '@/components/desk/panel'
import { formatNumber } from '@/lib/format'
import type { ChokepointFracture, FractureBand, QuoteFractureSummary } from '@/lib/types'
import { cn } from '@/lib/utils'

// Same three-tone convention CIIPanel's RATING_CLASS and risk-feed.tsx's
// SEVERITY_DOT already use, extended for a fourth (critical) band by
// intensity rather than a new color -- 'watch' and 'elevated' both read as
// wait-toned since neither is a real all-clear, 'critical' is the one band
// that gets the solid risk treatment.
const BAND_CLASS: Record<FractureBand, string> = {
  calm: 'bg-go/15 text-go',
  watch: 'bg-wait/15 text-wait',
  elevated: 'bg-wait/30 text-wait',
  critical: 'bg-risk/20 text-risk',
}

const BAND_LABEL: Record<FractureBand, string> = {
  calm: 'Calm',
  watch: 'Watch',
  elevated: 'Elevated',
  critical: 'Critical',
}

// Which of the four possible inputs actually fed this row's index -- never
// let a one-signal score read like a four-signal one. jwc_listed is always
// a real fact (the reference table is complete), so it's never the ONLY
// gap worth naming; transit_z/conflict_z/draft_restricted being short is.
const INPUT_LABEL: Record<string, string> = {
  transit_z: 'transit',
  conflict_z: 'conflict coverage',
  jwc_listed: 'war-risk listing',
  draft_restricted: 'draft advisory',
}
const ALL_INPUTS = ['transit_z', 'conflict_z', 'jwc_listed', 'draft_restricted']

function inputsAvailableNote(c: ChokepointFracture): string | null {
  if (c.inputs_available.length >= ALL_INPUTS.length) return null
  const present = c.inputs_available.map((k) => INPUT_LABEL[k] ?? k)
  return `${present.join(' + ') || 'no signal'} only`
}

function BandChip({ band }: { band: FractureBand }) {
  return (
    <span className={cn('rounded-sm px-1.5 py-px text-[10px] font-bold uppercase tracking-wide', BAND_CLASS[band])}>
      {BAND_LABEL[band]}
    </span>
  )
}

function ChokepointRow({ c }: { c: ChokepointFracture }) {
  const note = inputsAvailableNote(c)
  return (
    <tr>
      <td>
        <span className="text-[11px] font-medium text-foreground">{c.chokepoint_name}</span>
        {c.jwc_listed && (
          <span className="ml-1.5 text-[9px] uppercase tracking-wide text-muted-foreground">JWC listed</span>
        )}
      </td>
      <td>
        <BandChip band={c.band} />
      </td>
      <td className="desk-num text-right">{formatNumber(c.index, 1)}</td>
      <td className="text-right text-[10px] text-muted-foreground">
        {note ?? 'full signal set'}
      </td>
    </tr>
  )
}

export function FracturePanel({ fracture }: { fracture: QuoteFractureSummary | null }) {
  if (!fracture) {
    return (
      <Panel
        id="fracture"
        title="Chokepoint Fracture Index"
        hint="A combined 0-100 disruption score per chokepoint on this route -- real PortWatch transit anomaly, real GDELT conflict-coverage anomaly, and real Joint War Committee Listed Area membership, fused. A score built from fewer than the full signal set is capped at 'Watch', however high its raw index -- see opt.fracture's own module docstring."
      >
        <div className="flex h-full items-center justify-center text-center text-[12px] text-muted-foreground">
          Fracture data unavailable for this quote's route.
        </div>
      </Panel>
    )
  }

  if (fracture.chokepoints.length === 0) {
    return (
      <Panel
        id="fracture"
        title="Chokepoint Fracture Index"
        hint="A combined 0-100 disruption score per chokepoint on this route -- real PortWatch transit anomaly, real GDELT conflict-coverage anomaly, and real Joint War Committee Listed Area membership, fused."
      >
        <div className="flex h-full items-center justify-center text-center text-[12px] text-muted-foreground">
          This route transits no monitored chokepoint.
        </div>
      </Panel>
    )
  }

  return (
    <Panel
      id="fracture"
      title="Chokepoint Fracture Index"
      hint="A combined 0-100 disruption score per chokepoint on this route -- real PortWatch transit anomaly, real GDELT conflict-coverage anomaly, and real Joint War Committee Listed Area membership, fused. A score built from fewer than the full signal set is capped at 'Watch', however high its raw index -- see opt.fracture's own module docstring. 'Inputs' says exactly which signals fed each row, so a one-signal score is never mistaken for a four-signal one."
      meta={`${fracture.chokepoints.length} chokepoint${fracture.chokepoints.length === 1 ? '' : 's'}`}
      flush
    >
      <table className="desk-table">
        <thead>
          <tr>
            <th>Chokepoint</th>
            <th>Band</th>
            <th className="text-right">Index</th>
            <th className="text-right">Inputs</th>
          </tr>
        </thead>
        <tbody>
          {fracture.chokepoints.map((c) => (
            <ChokepointRow key={c.chokepoint_id} c={c} />
          ))}
        </tbody>
      </table>
      {fracture.jwc_listed_areas.length > 0 && (
        <div className="border-t border-border px-1.5 py-1 text-[9px] text-muted-foreground">
          Route enters {fracture.jwc_listed_areas.length} Joint War Committee Listed Area
          {fracture.jwc_listed_areas.length === 1 ? '' : 's'}: {fracture.jwc_listed_areas.join(', ')}
        </div>
      )}
    </Panel>
  )
}
