import { Panel } from '@/components/desk/panel'
import { Grade, scoreToGrade } from '@/components/desk/grade'
import type { FleetConfiguration, FleetMixFrontier } from '@/lib/types'
import { formatNumber, formatUsdCompact } from '@/lib/format'

function Row({
  c,
  requirementDwt,
  rejected,
}: {
  c: FleetConfiguration
  requirementDwt: number
  rejected?: boolean
}) {
  const usdPerMt = requirementDwt > 0 ? c.cost_p50_usd / requirementDwt : null
  return (
    <tr className={rejected ? 'opacity-55' : undefined}>
      <td className="font-semibold">{c.vessel_class}</td>
      <td className="desk-num text-right">{c.n_vessels}</td>
      <td className="desk-num text-right text-muted-foreground">{formatNumber(c.dwt_per_vessel)}</td>
      <td className="desk-num text-right text-muted-foreground">
        {formatNumber(c.total_capacity_dwt)}
      </td>
      <td className="desk-num text-right">
        {rejected ? '—' : c.voyage_days_per_vessel.toFixed(1)}
      </td>
      <td className="desk-num text-right font-semibold">
        {rejected ? '—' : formatUsdCompact(c.cost_p50_usd)}
      </td>
      <td className="desk-num text-right">
        {rejected || usdPerMt == null ? '—' : `$${usdPerMt.toFixed(2)}`}
      </td>
      <td className="desk-num text-right text-[10px] text-muted-foreground">
        {rejected ? '—' : `${formatUsdCompact(c.cost_p10_usd)}–${formatUsdCompact(c.cost_p90_usd)}`}
      </td>
      <td className="text-center">
        {rejected ? (
          <span className="text-muted-foreground">—</span>
        ) : (
          <Grade letter={scoreToGrade(c.reliability_score)} />
        )}
      </td>
      <td className="text-center">
        {c.requires_transshipment ? (
          <span
            className="rounded-sm bg-wait/15 px-1 text-[10px] font-semibold text-wait"
            title={c.transshipment_hub ?? undefined}
          >
            T/S
          </span>
        ) : (
          <span className="text-muted-foreground">·</span>
        )}
      </td>
    </tr>
  )
}

export function FleetMixTable({ frontier }: { frontier: FleetMixFrontier }) {
  return (
    <Panel
      className="h-full"
      id="fleet"
      title="Fleet Mix Frontier"
      hint="Cheapest feasible vessel-class configurations for this cargo and route, priced under the real forecast. Rel. is a reliability grade (fewer ships, no transshipment = higher). T/S marks a transshipment leg. Greyed rows were ruled out; reasons below."
      meta={`${formatNumber(frontier.requirement_dwt)} dwt required`}
      flush
    >
      <table className="desk-table">
        <thead>
          <tr>
            <th>Class</th>
            <th className="text-right">Ves.</th>
            <th className="text-right">DWT/ea</th>
            <th className="text-right">Total cap</th>
            <th className="text-right">Voy d</th>
            <th className="text-right">Cost p50</th>
            <th
              className="text-right"
              title="This configuration's own cost p50 ÷ cargo tonnes -- the actual chosen fleet mix, not the Rate Forecast panel's open-market class quote or the Landed Cost panel's freight component."
            >
              $/mt
            </th>
            <th className="text-right">p10–p90</th>
            <th className="text-center">Rel.</th>
            <th className="text-center">T/S</th>
          </tr>
        </thead>
        <tbody>
          {frontier.configurations.map((c) => (
            <Row
              key={`ok-${c.vessel_class}-${c.n_vessels}`}
              c={c}
              requirementDwt={frontier.requirement_dwt}
            />
          ))}
          {frontier.rejected_configurations.map((c) => (
            <Row
              key={`rej-${c.vessel_class}-${c.n_vessels}`}
              c={c}
              requirementDwt={frontier.requirement_dwt}
              rejected
            />
          ))}
        </tbody>
      </table>
      {frontier.rejected_configurations.length > 0 && (
        <ul className="space-y-0.5 border-t border-border bg-surface-2 px-2 py-1.5 text-[10px] text-muted-foreground">
          {frontier.rejected_configurations
            .filter((c) => c.infeasible_reason)
            .map((c) => (
              <li key={`why-${c.vessel_class}-${c.n_vessels}`}>
                <span className="font-semibold">{c.vessel_class} ×{c.n_vessels}:</span>{' '}
                {c.infeasible_reason}
              </li>
            ))}
        </ul>
      )}
    </Panel>
  )
}
