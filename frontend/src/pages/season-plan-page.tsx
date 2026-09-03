import { Plus, Trash2 } from 'lucide-react'
import { motion, useReducedMotion } from 'motion/react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { PageState, Panel } from '@/components/desk/panel'
import { Term } from '@/components/desk/term'
import { Button } from '@/components/ui/button'
import { Combobox, type ComboOption } from '@/components/ui/combobox'
import { Field } from '@/components/ui/field'
import { Tooltip } from '@/components/ui/tooltip'
import { fetchSeasonPlan } from '@/lib/api'
import { addDays, formatNumber, formatShortDate, prettyPort } from '@/lib/format'
import { useMoney } from '@/lib/money-context'
import { transition } from '@/lib/motion'
import type {
  PortCode,
  PortListing,
  SeasonParcelInput,
  SeasonPeriodCover,
  SeasonPlanResponse,
  VesselInput,
} from '@/lib/types'
import { cn } from '@/lib/utils'

/**
 * The season plan: a book of cargo lots scheduled across a fleet in one solve.
 *
 * This is the screen the problem statement actually asks for. Its wording is
 * "given cargo parcels, an origin-destination pair and a desired contract
 * duration, return *multiple* voyages" -- the point of moving off single spot
 * fixtures is covering a season with period tonnage, and a desk that prices
 * one voyage at a time cannot show that.
 *
 * `opt.voyage.schedule_voyages` has always solved it (F-87): a CP-SAT
 * pickup-and-delivery model over many parcels and many vessels. Every figure
 * here is that solver's own output. Nothing on this page is a per-lot quote
 * stitched together, because scheduling lots jointly is a different problem --
 * one vessel cannot serve two overlapping laycans, and only a joint solve sees
 * that.
 */

const inputCls =
  'h-7 w-full rounded-sm border border-input bg-surface px-2 text-body text-foreground ' +
  'transition-colors hover:border-muted-foreground/60 focus:border-primary focus:outline-none ' +
  'focus:ring-2 focus:ring-primary/40'

interface ParcelDraft extends Omit<SeasonParcelInput, 'origin_port' | 'dest_port'> {
  key: string
  origin_port: string
  dest_port: string
}

let parcelSeq = 0
let vesselSeq = 0

function newParcel(anchor: string, n: number): ParcelDraft {
  parcelSeq += 1
  // Each lot starts a fortnight after the last, so a fresh book is a plausible
  // sequence rather than N lots competing for the same week -- the user edits
  // every field anyway, and identical laycans would make the first solve
  // trivially infeasible for no instructive reason.
  const lead = 14 + (n - 1) * 21
  return {
    key: `p-${parcelSeq}`,
    parcel_id: `LOT-${String(n).padStart(2, '0')}`,
    origin_port: '',
    dest_port: '',
    commodity: 'Thermal Coal',
    volume_dwt: 55000,
    laycan_start: addDays(anchor, lead).toISOString().slice(0, 10),
    laycan_end: addDays(anchor, lead + 7).toISOString().slice(0, 10),
    revenue_usd: 2000000,
  }
}

interface VesselDraft {
  key: string
  vessel_id: string
  current_port: string
  available_from: string
}

function newVessel(anchor: string, n: number): VesselDraft {
  vesselSeq += 1
  return {
    key: `v-${vesselSeq}`,
    vessel_id: `SAIL_${n}`,
    current_port: '',
    available_from: anchor,
  }
}

/** Fixed Supramax spec for the fleet rows. Shown read-only on the page rather
 *  than hidden: these are real vessel figures the scheduler uses for speed,
 *  fuel and port feasibility, and a plan built on unseen assumptions is not
 *  auditable. Editing them per-vessel belongs with the quote form's full
 *  vessel editor, not here. */
const FLEET_SPEC = {
  vessel_class: 'Supramax' as const,
  dwt: 58000,
  draft_m: 12.8,
  loa_m: 190,
  beam_m: 32.2,
  speed_kn: 13,
  laden_fuel_consumption_tpd: 30,
  ballast_fuel_consumption_tpd: 26,
}

export function SeasonPlanPage({
  ports,
  latestDate,
}: {
  ports: PortListing[]
  latestDate: string | null
}) {
  const { money, moneyCompact } = useMoney()
  const reduced = useReducedMotion()

  // F-02, again. `latestDate` is the real last day of market data and it
  // arrives from the parent's own /meta fetch, i.e. AFTER this component's
  // first render. Seeding `useState` from `latestDate ?? today` therefore
  // loses a race the user cannot see: mount before /meta answers and the
  // entire cargo book is dated off the wall clock instead of off the data,
  // and it never corrects itself. Measured live, the headless run lost that
  // race every time -- laycans seeded from 2026-09-02 (today) rather than
  // 2026-08-20 (the data), and `as_of` went to the solver as a date the
  // dataset does not reach.
  //
  // The quote drawer hit this first and fixed it by resyncing only the
  // fields the user had not touched. Here it is simpler: nothing is worth
  // preserving until the user starts editing, so one flag covers it.
  // A lazy `useState` initialiser rather than `useRef(...).current`: both give
  // a value fixed for the component's life, but reading a ref during render is
  // the pattern that hides stale-value bugs, and the linter is right to flag
  // it. This one is genuinely a per-mount constant, which is what useState's
  // initialiser is for.
  const [fallback] = useState(() => latestDate ?? new Date().toISOString().slice(0, 10))
  const anchor = latestDate ?? fallback

  const [parcels, setParcels] = useState<ParcelDraft[]>(() => [
    newParcel(anchor, 1),
    newParcel(anchor, 2),
  ])
  const [vessels, setVessels] = useState<VesselDraft[]>(() => [newVessel(anchor, 1)])
  const [plan, setPlan] = useState<SeasonPlanResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Set the moment the user changes anything at all. Re-seeding after that
  // would throw away their work, which is worse than a stale default date.
  const touched = useRef(false)
  const reseeded = useRef(false)
  useEffect(() => {
    if (reseeded.current || touched.current || !latestDate || latestDate === fallback) return
    reseeded.current = true
    setParcels([newParcel(latestDate, 1), newParcel(latestDate, 2)])
    setVessels([newVessel(latestDate, 1)])
  }, [latestDate, fallback])

  const patchParcel = (key: string, p: Partial<ParcelDraft>) => {
    touched.current = true
    patch(setParcels, key, p)
  }
  const patchVessel = (key: string, p: Partial<VesselDraft>) => {
    touched.current = true
    patch(setVessels, key, p)
  }
  const addParcel = () => {
    touched.current = true
    setParcels((ps) => [...ps, newParcel(anchor, ps.length + 1)])
  }
  const addVessel = () => {
    touched.current = true
    setVessels((vs) => [...vs, newVessel(anchor, vs.length + 1)])
  }
  const removeParcel = (key: string) => {
    touched.current = true
    setParcels((ps) => ps.filter((x) => x.key !== key))
  }
  const removeVessel = (key: string) => {
    touched.current = true
    setVessels((vs) => vs.filter((x) => x.key !== key))
  }

  const options = useMemo<ComboOption[]>(
    () => ports.map((p) => ({ value: p.code, label: prettyPort(p.name), hint: p.code })),
    [ports],
  )
  const portName = (code: string) =>
    prettyPort(ports.find((p) => p.code === code)?.name ?? code)

  // A lot with non-positive tonnage isn't a business scenario the solver
  // needs to reject for us -- it's the frontend accepting something no real
  // cargo lot could be and then paying a round trip to find out. `volume_dwt`
  // has no `required`/`min`-driven native validation the way the quote
  // drawer's numeric fields do (this table isn't wrapped in a <form>), so the
  // check has to live here.
  const parcelsValid = parcels.every((p) => p.volume_dwt > 0)
  const ready =
    parcels.length > 0 &&
    vessels.length > 0 &&
    parcels.every((p) => p.origin_port && p.dest_port && p.origin_port !== p.dest_port) &&
    parcelsValid &&
    vessels.every((v) => v.current_port && v.vessel_id.trim())

  function run() {
    setLoading(true)
    setError(null)
    fetchSeasonPlan({
      as_of: anchor,
      contract_term_days: 90,
      parcels: parcels.map(({ key: _key, ...p }) => ({
        ...p,
        origin_port: p.origin_port as PortCode,
        dest_port: p.dest_port as PortCode,
      })),
      vessels: vessels.map<VesselInput>((v) => ({
        vessel_id: v.vessel_id,
        current_port: v.current_port as PortCode,
        available_from: v.available_from,
        ...FLEET_SPEC,
      })),
    })
      .then(setPlan)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : 'The season plan failed.'))
      .finally(() => setLoading(false))
  }

  return (
    <div className="grid h-full auto-rows-min content-start gap-2 overflow-auto" id="season-plan">
      <Panel
        title="Season Plan"
        soWhat={'The whole season\'s cargo book scheduled across a fleet in one solve, rather than one voyage at a time. This is where a decision that looks fine alone shows up as a clash with the rest of the programme.'}
        meta={
          // The pricing date is stated, not implied. It is the day whose real
          // market data the whole plan is costed against, and a plan is not
          // auditable if the reader cannot see which day that was.
          `${parcels.length} lot${parcels.length === 1 ? '' : 's'} · ${vessels.length} vessel${vessels.length === 1 ? '' : 's'} · priced from ${anchor}${latestDate ? '' : ' (data date unavailable)'}`
        }
        hint="Schedules a whole book of cargo lots across your fleet in one solve, rather than pricing them one at a time. One vessel cannot serve two overlapping laycans, and only a joint solve can see that -- so a lot rejected here is a real constraint finding, not a per-lot failure."
        actions={
          <Button
            variant="primary"
            size="sm"
            onClick={run}
            disabled={!ready || loading}
            title={
              loading
                ? 'Solving…'
                : !parcelsValid
                  ? 'Every lot needs a positive tonnage.'
                  : !ready
                    ? 'Every lot needs a load port and a discharge port (and they must differ), and every vessel needs an ID and a current port.'
                    : 'Build the plan'
            }
          >
            {loading ? 'Solving…' : plan ? 'Re-solve' : 'Build the plan'}
          </Button>
        }
      >
        <div className="space-y-3 p-1">
          {/* Why the button is dim, said in the open. A disabled control whose
              only explanation lives in a native `title` is unreachable by
              keyboard and, on most platforms, suppressed entirely on a
              disabled element -- so the user sees a dead button and no reason. */}
          {!ready && (
            <p className="panel-note">
              Every lot needs a load port and a different discharge port, and every vessel needs
              the port it is currently at, before the book can be scheduled.
            </p>
          )}
          {/* Cargo book */}
          <div>
            <div className="mb-1 flex items-center justify-between">
              <span className="stat-label">Cargo book</span>
              <Button
                size="xs"
                onClick={addParcel}
              >
                <Plus className="h-3 w-3" aria-hidden="true" />
                Add lot
              </Button>
            </div>
            <div className="overflow-x-auto">
              <table className="desk-table">
                <thead>
                  <tr>
                    <th>Lot</th>
                    <th>Load port</th>
                    <th>Discharge port</th>
                    <th className="text-right">Tonnes</th>
                    <th><Term term="laycan">Laycan</Term> opens</th>
                    <th>Laycan closes</th>
                    <th className="text-right">
                      <Tooltip content="What this lot is worth to you. Left at zero, the scheduler will correctly never assign a vessel to it — no revenue means no profit to gain, so it comes back unassigned by construction rather than by any port or laycan constraint.">
                        <span className="border-b border-dotted border-muted-foreground/50">
                          Revenue
                        </span>
                      </Tooltip>
                    </th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {parcels.map((p) => (
                    <tr key={p.key}>
                      <td>
                        <input
                          value={p.parcel_id}
                          onChange={(e) => patchParcel(p.key, { parcel_id: e.target.value })}
                          aria-label={`Reference for lot ${p.parcel_id}`}
                          className={cn(inputCls, 'w-24 font-mono')}
                        />
                      </td>
                      <td className="min-w-40">
                        <Combobox
                          value={p.origin_port}
                          onChange={(v) => patchParcel(p.key, { origin_port: v })}
                          options={options}
                          placeholder="Select…"
                          label={`Load port for ${p.parcel_id}`}
                        />
                      </td>
                      <td className="min-w-40">
                        <Combobox
                          value={p.dest_port}
                          onChange={(v) => patchParcel(p.key, { dest_port: v })}
                          options={options}
                          placeholder="Select…"
                          label={`Discharge port for ${p.parcel_id}`}
                        />
                      </td>
                      <td>
                        <input
                          type="number"
                          min={1}
                          value={p.volume_dwt}
                          onChange={(e) =>
                            patchParcel(p.key, { volume_dwt: Number(e.target.value) || 0 })
                          }
                          aria-label={`Tonnes for ${p.parcel_id}`}
                          aria-invalid={p.volume_dwt <= 0}
                          title={p.volume_dwt <= 0 ? 'Needs a positive tonnage.' : undefined}
                          className={cn(
                            inputCls,
                            'w-24 text-right font-mono',
                            p.volume_dwt <= 0 && 'border-risk focus:border-risk focus:ring-risk/40',
                          )}
                        />
                      </td>
                      <td>
                        <input
                          type="date"
                          value={p.laycan_start}
                          onChange={(e) => patchParcel(p.key, { laycan_start: e.target.value })}
                          aria-label={`Laycan opens for ${p.parcel_id}`}
                          className={cn(inputCls, 'w-32 font-mono')}
                        />
                      </td>
                      <td>
                        <input
                          type="date"
                          value={p.laycan_end}
                          min={p.laycan_start}
                          onChange={(e) => patchParcel(p.key, { laycan_end: e.target.value })}
                          aria-label={`Laycan closes for ${p.parcel_id}`}
                          className={cn(inputCls, 'w-32 font-mono')}
                        />
                      </td>
                      <td>
                        <input
                          type="number"
                          value={p.revenue_usd}
                          onChange={(e) =>
                            patchParcel(p.key, { revenue_usd: Number(e.target.value) || 0 })
                          }
                          aria-label={`Revenue for ${p.parcel_id}`}
                          className={cn(inputCls, 'w-28 text-right font-mono')}
                        />
                      </td>
                      <td>
                        <Button
                          variant="danger"
                          size="icon"
                          aria-label={`Remove ${p.parcel_id}`}
                          disabled={parcels.length === 1}
                          onClick={() => removeParcel(p.key)}
                        >
                          <Trash2 className="h-3 w-3" aria-hidden="true" />
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {/* Visible, not a hover title -- the whole point of catching this
                before the round trip is that the reader sees it without
                having to find a tooltip first. */}
            {!parcelsValid && (
              <p className="mt-1 text-body text-risk">
                Every lot needs a positive tonnage — the highlighted field(s) above.
              </p>
            )}
          </div>

          {/* Fleet */}
          <div className="border-t border-border pt-2">
            <div className="mb-1 flex items-center justify-between">
              <span className="stat-label">
                Fleet — name, current port, date open · {FLEET_SPEC.vessel_class},{' '}
                {formatNumber(FLEET_SPEC.dwt)} dwt, {FLEET_SPEC.draft_m} m draft,{' '}
                {FLEET_SPEC.speed_kn} kn
              </span>
              <Button
                size="xs"
                onClick={addVessel}
              >
                <Plus className="h-3 w-3" aria-hidden="true" />
                Add vessel
              </Button>
            </div>
            <div className="flex flex-wrap gap-2">
              {vessels.map((v) => (
                <div
                  key={v.key}
                  className="flex items-center gap-1 rounded-sm border border-border bg-surface-2 p-1"
                >
                  <input
                    value={v.vessel_id}
                    onChange={(e) => patchVessel(v.key, { vessel_id: e.target.value })}
                    aria-label={`Name of vessel ${v.vessel_id}`}
                    className={cn(inputCls, 'w-24 font-mono')}
                  />
                  <div className="w-40">
                    <Combobox
                      value={v.current_port}
                      onChange={(val) => patchVessel(v.key, { current_port: val })}
                      options={options}
                      placeholder="Currently at…"
                      label={`Port ${v.vessel_id} is currently at`}
                    />
                  </div>
                  {/* A real fleet does not open all at once, and the whole
                      shape of the plan turns on when each ship comes free --
                      a vessel that opens after a laycan closes simply cannot
                      take that lot. Fixing this at one date would have made
                      every plan a special case of the easy problem. */}
                  <input
                    type="date"
                    value={v.available_from}
                    onChange={(e) => patchVessel(v.key, { available_from: e.target.value })}
                    aria-label={`Date ${v.vessel_id} comes open`}
                    className={cn(inputCls, 'w-32 font-mono')}
                  />
                  <Button
                    variant="danger"
                    size="icon"
                    aria-label={`Remove ${v.vessel_id}`}
                    disabled={vessels.length === 1}
                    onClick={() => removeVessel(v.key)}
                  >
                    <Trash2 className="h-3 w-3" aria-hidden="true" />
                  </Button>
                </div>
              ))}
            </div>
          </div>
        </div>
      </Panel>

      {error && <PageState tone="error" title="The season plan failed" hint={error} />}

      {loading && !error && (
        <PageState
          tone="busy"
          title="Scheduling the book…"
          hint="A real CP-SAT solve over every lot and every vessel at once, maximising fleet profit net of fuel, idle time and demurrage."
        />
      )}

      {!plan && !loading && !error && (
        <PageState
          title="No plan built yet"
          hint="Give each lot a load and discharge port and each vessel a current port, then build the plan. Lots are scheduled together, not priced one at a time."
        />
      )}

      {plan && !loading && (
        <SeasonResult
          plan={plan}
          parcels={parcels}
          // The scheduler measures every hour from the EARLIEST vessel
          // availability, not from today, so the chart's axis has to start
          // there too. Labelling it with `anchor` was only ever right because
          // every vessel defaulted to the anchor date; now that a vessel can
          // open on its own date, using the anchor would silently mis-date
          // the whole plan by the offset between them.
          epoch={vessels.reduce(
            (min, v) => (v.available_from && v.available_from < min ? v.available_from : min),
            vessels[0]?.available_from || anchor,
          )}
          portName={portName}
          money={money}
          moneyCompact={moneyCompact}
          reduced={!!reduced}
        />
      )}
    </div>
  )
}

function patch<T extends { key: string }>(
  setter: React.Dispatch<React.SetStateAction<T[]>>,
  key: string,
  p: Partial<T>,
) {
  setter((xs) => xs.map((x) => (x.key === key ? { ...x, ...p } : x)))
}

function SeasonResult({
  plan,
  parcels,
  epoch,
  portName,
  money,
  moneyCompact,
  reduced,
}: {
  plan: SeasonPlanResponse
  parcels: ParcelDraft[]
  epoch: string
  portName: (c: string) => string
  money: (usd: number) => string
  moneyCompact: (usd: number) => string
  reduced: boolean
}) {
  const [hover, setHover] = useState<string | null>(null)
  const [pinned, setPinned] = useState<string | null>(null)
  // A pin outlives the pointer; a hover only shows while it lasts. Hover wins
  // while it is live so moving across the chart still previews each bar
  // without having to unpin first.
  const shownId = hover ?? pinned
  const shown = plan.assignments.find((a) => a.parcel_id === shownId) ?? null

  const byParcel = new Map(parcels.map((p) => [p.parcel_id, p]))
  const vesselIds = [...new Set(plan.assignments.map((a) => a.vessel_id))].sort()

  // The solver reports hours from its own epoch (the earliest vessel
  // availability), so the axis is hours-since-then throughout -- no date is
  // invented for a leg the solver did not date.
  const horizon = Math.max(1, ...plan.assignments.map((a) => a.finish_hours))
  const pct = (h: number) => (h / horizon) * 100

  // Dated gridlines. Two end labels on a 51-day axis tell you a voyage happens
  // somewhere in the middle third of a quarter, which is not a date -- and
  // "when does this ship actually sail" is the question the chart exists to
  // answer. Step is chosen so the axis carries roughly six to nine ticks
  // whatever the span, since a fortnightly grid on a 200-day plan is unreadable
  // and a monthly one on a 20-day plan has no ticks at all.
  const spanDays = horizon / 24
  const stepDays = spanDays <= 21 ? 3 : spanDays <= 60 ? 7 : spanDays <= 180 ? 14 : 30
  const ticks: { at: number; label: string }[] = []
  for (let d = 0; d <= spanDays; d += stepDays) {
    ticks.push({ at: (d / spanDays) * 100, label: formatShortDate(addDays(epoch, Math.round(d))) })
  }

  const ladenHours = plan.assignments.reduce(
    (s, a) => s + (a.finish_hours - a.start_operation_hours),
    0,
  )
  const waitHours = plan.assignments.reduce((s, a) => s + a.wait_hours, 0)
  const ballastHours = plan.assignments.reduce((s, a) => s + a.ballast_hours, 0)
  const fleetHours = vesselIds.length * horizon
  const utilisation = fleetHours > 0 ? ladenHours / fleetHours : 0

  return (
    <>
      <Panel
        title="The Plan"
        soWhat={'The chosen schedule and what it costs. If a lot lands at the wrong time for the plant, move that lot\'s window and re-solve rather than editing the answer by hand.'}
        meta={`${plan.solver_status} · ${plan.n_assigned} of ${plan.n_parcels} lots covered`}
        hint="Each bar is one vessel's voyage on the shared time axis, in hours from the earliest vessel availability. Bars on the same row are the same ship: seeing them end to end is what a period charter would be covering."
      >
        <div className="space-y-3 p-1">
          <div className="grid grid-cols-2 gap-x-6 md:grid-cols-4">
            <Stat label="Fleet profit" value={moneyCompact(plan.total_profit_usd)} tone="go" />
            <Stat label="Lots covered" value={`${plan.n_assigned} of ${plan.n_parcels}`} />
            <Stat label="Plan span" value={`${Math.round(horizon / 24)} days`} />
            <Stat
              label="Laden utilisation"
              value={`${Math.round(utilisation * 100)}%`}
              explain="Laden hours divided by total fleet hours across the plan span. The rest is waiting, ballasting or idle — it is the number a period charter has to earn back."
            />
          </div>

          {/* The Gantt. One row per vessel; this is the multi-voyage view the
              single-quote desk structurally cannot show.

              Detail is surfaced on hover and pinned on click, into the line
              below the chart, rather than through a native `title` on each
              bar: a bar can be a couple of pixels wide, and a tooltip that
              needs a one-second hover on a 3px target is not readable. The
              same hover-then-pin treatment the voyage timeline uses. */}
          <div className="space-y-1 border-t border-border pt-2">
            {vesselIds.map((vid) => {
              const legs = plan.assignments
                .filter((a) => a.vessel_id === vid)
                .sort((a, b) => a.start_operation_hours - b.start_operation_hours)
              return (
                <div key={vid} className="flex items-center gap-2">
                  <span className="w-20 shrink-0 truncate font-mono text-caption text-foreground">
                    {vid}
                  </span>
                  <div className="relative h-6 flex-1 rounded-sm bg-surface-2">
                    {/* Gridlines first, so every bar paints over them. */}
                    {ticks.map((t) => (
                      <div
                        key={t.at}
                        aria-hidden="true"
                        // bg-border, not bg-hairline: `--hairline` is a raw
                        // custom property and was never registered as a
                        // Tailwind colour, so `bg-hairline` compiles to
                        // nothing and the gridlines would simply not exist.
                        className="absolute inset-y-0 w-px bg-border"
                        style={{ left: `${t.at}%` }}
                      />
                    ))}
                    {/* Waiting before each voyage starts, drawn next so the
                        laden bar paints over it. */}
                    {legs.map((a) => (
                      <div
                        key={`w-${a.parcel_id}`}
                        aria-hidden="true"
                        className="absolute inset-y-0 rounded-sm bg-wait/35"
                        style={{
                          left: `${pct(a.arrival_hours)}%`,
                          width: `${Math.max(pct(a.wait_hours), 0)}%`,
                        }}
                      />
                    ))}
                    {legs.map((a, i) => {
                      const isShown = shown?.parcel_id === a.parcel_id
                      return (
                        <motion.button
                          key={a.parcel_id}
                          type="button"
                          onMouseEnter={() => setHover(a.parcel_id)}
                          onMouseLeave={() => setHover(null)}
                          onFocus={() => setHover(a.parcel_id)}
                          onBlur={() => setHover(null)}
                          onClick={() => setPinned((p) => (p === a.parcel_id ? null : a.parcel_id))}
                          aria-pressed={pinned === a.parcel_id}
                          aria-label={`${a.parcel_id} on ${vid}, ${Math.round((a.finish_hours - a.start_operation_hours) / 24)} days laden to ${portName(a.dest_port)}, ${money(a.profit_usd)} profit`}
                          className={cn(
                            'absolute inset-y-0 flex cursor-pointer items-center overflow-hidden rounded-sm bg-primary px-1.5',
                            'focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-ring',
                            isShown ? 'ring-2 ring-foreground/50' : 'hover:brightness-110',
                          )}
                          style={{
                            left: `${pct(a.start_operation_hours)}%`,
                            width: `${Math.max(pct(a.finish_hours - a.start_operation_hours), 1.5)}%`,
                          }}
                          initial={reduced ? false : { scaleX: 0, originX: 0 }}
                          animate={{ scaleX: 1 }}
                          transition={
                            reduced ? { duration: 0 } : { ...transition.base, delay: 0.06 * i }
                          }
                        >
                          <span className="truncate text-micro font-bold text-primary-foreground">
                            {a.parcel_id}
                          </span>
                        </motion.button>
                      )
                    })}
                  </div>
                </div>
              )
            })}
            {/* The dated axis, aligned to the same track the bars sit on: the
                w-20 vessel-name column plus the gap-2 between it and the
                track. Each label is centred on its own tick rather than
                left-aligned, so a tick and its date read as one mark. */}
            <div className="flex items-center gap-2">
              <span className="w-20 shrink-0" aria-hidden="true" />
              <div className="relative h-4 flex-1">
                {ticks.map((t) => (
                  <span
                    key={t.at}
                    className="absolute top-0 -translate-x-1/2 whitespace-nowrap text-micro text-muted-foreground"
                    style={{ left: `${t.at}%` }}
                  >
                    {t.label}
                  </span>
                ))}
              </div>
            </div>
          </div>

          {/* The detail line. Reserves its own height whether or not anything
              is selected, so hovering a bar never reflows the chart above it. */}
          <div
            className="min-h-9 rounded-sm border border-border bg-surface-2 px-2 py-1.5 text-caption"
            aria-live="polite"
          >
            {shown ? (
              <div className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5">
                <span className="font-mono font-semibold text-foreground">{shown.parcel_id}</span>
                <span className="text-muted-foreground">
                  {shown.vessel_id} → {portName(shown.dest_port)}
                </span>
                <span className="text-muted-foreground">
                  {((shown.finish_hours - shown.start_operation_hours) / 24).toFixed(1)}d laden
                </span>
                <span className="text-muted-foreground">
                  {(shown.wait_hours / 24).toFixed(1)}d waiting
                </span>
                <span className="text-muted-foreground">
                  {(shown.ballast_hours / 24).toFixed(1)}d ballast in
                </span>
                <span className="desk-num font-semibold text-go">{money(shown.profit_usd)}</span>
                {pinned === shown.parcel_id && (
                  <span className="text-micro text-muted-foreground">(pinned — click to release)</span>
                )}
              </div>
            ) : (
              <span className="text-muted-foreground">
                Hover a voyage for its timings and profit, or click one to keep it up.
              </span>
            )}
          </div>

          <div className="flex flex-wrap gap-3 border-t border-border pt-2 text-micro text-muted-foreground">
            <Legend className="bg-primary" label="Laden voyage" />
            <Legend className="bg-wait/35" label="Waiting to start" />
            <span>
              {Math.round(waitHours / 24)}d total waiting · {Math.round(ballastHours / 24)}d
              ballast across the fleet
            </span>
          </div>
        </div>
      </Panel>

      {plan.period_cover.length > 0 && (
        <PeriodCoverPanel cover={plan.period_cover} asOf={plan.as_of} money={money} />
      )}

      <div className="grid gap-2 lg:grid-cols-2">
        <Panel
          title="Voyages Scheduled"
        soWhat={'Each sailing the plan commits to, with its ship and dates. Use it as the working list to take to your owners and brokers.'}
          meta={`${plan.assignments.length}`}
          hint="Each row is one vessel assigned to one lot, with the solver's own timings and profit."
          flush
        >
          {plan.assignments.length === 0 ? (
            <div className="panel-state">
              <p className="panel-state-title">No lot could be covered</p>
              <p className="panel-state-hint">
                Every pairing was ruled out. The reasons are listed beside this panel.
              </p>
            </div>
          ) : (
            <table className="desk-table">
              <thead>
                <tr>
                  <th>Vessel</th>
                  <th>Lot</th>
                  <th>Discharge</th>
                  <th className="text-right">Wait</th>
                  <th className="text-right">Laden</th>
                  <th className="text-right">Profit</th>
                </tr>
              </thead>
              <tbody>
                {plan.assignments.map((a) => (
                  <tr key={`${a.vessel_id}-${a.parcel_id}`}>
                    <td className="font-semibold">{a.vessel_id}</td>
                    <td className="font-mono text-caption">{a.parcel_id}</td>
                    <td>{portName(a.dest_port)}</td>
                    <td className="desk-num text-right text-muted-foreground">
                      {(a.wait_hours / 24).toFixed(1)}d
                    </td>
                    <td className="desk-num text-right">
                      {((a.finish_hours - a.start_operation_hours) / 24).toFixed(1)}d
                    </td>
                    <td className="desk-num text-right font-semibold">{moneyCompact(a.profit_usd)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Panel>

        <Panel
          title="Lots Not Covered"
        soWhat={'The cargoes the plan could not fit, and why. Each one needs a decision from you: widen its window, add tonnage, or accept that it moves late.'}
          meta={`${plan.unassigned.length}`}
          hint="Every lot you sent is accounted for — assigned, or listed here with the real reason. A lot with no revenue is unassigned by construction rather than by any constraint, and says so."
          flush
        >
          {plan.unassigned.length === 0 ? (
            <div className="panel-state">
              <p className="panel-state-title">Every lot is covered</p>
            </div>
          ) : (
            <ul className="divide-y divide-border/60">
              {plan.unassigned.map((u) => {
                const p = byParcel.get(u.parcel_id)
                return (
                  <li key={u.parcel_id} className="px-2 py-2">
                    <div className="flex items-baseline justify-between gap-2">
                      <span className="font-mono text-caption font-semibold text-foreground">
                        {u.parcel_id}
                      </span>
                      {p && (
                        <span className="text-micro text-muted-foreground">
                          {portName(p.origin_port)} → {portName(p.dest_port)}
                        </span>
                      )}
                    </div>
                    <p className="mt-0.5 text-micro leading-relaxed text-muted-foreground">
                      {u.reason}
                    </p>
                  </li>
                )
              })}
            </ul>
          )}
          {plan.infeasible_pairs.length > 0 && (
            <div className="panel-note">
              Ruled out before the solve by real port limits:{' '}
              {plan.infeasible_pairs.slice(0, 4).map((p) => p.reason).join(' · ')}
              {plan.infeasible_pairs.length > 4 && ` · +${plan.infeasible_pairs.length - 4} more`}
            </div>
          )}
        </Panel>
      </div>
    </>
  )
}

/** A headline figure. `explain` goes through the desk's own Tooltip rather
 *  than a native `title`: a native tooltip is invisible to a keyboard user,
 *  takes about a second to appear and renders in OS chrome — which is exactly
 *  why the desk's explanatory text went unread until it was replaced. */
/**
 * Is this book worth the tonnage it consumes?
 *
 * The break-even hire comes straight from the solver's own output: fleet
 * profit over the ship-days the plan occupies. The benchmark beside it is the
 * real published Baltic class TC average at the pricing date.
 *
 * That benchmark is a SPOT index, and this panel says so on its face rather
 * than in a footnote. A 3/6/12-month period rate is a forward, negotiated,
 * broker-supplied number that exists nowhere in this system's data, so the
 * panel does not quote one -- it gives the bar any offer has to clear, and
 * lets the desk type in the rate it has actually been shown.
 */
function PeriodCoverPanel({
  cover,
  asOf,
  money,
}: {
  cover: SeasonPeriodCover[]
  asOf: string
  money: (usd: number) => string
}) {
  return (
    <Panel
      title="Period Cover"
        soWhat={'Whether taking a ship on hire for a stretch of the season beats fixing each voyage on the day. It wins when you have steady volume and rates are heading up; it loses when your volume is uncertain.'}
      meta={`break-even hire · priced from ${asOf}`}
      hint="The highest daily rate at which chartering in the tonnage to cover this plan still breaks even, against the real published spot TC average for the class. The benchmark is a spot index, not a period quote — this system holds no period charter rate, and does not invent one."
    >
      <div className="divide-y divide-border p-1">
        {cover.map((c) => (
          <ClassCover key={c.vessel_class} c={c} asOf={asOf} money={money} />
        ))}
      </div>
    </Panel>
  )
}

/**
 * One vessel class's break-even, benchmark and offer check.
 *
 * A component per class rather than a loop body, because the quoted-rate input
 * has to be per class. A Panamax period rate and a Supramax period rate are
 * different numbers negotiated separately; one shared input would check a
 * single figure against two different bars and report both answers as if the
 * desk had been quoted the same rate for both.
 */
function ClassCover({
  c,
  asOf,
  money,
}: {
  c: SeasonPeriodCover
  asOf: string
  money: (usd: number) => string
}) {
  const [offer, setOffer] = useState('')
  const offered = Number(offer)
  const hasOffer = offer.trim() !== '' && Number.isFinite(offered) && offered > 0
  const beats = c.verdict === 'cover_beats_spot'
  const clears = hasOffer && offered < c.break_even_hire_usd_per_day

  return (
    <div className="space-y-2 py-2 first:pt-0 last:pb-0">
      <div className="grid grid-cols-2 gap-x-6 md:grid-cols-4">
        <Stat
          label={`${c.vessel_class} break-even hire`}
          value={`${money(c.break_even_hire_usd_per_day)}/day`}
          tone={beats ? 'go' : undefined}
          explain={`Profit from this class (${money(c.profit_usd)}) divided by the ship-days it occupies (${c.n_vessels} vessel${c.n_vessels === 1 ? '' : 's'} × ${(c.ship_days / c.n_vessels).toFixed(1)} days = ${c.ship_days.toFixed(1)}). Idle vessels count: chartering three ships and using two still costs three ships' hire.`}
        />
        <Stat
          label="Spot TC average"
          value={
            c.spot_tc_average_usd_per_day == null
              ? '—'
              : `${money(c.spot_tc_average_usd_per_day)}/day`
          }
          explain={`The real published ${c.spot_tc_series_id} value${c.spot_tc_as_of ? ` on ${c.spot_tc_as_of}` : ''}. What these ships would earn trading spot — a spot index, not a period charter rate.`}
        />
        <Stat
          label="Room for hire"
          value={
            c.margin_over_spot_usd_per_day == null
              ? '—'
              : `${money(c.margin_over_spot_usd_per_day)}/day`
          }
          explain="Break-even less the spot average. Positive means the book earns more per ship-day than trading the ships spot — that gap is what a period charter has to fit inside."
        />
        <Stat label="Ship-days" value={c.ship_days.toFixed(1)} />
      </div>

      <p className="panel-note">
        {c.verdict === 'no_benchmark' ? (
          <>
            No published <span className="desk-num">{c.spot_tc_series_id}</span> value exists on{' '}
            {asOf}, so there is nothing real to compare the break-even against. The index is not
            published every calendar day; pricing from a day the market was open will give a
            benchmark. The break-even itself stands — it needs no market data.
          </>
        ) : beats ? (
          <>
            This book earns more per ship-day than these {c.vessel_class} vessels would earn
            trading spot. Covering it with chartered-in tonnage is worth doing at any hire below{' '}
            <span className="desk-num font-semibold text-foreground">
              {money(c.break_even_hire_usd_per_day)}/day
            </span>
            .
          </>
        ) : (
          <>
            These {c.vessel_class} vessels would earn more simply trading spot than this book pays
            them. The programme does not cover its own opportunity cost, so no period charter
            improves it — the book itself is what needs to change.
          </>
        )}
      </p>

      {/* The desk supplies the offer, because this system does not have one.
          Everything above is computed; this is the one number that has to come
          from a broker. */}
      <div className="flex flex-wrap items-end gap-2 border-t border-border pt-2">
        <Field label={`A ${c.vessel_class} period rate you have been quoted (USD/day)`}>
          <input
            type="number"
            min={0}
            value={offer}
            onChange={(e) => setOffer(e.target.value)}
            placeholder="e.g. 24500"
            className={cn(inputCls, 'w-40 font-mono')}
          />
        </Field>
        {hasOffer && (
          <p className={cn('pb-1 text-body font-semibold', clears ? 'text-go' : 'text-risk')}>
            {clears
              ? `Clears the bar by ${money(c.break_even_hire_usd_per_day - offered)}/day.`
              : `Short by ${money(offered - c.break_even_hire_usd_per_day)}/day — this book does not pay that hire.`}
          </p>
        )}
      </div>
    </div>
  )
}

function Stat({
  label,
  value,
  tone,
  explain,
}: {
  label: string
  value: string
  tone?: 'go'
  explain?: string
}) {
  const figure = (
    <span
      className={cn(
        'desk-num mt-0.5 block text-figure font-bold',
        tone === 'go' ? 'text-go' : 'text-foreground',
      )}
    >
      {value}
    </span>
  )
  return (
    <div>
      <div className="stat-label">{label}</div>
      {explain ? (
        <Tooltip content={explain} className="border-b border-dotted border-muted-foreground/50">
          {figure}
        </Tooltip>
      ) : (
        figure
      )}
    </div>
  )
}

function Legend({ className, label }: { className: string; label: string }) {
  return (
    <span className="flex items-center gap-1">
      <span className={cn('h-2 w-3 rounded-xs', className)} aria-hidden="true" />
      {label}
    </span>
  )
}
