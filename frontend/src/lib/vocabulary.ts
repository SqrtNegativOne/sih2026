/**
 * The desk speaks two registers, and this file draws the line between them.
 *
 * KEEP: the domain vocabulary of dry-bulk chartering -- laycan, ballast,
 * demurrage, COA, DWT, LOA, beam, draft, CII, chokepoint. These are the words
 * the people using this tool actually use, and replacing them with "loading
 * window" or "empty leg" would make the product read as though it were built
 * for someone else. They get a definition on demand, not a substitution.
 *
 * REPLACE: engineering vocabulary that leaked into the interface -- enum
 * constants, percentile shorthand, internal tier codes, endpoint paths, raw
 * field names. A charterer has no reason to know that a value's provenance
 * enum is `MODEL_DERIVED`, and `p50` is a statistics term, not a shipping one.
 *
 * In both cases the precise term stays reachable: replaced labels carry the
 * original in their tooltip, so nothing is lost, it is just no longer the
 * first thing you have to decode.
 */

/** Provenance, as the domain model names it. Mirrors src/data_builders/provenance.py. */
export type ProvenanceKind =
  | 'OBSERVED'
  | 'ESTIMATED'
  | 'MODEL_DERIVED'
  | 'DECLARED'
  | 'INFERRED'

export interface Term {
  /** What the interface shows. */
  label: string
  /** What it means, in a sentence. */
  definition: string
}

/**
 * Provenance in plain English. The chips these feed are a credibility feature,
 * not clutter -- the whole point of this codebase is that you can tell at a
 * glance whether a number was measured or modelled -- so the label is made
 * readable rather than made quieter.
 */
export const PROVENANCE: Record<ProvenanceKind, Term> = {
  OBSERVED: {
    label: 'measured',
    definition: 'Read directly off a real source document or feed. Not computed.',
  },
  ESTIMATED: {
    label: 'estimated',
    definition:
      'A real computation over real data, with a documented method and disclosed uncertainty.',
  },
  MODEL_DERIVED: {
    label: 'modelled',
    definition: 'The output of a fitted or trained model, not a direct observation.',
  },
  DECLARED: {
    label: 'stated',
    definition:
      'Stated as fact by an authoritative source — a port operator publication, or a figure you entered yourself.',
  },
  INFERRED: {
    label: 'inferred',
    definition:
      'A modelled read of observed evidence, not ground truth. Carries a confidence, not a probability.',
  },
}

/**
 * Percentiles, named by what they mean to someone making the decision rather
 * than by their statistical shorthand. The percentile itself stays in the
 * tooltip, because for anyone who does read p90 natively, "High" alone is less
 * precise than what they had.
 */
export const PERCENTILE = {
  p10: {
    label: 'Low',
    definition: 'The 10th percentile — only 1 outcome in 10 is expected to fall below this.',
  },
  p50: {
    label: 'Expected',
    definition: 'The median — half the simulated outcomes fall above this, half below.',
  },
  p90: {
    label: 'High',
    definition: 'The 90th percentile — only 1 outcome in 10 is expected to exceed this.',
  },
} satisfies Record<string, Term>

/**
 * Domain terms. These are NOT replaced in the interface -- they are the right
 * words -- they are simply explained where they appear, so someone new to
 * chartering is not stopped by them.
 */
export const GLOSSARY: Record<string, string> = {
  laycan:
    'The agreed window during which the vessel must arrive and be ready to load. Arrive before it and you wait; arrive after it and the charterer can cancel.',
  ballast:
    'Sailing without cargo — typically the repositioning leg to reach a load port. It burns fuel and earns nothing, so it is charged against the voyage that follows it.',
  laden: 'Sailing with cargo aboard.',
  demurrage:
    'What the charterer pays the owner for time at berth beyond the agreed allowance. A per-day penalty for slow loading or discharge.',
  COA: 'Contract of Affreightment — an agreement to move a stated quantity of cargo over a period, across multiple voyages, rather than fixing one ship for one trip.',
  TC: 'Time Charter — hiring the vessel by the day, with the charterer directing where it goes and paying for fuel.',
  DWT: 'Deadweight tonnes — the total weight a vessel can carry, including cargo, fuel, stores and crew.',
  LOA: 'Length Overall — the vessel’s full length. Ports cap it because a berth is only so long.',
  beam: 'The vessel’s width at its widest point. Capped by berth and lock width.',
  draft:
    'How deep the hull sits below the waterline. A vessel drawing more than the port’s permissible draft cannot enter, loaded.',
  chokepoint:
    'A narrow passage most traffic on a route must transit — Suez, Hormuz, Malacca, Bab-el-Mandeb, the Cape. Disruption there affects every voyage using it.',
  CII: 'Carbon Intensity Indicator — the IMO’s measure of a vessel’s CO₂ per tonne-mile, graded A to E. A poor grade can force a corrective action plan.',
  spot: 'The open market for a single voyage at today’s price, with no forward commitment.',
  ceiling:
    'The highest rate at which locking still beats waiting. Above it, the model says wait; at or below it, lock.',
  basis:
    'The adjustment from a class-wide benchmark rate to a specific route, where enough real route evidence exists to justify one.',
}
