import { useState } from 'react'
import { Panel } from '@/components/desk/panel'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { fetchLandedCost } from '@/lib/api'
import { formatNumber } from '@/lib/format'
import type { DataProvenance, LandedCostBreakdown, PortCode, VesselClass } from '@/lib/types'
import { cn } from '@/lib/utils'

function ProvenanceBadge({ provenance }: { provenance: DataProvenance | null }) {
  if (!provenance) return null
  // DECLARED is a real, user-entered assumption -- deliberately the one
  // variant that reads as "you said this," never blended visually with the
  // real/derived tones (OBSERVED/MODEL_DERIVED/ESTIMATED/INFERRED).
  const variant = provenance === 'DECLARED' ? 'outline' : 'secondary'
  return (
    <Badge variant={variant} className="text-[9px]">
      {provenance === 'DECLARED' ? 'your input' : provenance.toLowerCase().replace('_', ' ')}
    </Badge>
  )
}

function ComponentRow({
  label,
  usdPerMt,
  provenance,
  reason,
  title,
}: {
  label: string
  usdPerMt: number | null
  provenance: DataProvenance | null
  reason: string
  title?: string
}) {
  return (
    <div className="flex items-center justify-between gap-2 border-b border-border/60 px-1.5 py-1 last:border-b-0">
      <div className="flex min-w-0 flex-col">
        <span className="text-[11px] font-medium text-foreground" title={title}>
          {label}
        </span>
        {usdPerMt == null && (
          <span className="truncate text-[9px] text-muted-foreground" title={reason}>
            {reason}
          </span>
        )}
      </div>
      <div className="flex shrink-0 items-center gap-1.5">
        <ProvenanceBadge provenance={provenance} />
        <span
          className={cn(
            'desk-num text-[12px]',
            usdPerMt == null ? 'text-muted-foreground' : 'text-foreground',
          )}
        >
          {usdPerMt == null ? '—' : `$${formatNumber(usdPerMt, 2)}`}
        </span>
      </div>
    </div>
  )
}

interface LandedCostPanelProps {
  /** The real, assumption-free slice POST /quote already computed
   * (freight only) -- shown until/unless the user recomputes below. */
  breakdown: LandedCostBreakdown | null
  destPort: PortCode
  cargoVolumeMt: number
  freightUsdPerDay: number
  voyageDays: number | null
  vesselClass: VesselClass
}

/** User-entered commercial-term assumptions -- every field starts blank.
 * There is deliberately no pre-filled "example" value: a placeholder that
 * looks like a real number is exactly the "clearly labelled default" the
 * P6 spec warns against inventing. */
interface Assumptions {
  handlingRateUsdPerMt: string
  demurrageUsdPerDay: string
  laytimeAllowanceDays: string
  commodity: '' | 'iron_ore' | 'coal'
  convertToInr: boolean
}

const EMPTY_ASSUMPTIONS: Assumptions = {
  handlingRateUsdPerMt: '',
  demurrageUsdPerDay: '',
  laytimeAllowanceDays: '',
  commodity: '',
  convertToInr: false,
}

export function LandedCostPanel({
  breakdown,
  destPort,
  cargoVolumeMt,
  freightUsdPerDay,
  voyageDays,
  vesselClass,
}: LandedCostPanelProps) {
  const [assumptions, setAssumptions] = useState<Assumptions>(EMPTY_ASSUMPTIONS)
  const [recomputed, setRecomputed] = useState<LandedCostBreakdown | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const shown = recomputed ?? breakdown
  const hasAnyAssumption =
    assumptions.handlingRateUsdPerMt !== '' ||
    assumptions.demurrageUsdPerDay !== '' ||
    assumptions.laytimeAllowanceDays !== '' ||
    assumptions.commodity !== '' ||
    assumptions.convertToInr

  function recompute() {
    if (voyageDays == null) return
    setLoading(true)
    setError(null)
    fetchLandedCost({
      dest_port: destPort,
      cargo_volume_mt: cargoVolumeMt,
      freight_usd_per_day: freightUsdPerDay,
      voyage_days: voyageDays,
      vessel_class: vesselClass,
      handling_rate_usd_per_mt: assumptions.handlingRateUsdPerMt ? Number(assumptions.handlingRateUsdPerMt) : undefined,
      demurrage_usd_per_day: assumptions.demurrageUsdPerDay ? Number(assumptions.demurrageUsdPerDay) : undefined,
      laytime_allowance_days: assumptions.laytimeAllowanceDays ? Number(assumptions.laytimeAllowanceDays) : undefined,
      commodity: assumptions.commodity || undefined,
      convert_to_inr: assumptions.convertToInr,
    })
      .then(setRecomputed)
      .catch((err: unknown) => setError(err instanceof Error ? err.message : 'Recompute failed.'))
      .finally(() => setLoading(false))
  }

  if (!shown) {
    return (
      <Panel className="h-full" title="Landed Cost" meta="$/MT">
        <div className="flex h-full items-center justify-center text-center text-[11px] text-muted-foreground">
          No real transit-day estimate for this route -- freight can't be converted to $/MT yet.
        </div>
      </Panel>
    )
  }

  const allMissing = shown.components_missing.length > 0

  return (
    <Panel
      className="h-full"
      title="Landed Cost"
      meta={`${shown.components_included.length}/5 components real`}
      hint="freight + wait/delay + handling + demurrage + commodity price, each with its own provenance. Unavailable components show a reason, never a silent $0. Enter your own handling/demurrage/laytime/commodity assumptions below to fill the gaps -- POST /landed-cost, never a repo-invented default."
      flush
    >
      <div>
        <ComponentRow
          label="Freight"
          usdPerMt={shown.freight_usd_per_mt}
          provenance={shown.freight_provenance}
          reason=""
          title="One component of this landed-cost total, not the same figure as the Fleet Mix panel's $/mt (that's the actual chosen vessel configuration's cost) or the Rate Forecast panel's $/mt (that's today's open-market quote for the target class, converted by transit days -- before any real vessel or fleet mix is chosen)."
        />
        <ComponentRow
          label="Wait / delay"
          usdPerMt={shown.wait_cost_usd_per_mt}
          provenance={shown.wait_cost_provenance}
          reason={shown.wait_cost_reason}
        />
        <ComponentRow
          label="Handling"
          usdPerMt={shown.handling_cost_usd_per_mt}
          provenance={shown.handling_cost_provenance}
          reason={shown.handling_cost_reason}
        />
        <ComponentRow
          label="Demurrage"
          usdPerMt={shown.demurrage_cost_usd_per_mt}
          provenance={shown.demurrage_cost_provenance}
          reason={shown.demurrage_cost_reason}
        />
        <ComponentRow
          label="Commodity price"
          usdPerMt={shown.commodity_price_usd_per_mt}
          provenance={shown.commodity_price_provenance}
          reason={shown.commodity_price_reason}
        />
      </div>

      <div className="mt-1 flex items-center justify-between border-t border-border bg-surface-2 px-1.5 py-1.5">
        <div className="flex flex-col">
          <span className="text-[10px] font-bold uppercase tracking-wide text-primary">
            {allMissing ? 'Partial total (lower bound)' : 'Total'}
          </span>
          {allMissing && (
            <span className="text-[9px] text-muted-foreground">
              excludes: {shown.components_missing.join(', ')}
            </span>
          )}
        </div>
        <div className="text-right">
          <div className="desk-num text-[14px] font-semibold text-foreground">
            ${formatNumber(shown.partial_total_usd_per_mt, 2)}
          </div>
          {shown.partial_total_inr_per_mt != null && (
            <div className="desk-num text-[10px] text-muted-foreground">
              ₹{formatNumber(shown.partial_total_inr_per_mt, 0)} (fx {shown.fx_inr_per_usd})
            </div>
          )}
        </div>
      </div>

      {/* Your assumptions -- every value here is user-entered, never a
       * repo-invented default. Unmistakably a form, not a data display. */}
      <div className="border-t border-border bg-surface-2/60 p-1.5">
        <div className="mb-1 text-[9px] font-bold uppercase tracking-wide text-muted-foreground">
          Fill the gaps with your own assumptions
        </div>
        <div className="grid grid-cols-2 gap-1 sm:grid-cols-3">
          <label className="flex flex-col gap-0.5">
            <span className="stat-label">Handling $/MT</span>
            <Input
              className="h-6 text-[11px]"
              placeholder="unset"
              value={assumptions.handlingRateUsdPerMt}
              onChange={(e) => setAssumptions((a) => ({ ...a, handlingRateUsdPerMt: e.target.value }))}
            />
          </label>
          <label className="flex flex-col gap-0.5">
            <span className="stat-label">Demurrage $/day</span>
            <Input
              className="h-6 text-[11px]"
              placeholder="unset"
              value={assumptions.demurrageUsdPerDay}
              onChange={(e) => setAssumptions((a) => ({ ...a, demurrageUsdPerDay: e.target.value }))}
            />
          </label>
          <label className="flex flex-col gap-0.5">
            <span className="stat-label">Laytime allowance (d)</span>
            <Input
              className="h-6 text-[11px]"
              placeholder="unset"
              value={assumptions.laytimeAllowanceDays}
              onChange={(e) => setAssumptions((a) => ({ ...a, laytimeAllowanceDays: e.target.value }))}
            />
          </label>
          <label className="flex flex-col gap-0.5">
            <span className="stat-label">Commodity</span>
            <select
              className="h-6 rounded border border-input bg-background px-1 text-[11px]"
              value={assumptions.commodity}
              onChange={(e) => setAssumptions((a) => ({ ...a, commodity: e.target.value as Assumptions['commodity'] }))}
            >
              <option value="">unset</option>
              <option value="iron_ore">Iron ore</option>
              <option value="coal">Coal</option>
            </select>
          </label>
          <label className="flex items-center gap-1 self-end pb-0.5">
            <input
              type="checkbox"
              checked={assumptions.convertToInr}
              onChange={(e) => setAssumptions((a) => ({ ...a, convertToInr: e.target.checked }))}
            />
            <span className="text-[10px] text-muted-foreground">show ₹ (real FX)</span>
          </label>
          <button
            type="button"
            onClick={recompute}
            disabled={loading || voyageDays == null || !hasAnyAssumption}
            className="h-6 self-end rounded-[3px] border border-market bg-market/10 px-2 text-[10px] font-semibold text-market disabled:opacity-40"
          >
            {loading ? 'Recomputing…' : 'Recompute'}
          </button>
        </div>
        {error && <div className="mt-1 text-[10px] text-risk">{error}</div>}
      </div>
    </Panel>
  )
}
