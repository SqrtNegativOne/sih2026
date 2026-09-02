import { ExplanationList } from '@/components/desk/explanation'
import { Panel } from '@/components/desk/panel'
import type { Explanation, OptimizerRecommendation, PortListing } from '@/lib/types'
import { formatNumber, formatUsdCompact, prettyPort } from '@/lib/format'

const hrs = (h: number) => `${(h / 24).toFixed(1)}d`

export function VoyageAssignmentsTable({
  rec,
  ports,
  assignmentExplanations,
  repositioningExplanations,
}: {
  rec: OptimizerRecommendation
  ports: PortListing[]
  /** `quote.explanations.voyage_assignments` and `.repositioning` -- one
   *  plain-English rationale per decision the scheduler made. Both were
   *  computed on every quote and discarded before this. */
  assignmentExplanations?: Explanation[] | null
  repositioningExplanations?: Explanation[] | null
}) {
  const portName = (code: string) =>
    prettyPort(ports.find((p) => p.code === code)?.name ?? code)
  const { voyage_assignments: assigns, rejected_options: rejected, repositioning_actions: repo } = rec

  return (
    <Panel
      className="h-full"
      id="assignments"
      title="Voyage Assignments"
      hint="CP-SAT profit-maximising assignment of your supplied vessels to this cargo, plus repositioning advice for any idle vessel. Times are days from now; profit needs a cargo revenue figure in the quote form."
      meta={`${assigns.length} assigned · total ${formatUsdCompact(rec.total_voyage_profit_usd)}`}
      flush
    >
      {assigns.length > 0 ? (
        <table className="desk-table">
          <thead>
            <tr>
              <th>Vessel</th>
              <th>Parcel</th>
              <th>Dest</th>
              <th className="text-right">Arrive</th>
              <th className="text-right">Wait</th>
              <th className="text-right">Finish</th>
              <th className="text-right">Ballast</th>
              <th className="text-right">Profit</th>
            </tr>
          </thead>
          <tbody>
            {assigns.map((a) => (
              <tr key={`${a.vessel_id}-${a.parcel_id}`}>
                <td className="font-semibold">{a.vessel_id}</td>
                <td className="text-body text-muted-foreground">{a.parcel_id}</td>
                <td className="text-body">{portName(a.dest_port)}</td>
                <td className="desk-num text-right">{hrs(a.arrival_hours)}</td>
                <td className="desk-num text-right">{hrs(a.wait_hours)}</td>
                <td className="desk-num text-right">{hrs(a.finish_hours)}</td>
                <td className="desk-num text-right text-muted-foreground">{hrs(a.ballast_hours)}</td>
                <td className="desk-num text-right font-semibold">{formatUsdCompact(a.profit_usd)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p className="px-2 py-3 text-lead text-muted-foreground">
          No vessel assigned to this cargo — supply a vessel and a cargo revenue figure to assign.
        </p>
      )}

      {repo.length > 0 && (
        <div className="border-t border-border bg-surface-2 px-2 py-2">
          <div className="mb-1 text-caption font-bold uppercase tracking-wide text-muted-foreground">
            Repositioning
          </div>
          <ul className="space-y-0.5 text-body">
            {repo.map((r) => (
              <li key={r.vessel_id} className="flex justify-between gap-2">
                <span>
                  <span className="font-semibold">{r.vessel_id}</span>{' '}
                  {r.is_staying ? (
                    <>stay at {portName(r.current_port)}</>
                  ) : (
                    <>
                      {portName(r.current_port)} → {portName(r.recommended_port)}
                    </>
                  )}
                </span>
                <span
                  className="desk-num text-muted-foreground"
                  title={
                    r.data_provenance === 'ESTIMATED'
                      ? 'Built from PortWatch export-tonnage estimates (their own model output, not a measured figure).'
                      : 'No real tonnage-field coverage for this port -- neutral 0.5 prior, not data-backed.'
                  }
                >
                  p(cargo) {formatNumber(r.cargo_probability_within_window * 100, 0)}%
                  {r.data_provenance === 'ESTIMATED' ? ' (est.)' : r.probability_is_real_data ? '' : ' (prior)'}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {rejected.length > 0 && (
        <ul className="space-y-0.5 border-t border-border px-2 py-2 text-caption text-muted-foreground">
          {rejected.map((r) => (
            <li key={`${r.vessel_id}-${r.parcel_id}`}>
              <span className="font-semibold">
                {r.vessel_id} / {r.parcel_id}:
              </span>{' '}
              {r.reason}
            </li>
          ))}
        </ul>
      )}
      {(assignmentExplanations?.length || repositioningExplanations?.length) ? (
        <div className="space-y-1 border-t border-border px-2 pb-2">
          <ExplanationList explanations={assignmentExplanations} label="Why these assignments" />
          <ExplanationList
            explanations={repositioningExplanations}
            label="Why these repositioning moves"
          />
        </div>
      ) : null}
    </Panel>
  )
}
