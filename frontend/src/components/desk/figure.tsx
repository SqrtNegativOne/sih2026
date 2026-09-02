import NumberFlow, { NumberFlowGroup, type Format } from '@number-flow/react'
import { useReducedMotion } from 'motion/react'
import { DURATION, EASE } from '@/lib/motion'
import { cn } from '@/lib/utils'

/**
 * A number that transitions to its new value instead of snapping to it.
 *
 * Why this earns its place on a trading desk rather than being decoration: a
 * quote replaces every figure on screen at once, and a hard swap gives no
 * signal about *which* numbers moved or by how much. Rolling digits make the
 * delta legible peripherally — the eye catches the rate climbing even when it
 * is reading the verdict.
 *
 * Formatting goes through `Intl` rather than the string helpers in lib/format,
 * because NumberFlow needs a numeric value to interpolate. `resolve` below is
 * therefore held to reproducing those helpers byte-for-byte — verified against
 * 21 magnitudes including the sign and threshold boundaries.
 */

type Kind = 'usd' | 'usdCompact' | 'number' | 'percent'

interface Resolved {
  /** What NumberFlow interpolates — already scaled for compact units. */
  value: number
  // NumberFlow's own Format, not Intl.NumberFormatOptions: it narrows
  // `notation` to exclude scientific/engineering, so the broader Intl type is
  // not assignable. Caught by the production build's typecheck, which is
  // stricter than `tsc --noEmit` under this repo's config.
  opts: Format
  prefix: string
  suffix: string
}

/**
 * Resolves a figure to exactly what `lib/format` would print. A figure is
 * allowed to animate; it is not allowed to change what it says.
 *
 * `usdCompact` cannot be expressed as one Intl config. formatUsdCompact
 * switches precision by magnitude — two decimals in the millions, one in the
 * thousands, plain comma-grouped currency below $10,000 — and it does its own
 * thresholding, so 999,999 prints as "$1000.0K" where Intl's compact notation
 * would say "$1.0M". Rather than approximate that, this mirrors the helper's
 * branching directly and scales the value itself, which reproduces every case
 * including that boundary. Both discrepancies here were found by reading the
 * rendered table, not by assuming the two formatters agreed.
 */
function resolve(kind: Kind, digits: number, value: number): Resolved {
  switch (kind) {
    case 'usd':
      return {
        value,
        opts: { style: 'currency', currency: 'USD', minimumFractionDigits: 0, maximumFractionDigits: 0 },
        prefix: '',
        suffix: '',
      }
    case 'usdCompact': {
      const abs = Math.abs(value)
      const sign = value < 0 ? '-' : ''
      if (abs >= 1_000_000) {
        return {
          value: abs / 1_000_000,
          opts: { minimumFractionDigits: 2, maximumFractionDigits: 2, useGrouping: false },
          prefix: `${sign}$`,
          suffix: 'M',
        }
      }
      if (abs >= 10_000) {
        return {
          value: abs / 1_000,
          opts: { minimumFractionDigits: 1, maximumFractionDigits: 1, useGrouping: false },
          prefix: `${sign}$`,
          suffix: 'K',
        }
      }
      return {
        value: Math.round(abs),
        opts: { minimumFractionDigits: 0, maximumFractionDigits: 0 },
        prefix: `${sign}$`,
        suffix: '',
      }
    }
    case 'percent':
      return {
        value,
        opts: { style: 'percent', maximumFractionDigits: digits },
        prefix: '',
        suffix: '',
      }
    default:
      return {
        value,
        opts: { minimumFractionDigits: digits, maximumFractionDigits: digits },
        prefix: '',
        suffix: '',
      }
  }
}

export function Figure({
  value,
  kind = 'number',
  digits = 0,
  className,
  prefix,
  suffix,
}: {
  value: number
  kind?: Kind
  /** Fraction digits for `number` and `percent`. */
  digits?: number
  className?: string
  prefix?: string
  suffix?: string
}) {
  const reduced = useReducedMotion()
  const r = resolve(kind, digits, value)
  const pre = `${prefix ?? ''}${r.prefix}`
  const post = `${r.suffix}${suffix ?? ''}`

  // The same string NumberFlow paints, from the same options — so the
  // accessible text and the visible text can never disagree.
  const text = `${pre}${new Intl.NumberFormat('en-US', r.opts).format(r.value)}${post}`

  return (
    <span className={cn('tabular-nums', className)}>
      {/*
        NumberFlow renders into a shadow root, and it renders EVERY digit 0-9
        stacked per column so it can animate by translation. Inspected live,
        that shadow content carries no aria-hidden, no role and no label: its
        text is literally "01234567890123456789,012...", so a screen reader
        walks it and reads digit soup, and nothing outside the shadow root can
        read the value at all — the number is unselectable and uncopyable,
        which on a desk where people lift rates into an email is a real
        regression, not a nicety.

        So the animated element is hidden from assistive tech entirely and the
        real value is carried alongside it as text. Screen readers and
        copy/paste get the formatted figure; eyes get the rolling one.
      */}
      <span className="sr-only">{text}</span>
      <span aria-hidden="true">
        <NumberFlow
          value={r.value}
          format={r.opts}
          prefix={pre}
          suffix={post}
          // Under reduced motion the value still updates — it just arrives
          // rather than travelling. Never skip the update itself.
          animated={!reduced}
          transformTiming={{ duration: DURATION.base * 1000, easing: `cubic-bezier(${EASE.join(',')})` }}
          spinTiming={{ duration: DURATION.slow * 1000, easing: `cubic-bezier(${EASE.join(',')})` }}
          opacityTiming={{ duration: DURATION.fast * 1000, easing: 'ease-out' }}
        />
      </span>
    </span>
  )
}

/**
 * Wrap a set of Figures that should roll in lockstep — a row of related
 * money, or a before/after pair. Without this each digit column times
 * independently and the group looks like it is shuffling.
 */
export function FigureGroup({ children }: { children: React.ReactNode }) {
  return <NumberFlowGroup>{children}</NumberFlowGroup>
}
