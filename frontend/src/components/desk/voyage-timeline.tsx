import { motion, useReducedMotion } from 'motion/react'
import { useState } from 'react'
import { Panel, PanelEmpty } from '@/components/desk/panel'
import { Term } from '@/components/desk/term'
import { addDays, formatShortDate, prettyPort } from '@/lib/format'
import { transition } from '@/lib/motion'
import type { PortListing, QuoteResult } from '@/lib/types'
import { cn } from '@/lib/utils'

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
 *    the panel says which components it is a sum of. It is not a commitment,
 *    and it is not presented as one.
 *  - Port waits are expected values from the same data the Port Constraints
 *    panel shows, carrying that panel's own caveats.
 */

interface Segment {
  key: string
  label: string
  /** Days from `as_of`. */
  start: number
  days: number
  fill: string
  detail: string
}

export function VoyageTimeline({
  quote,
  ports,
}: {
  quote: QuoteResult
  ports: PortListing[]
}) {
  const reduced = useReducedMotion()
  const [hover, setHover] = useState<string | null>(null)

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

  // `assumed_transit_days` is genuinely nullable -- the backend returns null
  // when it has no real transit estimate for the route. Without it there is no
  // voyage to lay on an axis, and inventing a duration to keep the chart
  // looking complete is exactly what this codebase forbids. Say so instead.
  const transit = quote.assumed_transit_days
  if (transit == null) {
    return (
      <Panel className="h-full" id="timeline" title="Voyage Timeline" hint={hint}>
        <PanelEmpty
          title="No transit estimate for this route"
          hint="The timeline needs a real transit duration to place the voyage on an axis, and none was computed for this origin and destination."
        />
      </Panel>
    )
  }

  // The vessel can only start loading once it is inside the laycan window, so
  // the voyage clock starts at laycan_start -- not at "now" and not at the end
  // of the lock window.
  const loadStart = laycanStart
  const sail = loadStart + loadWait
  const arrive = sail + transit + weather
  const free = arrive + dischargeWait
  const horizon = Math.max(free, laycanEnd) * 1.02

  const lockStart = quote.optimal_entry_window_start_day
  const lockEnd = quote.optimal_entry_window_end_day
  const hasLockWindow = lockStart != null && lockEnd != null

  const segments: Segment[] = [
    {
      key: 'wait-laycan',
      label: 'Before loading opens',
      start: 0,
      days: laycanStart,
      fill: 'var(--muted-foreground)',
      detail: `${laycanStart} days from today until the laycan window opens on ${formatShortDate(addDays(quote.as_of, laycanStart))}.`,
    },
    {
      key: 'load-wait',
      label: 'Waiting to berth',
      start: loadStart,
      days: loadWait,
      fill: 'var(--wait)',
      detail: `${loadWait.toFixed(1)} days expected queueing at ${portName(quote.origin_port)} before loading can start. Expected value from the same port data the Port Constraints panel shows.`,
    },
    {
      key: 'transit',
      label: 'At sea',
      start: sail,
      days: transit + weather,
      fill: 'var(--structure)',
      detail:
        `${transit.toFixed(1)} days of transit` +
        (weather > 0 ? `, plus ${weather.toFixed(1)} days of expected weather delay` : '') +
        `, ${portName(quote.origin_port)} to ${portName(quote.dest_port)}.`,
    },
    {
      key: 'discharge-wait',
      label: 'Waiting to discharge',
      start: arrive,
      days: dischargeWait,
      fill: 'var(--wait)',
      detail: `${dischargeWait.toFixed(1)} days expected queueing at ${portName(quote.dest_port)} before discharge can start.`,
    },
  ].filter((s) => s.days > 0.05)

  const pct = (d: number) => `${Math.max(0, Math.min(100, (d / horizon) * 100))}%`

  return (
    <Panel
      className="h-full"
      id="timeline"
      title="Voyage Timeline"
      hint={hint}
      meta={`${Math.round(free)} days from today to free at ${portName(quote.dest_port)}`}
    >
      <div className="flex h-full flex-col gap-3">
        {/* The axis */}
        {/* pt-7 reserves a real 28px lane above the track for the lock-window
            marker and its label. They used to be positioned with a negative
            offset off `top-0`, which put the label outside the container and
            the panel's own `overflow` clipped it -- visible in a screenshot as
            a half-height "lock window" with its top sheared off. Nothing is
            positioned outside its parent here any more. */}
        <div className="relative pt-7">
          {/* The lock window sits ABOVE the track, not inside it: it is a
              decision deadline, not a phase of the voyage, and drawing it as a
              segment would imply the ship is doing something during it. */}
          {hasLockWindow && (
            <>
              <div
                className="absolute top-0 whitespace-nowrap text-micro font-semibold text-go"
                style={{ left: pct(lockStart) }}
              >
                lock window
              </div>
              <div
                className="absolute top-4 h-1.5 rounded-full bg-go/70"
                style={{ left: pct(lockStart), width: pct(lockEnd - lockStart) }}
                title={`The model's preferred entry window: ${formatShortDate(addDays(quote.as_of, lockStart))} to ${formatShortDate(addDays(quote.as_of, lockEnd))}.`}
              />
            </>
          )}

          <div className="flex h-6 w-full overflow-hidden rounded-sm border border-border bg-surface-2">
            {/* No sr-only label inside these: `aria-label` already supplies the
                accessible name, and an element with aria-label ignores its own
                text content for naming -- the extra span was dead weight a
                screen reader could announce twice. */}
            {segments.map((s, i) => (
              <motion.button
                key={s.key}
                type="button"
                onMouseEnter={() => setHover(s.key)}
                onMouseLeave={() => setHover(null)}
                onFocus={() => setHover(s.key)}
                onBlur={() => setHover(null)}
                title={s.detail}
                aria-label={`${s.label}: ${s.days.toFixed(1)} days`}
                className={cn(
                  'relative h-full cursor-help border-r border-background/40 last:border-r-0',
                  'transition-opacity focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-ring',
                  hover && hover !== s.key ? 'opacity-45' : 'opacity-100',
                )}
                style={{ width: pct(s.days), background: s.fill }}
                initial={reduced ? false : { scaleX: 0 }}
                animate={{ scaleX: 1 }}
                transition={
                  reduced ? { duration: 0 } : { ...transition.base, delay: 0.1 + i * 0.07 }
                }
              />
            ))}
          </div>

          {/* Milestones */}
          <div className="relative mt-1 h-8">
            {[
              { d: 0, label: 'today' },
              { d: laycanStart, label: formatShortDate(addDays(quote.as_of, laycanStart)) },
              { d: arrive, label: `arrive ${formatShortDate(addDays(quote.as_of, Math.round(arrive)))}` },
            ].map((m, i) => (
              <div
                key={m.label}
                className={cn(
                  'absolute top-0 text-micro text-muted-foreground',
                  i === 2 ? '-translate-x-full' : i === 1 ? '-translate-x-1/2' : '',
                )}
                style={{ left: pct(m.d) }}
              >
                <span className="block h-1.5 w-px bg-border" aria-hidden="true" />
                <span className="whitespace-nowrap">{m.label}</span>
              </div>
            ))}
          </div>
        </div>

        {/* Legend doubles as the readout: hovering a segment dims the others,
            and each row states its own real figure so the panel is fully
            readable without any pointer interaction at all. */}
        <ul className="mt-auto space-y-1">
          {segments.map((s) => (
            <li
              key={s.key}
              className={cn(
                'flex items-baseline justify-between gap-2 transition-opacity',
                hover && hover !== s.key ? 'opacity-45' : 'opacity-100',
              )}
            >
              <span className="flex items-center gap-2 text-caption text-muted-foreground">
                <span
                  className="h-2 w-2 shrink-0 rounded-xs"
                  style={{ background: s.fill }}
                  aria-hidden="true"
                />
                {s.label}
              </span>
              <span className="desk-num text-caption text-foreground">
                {s.days.toFixed(1)}d
              </span>
            </li>
          ))}
        </ul>

        <p className="border-t border-border pt-2 text-micro leading-relaxed text-muted-foreground">
          Projected end date is the sum of the segments above — the{' '}
          <Term term="laycan">laycan</Term> opening, the expected berth queue at each end, transit and
          the weather buffer. It is a computed projection from real inputs, not a schedule anyone has
          committed to.
        </p>
      </div>
    </Panel>
  )
}
