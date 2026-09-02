import { AnimatePresence, motion } from 'motion/react'
import { X } from 'lucide-react'
import { useEffect, useState } from 'react'
import { resolveTheme, setTheme, type Theme } from '@/lib/theme'
import { saveSettings, type DeskSettings } from '@/lib/settings'
import { cn } from '@/lib/utils'

/**
 * The Settings panel.
 *
 * Applies immediately rather than behind a Save button — every field here is a
 * preference with no side effect beyond the next quote's starting values, so a
 * commit step would be ceremony. "Reset to defaults" is the undo.
 *
 * Same modal contract as the quote drawer, for the same reasons: Escape and
 * backdrop both dismiss, the backdrop sits at z-50 so it actually covers the
 * content it blocks (F-58), and nothing opens on mount (F-53).
 */
export function SettingsDrawer({
  open,
  onClose,
  settings,
  onChange,
  fx,
}: {
  open: boolean
  onClose: () => void
  settings: DeskSettings
  onChange: (next: DeskSettings) => void
  /** The real USD/INR observation, so the currency control can state its rate
   *  and disable rupees outright when no real rate exists. */
  fx: { inrPerUsd: number | null; asOf: string | null }
}) {
  const [theme, setThemeState] = useState<Theme>(() =>
    typeof document !== 'undefined' && document.documentElement.classList.contains('light')
      ? 'light'
      : resolveTheme(),
  )

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  function set<K extends keyof DeskSettings>(key: K, value: DeskSettings[K]) {
    const next = { ...settings, [key]: value }
    onChange(next)
    saveSettings(next)
  }

  return (
    <AnimatePresence>
      {open && (
        <>
          <motion.div
            className="fixed inset-0 z-50 bg-black/30"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={onClose}
            aria-hidden="true"
          />
          <motion.aside
            role="dialog"
            aria-modal="true"
            aria-label="Settings"
            className="fixed right-0 top-0 z-50 flex h-full w-90 max-w-[92vw] flex-col border-l border-border bg-surface shadow-raised"
            initial={{ x: '100%' }}
            animate={{ x: 0 }}
            exit={{ x: '100%' }}
            transition={{ type: 'spring', stiffness: 320, damping: 34 }}
          >
            <div className="flex h-10 shrink-0 items-center justify-between border-b border-border bg-navbar px-3 text-navbar-foreground">
              <span className="text-lead font-bold uppercase tracking-wide">Settings</span>
              <button
                type="button"
                onClick={onClose}
                aria-label="Close settings"
                title="Close (Esc)"
                className="rounded-sm p-1 transition-colors hover:bg-white/10 focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-white"
              >
                <X className="h-4 w-4" aria-hidden="true" />
              </button>
            </div>

            <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-3">
              <Section
                title="Currency & numbers"
                note="Every figure is computed in US dollars, because that is what dry-bulk freight is quoted and settled in. Rupees are a display conversion applied at render time."
              >
                <div
                  role="group"
                  aria-label="Display currency"
                  className="inline-flex overflow-hidden rounded-sm border border-border"
                >
                  {(['USD', 'INR'] as const).map((c) => (
                    <button
                      key={c}
                      type="button"
                      aria-pressed={settings.currency === c}
                      disabled={c === 'INR' && fx.inrPerUsd == null}
                      onClick={() => set('currency', c)}
                      title={
                        c === 'INR' && fx.inrPerUsd == null
                          ? 'No real USD/INR observation covers the current pricing date, so rupees cannot be shown without inventing a rate.'
                          : undefined
                      }
                      className={cn(
                        'cursor-pointer border-r border-border px-3 py-1 text-caption font-semibold transition-colors last:border-r-0',
                        'focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-ring',
                        'disabled:cursor-not-allowed disabled:opacity-45',
                        settings.currency === c
                          ? 'bg-primary text-primary-foreground'
                          : 'bg-surface text-muted-foreground hover:bg-surface-2 hover:text-foreground',
                      )}
                    >
                      {c === 'USD' ? '$ USD' : '₹ INR'}
                    </button>
                  ))}
                </div>
                {/* The rate and its provenance, stated. A conversion the reader
                    cannot audit is worth less than no conversion. */}
                <p className="text-micro leading-relaxed text-muted-foreground">
                  {fx.inrPerUsd != null ? (
                    <>
                      Rate <span className="desk-num text-foreground">₹{fx.inrPerUsd}</span> per USD
                      {fx.asOf ? ` — real FRED DEXINUS observation of ${fx.asOf}.` : '.'} Rupee
                      figures use Indian digit grouping and lakh/crore.
                    </>
                  ) : (
                    <>
                      No real USD/INR observation is available, so rupee display is unavailable —
                      the desk will not invent a rate to satisfy a preference.
                    </>
                  )}
                </p>
              </Section>

              <Section
                title="Appearance"
                note="Dark is the desk's designed look. The choice is remembered on this browser."
              >
                <div
                  role="group"
                  aria-label="Theme"
                  className="inline-flex overflow-hidden rounded-sm border border-border"
                >
                  {(['dark', 'light'] as const).map((t) => (
                    <button
                      key={t}
                      type="button"
                      aria-pressed={theme === t}
                      onClick={() => {
                        setTheme(t)
                        setThemeState(t)
                      }}
                      className={cn(
                        'cursor-pointer border-r border-border px-3 py-1 text-caption font-semibold capitalize transition-colors last:border-r-0',
                        'focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-ring',
                        theme === t
                          ? 'bg-primary text-primary-foreground'
                          : 'bg-surface text-muted-foreground hover:bg-surface-2 hover:text-foreground',
                      )}
                    >
                      {t}
                    </button>
                  ))}
                </div>
              </Section>

            </div>
          </motion.aside>
        </>
      )}
    </AnimatePresence>
  )
}

function Section({
  title,
  note,
  children,
}: {
  title: string
  note?: string
  children: React.ReactNode
}) {
  return (
    <section className="space-y-2">
      <h3 className="text-caption font-bold uppercase tracking-[0.04em] text-primary">{title}</h3>
      {note && <p className="text-micro leading-relaxed text-muted-foreground">{note}</p>}
      {children}
    </section>
  )
}

