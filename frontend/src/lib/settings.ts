import type { Currency } from '@/lib/format'

/**
 * Desk preferences.
 *
 * Settings was one of four controls in the top bar that said "not implemented"
 * — honest, but a control that announces its own absence is worse than no
 * control, and there are real preferences worth persisting: the display
 * currency, which changes every figure on every screen, and the theme.
 *
 * Quote-form defaults were tried here and removed. Prefilling a form from a
 * settings panel reads as configuration rather than as a product, and nobody
 * goes to Settings to change a port they are about to type anyway -- the quote
 * form is the right place to ask for quote inputs.
 *
 * Deliberately NOT stored on a server. There is no account system yet, so a
 * per-browser preference is the honest scope — and framing it that way now
 * means the eventual move to per-user rows is a migration, not a redesign:
 * `DeskSettings` is already the exact shape a `user_settings` row would hold.
 *
 * Nothing here changes what any figure MEANS. Currency is a presentation
 * conversion from a real observed rate; the theme is paint.
 */

export interface DeskSettings {
  /** Display currency. Every figure is COMPUTED in USD -- freight is quoted
   *  and settled in dollars -- so this is a presentation conversion applied at
   *  render time from the real FRED USD/INR observation, never a second stored
   *  copy of a number. If no real rate covers the pricing date the desk stays
   *  in dollars regardless of this setting. */
  currency: Currency
}

export const DEFAULT_SETTINGS: DeskSettings = {
  currency: 'USD',
}

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
