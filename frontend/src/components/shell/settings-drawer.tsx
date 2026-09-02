import { AnimatePresence, motion } from 'motion/react'
import { RotateCcw, X } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Combobox, type ComboOption } from '@/components/ui/combobox'
import { prettyPort } from '@/lib/format'
import { resolveTheme, setTheme, type Theme } from '@/lib/theme'
import {
  DEFAULT_SETTINGS,
  clearSettings,
  saveSettings,
  type DeskSettings,
} from '@/lib/settings'
import type { PortListing } from '@/lib/types'
import { cn } from '@/lib/utils'

const inputCls =
  'h-7 w-full rounded-sm border border-input bg-surface px-2 text-lead text-foreground ' +
  'transition-colors placeholder:text-muted-foreground/70 hover:border-muted-foreground/60 ' +
  'focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/40'

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
  ports,
  settings,
  onChange,
  fx,
  system,
}: {
  open: boolean
  onClose: () => void
  ports: PortListing[]
  settings: DeskSettings
  onChange: (next: DeskSettings) => void
  /** The real USD/INR observation, so the currency control can state its rate
   *  and disable rupees outright when no real rate exists. */
  fx: { inrPerUsd: number | null; asOf: string | null }
  /** Facts about what is loaded, for the System & data section. */
  system: { latestDate: string | null; portCount: number; satellitePorts: number }
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

  const portOptions = useMemo<ComboOption[]>(
    () => ports.map((p) => ({ value: p.code, label: prettyPort(p.name), hint: p.code })),
    [ports],
  )

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

              <Section
                title="New quote defaults"
                note="Prefilled when you open the quote form. Every field stays editable there — the quote is always computed from what you actually submit."
              >
                <Field label="Origin port">
                  <Combobox
                    value={settings.defaultOriginPort}
                    onChange={(v) => set('defaultOriginPort', v)}
                    options={portOptions}
                    placeholder="No default"
                  />
                </Field>
                <Field label="Destination port">
                  <Combobox
                    value={settings.defaultDestPort}
                    onChange={(v) => set('defaultDestPort', v)}
                    options={portOptions}
                    placeholder="No default"
                  />
                </Field>
                <div className="grid grid-cols-2 gap-2">
                  <Field label="Cargo volume (t)">
                    <input
                      type="number"
                      min={1}
                      value={settings.defaultCargoVolumeDwt}
                      onChange={(e) => set('defaultCargoVolumeDwt', e.target.value)}
                      className={cn(inputCls, 'font-mono')}
                    />
                  </Field>
                  <Field label="Contract term (days)">
                    <input
                      type="number"
                      min={1}
                      value={settings.defaultContractTermDays}
                      onChange={(e) => set('defaultContractTermDays', e.target.value)}
                      className={cn(inputCls, 'font-mono')}
                    />
                  </Field>
                </div>
                <Field label="Commodity">
                  <input
                    value={settings.defaultCommodity}
                    onChange={(e) => set('defaultCommodity', e.target.value)}
                    className={inputCls}
                  />
                </Field>
                <div className="grid grid-cols-2 gap-2">
                  <Field label="Laycan opens in (days)">
                    <input
                      type="number"
                      min={0}
                      value={settings.laycanLeadDays}
                      onChange={(e) => set('laycanLeadDays', Number(e.target.value) || 0)}
                      className={cn(inputCls, 'font-mono')}
                    />
                  </Field>
                  <Field label="Laycan width (days)">
                    <input
                      type="number"
                      min={1}
                      value={settings.laycanWindowDays}
                      onChange={(e) => set('laycanWindowDays', Number(e.target.value) || 1)}
                      className={cn(inputCls, 'font-mono')}
                    />
                  </Field>
                </div>
                <Field label={`Risk tolerance — ${settings.defaultRiskTolerance}`}>
                  <input
                    type="range"
                    min={0}
                    max={1}
                    step={0.1}
                    value={settings.defaultRiskTolerance}
                    onChange={(e) => set('defaultRiskTolerance', e.target.value)}
                    className="h-1 w-full cursor-pointer appearance-none rounded bg-muted accent-primary"
                  />
                  <div className="flex justify-between text-micro uppercase tracking-wide text-muted-foreground">
                    <span>Risk-neutral</span>
                    <span>Risk-averse</span>
                  </div>
                </Field>
              </Section>

              <Section
                title="System & data"
                note="What this desk is actually running on. Stated rather than implied, because the honest answer to 'how much of this is real?' is a list."
              >
                <dl className="space-y-1">
                  {[
                    ['Market data through', system.latestDate ?? '—'],
                    ['Ports priced', String(system.portCount)],
                    ['Satellite-covered ports', `${system.satellitePorts} of ${system.portCount}`],
                    ['USD/INR rate', fx.inrPerUsd != null ? `₹${fx.inrPerUsd} (${fx.asOf})` : 'unavailable'],
                  ].map(([k, v]) => (
                    <div key={k} className="stat-row">
                      <dt className="stat-label">{k}</dt>
                      <dd className="stat-value">{v}</dd>
                    </div>
                  ))}
                </dl>
                <p className="text-micro leading-relaxed text-muted-foreground">
                  Satellite vessel counts exist only for ports with a processed Sentinel-1 scene;
                  the panel does not render for the others rather than showing an estimate.
                </p>
              </Section>

              <Section
                title="Storage"
                note="Preferences live in this browser only — there is no account system yet, so nothing here is shared between machines or people."
              >
                <Button
                  variant="danger"
                  size="sm"
                  onClick={() => {
                    clearSettings()
                    onChange({ ...DEFAULT_SETTINGS })
                  }}
                >
                  <RotateCcw className="h-3 w-3" aria-hidden="true" />
                  Reset to defaults
                </Button>
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

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-caption font-semibold uppercase tracking-wide text-muted-foreground">
        {label}
      </span>
      {children}
    </label>
  )
}
