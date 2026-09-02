import { useState } from 'react'
import { Panel } from '@/components/desk/panel'
import { fetchBackhaul } from '@/lib/api'
import { formatNumber, prettyPort } from '@/lib/format'
import type { BackhaulOpportunityScore, PortCode, VesselInput } from '@/lib/types'
import { cn } from '@/lib/utils'
import { useMoney } from '@/lib/money-context'

function ResultRow({ r }: { r: BackhaulOpportunityScore }) {
  const ev = r.pairing_evidence
  const { moneyCompact } = useMoney()
  return (
    <div className="border-b border-border p-2 last:border-b-0">
      <div className="flex items-center justify-between gap-2">
        <span className="text-body font-semibold">{prettyPort(r.candidate_load_port)}</span>
        {r.score_usd != null ? (
          <span
            className={cn(
              'desk-num text-body font-semibold',
              r.score_usd > 0 ? 'text-go' : 'text-muted-foreground',
            )}
            title="P(cargo) x today's real TC quote x window - real ballast fuel cost. Same class-level rate at every port -- see the panel hint."
          >
            {moneyCompact(r.score_usd)}
          </span>
        ) : (
          <span
            className="desk-num text-body font-semibold text-muted-foreground"
            title="No real TC quote for this class/date -- ranked by cargo probability alone."
          >
            score {formatNumber(r.score, 3)}
          </span>
        )}
      </div>
      <div className="mt-0.5 flex flex-wrap gap-1">
        <span className="rounded-sm border border-border px-1 py-px font-mono text-micro text-muted-foreground">
          {formatNumber(r.ballast_days, 1)}d ballast · {moneyCompact(r.ballast_cost_usd)} fuel
        </span>
        <span className="rounded-sm border border-border px-1 py-px font-mono text-micro text-muted-foreground">
          P(cargo) {formatNumber(r.cargo_probability, 2)}
          {!r.cargo_probability_is_real_data && ' (prior)'}
        </span>
        {!r.class_feasibility.is_feasible && (
          <span className="rounded-sm border border-risk/40 bg-risk-soft px-1 py-px font-mono text-micro text-risk">
            infeasible
          </span>
        )}
        {ev.cross_port && !ev.load_port_has_coverage ? (
          <span className="rounded-sm border border-border px-1 py-px font-mono text-micro text-muted-foreground">
            no pairing evidence yet
          </span>
        ) : ev.pairing_rate != null ? (
          <span className="rounded-sm border border-border px-1 py-px font-mono text-micro text-muted-foreground">
            pairing {formatNumber(ev.pairing_rate * 100, 0)}% (n={ev.n_total_vessels}
            {!ev.is_sufficient && ', thin'})
          </span>
        ) : null}
      </div>
    </div>
  )
}

export function BackhaulPanel({
  vessel,
  dischargePort,
}: {
  vessel: VesselInput | undefined
  dischargePort: PortCode
}) {
  const [results, setResults] = useState<BackhaulOpportunityScore[] | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  function runSweep() {
    if (!vessel) return
    setLoading(true)
    setError(null)
    fetchBackhaul({ vessel, dischargePort })
      .then((r) => setResults(r.results))
      .catch((err: unknown) => setError(err instanceof Error ? err.message : 'Backhaul sweep failed.'))
      .finally(() => setLoading(false))
  }

  return (
    <Panel
      className="h-full"
      title="Backhaul Opportunity"
      meta="informational -- never moves the recommendation"
      hint="Score = P(class-appropriate cargo within the window) x today's real TC quote x window - real ballast fuel cost, after this vessel discharges here. The TC quote is class-level, identical at every port shown (no route-level rate geography yet) -- so this ranks by real cargo likelihood and real ballast cost, not by 'rates are better here'. credit_usd_per_mt is always null -- no rate field exists in the data to validate one against."
      actions={
        vessel && (
          <button
            type="button"
            onClick={runSweep}
            disabled={loading}
            className="h-5 rounded-sm border border-market bg-market/10 px-2 text-caption font-semibold text-market disabled:opacity-40"
          >
            {loading ? 'Scoring…' : results ? 'Re-run' : 'Score every port'}
          </button>
        )
      }
      flush
    >
      {!vessel ? (
        <div className="flex h-full items-center justify-center p-2 text-center text-body text-muted-foreground">
          Add a vessel to the quote to score backhaul opportunities for it.
        </div>
      ) : error ? (
        <div className="p-2 text-body text-risk">{error}</div>
      ) : !results ? (
        <div className="flex h-full items-center justify-center p-2 text-center text-body text-muted-foreground">
          {loading
            ? `Scoring ${vessel.vessel_id} against every other real port -- a real multi-second sweep, not cached.`
            : `Score ${vessel.vessel_id}'s backhaul opportunity after discharging at ${prettyPort(dischargePort)}.`}
        </div>
      ) : (
        <div>
          {results
            .slice(0, 8)
            .map((r) => <ResultRow key={r.candidate_load_port} r={r} />)}
        </div>
      )}
    </Panel>
  )
}
