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

/** What the OS asks for. Dark when it has no opinion. */
export function systemTheme(): Theme {
  return window.matchMedia?.('(prefers-color-scheme: light)').matches ? 'light' : 'dark'
}

export function resolveTheme(): Theme {
  return storedTheme() ?? systemTheme()
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
 * Follow the OS while the user has not chosen explicitly. Returns an
 * unsubscribe. Once a choice is stored this becomes inert, which is the
 * behaviour people expect: an explicit toggle should not be silently undone
 * when the machine flips to night mode.
 */
export function watchSystemTheme(onChange: (t: Theme) => void): () => void {
  const mq = window.matchMedia?.('(prefers-color-scheme: light)')
  if (!mq) return () => {}
  const handler = () => {
    if (storedTheme() !== null) return
    const next = systemTheme()
    applyTheme(next)
    onChange(next)
  }
  mq.addEventListener('change', handler)
  return () => mq.removeEventListener('change', handler)
}
