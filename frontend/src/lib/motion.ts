import type { Transition, Variants } from 'motion/react'

/**
 * The desk's motion vocabulary.
 *
 * Every animation on this product has to answer one of four questions --
 * *where did this come from*, *what just changed*, *what is loading*, *what
 * did I just do*. Motion that answers none of them is decoration and does not
 * ship. These tokens exist so that the answer is expressed consistently: a
 * value settling looks the same everywhere, a panel opening looks the same
 * everywhere, and nobody has to invent a duration at a call site.
 *
 * Durations are deliberately short. This is a tool someone uses for hours;
 * anything that reads as "premium" on a landing page reads as *slow* by the
 * twentieth quote of the day.
 */

/** Milliseconds. Exported for the rare consumer that needs a raw number. */
export const DURATION = {
  /** Hover, press, focus. Perceived as instant. */
  instant: 0.1,
  /** Colour and opacity changes on interactive elements. */
  fast: 0.15,
  /** The default: a panel opening, a value settling, a chip appearing. */
  base: 0.25,
  /** Chart data arriving, a camera move. */
  slow: 0.4,
  /** Reserved for the verdict, the one moment worth waiting for. */
  deliberate: 0.6,
} as const

/**
 * A single ease-out curve for almost everything. Motion that starts fast and
 * settles reads as responsive; symmetric ease-in-out reads as sluggish because
 * the first 40% of the duration barely moves.
 */
export const EASE = [0.22, 0.61, 0.36, 1] as const

/** For an element leaving: quicker, and it may start slowly. */
export const EASE_OUT = [0.4, 0, 1, 1] as const

export const transition = {
  instant: { duration: DURATION.instant, ease: EASE },
  fast: { duration: DURATION.fast, ease: EASE },
  base: { duration: DURATION.base, ease: EASE },
  slow: { duration: DURATION.slow, ease: EASE },
  deliberate: { duration: DURATION.deliberate, ease: EASE },
  exit: { duration: DURATION.fast, ease: EASE_OUT },
  /** Camera moves and drawer slides: physical, not timed. */
  spring: { type: 'spring', stiffness: 320, damping: 34 },
  /** Softer spring for large surfaces (the map camera). */
  springSoft: { type: 'spring', stiffness: 180, damping: 26 },
} satisfies Record<string, Transition>

/**
 * Content arriving in a panel after a real computation. Small offset on
 * purpose: 4px reads as "settled into place", 20px reads as a slide-in and
 * draws attention away from the number itself.
 */
export const enterUp: Variants = {
  hidden: { opacity: 0, y: 4 },
  shown: { opacity: 1, y: 0, transition: transition.base },
}

/**
 * A short stagger for a list of supporting facts under a headline. Capped
 * deliberately: past ~6 children the last item's delay becomes a visible wait,
 * so callers with long lists should animate the container, not the rows.
 */
export function staggerChildren(count: number): Variants {
  const step = count > 6 ? 0.02 : 0.04
  return {
    hidden: {},
    shown: { transition: { staggerChildren: step, delayChildren: 0.05 } },
  }
}

/**
 * A chart line drawing itself. Consumers animate `pathLength` from 0 to 1;
 * SVG needs `pathLength={1}` on the path for this to be unit-independent.
 * Fires once per dataset -- never on every re-render, which turns a chart into
 * a strobe as the user changes a filter.
 */
export const drawPath: Variants = {
  hidden: { pathLength: 0, opacity: 0 },
  shown: {
    pathLength: 1,
    opacity: 1,
    transition: { pathLength: { duration: DURATION.slow, ease: EASE }, opacity: { duration: 0.1 } },
  },
}

/**
 * An area band under a drawn line: fades up rather than wiping.
 *
 * Note for consumers: if you also need a resting opacity below 1 (a shaded
 * band usually does), express it in the variant, not in a `style` alongside
 * it. An inline `style={{opacity}}` wins the cascade over an opacity variant
 * and the element silently never appears -- which is exactly what happened to
 * the walk-away curve's gap band on its first build.
 */
export const fadeBand: Variants = {
  hidden: { opacity: 0 },
  shown: { opacity: 1, transition: { duration: DURATION.slow, ease: EASE, delay: 0.1 } },
}
