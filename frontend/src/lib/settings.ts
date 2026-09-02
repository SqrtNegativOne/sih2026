import type { Currency } from '@/lib/format'
import type { VesselClass } from '@/lib/types'

/**
 * Desk preferences.
 *
 * Settings was one of four controls in the top bar that said "not implemented"
 * — honest, but a control that announces its own absence is worse than no
 * control, and there is a real preference here worth persisting: every quote
 * starts from the same six fields, and retyping them on each run is the most
 * repeated friction in the product.
 *
 * Deliberately NOT stored on a server. There is no account system yet, so a
 * per-browser preference is the honest scope — and framing it that way now
 * means the eventual move to per-user rows is a migration, not a redesign:
 * `DeskSettings` is already the exact shape a `user_settings` row would hold.
 *
 * Nothing here changes what any figure means. These are starting values for a
 * form the user then edits and submits; the quote is computed from what was
 * actually submitted, never from a stored preference.
 */

export interface DeskSettings {
  /** Display currency. Every figure is COMPUTED in USD -- freight is quoted
   *  and settled in dollars -- so this is a presentation conversion applied at
   *  render time from the real FRED USD/INR observation, never a second stored
   *  copy of a number. If no real rate covers the pricing date the desk stays
   *  in dollars regardless of this setting. */
  currency: Currency
  /** Prefilled on a new quote. Empty means "no default, pick one". */
  defaultOriginPort: string
  defaultDestPort: string
  defaultCommodity: string
  defaultCargoVolumeDwt: string
  defaultContractTermDays: string
  /** 0 = risk-neutral, 1 = fully risk-averse. Same meaning as the drawer's slider. */
  defaultRiskTolerance: string
  /** Laycan window, as an offset from the pricing date. */
  laycanLeadDays: number
  laycanWindowDays: number
  /** Show the honesty chips (provenance, sample sizes) inline. Off hides them
   *  behind their tooltips only -- it never removes a caveat from the page. */
  showProvenanceChips: boolean
}

export const DEFAULT_SETTINGS: DeskSettings = {
  currency: 'USD',
  defaultOriginPort: '',
  defaultDestPort: '',
  defaultCommodity: 'Thermal Coal',
  defaultCargoVolumeDwt: '75000',
  defaultContractTermDays: '30',
  defaultRiskTolerance: '0',
  laycanLeadDays: 14,
  laycanWindowDays: 7,
  showProvenanceChips: true,
}

export const VESSEL_CLASSES: VesselClass[] = ['Capesize', 'Panamax', 'Supramax', 'Handysize']

const STORAGE_KEY = 'desk-settings'

/**
 * Reads stored settings, merged over the defaults.
 *
 * Merging rather than replacing matters: a stored blob written by an older
 * build will be missing any field added since, and spreading it over the
 * defaults means a new setting arrives with its default instead of as
 * `undefined` halfway down a form.
 */
export function loadSettings(): DeskSettings {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return { ...DEFAULT_SETTINGS }
    const parsed = JSON.parse(raw) as Partial<DeskSettings>
    return { ...DEFAULT_SETTINGS, ...parsed }
  } catch {
    // Private windows and blocked site-data throw on access rather than
    // returning null, so this has to be a catch, not a null check.
    return { ...DEFAULT_SETTINGS }
  }
}

export function saveSettings(s: DeskSettings): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(s))
  } catch {
    // Persisting is a convenience; failing to persist must not break the
    // form, which has already applied the change in memory.
  }
}

export function clearSettings(): void {
  try {
    localStorage.removeItem(STORAGE_KEY)
  } catch {
    /* nothing to do */
  }
}
