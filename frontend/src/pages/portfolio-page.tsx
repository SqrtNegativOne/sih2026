import { useMemo, useState } from 'react'
import { PageState, Panel } from '@/components/desk/panel'
import { StatRow } from '@/components/desk/stat'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Combobox, type ComboOption } from '@/components/ui/combobox'
import { Field } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { useElementSize } from '@/hooks/use-element-size'
import { fetchPortfolio } from '@/lib/api'
import { formatNumber, formatPct, prettyPort } from '@/lib/format'
import type { PortCode, PortfolioMixResult, PortfolioResponse, PortListing, VesselClass } from '@/lib/types'
import { cn } from '@/lib/utils'
import { useMoney } from '@/lib/money-context'

const VESSEL_CLASSES: VesselClass[] = ['Capesize', 'Panamax', 'Supramax', 'Handysize']


/** Spot/TC/COA share the same three-way palette everywhere on this page --
 * market (spot's own screen colour, since spot IS the open market), go
 * (period TC: locked in, no price risk left), wait (COA: the disclosed
 * documented middle ground, opt.portfolio's own term for it). */
const CHANNEL = {
  spot: {
    key: 'spot_fraction' as const,
    label: 'Spot',
    bar: 'bg-market',
    // The percentage sits ON the filled segment, so it needs the foreground
    // paired with that fill rather than a fixed white -- the dark theme's
    // semantics are light inks.
    on: 'text-market-fg',
    text: 'text-market',
  },
  tc: {
    key: 'tc_fraction' as const,
    label: 'Period TC',
    bar: 'bg-go',
    on: 'text-go-fg',
    text: 'text-go',
  },
  coa: {
    key: 'coa_fraction' as const,
    label: 'COA',
    bar: 'bg-wait',
    on: 'text-wait-fg',
    text: 'text-wait',
  },
}

function MixBar({ mix, className }: { mix: PortfolioMixResult; className?: string }) {
  return (
    <div className={cn('flex h-4 w-full overflow-hidden rounded-sm border border-border', className)}>
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
              <span className={cn('text-micro font-bold uppercase tracking-wide', c.on)}>
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
          <span key={k} className="flex items-center gap-1 text-caption text-muted-foreground">
            <span className={cn('h-2 w-2 rounded-xs', c.bar)} />
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
  const { moneyCompact } = useMoney()
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

  // A frontier is only a frontier when the mixes actually differ. When every
  // risk-aversion setting lands on the same expected cost and the same
  // variance -- which is the real answer whenever one channel dominates at
  // every k -- the axes have nothing to separate, and the chart drew all
  // eight points stacked in the bottom-left corner of an otherwise empty
  // 150px box. That reads as a broken chart rather than as the finding it is,
  // so say the finding instead. The table below still lists every row.
  const costSpread = xMax - xMin
  const riskSpread = Math.max(...ys) - Math.min(...ys)
  const isDegenerate = costSpread < Math.abs(xMax) * 1e-6 && riskSpread < 1
  if (isDegenerate) {
    // Naming the dominant channel and the lever that would change it turns a
    // dead chart into a finding. The cause is always the same shape: one
    // channel's risk-adjusted cost beats the others by more than any k can
    // close, and with this page's own defaults that channel is period TC,
    // because spot's stockout exposure is priced high enough to rule it out
    // before risk aversion is even considered.
    const r = recommended
    const dominant =
      r.tc_fraction >= 0.99 ? 'period TC' : r.spot_fraction >= 0.99 ? 'spot' : r.coa_fraction >= 0.99 ? 'COA' : null
    return (
      <div className="rounded-sm border border-dashed border-border bg-surface-2/60 px-4 py-4">
        <p className="text-body font-semibold text-foreground">
          {dominant ? `100% ${dominant} wins at every risk setting.` : 'Every mix on this frontier is identical.'}
        </p>
        <p className="mt-1 max-w-[76ch] text-caption leading-relaxed text-muted-foreground">
          All {frontier.length} risk-aversion settings return the same expected cost (
          {moneyCompact(xMax)}) and the same variance, so there is no cost-versus-risk curve to
          trace. That is a real finding, not a missing one: under these inputs one channel is
          cheaper <em>after</em> its risk penalty than any blend, so no amount of risk aversion
          changes the answer.
        </p>
        <p className="mt-2 max-w-[76ch] text-caption leading-relaxed text-muted-foreground">
          <span className="font-semibold text-foreground">To see a real trade-off,</span> move the
          two inputs that price spot exposure: raise the spot sourcing rate (how fast you can
          actually find tonnage) or lower the stockout cost. At a sourcing rate of 0.05/day a
          stockout takes about {formatNumber(1 / 0.05, 0)} days to resolve, which makes any spot
          share expensive before risk aversion is considered.
        </p>
      </div>
    )
  }

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
        <text x={CHART_PAD.l - 6} y={CHART_PAD.t + 4} textAnchor="end" className="fill-muted-foreground text-micro">
          risk
        </text>
        <text x={w - CHART_PAD.r} y={CHART_H - 4} textAnchor="end" className="fill-muted-foreground text-micro">
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
            <title>{`k=${m.risk_aversion_k}: ${moneyCompact(m.expected_cost_usd)}, std ${moneyCompact(m.cost_std_usd)}`}</title>
          </circle>
        ))}
        <circle cx={x(recommended.expected_cost_usd)} cy={y(recommended.cost_std_usd)} r={4.5} fill="var(--primary)" stroke="white" strokeWidth={1.5}>
          <title>{`Recommended (k=${recommended.risk_aversion_k}): ${moneyCompact(recommended.expected_cost_usd)}, std ${moneyCompact(recommended.cost_std_usd)}`}</title>
        </circle>
      </svg>
    </div>
  )
}

/**
 * What the recommended mix buys you against the obvious alternative: putting
 * every voyage on the spot market.
 *
 * Both figures were already on the page, in a different panel, as unlinked
 * rows — a reader had to subtract "Pure-spot cost" from "Expected cost" by
 * hand and then decide whether the difference was worth it. Stated as a
 * trade ("you pay X more to remove Y of swing") it is the single most useful
 * sentence this page can produce, and it stays useful even when the frontier
 * collapses to one point, which is exactly when the rest of the screen stops
 * saying anything.
 */
function SpotComparison({ result }: { result: PortfolioResponse }) {
  const { moneyCompact } = useMoney()
  const rec = result.recommended
  const premium = rec.expected_cost_usd - result.spot_cost_usd
  const varianceRemoved = result.spot_cost_std_usd - rec.cost_std_usd
  const cheaper = premium < 0

  // Only meaningful when the mix actually differs from pure spot.
  if (Math.abs(premium) < 1 && Math.abs(varianceRemoved) < 1) return null

  return (
    <div className="rounded-sm border border-border bg-surface-2 px-2 py-2">
      <div className="text-micro font-semibold uppercase tracking-wide text-muted-foreground">
        Versus buying every voyage on the spot market
      </div>
      <p className="mt-1 text-caption leading-relaxed text-foreground">
        {cheaper ? (
          <>
            This mix is{' '}
            <span className="font-semibold text-go">{moneyCompact(Math.abs(premium))}</span>{' '}
            cheaper in expectation
          </>
        ) : (
          <>
            You pay{' '}
            <span className="font-semibold text-wait">{moneyCompact(premium)}</span> more in
            expectation
          </>
        )}
        {varianceRemoved > 0 ? (
          <>
            {' '}
            and remove{' '}
            <span className="font-semibold text-go">
              {moneyCompact(varianceRemoved)}
            </span>{' '}
            of cost swing (one standard deviation).
          </>
        ) : (
          <> , with no reduction in cost swing.</>
        )}
      </p>
      {!cheaper && varianceRemoved > 0 && (
        <p className="mt-1 text-micro leading-relaxed text-muted-foreground">
          That is {formatNumber(premium / varianceRemoved, 2)} paid per dollar of swing removed —
          the price of certainty under your stated stockout cost and sourcing rate.
        </p>
      )}
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
  const { moneyCompact } = useMoney()
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

  // `contract_term_days` is the one field here the backend requires strictly
  // positive (`gt=0`); the other three numeric inputs accept 0 (`ge=0`), so
  // `Number(...) || 0` degrading a blank/invalid field to 0 is actually a
  // valid submission for them. It is not valid here -- an empty or negative
  // term silently became 0 and was submitted anyway, paying a round trip for
  // a 422 the client could catch instantly.
  const contractTermValid = Number.isFinite(Number(form.contractTermDays)) && Number(form.contractTermDays) > 0

  function run() {
    if (!contractTermValid) return
    setLoading(true)
    setError(null)
    fetchPortfolio({
      vesselClass: form.vesselClass,
      contractTermDays: Number(form.contractTermDays),
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
    <div className="flex h-full flex-col gap-2 overflow-hidden p-2" id="portfolio">
      <Panel
        title="Portfolio Mix"
        soWhat={'How much of the season\'s tonnage to lock on long-term contracts now versus leave to the spot market. Locking everything removes risk but gives up any fall in rates; locking nothing does the opposite.'}
        meta="spot / period TC / COA"
        hint="The PS's own stated objective: moving from multiple single spot contracts to short/medium term multiple-voyage coverage. Optimizes the mix of spot, period-TC, and COA (contract of affreightment) coverage against a real stockout-risk penalty."
      >
        <div className="flex flex-wrap items-end gap-2 p-1">
          <Field label="Vessel class">
            <select
              value={form.vesselClass}
              onChange={(e) => set('vesselClass', e.target.value as VesselClass)}
              className="h-7 w-28 cursor-pointer rounded-sm border border-input bg-surface px-2 text-body transition-colors hover:border-muted-foreground/60 focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/40"
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
              aria-invalid={!contractTermValid}
              className={cn(
                'h-7 w-24 text-body',
                !contractTermValid && 'border-risk focus:border-risk focus:ring-risk/40',
              )}
              value={form.contractTermDays}
              onChange={(e) => set('contractTermDays', e.target.value)}
            />
          </Field>
          <Field label="Plant burden cover (days)" hint="Real business input: how many days of buffer stock the plant is carrying.">
            <Input
              type="number"
              min={0}
              className="h-7 w-28 text-body"
              value={form.plantBurdenCoverDays}
              onChange={(e) => set('plantBurdenCoverDays', e.target.value)}
            />
          </Field>
          <Field label="Stockout cost (USD)" hint="Real business input: the cost of a production stockout.">
            <Input
              type="number"
              min={0}
              className="h-7 w-32 text-body"
              value={form.stockoutCostUsd}
              onChange={(e) => set('stockoutCostUsd', e.target.value)}
            />
          </Field>
          <Field label="Spot sourcing rate (/day)" hint="Real business input: how fast SAIL can source spot tonnage, as a daily Poisson rate.">
            <Input
              type="number"
              min={0}
              step={0.01}
              className="h-7 w-24 text-body"
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
          <Button
            variant="primary"
            size="md"
            onClick={run}
            disabled={loading || !contractTermValid}
            title={
              loading
                ? 'A real optimisation is running.'
                : !contractTermValid
                  ? 'Contract term needs to be a positive number of days.'
                  : 'Optimise the coverage mix'
            }
          >
            {loading ? 'Solving…' : result ? 'Re-run analysis' : 'Run analysis'}
          </Button>
          {!contractTermValid && (
            <span className="self-center text-caption text-risk">
              Contract term needs to be a positive number of days.
            </span>
          )}
        </div>

        <div className="border-t border-border px-2 pb-2 pt-1">
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
            <div className="flex max-w-md justify-between text-micro uppercase tracking-wide text-muted-foreground">
              <span>Cost-minimizing</span>
              <span>Risk-averse</span>
            </div>
          </Field>
        </div>
      </Panel>

      {error && (
        <PageState
          tone="error"
          title="The portfolio analysis failed"
          hint={error}
          action={
            <Button variant="danger" size="sm" onClick={run}>
              Try again
            </Button>
          }
        />
      )}

      {loading && !error && (
        <PageState
          tone="busy"
          title="Optimising the coverage mix…"
          hint="Solving the spot / period-TC / COA split across the full risk-aversion frontier. This is a real optimisation, not a cached result."
        />
      )}

      {!result && !loading && !error && (
        <PageState
          title="No mix computed yet"
          hint="Set the plant burden cover, stockout cost and spot sourcing rate above, then run the analysis to see the recommended spot / period-TC / COA coverage."
        />
      )}

      {result && (
        <div className="grid min-h-0 flex-1 auto-rows-min grid-cols-1 content-start gap-2 overflow-auto lg:grid-cols-2">
          <Panel
            title="Recommended Mix"
        soWhat={'The split the optimiser would choose given your appetite for risk. If it recommends more period cover than you can actually contract this quarter, take the next-best point on the frontier below.'}
            meta={`k = ${result.recommended.risk_aversion_k}`}
            hint="The coverage split the optimiser picks at your current risk-aversion setting, and what it costs relative to simply buying every voyage on the spot market."
            className="lg:col-span-1"
          >
            <div className="flex flex-col gap-2 p-1">
              <MixBar mix={result.recommended} />
              <MixLegend />

              {/* The comparison against pure spot is the information this page
                  exists to produce, and it was previously two unlinked rows in
                  a different panel that a reader had to subtract by hand. It
                  is the only part of the screen that stays informative when
                  the frontier itself is degenerate. */}
              <SpotComparison result={result} />

              <div className="mt-1 flex flex-col gap-1 border-t border-border pt-2">
                <StatRow label="Expected cost" value={moneyCompact(result.recommended.expected_cost_usd)} />
                <StatRow label="Cost std. dev." value={moneyCompact(result.recommended.cost_std_usd)} />
                <StatRow
                  label="Stockout probability"
                  value={formatPct(result.recommended.stockout_probability)}
                  tone={result.recommended.stockout_probability > 0.2 ? 'risk' : 'plain'}
                />
                <StatRow label="Stockout penalty (expected)" value={moneyCompact(result.recommended.stockout_penalty_usd)} />
              </div>
            </div>
          </Panel>

          <Panel title="Scenario Context"
        soWhat={'The demand and rate assumptions this mix was optimised against. If your own view of the season differs, change these and re-run — the recommendation is only as good as the scenario.'} className="lg:col-span-1">
            <div className="flex flex-col gap-1 p-1">
              <StatRow label="Vessel class" value={result.vessel_class} />
              <StatRow label="As of" value={result.as_of} />
              <StatRow label="Today's TC quote" value={`${moneyCompact(result.today_quote_usd_per_day)}/day`} />
              <StatRow label="Pure-spot cost (this term)" value={moneyCompact(result.spot_cost_usd)} />
              <StatRow label="Pure-spot cost std. dev." value={moneyCompact(result.spot_cost_std_usd)} />
              <div className="stat-row">
                <span className="stat-label">Rate basis</span>
                <Badge variant={result.route_basis_applied ? 'secondary' : 'outline'} className="text-micro">
                  {result.route_basis_applied ? 'Route-specific' : 'Class-only'}
                </Badge>
              </div>
            </div>
          </Panel>

          <Panel
            title="Efficient Frontier"
        soWhat={'Every sensible trade-off between expected cost and how badly a bad year could go. Points below the curve are simply worse on both counts; pick your point on the curve, not off it.'}
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
                        <td className="desk-num text-right">{moneyCompact(m.expected_cost_usd)}</td>
                        <td className="desk-num text-right text-muted-foreground">{moneyCompact(m.cost_std_usd)}</td>
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
