import { Tooltip } from '@/components/ui/tooltip'
import { useState } from 'react'
import { Panel } from '@/components/desk/panel'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { fetchLandedCost } from '@/lib/api'
import { formatNumber } from '@/lib/format'
import type { DataProvenance, LandedCostBreakdown, PortCode, VesselClass } from '@/lib/types'
import { componentLabel, humanizeReason, wasRewritten } from '@/lib/humanize'
import { PROVENANCE, type ProvenanceKind } from '@/lib/vocabulary'
import { cn } from '@/lib/utils'

/** The panel's plain-English Layer 2 (see Panel's `soWhat` prop). Declared
 *  once here because this component renders the same panel in several states
 *  -- loading, error, empty, populated -- and the explanation is the same in
 *  all of them. */
const SO_WHAT =
  'What one tonne of this cargo costs delivered to the plant gate, freight ' +
  'and port charges included. This is the number to compare against buying ' +
  'the same tonne domestically; if it is higher, the import does not pay.'

function ProvenanceBadge({ provenance }: { provenance: DataProvenance | null }) {
  if (!provenance) return null
  // DECLARED is a real, user-entered assumption -- deliberately the one
  // variant that reads as "you said this," never blended visually with the
  // real/derived tones (OBSERVED/MODEL_DERIVED/ESTIMATED/INFERRED).
  const variant = provenance === 'DECLARED' ? 'outline' : 'secondary'
  // "your input" for DECLARED, otherwise the shared plain-English word from
  // lib/vocabulary rather than a lowercased enum ("model derived"). The enum
  // and the full definition stay in the tooltip.
  const t = PROVENANCE[provenance as ProvenanceKind]
  return (
    <Badge
      variant={variant}
      className="cursor-help text-micro"
      title={t ? `${t.definition} (${provenance})` : undefined}
    >
      {provenance === 'DECLARED' ? 'your input' : (t?.label ?? provenance.toLowerCase())}
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
    <div className="flex items-center justify-between gap-2 border-b border-border/60 px-2 py-1 last:border-b-0">
      <div className="flex min-w-0 flex-col">
        {/* A real Tooltip when there is something to explain, plain text when
            there is not -- `title` never opens on keyboard focus or on touch,
            and the Freight row's caveat (which of this desk's three different
            $/mt figures this one is) is precisely the sort of thing a reader
            has to be able to reach. */}
        {title ? (
          <Tooltip content={title} className="cursor-help">
            <span className="border-b border-dotted border-muted-foreground/50 text-body font-medium text-foreground">
              {label}
            </span>
          </Tooltip>
        ) : (
          <span className="text-body font-medium text-foreground">{label}</span>
        )}
        {/* The API's own reason strings carry raw field names and endpoint
            paths ("opex_usd_per_day is not available at the /quote level --
            see POST /landed-cost"). The caveat is exactly right and stays; the
            implementation vocabulary does not. Unmatched messages pass through
            verbatim, and the original is always kept in the tooltip so the
            precise wording stays auditable. Wrapped rather than truncated --
            this is the text that says which parts of the total are real, so
            clipping it mid-sentence defeats the point. */}
        {usdPerMt == null && (
          <span
            className="text-micro leading-relaxed text-muted-foreground"
            title={wasRewritten(reason) ? reason : undefined}
          >
            {humanizeReason(reason)}
          </span>
        )}
      </div>
      <div className="flex shrink-0 items-center gap-2">
        <ProvenanceBadge provenance={provenance} />
        <span
          className={cn(
            'desk-num text-lead',
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
}

const EMPTY_ASSUMPTIONS: Assumptions = {
  handlingRateUsdPerMt: '',
  demurrageUsdPerDay: '',
  laytimeAllowanceDays: '',
  commodity: '',
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
    assumptions.commodity !== ''

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
    })
      .then(setRecomputed)
      .catch((err: unknown) => setError(err instanceof Error ? err.message : 'Recompute failed.'))
      .finally(() => setLoading(false))
  }

  if (!shown) {
    return (
      <Panel className="h-full" title="Landed Cost"
      soWhat={SO_WHAT} meta="$/MT">
        <div className="flex h-full items-center justify-center text-center text-body text-muted-foreground">
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
      soWhat={SO_WHAT}
      meta={`${shown.components_included.length}/5 components real`}
      hint="What a tonne actually costs delivered: freight, waiting time, handling, demurrage and the commodity itself, each labelled with where its figure came from. A component that cannot be priced states why instead of quietly counting as zero. Fill in your own handling, demurrage, laytime and commodity assumptions below to complete the total — this desk will never substitute an invented default for a commercial term you have not given it."
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

      <div className="mt-1 flex items-center justify-between border-t border-border bg-surface-2 px-2 py-2">
        <div className="flex flex-col">
          <span className="text-caption font-bold uppercase tracking-wide text-primary">
            {allMissing ? 'Partial total (lower bound)' : 'Total'}
          </span>
          {allMissing && (
            <span className="text-micro text-muted-foreground">
              {/* The API returns these as field names (`handling_cost`,
                  `war_risk`); COMPONENT_LABEL maps them to the same words the
                  rows above use, so the exclusion list and the rows it refers
                  to finally agree. Unknown keys fall through unchanged. */}
              excludes: {shown.components_missing.map(componentLabel).join(', ')}
            </span>
          )}
        </div>
        <div className="text-right">
          <div className="desk-num text-figure font-semibold text-foreground">
            ${formatNumber(shown.partial_total_usd_per_mt, 2)}
          </div>
          {shown.partial_total_inr_per_mt != null && (
            <div className="desk-num text-caption text-muted-foreground">
              ₹{formatNumber(shown.partial_total_inr_per_mt, 0)} (fx {shown.fx_inr_per_usd})
            </div>
          )}
        </div>
      </div>

      {/* Your assumptions -- every value here is user-entered, never a
       * repo-invented default. Unmistakably a form, not a data display. */}
      <div className="border-t border-border bg-surface-2/60 p-2">
        <div className="mb-1 text-micro font-bold uppercase tracking-wide text-muted-foreground">
          Fill the gaps with your own assumptions
        </div>
        <div className="grid grid-cols-2 gap-1 sm:grid-cols-3">
          <label className="flex flex-col gap-0.5">
            <span className="stat-label">Handling $/MT</span>
            <Input
              className="h-6 text-body"
              placeholder="unset"
              value={assumptions.handlingRateUsdPerMt}
              onChange={(e) => setAssumptions((a) => ({ ...a, handlingRateUsdPerMt: e.target.value }))}
            />
          </label>
          <label className="flex flex-col gap-0.5">
            <span className="stat-label">Demurrage $/day</span>
            <Input
              className="h-6 text-body"
              placeholder="unset"
              value={assumptions.demurrageUsdPerDay}
              onChange={(e) => setAssumptions((a) => ({ ...a, demurrageUsdPerDay: e.target.value }))}
            />
          </label>
          <label className="flex flex-col gap-0.5">
            <span className="stat-label">Laytime allowance (d)</span>
            <Input
              className="h-6 text-body"
              placeholder="unset"
              value={assumptions.laytimeAllowanceDays}
              onChange={(e) => setAssumptions((a) => ({ ...a, laytimeAllowanceDays: e.target.value }))}
            />
          </label>
          <label className="flex flex-col gap-0.5">
            <span className="stat-label">Commodity</span>
            <select
              className="h-6 rounded border border-input bg-background px-1 text-body"
              value={assumptions.commodity}
              onChange={(e) => setAssumptions((a) => ({ ...a, commodity: e.target.value as Assumptions['commodity'] }))}
            >
              <option value="">unset</option>
              <option value="iron_ore">Iron ore</option>
              <option value="coal">Coal</option>
            </select>
          </label>
          {/* The "show ₹" checkbox that used to live here is gone. Currency is
              a desk-wide preference now (Settings → Currency & numbers), served
              by the same real FRED USD/INR observation this panel was already
              using -- so rupees apply to every figure on every screen instead of
              to one panel's total. A per-panel currency control would let two
              parts of the same page disagree about what currency they are in. */}
          <button
            type="button"
            onClick={recompute}
            disabled={loading || voyageDays == null || !hasAnyAssumption}
            className="h-6 self-end rounded-sm border border-market bg-market/10 px-2 text-caption font-semibold text-market disabled:opacity-40"
          >
            {loading ? 'Recomputing…' : 'Recompute'}
          </button>
        </div>
        {error && <div className="mt-1 text-caption text-risk">{error}</div>}
      </div>
    </Panel>
  )
}
