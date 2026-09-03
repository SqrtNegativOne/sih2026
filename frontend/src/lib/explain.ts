import { useSyncExternalStore } from 'react'

/**
 * Explain mode — the desk's missing "Layer 2".
 *
 * The review's structural diagnosis was that this app has Layer 1 (the answer:
 * WAIT, $19,444/day) and Layer 3 (the evidence: fans, tiers, provenance,
 * n_obs), but nothing in between. A SAIL employee reads the headline verdict,
 * understands it, and then hits `contingent_infeasible`, "Index type:
 * RELATIVE" and "mean realised regret $/day" with no idea what any of it is
 * for. Every panel needed one visible sentence answering *what question does
 * this panel answer, and what do I do if the number is bad?*
 *
 * Two things made this non-trivial to just "add a line to every panel":
 *
 *  1. `Panel` already had a `hint` prop, and it already carried a decent
 *     one-line description — but behind a hover tooltip, where a first-time
 *     reader never finds it. Answering "what is this" on hover is not the same
 *     as answering "what do I do about it" in the layout.
 *
 *  2. The Voyage Desk lays its panels out in explicitly sized rows
 *     (`h-[Npx]`), and a permanent extra line in every header would eat into
 *     content on a screen already flagged as tight at 1366x768. A trader who
 *     knows what a walk-away line is should not pay for the explanation
 *     forever.
 *
 * So the text is always in the source, and its visibility is one switch. It
 * defaults ON: the person this was written for is the one who has never seen
 * the desk before, and the person who wants it off is by definition able to
 * find the toggle. The choice persists per browser.
 *
 * Deliberately NOT React context: `Panel` is rendered from ~20 files including
 * a few that mount outside the main shell tree, and a provider that some
 * subtree silently misses fails by showing nothing — the exact failure this is
 * meant to fix. A module-level store read through `useSyncExternalStore` is
 * correct from anywhere, concurrent-safe, and re-renders every subscriber on
 * change.
 */

const STORAGE_KEY = 'desk.explainMode'

function readStored(): boolean {
  // Any of localStorage-disabled, private mode, or a cleared profile throws or
  // returns null here. Default ON is the honest fallback: showing the
  // explanations to someone whose preference we cannot read costs them one
  // click, whereas hiding them costs a first-time reader the whole point.
  try {
    return window.localStorage.getItem(STORAGE_KEY) !== 'off'
  } catch {
    return true
  }
}

let enabled = readStored()
const listeners = new Set<() => void>()

function subscribe(onChange: () => void): () => void {
  listeners.add(onChange)
  return () => listeners.delete(onChange)
}

function getSnapshot(): boolean {
  return enabled
}

export function setExplainMode(next: boolean): void {
  if (next === enabled) return
  enabled = next
  try {
    window.localStorage.setItem(STORAGE_KEY, next ? 'on' : 'off')
  } catch {
    // A browser that will not persist the choice still honours it for this
    // session — the in-memory value above is what every subscriber reads.
  }
  for (const listener of listeners) listener()
}

/** True when panels should render their plain-English "so what" line. */
export function useExplainMode(): boolean {
  // The third argument is the server/prerender snapshot. This app is
  // client-rendered, but passing it keeps the hook correct if it is ever
  // rendered without a `window`.
  return useSyncExternalStore(subscribe, getSnapshot, () => true)
}
