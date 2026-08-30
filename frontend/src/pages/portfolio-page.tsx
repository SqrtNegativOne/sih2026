import { useMemo, useState } from 'react'
import { Panel } from '@/components/desk/panel'
import { StatRow } from '@/components/desk/stat'
import { Badge } from '@/components/ui/badge'
import { Combobox, type ComboOption } from '@/components/ui/combobox'
import { Input } from '@/components/ui/input'
import { useElementSize } from '@/hooks/use-element-size'
import { fetchPortfolio } from '@/lib/api'
import { formatNumber, formatPct, formatUsdCompact, prettyPort } from '@/lib/format'
import type { PortCode, PortfolioMixResult, PortfolioResponse, PortListing, VesselClass } from '@/lib/types'
import { cn } from '@/lib/utils'

const VESSEL_CLASSES: VesselClass[] = ['Capesize', 'Panamax', 'Supramax', 'Handysize']

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="stat-label" title={hint}>
        {label}
      </span>
      {children}
    </div>
  )
}

/** Spot/TC/COA share the same three-way palette everywhere on this page --
 * market (spot's own screen colour, since spot IS the open market), go
 * (period TC: locked in, no price risk left), wait (COA: the disclosed
 * documented middle ground, opt.portfolio's own term for it). */
const CHANNEL = {
  spot: { key: 'spot_fraction' as const, label: 'Spot', bar: 'bg-market', text: 'text-market' },
  tc: { key: 'tc_fraction' as const, label: 'Period TC', bar: 'bg-go', text: 'text-go' },
  coa: { key: 'coa_fraction' as const, label: 'COA', bar: 'bg-wait', text: 'text-wait' },
}

function MixBar({ mix, className }: { mix: PortfolioMixResult; className?: string }) {
  return (
    <div className={cn('flex h-4 w-full overflow-hidden rounded-[2px] border border-border', className)}>
      {(['spot', 'tc', 'coa'] as const).map((k) => {
        const c = CHANNEL[k]
        const frac = mix[c.key]
        if (frac <= 0) return null
        return (
          <div
            key={k}
            className={cn(c.bar, 'flex items-center justify-center')}
            style={{ width: `${frac * 100}%` }}
            title={`${c.label} ${formatPct(frac)}`}
          >
            {frac >= 0.12 && (
              <span className="text-[8px] font-bold uppercase tracking-wide text-white/90">
                {formatPct(frac)}
              </span>
            )}
          </div>
        )
      })}
    </div>
  )
}

function MixLegend() {
  return (
    <div className="flex flex-wrap gap-2">
      {(['spot', 'tc', 'coa'] as const).map((k) => {
        const c = CHANNEL[k]
        return (
          <span key={k} className="flex items-center gap-1 text-[10px] text-muted-foreground">
            <span className={cn('h-2 w-2 rounded-[1px]', c.bar)} />
            {c.label}
          </span>
        )
      })}
    </div>
  )
}

const CHART_H = 150
const CHART_PAD = { t: 12, r: 14, b: 24, l: 44 }

function FrontierChart({
  frontier,
  recommended,
}: {
  frontier: PortfolioMixResult[]
  recommended: PortfolioMixResult
}) {
  const [box, ref] = useElementSize<HTMLDivElement>()
  const w = Math.max(box.width, 260)

  const all = [...frontier, recommended]
  const xs = all.map((m) => m.expected_cost_usd)
  const ys = all.map((m) => m.cost_std_usd)
  const xMin = Math.min(...xs)
  const xMax = Math.max(...xs)
  const yMin = 0
  const yMax = Math.max(...ys) || 1
  const xSpan = xMax - xMin || 1

  const x = (v: number) => CHART_PAD.l + ((v - xMin) / xSpan) * (w - CHART_PAD.l - CHART_PAD.r)
  const y = (v: number) =>
    CHART_PAD.t + (1 - (v - yMin) / (yMax - yMin || 1)) * (CHART_H - CHART_PAD.t - CHART_PAD.b)

  const sorted = [...frontier].sort((a, b) => a.expected_cost_usd - b.expected_cost_usd)
  const line = sorted
    .map((m, i) => `${i === 0 ? 'M' : 'L'}${x(m.expected_cost_usd).toFixed(1)} ${y(m.cost_std_usd).toFixed(1)}`)
    .join(' ')

  return (
    <div ref={ref} className="w-full">
      <svg width={w} height={CHART_H} className="block">
        <line
          x1={CHART_PAD.l}
          x2={w - CHART_PAD.r}
          y1={CHART_H - CHART_PAD.b}
          y2={CHART_H - CHART_PAD.b}
          stroke="var(--border)"
          strokeWidth={1}
        />
        <line
          x1={CHART_PAD.l}
          x2={CHART_PAD.l}
          y1={CHART_PAD.t}
          y2={CHART_H - CHART_PAD.b}
          stroke="var(--border)"
          strokeWidth={1}
        />
        <text x={CHART_PAD.l - 6} y={CHART_PAD.t + 4} textAnchor="end" className="fill-muted-foreground text-[8px]">
          risk
        </text>
        <text x={w - CHART_PAD.r} y={CHART_H - 4} textAnchor="end" className="fill-muted-foreground text-[8px]">
          expected cost →
        </text>

        <path d={line} fill="none" stroke="var(--market)" strokeWidth={1.5} opacity={0.6} />
        {sorted.map((m) => (
          <circle
            key={m.risk_aversion_k}
            cx={x(m.expected_cost_usd)}
            cy={y(m.cost_std_usd)}
            r={2.5}
            fill="var(--market)"
          >
            <title>{`k=${m.risk_aversion_k}: ${formatUsdCompact(m.expected_cost_usd)}, std ${formatUsdCompact(m.cost_std_usd)}`}</title>
          </circle>
        ))}
        <circle cx={x(recommended.expected_cost_usd)} cy={y(recommended.cost_std_usd)} r={4.5} fill="var(--primary)" stroke="white" strokeWidth={1.5}>
          <title>{`Recommended (k=${recommended.risk_aversion_k}): ${formatUsdCompact(recommended.expected_cost_usd)}, std ${formatUsdCompact(recommended.cost_std_usd)}`}</title>
        </circle>
      </svg>
    </div>
  )
}

interface FormState {
  vesselClass: VesselClass
  contractTermDays: string
  plantBurdenCoverDays: string
  stockoutCostUsd: string
  spotSourcingHazardRatePerDay: string
  riskAversionK: string
  originPort: string
  destPort: string
}

const DEFAULTS: FormState = {
  vesselClass: 'Panamax',
  contractTermDays: '180',
  plantBurdenCoverDays: '10',
  stockoutCostUsd: '250000',
  spotSourcingHazardRatePerDay: '0.05',
  riskAversionK: '1',
  originPort: '',
  destPort: '',
}

export function PortfolioPage({ ports }: { ports: PortListing[] }) {
  const options: ComboOption[] = useMemo(
    () => ports.map((p) => ({ value: p.code, label: prettyPort(p.name), hint: p.code })),
    [ports],
  )

  const [form, setForm] = useState<FormState>(DEFAULTS)
  const [result, setResult] = useState<PortfolioResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  function set<K extends keyof FormState>(key: K, value: FormState[K]) {
    setForm((f) => ({ ...f, [key]: value }))
  }

  function run() {
    setLoading(true)
    setError(null)
    fetchPortfolio({
      vesselClass: form.vesselClass,
      contractTermDays: Number(form.contractTermDays) || 0,
      plantBurdenCoverDays: Number(form.plantBurdenCoverDays) || 0,
      stockoutCostUsd: Number(form.stockoutCostUsd) || 0,
      spotSourcingHazardRatePerDay: Number(form.spotSourcingHazardRatePerDay) || 0,
      riskAversionK: Number(form.riskAversionK) || 0,
      originPort: form.originPort ? (form.originPort as PortCode) : undefined,
      destPort: form.destPort ? (form.destPort as PortCode) : undefined,
    })
      .then(setResult)
      .catch((err: unknown) => setError(err instanceof Error ? err.message : 'Portfolio analysis failed.'))
      .finally(() => setLoading(false))
  }

  return (
    <div className="flex h-full flex-col gap-1.5 overflow-hidden p-1.5" id="portfolio">
      <Panel
        title="Portfolio Mix"
        meta="spot / period TC / COA"
        hint="The PS's own stated objective: moving from multiple single spot contracts to short/medium term multiple-voyage coverage. Optimizes the mix of spot, period-TC, and COA (contract of affreightment) coverage against a real stockout-risk penalty."
      >
        <div className="flex flex-wrap items-end gap-2 p-1">
          <Field label="Vessel class">
            <select
              value={form.vesselClass}
              onChange={(e) => set('vesselClass', e.target.value as VesselClass)}
              className="h-7 w-28 rounded border border-input bg-surface px-1.5 text-[11px]"
            >
              {VESSEL_CLASSES.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Contract term (days)" hint="The coverage horizon this mix is optimized over.">
            <Input
              type="number"
              min={1}
              className="h-7 w-24 text-[11px]"
              value={form.contractTermDays}
              onChange={(e) => set('contractTermDays', e.target.value)}
            />
          </Field>
          <Field label="Plant burden cover (days)" hint="Real business input: how many days of buffer stock the plant is carrying.">
            <Input
              type="number"
              min={0}
              className="h-7 w-28 text-[11px]"
              value={form.plantBurdenCoverDays}
              onChange={(e) => set('plantBurdenCoverDays', e.target.value)}
            />
          </Field>
          <Field label="Stockout cost (USD)" hint="Real business input: the cost of a production stockout.">
            <Input
              type="number"
              min={0}
              className="h-7 w-32 text-[11px]"
              value={form.stockoutCostUsd}
              onChange={(e) => set('stockoutCostUsd', e.target.value)}
            />
          </Field>
          <Field label="Spot sourcing rate (/day)" hint="Real business input: how fast SAIL can source spot tonnage, as a daily Poisson rate.">
            <Input
              type="number"
              min={0}
              step={0.01}
              className="h-7 w-24 text-[11px]"
              value={form.spotSourcingHazardRatePerDay}
              onChange={(e) => set('spotSourcingHazardRatePerDay', e.target.value)}
            />
          </Field>
          <Field label="Origin (optional)" hint="If given with destination, uses real route-specific rate basis instead of the class benchmark alone.">
            <div className="w-40">
              <Combobox value={form.originPort} onChange={(v) => set('originPort', v)} options={options} placeholder="Class-only" />
            </div>
          </Field>
          <Field label="Destination (optional)">
            <div className="w-40">
              <Combobox value={form.destPort} onChange={(v) => set('destPort', v)} options={options} placeholder="Class-only" />
            </div>
          </Field>
          <button
            type="button"
            onClick={run}
            disabled={loading}
            className="h-7 rounded-[3px] border border-market bg-market/10 px-3 text-[11px] font-semibold text-market disabled:opacity-40"
          >
            {loading ? 'Solving…' : 'Run analysis'}
          </button>
        </div>

        <div className="border-t border-border px-2 pb-1.5 pt-1">
          <Field label={`Risk aversion — k = ${form.riskAversionK}`} hint="How many real spot-cost standard deviations you're willing to pay to avoid, for the recommended mix. 0 = minimize expected cost only.">
            <input
              type="range"
              min={0}
              max={8}
              step={0.1}
              value={form.riskAversionK}
              onChange={(e) => set('riskAversionK', e.target.value)}
              className="h-1 w-full max-w-md cursor-pointer appearance-none rounded bg-muted accent-primary"
            />
            <div className="flex max-w-md justify-between text-[9px] uppercase tracking-wide text-muted-foreground">
              <span>Cost-minimizing</span>
              <span>Risk-averse</span>
            </div>
          </Field>
        </div>
      </Panel>

      {error && <div className="p-2 text-sm text-risk">{error}</div>}

      {!result && !loading && (
        <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
          Set the three real business inputs above, then run the analysis to see the recommended spot/period/COA
          coverage mix.
        </div>
      )}

      {result && (
        <div className="grid min-h-0 flex-1 grid-cols-1 gap-1.5 overflow-auto lg:grid-cols-2">
          <Panel title="Recommended Mix" meta={`k = ${result.recommended.risk_aversion_k}`} className="lg:col-span-1">
            <div className="flex flex-col gap-2 p-1">
              <MixBar mix={result.recommended} />
              <MixLegend />
              <div className="mt-1 flex flex-col gap-1">
                <StatRow label="Expected cost" value={formatUsdCompact(result.recommended.expected_cost_usd)} />
                <StatRow label="Cost std. dev." value={formatUsdCompact(result.recommended.cost_std_usd)} />
                <StatRow
                  label="Stockout probability"
                  value={formatPct(result.recommended.stockout_probability)}
                  tone={result.recommended.stockout_probability > 0.2 ? 'risk' : 'plain'}
                />
                <StatRow label="Stockout penalty (expected)" value={formatUsdCompact(result.recommended.stockout_penalty_usd)} />
              </div>
            </div>
          </Panel>

          <Panel title="Scenario Context" className="lg:col-span-1">
            <div className="flex flex-col gap-1 p-1">
              <StatRow label="Vessel class" value={result.vessel_class} />
              <StatRow label="As of" value={result.as_of} />
              <StatRow label="Today's TC quote" value={`${formatUsdCompact(result.today_quote_usd_per_day)}/day`} />
              <StatRow label="Pure-spot cost (this term)" value={formatUsdCompact(result.spot_cost_usd)} />
              <StatRow label="Pure-spot cost std. dev." value={formatUsdCompact(result.spot_cost_std_usd)} />
              <div className="stat-row">
                <span className="stat-label">Rate basis</span>
                <Badge variant={result.route_basis_applied ? 'secondary' : 'outline'} className="text-[9px]">
                  {result.route_basis_applied ? 'Route-specific' : 'Class-only'}
                </Badge>
              </div>
            </div>
          </Panel>

          <Panel
            title="Efficient Frontier"
            meta="cost vs. risk"
            hint="Each point is the optimal mix at a different risk_aversion_k -- moving right-to-left trades expected cost for lower variance. The filled dot is your current k setting above."
            className="lg:col-span-2"
          >
            <div className="flex flex-col gap-2 p-1">
              <FrontierChart frontier={result.frontier} recommended={result.recommended} />
              <div className="overflow-x-auto">
                <table className="desk-table">
                  <thead>
                    <tr>
                      <th className="text-right">k</th>
                      <th>Mix</th>
                      <th className="text-right">Spot</th>
                      <th className="text-right">TC</th>
                      <th className="text-right">COA</th>
                      <th className="text-right">Cost</th>
                      <th className="text-right">Std dev</th>
                      <th className="text-right">Stockout %</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.frontier.map((m) => (
                      <tr
                        key={m.risk_aversion_k}
                        className={cn(m.risk_aversion_k === result.recommended.risk_aversion_k && 'bg-accent')}
                      >
                        <td className="desk-num text-right">{m.risk_aversion_k}</td>
                        <td className="w-32">
                          <MixBar mix={m} className="h-2.5" />
                        </td>
                        <td className="desk-num text-right text-market">{formatPct(m.spot_fraction)}</td>
                        <td className="desk-num text-right text-go">{formatPct(m.tc_fraction)}</td>
                        <td className="desk-num text-right text-wait">{formatPct(m.coa_fraction)}</td>
                        <td className="desk-num text-right">{formatUsdCompact(m.expected_cost_usd)}</td>
                        <td className="desk-num text-right text-muted-foreground">{formatUsdCompact(m.cost_std_usd)}</td>
                        <td className="desk-num text-right text-muted-foreground">{formatNumber(m.stockout_probability * 100, 1)}%</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </Panel>
        </div>
      )}
    </div>
  )
}
