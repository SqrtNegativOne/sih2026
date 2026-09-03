import { Anchor, Clock, Ship } from 'lucide-react'
import { motion, useReducedMotion } from 'motion/react'
import { useState, type ComponentType } from 'react'
import { Panel, PanelEmpty } from '@/components/desk/panel'
import { Term } from '@/components/desk/term'
import { addDays, formatShortDate, prettyPort } from '@/lib/format'
import { transition } from '@/lib/motion'
import type { PortListing, QuoteResult } from '@/lib/types'
import { cn } from '@/lib/utils'

/** The panel's plain-English Layer 2 (see Panel's `soWhat` prop). Declared
 *  once here because this component renders the same panel in several states
 *  -- loading, error, empty, populated -- and the explanation is the same in
 *  all of them. */
const SO_WHAT =
  'Day by day, where the ship is and what it is doing, from loading to ' +
  'discharge. If the arrival lands after the plant needs the cargo, either ' +
  'move the laycan earlier or accept a faster, more expensive ship.'

/**
 * The whole voyage on one axis.
 *
 * Every figure here was already on the desk and every one of them was a bare
 * number in a different panel: the lock window as a date range in the verdict's
 * "Timing" column, the laycan as two dates in the summary strip, the port waits
 * as two cells in a table, the transit as a field rendered nowhere at all. A
 * reader had to hold five separate numbers in their head to answer "when does
 * this ship actually get there", which is the first question anyone asks.
 *
 * Laid on a shared time axis they answer it at a glance, and the *shape* starts
 * carrying information the numbers never did -- how much slack sits between
 * deciding and loading, whether the port waits are a rounding error next to the
 * transit or a real part of the voyage.
 *
 * Honesty, since this composes figures rather than reading one off:
 *  - Every segment length is a real field. Nothing is padded to look tidy.
 *  - The end date is DERIVED by summing them, so it is labelled "projected" and
 *    the panel says which components it is a sum of. It is not a commitment.
 *  - Port waits are expected values from the same data the Port Constraints
 *    panel shows, and carry that panel's caveats.
 */

interface Segment {
  key: string
  label: string
  short: string
  start: number
  days: number
  tone: 'idle' | 'wait' | 'sea'
  icon: ComponentType<{ className?: string }>
  detail: string
}

/**
 * Each tone carries its bar fill, the label colour that sits ON that fill, and
 * the text colour for the same tone on the panel's own background.
 *
 * `onBar` is a paired token, not `text-background`: the fills here are
 * mid-tone, so a label keyed to the page background fails in both themes at
 * once -- measured 2.57:1 on dark and 1.67:1 on light before this. The wait and
 * sea rows reuse the same fill/foreground pairs already proven elsewhere on the
 * desk, and the idle row uses a light enough tint that the normal foreground
 * reads on it.
 */
const TONE: Record<Segment['tone'], { bar: string; onBar: string; text: string }> = {
  idle: { bar: 'bg-muted-foreground/25', onBar: 'text-foreground', text: 'text-muted-foreground' },
  wait: { bar: 'bg-wait', onBar: 'text-wait-fg', text: 'text-wait-on-soft' },
  sea: { bar: 'bg-primary', onBar: 'text-primary-foreground', text: 'text-primary' },
}

export function VoyageTimeline({ quote, ports }: { quote: QuoteResult; ports: PortListing[] }) {
  const reduced = useReducedMotion()
  // Click to pin a segment open; hover previews it. Pinning matters because
  // the detail text is long enough to want to read without holding a pointer
  // still, and it is the only way to reach it on a touch screen.
  const [pinned, setPinned] = useState<string | null>(null)
  const [hover, setHover] = useState<string | null>(null)
  const active = hover ?? pinned

  const portName = (code: string) =>
    prettyPort(ports.find((p) => p.code === code)?.name ?? code)

  const hint =
    'Every segment is a real figure already shown elsewhere on the desk -- the laycan window, the ' +
    'expected berth queue at each end, the transit estimate and the weather buffer -- placed on one ' +
    'time axis. The end date is derived by summing them, so it is a projection, not a commitment.'

  const dayOf = (iso: string) =>
    Math.round(
      (new Date(`${iso}T00:00:00Z`).getTime() - new Date(`${quote.as_of}T00:00:00Z`).getTime()) /
        86_400_000,
    )

  const laycanStart = dayOf(quote.laycan_start)
  const laycanEnd = dayOf(quote.laycan_end)
  const loadWait = quote.origin_port_check.expected_wait_days
  const dischargeWait = quote.dest_port_check.expected_wait_days
  const weather = quote.transit_buffer?.expected_delay_days ?? 0

  // Genuinely nullable: the backend returns null when it has no real transit
  // estimate. Inventing a duration to keep the chart looking complete is
  // exactly what this codebase forbids.
  const transit = quote.assumed_transit_days
  if (transit == null) {
    return (
      <Panel className="h-full" id="timeline" title="Voyage Timeline"
      soWhat={SO_WHAT} hint={hint}>
        <PanelEmpty
          title="No transit estimate for this route"
          hint="The timeline needs a real transit duration to place the voyage on an axis, and none was computed for this origin and destination."
        />
      </Panel>
    )
  }

  const sail = laycanStart + loadWait
  const arrive = sail + transit + weather
  const free = arrive + dischargeWait
  const horizon = Math.max(free, laycanEnd) || 1

  const lockStart = quote.optimal_entry_window_start_day
  const lockEnd = quote.optimal_entry_window_end_day
  const hasLock = lockStart != null && lockEnd != null

  const segments: Segment[] = [
    {
      key: 'idle',
      label: 'Before loading opens',
      short: 'Pre-laycan',
      start: 0,
      days: laycanStart,
      tone: 'idle' as const,
      icon: Clock,
      detail: `${laycanStart} days from today until the laycan window opens on ${formatShortDate(addDays(quote.as_of, laycanStart))}. Nothing is committed during this time — it is the gap between deciding and being able to load.`,
    },
    {
      key: 'load',
      label: `Waiting to berth at ${portName(quote.origin_port)}`,
      short: 'Load queue',
      start: laycanStart,
      days: loadWait,
      tone: 'wait' as const,
      icon: Anchor,
      detail: `${loadWait.toFixed(1)} days of expected queueing before loading can start. This is an expected value from the same port data the Port Constraints panel shows, not a booked slot.`,
    },
    {
      key: 'sea',
      label: `At sea, ${portName(quote.origin_port)} to ${portName(quote.dest_port)}`,
      short: 'At sea',
      start: sail,
      days: transit + weather,
      tone: 'sea' as const,
      icon: Ship,
      detail:
        `${transit.toFixed(1)} days of transit` +
        (weather > 0
          ? `, plus ${weather.toFixed(1)} days of expected weather delay from the marine forecast and cyclone climatology.`
          : ', with no weather delay expected on this route and date.'),
    },
    {
      key: 'discharge',
      label: `Waiting to discharge at ${portName(quote.dest_port)}`,
      short: 'Discharge queue',
      start: arrive,
      days: dischargeWait,
      tone: 'wait' as const,
      icon: Anchor,
      detail: `${dischargeWait.toFixed(1)} days of expected queueing at the discharge end before the vessel is free. This is what the vessel is paid for but not moving.`,
    },
  ].filter((s) => s.days > 0.05)

  const pct = (d: number) => (d / horizon) * 100
  const shown = segments.find((s) => s.key === active) ?? null
  const seaDays = transit + weather
  const waitDays = loadWait + dischargeWait

  return (
    <Panel
      className="h-full"
      id="timeline"
      title="Voyage Timeline"
      soWhat={SO_WHAT}
      hint={hint}
      meta={`${Math.round(free)} days to free at ${portName(quote.dest_port)}`}
    >
      <div className="flex h-full flex-col gap-2">
        {/* Lane above the track for the decision window. It is a deadline, not
            a phase of the voyage -- drawing it as a segment would imply the
            ship is doing something during it. */}
        <div className="relative h-4">
          {hasLock && (
            <div
              className="absolute inset-y-0 flex items-center gap-1.5"
              style={{ left: `${pct(lockStart)}%` }}
            >
              <span className="h-1.5 rounded-full bg-go" style={{ width: `${Math.max(pct(lockEnd - lockStart), 1)}vw` }} />
              <span className="whitespace-nowrap text-micro font-semibold text-go">
                lock window · {formatShortDate(addDays(quote.as_of, lockStart))}–
                {formatShortDate(addDays(quote.as_of, lockEnd))}
              </span>
            </div>
          )}
        </div>

        {/* The track. Segments are separated by real gaps and carry their own
            label inline when wide enough, so the common case needs no legend
            and no pointer at all. */}
        <div className="flex h-9 w-full items-stretch gap-0.5">
          {segments.map((s, i) => {
            const w = pct(s.days)
            const isActive = active === s.key
            const Icon = s.icon
            return (
              <motion.button
                key={s.key}
                type="button"
                onMouseEnter={() => setHover(s.key)}
                onMouseLeave={() => setHover(null)}
                onFocus={() => setHover(s.key)}
                onBlur={() => setHover(null)}
                onClick={() => setPinned((p) => (p === s.key ? null : s.key))}
                aria-pressed={pinned === s.key}
                aria-label={`${s.label}: ${s.days.toFixed(1)} days`}
                className={cn(
                  'group relative flex min-w-0 items-center justify-center overflow-hidden rounded-sm',
                  'cursor-pointer transition-[opacity,filter] duration-150',
                  'focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-ring',
                  TONE[s.tone].bar,
                  active && !isActive ? 'opacity-40' : 'opacity-100',
                  isActive && 'brightness-110',
                )}
                style={{ width: `${w}%` }}
                initial={reduced ? false : { scaleX: 0, originX: 0 }}
                animate={{ scaleX: 1 }}
                transition={reduced ? { duration: 0 } : { ...transition.base, delay: 0.08 + i * 0.07 }}
              >
                {w > 9 && (
                  <span
                    className={cn(
                      'flex min-w-0 items-center gap-1 px-1.5 text-micro font-bold',
                      TONE[s.tone].onBar,
                    )}
                  >
                    <Icon className="h-3 w-3 shrink-0" />
                    {w > 16 && <span className="truncate">{s.short}</span>}
                    <span className="shrink-0 tabular-nums">{s.days.toFixed(1)}d</span>
                  </span>
                )}
              </motion.button>
            )
          })}
        </div>

        {/* Milestones, tied to the same axis. */}
        <div className="relative h-7">
          {[
            { d: 0, label: 'today', align: 'start' as const },
            { d: laycanStart, label: formatShortDate(addDays(quote.as_of, laycanStart)), align: 'mid' as const },
            { d: arrive, label: formatShortDate(addDays(quote.as_of, Math.round(arrive))), align: 'end' as const },
          ].map((m) => (
            <div
              key={m.label}
              className={cn(
                'absolute top-0 flex flex-col',
                m.align === 'mid' && '-translate-x-1/2 items-center',
                m.align === 'end' && '-translate-x-full items-end',
              )}
              style={{ left: `${pct(m.d)}%` }}
            >
              <span className="h-2 w-px bg-border" aria-hidden="true" />
              <span className="whitespace-nowrap text-micro text-muted-foreground">{m.label}</span>
            </div>
          ))}
        </div>

        {/* One detail area that swaps with the active segment, instead of a
            static legend repeating what the track already says. Falls back to
            the summary a reader most wants when nothing is selected: how much
            of this voyage is actually spent moving. */}
        <div className="mt-auto flex min-h-14 flex-1 flex-col justify-center rounded-sm border border-border bg-surface-2 px-2 py-2">
          {shown ? (
            <motion.div
              key={shown.key}
              initial={reduced ? false : { opacity: 0, y: 3 }}
              animate={{ opacity: 1, y: 0 }}
              transition={reduced ? { duration: 0 } : transition.fast}
            >
              <div className="flex items-baseline justify-between gap-2">
                <span className={cn('text-caption font-bold', TONE[shown.tone].text)}>
                  {shown.label}
                </span>
                <span className="desk-num shrink-0 text-caption font-semibold text-foreground">
                  {shown.days.toFixed(1)} days
                </span>
              </div>
              <p className="mt-1 text-micro leading-relaxed text-muted-foreground">{shown.detail}</p>
            </motion.div>
          ) : (
            <div>
              <div className="flex items-baseline justify-between gap-2">
                <span className="text-caption font-semibold text-foreground">
                  {Math.round((seaDays / free) * 100)}% of this voyage is spent moving
                </span>
                <span className="desk-num shrink-0 text-caption text-muted-foreground">
                  {seaDays.toFixed(1)}d at sea · {waitDays.toFixed(1)}d queueing
                </span>
              </div>
              <p className="mt-1 text-micro leading-relaxed text-muted-foreground">
                Hover or click a segment for what it is. Projected end is the sum of the segments —
                the <Term term="laycan">laycan</Term> opening, both berth queues, transit and the
                weather buffer. A computed projection from real inputs, not a committed schedule.
              </p>
            </div>
          )}
        </div>
      </div>
    </Panel>
  )
}
