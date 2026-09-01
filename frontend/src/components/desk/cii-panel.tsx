import { Panel } from '@/components/desk/panel'
import { formatNumber } from '@/lib/format'
import type { CIIRating, VesselCIIProjection, VoyageEmissions } from '@/lib/types'
import { cn } from '@/lib/utils'

// A/B use the go tokens (better than or in line with required), C uses wait
// (moderate), D/E use risk (worse than required) -- the same three-tone
// convention CONGESTION_CLASS (port-checks-table.tsx) already uses, reused
// rather than adding a new color scale.
const RATING_CLASS: Record<CIIRating, string> = {
  A: 'bg-go/15 text-go',
  B: 'bg-go/15 text-go',
  C: 'bg-wait/15 text-wait',
  D: 'bg-risk/15 text-risk',
  E: 'bg-risk/15 text-risk',
}

function RatingChip({ rating }: { rating: CIIRating }) {
  return (
    <span className={cn('rounded-sm px-2 py-px text-body font-bold', RATING_CLASS[rating])}>
      {rating}
    </span>
  )
}

function formatSignedPct(value: number): string {
  const sign = value > 0 ? '+' : ''
  return `${sign}${formatNumber(value, 1)}%`
}

function VesselRow({ p }: { p: VesselCIIProjection }) {
  return (
    <tr>
      <td>
        <span className="text-body font-medium text-foreground">{p.vessel_id}</span>
        <span className="ml-1 text-caption uppercase tracking-wide text-muted-foreground">
          {p.vessel_class}
        </span>
      </td>
      <td>
        <RatingChip rating={p.rating} />
      </td>
      <td className="desk-num text-right">{formatNumber(p.attained_cii, 2)}</td>
      <td className="desk-num text-right">{formatNumber(p.required_cii, 2)}</td>
      <td
        className={cn(
          'desk-num text-right font-semibold',
          p.margin_pct >= 0 ? 'text-go' : 'text-risk',
        )}
      >
        {formatSignedPct(p.margin_pct)}
      </td>
    </tr>
  )
}

export function CIIPanel({ emissions }: { emissions: VoyageEmissions | null }) {
  if (!emissions) {
    return (
      <Panel
        className="h-full"
        id="cii"
        title="Carbon Intensity (CII)"
        hint="IMO Carbon Intensity Indicator projection for each real vessel on this quote's route: attained vs required CII (gCO2/dwt·nm) and the A-E rating those two numbers imply."
      >
        <div className="flex h-full items-center justify-center text-center text-lead text-muted-foreground">
          Add a vessel to the quote to project its IMO carbon rating.
        </div>
      </Panel>
    )
  }

  return (
    <Panel
      className="h-full"
      id="cii"
      title="Carbon Intensity (CII)"
      hint="IMO Carbon Intensity Indicator projection for each real vessel on this quote's route: attained vs required CII (gCO2/dwt·nm) and the A-E rating those two numbers imply. Ballast fuel to reach the load port is charged against the laden leg's transport work -- the conservative reading; see emissions.projection's docstring."
      meta={`${emissions.projections.length} vessel${emissions.projections.length === 1 ? '' : 's'} · ${emissions.rating_year} rating`}
      flush
    >
      <table className="desk-table">
        <thead>
          <tr>
            <th>Vessel</th>
            <th>Rating</th>
            <th className="text-right">Attained</th>
            <th className="text-right">Required</th>
            <th className="text-right">Margin</th>
          </tr>
        </thead>
        <tbody>
          {emissions.projections.map((p) => (
            <VesselRow key={p.vessel_id} p={p} />
          ))}
        </tbody>
      </table>
      <div className="border-t border-border px-2 py-1 text-micro text-muted-foreground">
        gCO2/dwt·nm, both columns · positive margin = better than required
      </div>
    </Panel>
  )
}
