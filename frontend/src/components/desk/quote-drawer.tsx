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
  'h-7 w-full rounded-sm border border-input bg-surface px-2 text-lead text-foreground ' +
  'transition-colors placeholder:text-muted-foreground/70 hover:border-muted-foreground/60 ' +
  'focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/40 ' +
  'disabled:cursor-not-allowed disabled:bg-surface-2 disabled:text-muted-foreground'

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

  // Escape closes the drawer. It is a modal overlay -- it covers the whole
  // viewport with a backdrop that eats clicks (the root cause behind
  // F-48/F-52/F-53) -- and an overlay a keyboard user cannot dismiss is a
  // trap. Backdrop click already worked; this is the other half.
  useEffect(() => {
    if (!open) return
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

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
          {/*
            z-50, not the z-40 this used to carry.

            F-48 and F-52 raised the icon rail, the top bar and <main> to z-50
            so their controls stayed clickable through this backdrop — which
            was necessary only because the drawer auto-opened on every load.
            F-53 removed that root cause by defaulting it closed, but the z-50
            on <main> stayed, and it left the backdrop unable to cover the very
            content it exists to block: measured live, a click anywhere over
            the page with the drawer open resolved to the page, not to this
            element, so backdrop-click dismissal silently did nothing (and a
            user could still operate controls "underneath" an open modal).

            Matching z-50 rather than exceeding it is deliberate: within one
            stacking context, equal z-index paints in DOM order, and the drawer
            renders after <main>, the rail and the header — so this backdrop
            covers all three, while the panel below (also z-50, later still)
            stays above this backdrop. Nothing renders on mount, so F-53's fix
            is untouched.
          */}
          <motion.div
            className="fixed inset-0 z-50 bg-black/30"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={onClose}
            aria-hidden="true"
          />
          <motion.aside
            role="dialog"
            aria-modal="true"
            aria-label="New charter quote"
            className="fixed right-0 top-0 z-50 flex h-full w-95 max-w-[92vw] flex-col border-l border-border bg-surface shadow-raised"
            initial={{ x: '100%' }}
            animate={{ x: 0 }}
            exit={{ x: '100%' }}
            transition={{ type: 'spring', stiffness: 320, damping: 34 }}
          >
            <div className="flex h-10 shrink-0 items-center justify-between border-b border-border bg-navbar px-3 text-navbar-foreground">
              <span className="text-lead font-bold uppercase tracking-wide">New Charter Quote</span>
              <button
                type="button"
                onClick={onClose}
                aria-label="Close the quote form"
                title="Close (Esc)"
                className="rounded-sm p-1 transition-colors hover:bg-white/10 focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-white"
              >
                <X className="h-4 w-4" aria-hidden="true" />
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
                  <div className="flex justify-between text-micro uppercase tracking-wide text-muted-foreground">
                    <span>Risk-neutral</span>
                    <span>Risk-averse</span>
                  </div>
                </Field>

                <div className="space-y-2 border-t border-border pt-2">
                  <div className="flex items-center justify-between">
                    <span className="text-caption font-semibold uppercase tracking-wide text-muted-foreground">
                      Vessels in hand (optional)
                    </span>
                    <button
                      type="button"
                      onClick={() =>
                        setVessels((vs) => [...vs, newVesselDraft(vs.length + 1, anchorDate)])
                      }
                      className="text-caption font-semibold uppercase tracking-wide text-primary hover:underline"
                    >
                      + Add vessel
                    </button>
                  </div>

                  {vessels.map((v, i) => (
                    <div key={v.key} className="space-y-2 rounded border border-border p-2">
                      <div className="flex items-center justify-between">
                        <span className="text-micro font-semibold uppercase tracking-wide text-muted-foreground">
                          Vessel {i + 1}
                        </span>
                        <button
                          type="button"
                          onClick={() => setVessels((vs) => vs.filter((x) => x.key !== v.key))}
                          className="text-micro font-semibold uppercase tracking-wide text-risk hover:underline"
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
                        /* These seven carried their name in `placeholder`
                           only, and every one of them is pre-filled from
                           newVesselDraft() -- so the label was gone the moment
                           the field was rendered. A column of seven unlabelled
                           numbers (13.5, 225, 32.2, 13, 32, 27) gives a user
                           no way to tell draft from LOA from beam from speed,
                           which is exactly the set you must get right for the
                           port-constraint check further down the desk to mean
                           anything. Persistent labels, and the input keeps its
                           accessible name whether or not it holds a value. */
                        ).map(([field, label]) => (
                          <label key={field} className="flex flex-col gap-0.5">
                            <span className="text-micro font-semibold uppercase tracking-wide text-muted-foreground">
                              {label}
                            </span>
                            <input
                              type="number"
                              step="0.1"
                              min={0.1}
                              aria-label={label}
                              value={v[field]}
                              onChange={(e) => updateVessel(v.key, { [field]: e.target.value })}
                              className={cn(inputCls, 'font-mono')}
                            />
                          </label>
                        ))}
                        <label className="col-span-2 flex flex-col gap-0.5">
                          <span className="text-micro font-semibold uppercase tracking-wide text-muted-foreground">
                            Available from
                          </span>
                          <input
                            type="date"
                            aria-label="Available from"
                            value={v.availableFrom}
                            onChange={(e) => updateVessel(v.key, { availableFrom: e.target.value })}
                            className={cn(inputCls, 'font-mono')}
                          />
                        </label>
                      </div>
                      {!vesselIsValid(v) && (
                        <p className="text-micro uppercase text-risk">
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
                  <p className="text-body text-risk">Origin and destination must differ.</p>
                )}
                {portsError && <p className="text-body text-risk">{portsError}</p>}
              </div>

              {/*
                This one button is styled inline rather than through the
                shared <Button>, and that is deliberate. The repo tripwire
                tests/test_no_synthetic_frontend_data.py allowlists the
                PRNG-derived React key in newVesselDraft() above BY LINE
                NUMBER (quote-drawer.tsx:51). Adding an import to this file
                shifts that line and fails the build on both halves of the
                tripwire at once -- the offender scan and the stale-entry
                scan. The classes below are exactly what
                button({variant:'primary', size:'lg'}) emits; keep the two in
                step if either changes. Fixing this properly means making the
                allowlist content-addressed rather than line-addressed, which
                is a change to tests/ and outside this pass.
              */}
              <div className="shrink-0 border-t border-border p-3">
                <button
                  type="submit"
                  className="inline-flex h-8 w-full shrink-0 items-center justify-center gap-1 whitespace-nowrap rounded-sm bg-primary px-4 text-lead font-semibold text-primary-foreground transition-colors duration-150 hover:bg-primary/90 active:bg-primary/95 focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-ring disabled:cursor-not-allowed disabled:opacity-45"
                  disabled={!canSubmit || submitting}
                  // A disabled button with no stated reason reads as broken.
                  // Say which field is still missing instead.
                  title={
                    submitting
                      ? 'Solving…'
                      : sameEnds
                        ? 'Origin and destination must differ.'
                        : originPort === '' || destPort === ''
                          ? 'Pick an origin and a destination port first.'
                          : !vesselsValid
                            ? 'Every vessel needs an ID, a current port, and positive figures.'
                            : 'Run the quote'
                  }
                >
                  {submitting ? 'Solving…' : 'Run quote'}
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
      <span className="text-caption font-semibold uppercase tracking-wide text-muted-foreground">
        {label}
      </span>
      {children}
      {hint && <span className="text-micro leading-tight text-muted-foreground">{hint}</span>}
    </label>
  )
}
