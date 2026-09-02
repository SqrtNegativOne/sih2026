/**
 * Theme control.
 *
 * Dark is the product's primary look and the default when nothing else is
 * known. A stored choice always wins; absent one, the OS preference decides;
 * absent that, dark.
 *
 * The class goes on <html> (not <body>) so the pre-paint script in index.html
 * can set it before React mounts -- a theme applied in an effect flashes the
 * wrong palette for a frame, which on this palette pair is a full white-to-
 * near-black flash.
 *
 * Only `.light` is ever added or removed. Dark lives on bare `:root`, so the
 * absence of the class IS the dark theme and there is no state where neither
 * palette is defined.
 */

export type Theme = 'dark' | 'light'

const STORAGE_KEY = 'desk-theme'

/** The stored choice, if the user has made one and storage is readable. */
export function storedTheme(): Theme | null {
  try {
    const v = localStorage.getItem(STORAGE_KEY)
    return v === 'dark' || v === 'light' ? v : null
  } catch {
    // Private windows and blocked site-data both throw on access rather than
    // returning null, so this has to be a try/catch, not a null check.
    return null
  }
}

/** What the OS asks for. Read only for reporting — see resolveTheme. */
export function systemTheme(): Theme {
  return window.matchMedia?.('(prefers-color-scheme: light)').matches ? 'light' : 'dark'
}

/**
 * Dark unless the user has explicitly chosen light. The OS preference does
 * NOT get a vote.
 *
 * This used to fall back to `systemTheme()`, and it meant anyone on a
 * light-mode machine — the majority — opened the product and saw the light
 * theme, having never been shown the one it was designed around. "Dark is the
 * primary experience" and "defer to the OS" are contradictory instructions,
 * and deferring silently won. A product with a deliberate look ships that look
 * first and lets people opt out; the toggle is right there in the top bar.
 */
export function resolveTheme(): Theme {
  return storedTheme() ?? 'dark'
}

export function applyTheme(theme: Theme): void {
  document.documentElement.classList.toggle('light', theme === 'light')
  document.documentElement.style.colorScheme = theme
}

export function setTheme(theme: Theme): void {
  applyTheme(theme)
  try {
    localStorage.setItem(STORAGE_KEY, theme)
  } catch {
    // Persisting is a convenience; failing to persist must not break the
    // toggle itself, which has already applied above.
  }
}

/**
 * Kept as a no-op subscription so callers keep a stable API, but the desk no
 * longer follows the OS at all: dark is the product's look, and a machine
 * flipping to day mode should not repaint a tool someone is mid-decision in.
 * The toggle is the only thing that changes the theme.
 */
export function watchSystemTheme(_onChange: (t: Theme) => void): () => void {
  return () => {}
}
