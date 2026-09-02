import { motion, useReducedMotion } from 'motion/react'
import { Panel, PanelEmpty } from '@/components/desk/panel'
import { useElementSize } from '@/hooks/use-element-size'
import { addDays, formatShortDate, formatUsd } from '@/lib/format'
import { drawPath, transition } from '@/lib/motion'
import type { QuoteResult } from '@/lib/types'

const H = 168
const PAD = { t: 14, r: 12, b: 20, l: 52 }

/**
 * The walk-away line, over the whole planning horizon.
 *
 * `stopping_result.exercise_boundary_usd_per_day` is the Longstaff-Schwartz
 * optimal-stopping boundary: for each day between now and the horizon, the
 * highest rate at which locking is still at least as good as continuing to
 * wait. The solver's own rule is exactly
 *
 *     LOCK if today_quote_usd_per_day <= boundary[0] (+ weather buffer)
 *
 * so this curve is not an illustration of the verdict -- it *is* the verdict,
 * drawn. The single "Ceiling" figure the verdict panel already shows is day 1
 * of this line; everything to the right of it is the part that was being
 * computed on every quote and thrown away.
 *
 * Honesty notes that stay on screen rather than in this comment:
 *  - The whole curve is MODEL_DERIVED from a Monte Carlo over the real
 *    forecast fan, not an observed price. The path count is shown.
 *  - The final point is a terminal condition, not a forecast: at the horizon
 *    there is no "wait" left, so the boundary collapses to the strike. It is
 *    drawn but excluded from "where the line bottoms", and labelled.
 */
export function WalkAwayCurve({ quote }: { quote: QuoteResult }) {
  const [box, ref] = useElementSize<HTMLDivElement>()
  const reduced = useReducedMotion()
  const stopping = quote.full_recommendation.stopping_result

  const hint =
    "The highest rate at which locking still beats waiting, for every day between now and the model's " +
    'horizon. Today\'s rate sitting above the line means waiting is currently worth more than locking -- ' +
    'which is precisely how the LOCK/WAIT verdict is decided. The line is model-derived from a Monte Carlo ' +
    'over the real forecast fan, not an observed price.'

  if (!stopping || stopping.exercise_boundary_usd_per_day.length === 0) {
    return (
      <Panel className="h-full" id="walkaway" title="Walk-Away Line" hint={hint}>
        <PanelEmpty
          title="No optimal-stopping solution for this quote"
          hint="The boundary needs a forecast fan the solver can calibrate against; none was available for this route and date."
        />
      </Panel>
    )
  }

  const boundary = stopping.exercise_boundary_usd_per_day
  const today = quote.today_quote_usd_per_day
  const w = Math.max(box.width, 280)
  const n = boundary.length

  /*
   * The line the VERDICT actually tests against is not boundary[0].
   *
   * opt.stopping applies the weather/cyclone transit buffer on top of the raw
   * LSMC boundary before deciding -- `adjusted = boundary[0] + weather_cost
   * per day`, then `LOCK if today <= adjusted` -- and that adjusted figure is
   * what the quote exposes as `ceiling_usd_per_day` and what the verdict panel
   * already labels "Ceiling".
   *
   * Reading this panel off the raw boundary instead produced a chart that
   * contradicted the verdict printed directly above it. Real case, VIZAG ->
   * RICHARDS_BAY: today 18,790, raw boundary 18,141, but a 4.1-day weather
   * buffer lifts the line to 20,737 -- the verdict is LOCK while this panel
   * would have said "above the line, so wait".
   *
   * This is the same failure as F-06, where a caption keyed off the fused
   * verdict instead of the number it was describing. So: the curve still draws
   * the raw boundary, because that is the real modelled shape across the
   * horizon, but every *decision* statement here keys off the same adjusted
   * figure the solver used, and the gap between the two is stated rather than
   * hidden.
   */
  const decisionLine = quote.ceiling_usd_per_day
  const weatherLift = decisionLine - boundary[0]
  const hasWeatherLift = Math.abs(weatherLift) > 1

  // The last point is the terminal condition (boundary == strike, because at
  // the horizon there is nothing left to wait for). Including it in "the
  // lowest the line gets" would report a number that is an artefact of the
  // horizon rather than a real projected trough.
  const interior = boundary.slice(0, Math.max(1, n - 1))
  const troughValue = Math.min(...interior)
  const troughIndex = interior.indexOf(troughValue)

  const lo = Math.min(today, decisionLine, ...boundary)
  const hi = Math.max(today, decisionLine, ...boundary)
  const span = hi - lo || 1
  const yMin = lo - span * 0.12
  const yMax = hi + span * 0.12

  const x = (day: number) => PAD.l + (day / Math.max(1, n - 1)) * (w - PAD.l - PAD.r)
  const y = (v: number) => PAD.t + (1 - (v - yMin) / (yMax - yMin)) * (H - PAD.t - PAD.b)

  const line = boundary.map((v, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join(' ')

  // The band between today's rate and the boundary: the gap that has to close
  // before locking becomes correct. Its lower edge is the boundary and its
  // upper edge is today's rate, so the traced edge is min(boundary, today) --
  // `max` traces today's own line on both passes and encloses nothing, which
  // is why the band was invisible on first build.
  const yToday = y(today)
  const aboveBand =
    boundary
      .map((v, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)} ${y(Math.min(v, today)).toFixed(1)}`)
      .join(' ') + ` L${x(n - 1).toFixed(1)} ${yToday.toFixed(1)} L${x(0).toFixed(1)} ${yToday.toFixed(1)} Z`

  // Keep the trough label inside the plot: flip its anchor near either edge
  // rather than letting a centred label overrun the panel.
  const troughX = x(troughIndex)
  const nearRight = troughX > w - PAD.r - 70
  const nearLeft = troughX < PAD.l + 70
  const troughAnchor = nearRight ? 'end' : nearLeft ? 'start' : 'middle'
  const troughLabelX = nearRight ? w - PAD.r : nearLeft ? PAD.l : troughX

  // Keyed off the adjusted line, so this panel and the verdict can never
  // disagree.
  const gap = today - decisionLine
  const isAbove = gap > 0
  const troughDate = formatShortDate(addDays(quote.as_of, troughIndex + 1))

  const anim = reduced ? 'shown' : undefined

  return (
    <Panel
      className="h-full"
      id="walkaway"
      title="Walk-Away Line"
      hint={hint}
      meta={`${n}-day horizon · ${stopping.n_paths.toLocaleString('en-US')} simulated paths`}
      flush
    >
      <div className="flex h-full flex-col">
        <div ref={ref} className="w-full px-2 pt-2">
          <svg width={w} height={H} className="block" role="img" aria-label={ariaSummary(today, decisionLine, isAbove, troughValue, troughDate)}>
            {/* Gap band: how far today's rate still has to fall. */}
            {isAbove && (
              // Opacity is the animated property AND the final look, so it has
              // to be one declaration -- a `style={{opacity}}` alongside an
              // opacity variant wins the cascade and the band never appears.
              <motion.path
                d={aboveBand}
                fill="var(--wait)"
                initial={reduced ? { opacity: 0.16 } : { opacity: 0 }}
                animate={{ opacity: 0.16 }}
                transition={reduced ? { duration: 0 } : { ...transition.slow, delay: 0.1 }}
              />
            )}

            {/* Today's rate -- the thing that has to come down to the line. */}
            <line
              x1={PAD.l}
              x2={w - PAD.r}
              y1={yToday}
              y2={yToday}
              stroke="var(--foreground)"
              strokeWidth={1}
              strokeDasharray="3 3"
              opacity={0.55}
            />
            <text x={PAD.l - 6} y={yToday + 3} textAnchor="end" className="fill-muted-foreground text-micro">
              today
            </text>

            {/* The boundary itself. */}
            <motion.path
              d={line}
              fill="none"
              stroke="var(--go)"
              strokeWidth={1.75}
              strokeLinejoin="round"
              pathLength={1}
              initial={reduced ? false : 'hidden'}
              animate={anim ?? 'shown'}
              variants={drawPath}
            />

            {/* Day 1 -- the same figure the verdict panel calls "Ceiling". */}
            <motion.g
              initial={reduced ? false : { opacity: 0 }}
              animate={{ opacity: 1 }}
              transition={{ ...transition.base, delay: reduced ? 0 : 0.35 }}
            >
              <circle cx={x(0)} cy={y(decisionLine)} r={3.5} fill="var(--go)" stroke="var(--surface)" strokeWidth={1.5} />
              <circle cx={x(troughIndex)} cy={y(troughValue)} r={2.5} fill="var(--market)" />
              {/* The trough often sits near the right edge (the boundary
                  declines across the horizon), so a centred label runs off the
                  plot. Anchor it to whichever side keeps it inside. */}
              <text
                x={troughLabelX}
                y={y(troughValue) + 12}
                textAnchor={troughAnchor}
                className="fill-muted-foreground text-micro"
              >
                lowest {formatUsd(troughValue)}
              </text>
            </motion.g>

            {/* Y bounds and the horizon tick. */}
            <text x={PAD.l - 6} y={PAD.t + 4} textAnchor="end" className="fill-muted-foreground text-micro">
              {formatUsd(Math.round(yMax / 100) * 100)}
            </text>
            <text x={PAD.l - 6} y={H - PAD.b} textAnchor="end" className="fill-muted-foreground text-micro">
              {formatUsd(Math.round(yMin / 100) * 100)}
            </text>
            <text x={PAD.l} y={H - 4} className="fill-muted-foreground text-micro">
              today
            </text>
            <text x={w - PAD.r} y={H - 4} textAnchor="end" className="fill-muted-foreground text-micro">
              +{n}d
            </text>
          </svg>
        </div>

        <div className="mt-auto space-y-1 border-t border-border px-2 py-2">
          <div className="stat-row">
            <span className="stat-label">Walk-away line today</span>
            <span className="stat-value font-semibold text-go">{formatUsd(decisionLine)}</span>
          </div>
          {/* When the weather buffer moves the line, say so and by how much --
              otherwise the figure above silently disagrees with the curve's
              own day-1 point and there is nothing on screen to explain it. */}
          {hasWeatherLift && (
            <div className="stat-row">
              <span className="stat-label">
                of which weather buffer {weatherLift > 0 ? 'raises it by' : 'lowers it by'}
              </span>
              <span className="stat-value text-caption text-muted-foreground">
                {formatUsd(Math.abs(weatherLift))}
              </span>
            </div>
          )}
          <div className="stat-row">
            <span className="stat-label">
              {isAbove ? "Today's rate is above it by" : "Today's rate is below it by"}
            </span>
            <span className={`stat-value font-semibold ${isAbove ? 'text-wait' : 'text-go'}`}>
              {formatUsd(Math.abs(gap))}
            </span>
          </div>
          <p className="pt-1 text-caption leading-relaxed text-muted-foreground">
            {isAbove ? (
              <>
                At {formatUsd(today)} today you are paying more than the model thinks this charter is
                worth locking at, so it says <strong className="text-foreground">wait</strong>. The line
                is lowest around {troughDate} at {formatUsd(troughValue)}.
              </>
            ) : (
              <>
                At {formatUsd(today)} today you are inside the line, so it says{' '}
                <strong className="text-foreground">lock</strong>. Waiting is not expected to beat this.
              </>
            )}{' '}
            {hasWeatherLift && (
              <>
                The green curve is the raw model boundary; the weather buffer moves today&apos;s
                decision line to {formatUsd(decisionLine)}, which is the figure the verdict uses.{' '}
              </>
            )}
            The final point is where the horizon ends, not a forecast — with no time left to wait, the
            line meets the strike by construction.
          </p>
        </div>
      </div>
    </Panel>
  )
}

/** Charts need a text equivalent; this is what a screen reader is told. */
function ariaSummary(
  today: number,
  decisionLine: number,
  isAbove: boolean,
  trough: number,
  troughDate: string,
): string {
  return (
    `Walk-away line. Today's rate ${formatUsd(today)} is ` +
    `${isAbove ? 'above' : 'at or below'} today's walk-away line of ${formatUsd(decisionLine)}. ` +
    `The line reaches its lowest point of ${formatUsd(trough)} around ${troughDate}.`
  )
}
