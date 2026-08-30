import { AnimatePresence, motion } from 'motion/react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { X } from 'lucide-react'
import { Combobox, type ComboOption } from '@/components/ui/combobox'
import { prettyPort } from '@/lib/format'
import type { PortListing, QuoteRequest, VesselClass, VesselInput } from '@/lib/types'
import { cn } from '@/lib/utils'

interface QuoteDrawerProps {
  open: boolean
  onClose: () => void
  ports: PortListing[]
  portsError: string | null
  latestDate: string | null
  submitting: boolean
  onSubmit: (req: QuoteRequest) => void
}

const VESSEL_CLASSES: VesselClass[] = ['Capesize', 'Panamax', 'Supramax', 'Handysize']
const COMMODITIES = [
  'Thermal Coal',
  'Coking Coal',
  'Iron Ore',
  'Bauxite',
  'Alumina',
  'Limestone',
  'Grain',
  'Fertiliser',
  'Petcoke',
  'Clinker',
  'Dry Bulk',
].map((c) => ({ value: c, label: c }))

interface VesselDraft {
  key: string
  vesselId: string
  vesselClass: VesselClass
  port: string
  availableFrom: string
  dwt: string
  draftM: string
  loaM: string
  beamM: string
  speedKn: string
  ladenFuel: string
  ballastFuel: string
}

function newVesselDraft(n: number, availableFrom: string): VesselDraft {
  return {
    key: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    vesselId: `SAIL_${n}`,
    vesselClass: 'Panamax',
    port: '',
    availableFrom,
    dwt: '75000',
    draftM: '13.5',
    loaM: '225',
    beamM: '32.2',
    speedKn: '13',
    ladenFuel: '32',
    ballastFuel: '27',
  }
}

function vesselIsValid(v: VesselDraft): boolean {
  return (
    v.vesselId.trim() !== '' &&
    v.port !== '' &&
    [v.dwt, v.draftM, v.loaM, v.beamM, v.speedKn, v.ladenFuel, v.ballastFuel].every(
      (x) => Number(x) > 0,
    )
  )
}

const inputCls =
  'h-7 w-full rounded border border-input bg-surface px-2 text-[12px] text-foreground focus:outline-none focus:ring-2 focus:ring-primary'

export function QuoteDrawer({
  open,
  onClose,
  ports,
  portsError,
  latestDate,
  submitting,
  onSubmit,
}: QuoteDrawerProps) {
  const addDaysIso = (iso: string, days: number) =>
    new Date(new Date(`${iso}T00:00:00Z`).getTime() + days * 86_400_000)
      .toISOString()
      .slice(0, 10)

  // Snapshot of the mount-time fallback ("today", used only because
  // `latestDate` hasn't arrived from the parent's /meta fetch yet) -- frozen
  // in a ref so it stays fixed across re-renders. Bugfix: an earlier version
  // recomputed this fallback live from `latestDate` on every render, so by
  // the time the resync effect below ran (after `latestDate` had already
  // updated and triggered the very re-render that made the effect fire),
  // the "fallback to compare against" had already silently become today's
  // *real* value -- the prev-vs-fallback check could never match, and the
  // resync never fired at all, permanently leaving `asOf` on today's date
  // even when that's after the real data the backend has (as it is
  // whenever "today" has moved past the last real trading day).
  const fallback = useRef(latestDate ?? new Date().toISOString().slice(0, 10)).current
  const anchorDate = latestDate ?? fallback

  const [cargoVolume, setCargoVolume] = useState('75000')
  const [originPort, setOriginPort] = useState('')
  const [destPort, setDestPort] = useState('')
  const [asOf, setAsOf] = useState(fallback)
  const [laycanStart, setLaycanStart] = useState(addDaysIso(fallback, 14))
  const [laycanEnd, setLaycanEnd] = useState(addDaysIso(fallback, 21))
  const [contractTermDays, setContractTermDays] = useState('30')
  const [commodity, setCommodity] = useState('Thermal Coal')
  const [riskTolerance, setRiskTolerance] = useState('0')
  const [vessels, setVessels] = useState<VesselDraft[]>([])
  const [revenueUsd, setRevenueUsd] = useState('')

  // F-02 fix: `latestDate` arrives from the parent's own /meta fetch, which
  // resolves strictly after this component's first render -- so the
  // useState() calls above always seed off `fallback` ("today"), never off
  // the real latest-market-date, even though the JSX below reads
  // `latestDate` correctly once it lands. That mismatch meant the very
  // first quote a new user ran priced against a date the market has no
  // data for, and failed. Re-sync the three date fields the moment the
  // real latestDate arrives -- but only once, and only if the user hasn't
  // already edited them away from the (wrong) fallback in the meantime.
  // Compares against the frozen `fallback` snapshot above, not a live
  // recomputation -- see the bugfix note there.
  const resynced = useRef(false)
  useEffect(() => {
    if (resynced.current || !latestDate) return
    resynced.current = true
    setAsOf((prev) => (prev === fallback ? latestDate : prev))
    setLaycanStart((prev) => (prev === addDaysIso(fallback, 14) ? addDaysIso(latestDate, 14) : prev))
    setLaycanEnd((prev) => (prev === addDaysIso(fallback, 21) ? addDaysIso(latestDate, 21) : prev))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [latestDate])

  const portOptions = useMemo<ComboOption[]>(
    // F-39: p.name is the raw port id (e.g. "Newcastle_AU") -- prettyPort
    // matches every other screen's display convention ("Newcastle AU").
    () => ports.map((p) => ({ value: p.code, label: prettyPort(p.name), hint: p.code })),
    [ports],
  )

  const vesselsValid = vessels.every(vesselIsValid)
  const sameEnds = originPort !== '' && originPort === destPort
  const canSubmit = originPort !== '' && destPort !== '' && !sameEnds && vesselsValid

  function updateVessel(key: string, patch: Partial<VesselDraft>) {
    setVessels((vs) => vs.map((v) => (v.key === key ? { ...v, ...patch } : v)))
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!canSubmit) return
    const vesselInputs: VesselInput[] | undefined =
      vessels.length > 0
        ? vessels.map((v) => ({
            vessel_id: v.vesselId,
            vessel_class: v.vesselClass,
            current_port: v.port as VesselInput['current_port'],
            available_from: v.availableFrom,
            dwt: Number(v.dwt),
            draft_m: Number(v.draftM),
            loa_m: Number(v.loaM),
            beam_m: Number(v.beamM),
            speed_kn: Number(v.speedKn),
            laden_fuel_consumption_tpd: Number(v.ladenFuel),
            ballast_fuel_consumption_tpd: Number(v.ballastFuel),
          }))
        : undefined

    onSubmit({
      cargo_volume_dwt: Number(cargoVolume),
      origin_port: originPort as QuoteRequest['origin_port'],
      dest_port: destPort as QuoteRequest['dest_port'],
      laycan_start: laycanStart,
      laycan_end: laycanEnd,
      contract_term_days: Number(contractTermDays),
      commodity,
      as_of: asOf || undefined,
      risk_tolerance: Number(riskTolerance),
      vessels: vesselInputs,
      revenue_usd: vessels.length > 0 && revenueUsd !== '' ? Number(revenueUsd) : undefined,
    })
  }

  return (
    <AnimatePresence>
      {open && (
        <>
          <motion.div
            className="fixed inset-0 z-40 bg-black/30"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={onClose}
          />
          <motion.aside
            className="fixed right-0 top-0 z-50 flex h-full w-[380px] max-w-[92vw] flex-col border-l border-border bg-surface"
            initial={{ x: '100%' }}
            animate={{ x: 0 }}
            exit={{ x: '100%' }}
            transition={{ type: 'spring', stiffness: 320, damping: 34 }}
          >
            <div className="flex h-10 shrink-0 items-center justify-between border-b border-border bg-navbar px-3 text-navbar-foreground">
              <span className="text-[12px] font-bold uppercase tracking-wide">New Charter Quote</span>
              <button type="button" onClick={onClose} className="rounded p-1 hover:bg-white/10">
                <X className="h-4 w-4" />
              </button>
            </div>

            <form onSubmit={handleSubmit} className="flex min-h-0 flex-1 flex-col">
              <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-3">
                <div className="grid grid-cols-2 gap-2">
                  <Field label="Cargo volume (tonnes)">
                    <input
                      type="number"
                      min={1}
                      required
                      value={cargoVolume}
                      onChange={(e) => setCargoVolume(e.target.value)}
                      className={cn(inputCls, 'font-mono')}
                    />
                  </Field>
                  <Field label="Cargo type">
                    <Combobox
                      value={commodity}
                      onChange={setCommodity}
                      options={COMMODITIES}
                      allowFreeText
                      placeholder="Type or pick…"
                    />
                  </Field>
                </div>

                <Field label="Origin port">
                  <Combobox
                    value={originPort}
                    onChange={setOriginPort}
                    options={portOptions}
                    placeholder={portsError ? 'Ports unavailable' : 'Search ports…'}
                    disabled={portOptions.length === 0}
                  />
                </Field>
                <Field label="Destination port">
                  <Combobox
                    value={destPort}
                    onChange={setDestPort}
                    options={portOptions}
                    placeholder={portsError ? 'Ports unavailable' : 'Search ports…'}
                    disabled={portOptions.length === 0}
                  />
                </Field>

                <div className="grid grid-cols-2 gap-2">
                  <Field
                    label="Price as of"
                    hint={
                      latestDate
                        ? `Which day's market data to price from. Real data runs through ${latestDate} -- the calendar won't let you pick later.`
                        : "Which day's market data to price from."
                    }
                  >
                    <input
                      type="date"
                      value={asOf}
                      max={latestDate ?? undefined}
                      onChange={(e) => {
                        const v = e.target.value
                        // Belt-and-braces: `max` stops the picker UI, but a
                        // typed/pasted value can still slip past it in some
                        // browsers -- clamp here too so this field can never
                        // hold a date the backend has no data for.
                        setAsOf(latestDate && v > latestDate ? latestDate : v)
                      }}
                      className={cn(inputCls, 'font-mono')}
                    />
                  </Field>
                  <Field label="Contract term (days)">
                    <input
                      type="number"
                      min={1}
                      required
                      value={contractTermDays}
                      onChange={(e) => setContractTermDays(e.target.value)}
                      className={cn(inputCls, 'font-mono')}
                    />
                  </Field>
                </div>

                <div className="grid grid-cols-2 gap-2">
                  <Field label="Laycan start" hint="Future dates are fine here.">
                    <input
                      type="date"
                      required
                      value={laycanStart}
                      onChange={(e) => setLaycanStart(e.target.value)}
                      className={cn(inputCls, 'font-mono')}
                    />
                  </Field>
                  <Field label="Laycan end">
                    <input
                      type="date"
                      required
                      min={laycanStart}
                      value={laycanEnd}
                      onChange={(e) => setLaycanEnd(e.target.value)}
                      className={cn(inputCls, 'font-mono')}
                    />
                  </Field>
                </div>

                <Field label={`Risk tolerance — ${riskTolerance}`}>
                  <input
                    type="range"
                    min={0}
                    max={1}
                    step={0.1}
                    value={riskTolerance}
                    onChange={(e) => setRiskTolerance(e.target.value)}
                    className="h-1 w-full cursor-pointer appearance-none rounded bg-muted accent-primary"
                  />
                  <div className="flex justify-between text-[9px] uppercase tracking-wide text-muted-foreground">
                    <span>Risk-neutral</span>
                    <span>Risk-averse</span>
                  </div>
                </Field>

                <div className="space-y-2 border-t border-border pt-2">
                  <div className="flex items-center justify-between">
                    <span className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
                      Vessels in hand (optional)
                    </span>
                    <button
                      type="button"
                      onClick={() =>
                        setVessels((vs) => [...vs, newVesselDraft(vs.length + 1, anchorDate)])
                      }
                      className="text-[10px] font-semibold uppercase tracking-wide text-primary hover:underline"
                    >
                      + Add vessel
                    </button>
                  </div>

                  {vessels.map((v, i) => (
                    <div key={v.key} className="space-y-2 rounded border border-border p-2">
                      <div className="flex items-center justify-between">
                        <span className="text-[9px] font-semibold uppercase tracking-wide text-muted-foreground">
                          Vessel {i + 1}
                        </span>
                        <button
                          type="button"
                          onClick={() => setVessels((vs) => vs.filter((x) => x.key !== v.key))}
                          className="text-[9px] font-semibold uppercase tracking-wide text-risk hover:underline"
                        >
                          Remove
                        </button>
                      </div>
                      <div className="grid grid-cols-2 gap-2">
                        <input
                          value={v.vesselId}
                          onChange={(e) => updateVessel(v.key, { vesselId: e.target.value })}
                          placeholder="Vessel ID"
                          className={cn(inputCls, 'font-mono')}
                        />
                        <select
                          value={v.vesselClass}
                          onChange={(e) =>
                            updateVessel(v.key, { vesselClass: e.target.value as VesselClass })
                          }
                          className={inputCls}
                        >
                          {VESSEL_CLASSES.map((c) => (
                            <option key={c} value={c}>
                              {c}
                            </option>
                          ))}
                        </select>
                        <select
                          value={v.port}
                          onChange={(e) => updateVessel(v.key, { port: e.target.value })}
                          className={cn(inputCls, 'col-span-2')}
                        >
                          <option value="">Current port…</option>
                          {ports.map((p) => (
                            <option key={p.code} value={p.code}>
                              {prettyPort(p.name)}
                            </option>
                          ))}
                        </select>
                        {(
                          [
                            ['dwt', 'DWT'],
                            ['draftM', 'Draft m'],
                            ['loaM', 'LOA m'],
                            ['beamM', 'Beam m'],
                            ['speedKn', 'Speed kn'],
                            ['ladenFuel', 'Laden t/d'],
                            ['ballastFuel', 'Ballast t/d'],
                          ] as const
                        ).map(([field, label]) => (
                          <input
                            key={field}
                            type="number"
                            step="0.1"
                            min={0.1}
                            placeholder={label}
                            value={v[field]}
                            onChange={(e) => updateVessel(v.key, { [field]: e.target.value })}
                            className={cn(inputCls, 'font-mono')}
                          />
                        ))}
                        <input
                          type="date"
                          value={v.availableFrom}
                          onChange={(e) => updateVessel(v.key, { availableFrom: e.target.value })}
                          className={cn(inputCls, 'col-span-2 font-mono')}
                        />
                      </div>
                      {!vesselIsValid(v) && (
                        <p className="text-[9px] uppercase text-risk">
                          Needs an ID, a current port, and positive figures.
                        </p>
                      )}
                    </div>
                  ))}

                  {vessels.length > 0 && (
                    <Field label="Cargo revenue (USD, optional)">
                      <input
                        type="number"
                        min={0}
                        placeholder="Blank means no vessel assigned to cargo"
                        value={revenueUsd}
                        onChange={(e) => setRevenueUsd(e.target.value)}
                        className={cn(inputCls, 'font-mono')}
                      />
                    </Field>
                  )}
                </div>

                {sameEnds && (
                  <p className="text-[11px] text-risk">Origin and destination must differ.</p>
                )}
                {portsError && <p className="text-[11px] text-risk">{portsError}</p>}
              </div>

              <div className="shrink-0 border-t border-border p-3">
                <button
                  type="submit"
                  disabled={!canSubmit || submitting}
                  className="h-8 w-full rounded bg-primary text-[12px] font-semibold uppercase tracking-wide text-primary-foreground transition-colors hover:bg-primary/90 disabled:opacity-50"
                >
                  {submitting ? 'Solving…' : 'Run Quote'}
                </button>
              </div>
            </form>
          </motion.aside>
        </>
      )}
    </AnimatePresence>
  )
}

function Field({
  label,
  hint,
  children,
}: {
  label: string
  hint?: string
  children: React.ReactNode
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
        {label}
      </span>
      {children}
      {hint && <span className="text-[9px] leading-tight text-muted-foreground">{hint}</span>}
    </label>
  )
}
