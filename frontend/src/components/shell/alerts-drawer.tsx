import { Bell, Plus, RefreshCw, Trash2, X } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Field } from '@/components/ui/field'
import {
  createWatch,
  deleteWatch,
  evaluateAlerts,
  fetchAlerts,
  markAlertsRead,
  setWatchActive,
} from '@/lib/api'
import { useAuth } from '@/lib/auth-context'
import { formatIsoShort } from '@/lib/format'
import type { AlertsResponse, VesselClass, WatchKind } from '@/lib/types'
import { cn } from '@/lib/utils'

/**
 * Standing alerts: what you asked to be told about, and what has happened.
 *
 * The top bar carried a notification bell once. It was removed in F-79 with an
 * explicit note — *alerts need somewhere to persist and someone to notify, and
 * until then the icon promises a capability that does not exist anywhere in
 * the stack*. Both now exist, so the bell is back with the capability behind
 * it rather than in place of it.
 *
 * One thing this panel is careful never to imply: nothing here **delivers**
 * anything. No email, no SMS, no push. Firings are recorded and shown when you
 * open the desk. The backend reports `delivers_notifications: false` and this
 * says so in plain words, because a bell that looks like it will reach you
 * when you are not looking is the same broken promise in a new shape.
 */

const inputCls =
  'h-8 w-full rounded-sm border border-input bg-surface px-2 text-body text-foreground ' +
  'transition-colors placeholder:text-muted-foreground/70 hover:border-muted-foreground/60 ' +
  'focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/40'

const CLASSES: VesselClass[] = ['Capesize', 'Panamax', 'Supramax', 'Handysize']

const KIND_LABEL: Record<WatchKind, string> = {
  rate_crosses: 'Rate crosses a level',
  rate_moves: 'Rate moves by a percentage',
  outcome_overdue: 'Ledger entries left unsettled',
}

export function AlertsDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { can } = useAuth()
  const canManage = can('chartering_manager')

  const [data, setData] = useState<AlertsResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [adding, setAdding] = useState(false)

  const [kind, setKind] = useState<WatchKind>('rate_crosses')
  const [label, setLabel] = useState('')
  const [vesselClass, setVesselClass] = useState<VesselClass>('Supramax')
  const [threshold, setThreshold] = useState('20000')
  const [direction, setDirection] = useState<'above' | 'below'>('below')
  const [movePct, setMovePct] = useState('5')
  const [windowDays, setWindowDays] = useState('30')
  const [overdueDays, setOverdueDays] = useState('14')

  const reload = useCallback(async () => {
    try {
      setData(await fetchAlerts())
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load alerts.')
    }
  }, [])

  useEffect(() => {
    if (!open) return
    void reload()
    // Opening the drawer is reading them, so the bell clears. The firings
    // stay — clearing the count is not deleting the record.
    void markAlertsRead()
  }, [open, reload])

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!open) return null

  async function act(fn: () => Promise<unknown>) {
    setBusy(true)
    setError(null)
    try {
      await fn()
      await reload()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'That was refused.')
    } finally {
      setBusy(false)
    }
  }

  const kindHelp = new Map((data?.kinds ?? []).map((k) => [k.value, k.description]))
  const watchById = new Map((data?.watches ?? []).map((w) => [w.watch_id, w]))

  return (
    <>
      <div className="fixed inset-0 z-40 bg-black/40" onClick={onClose} aria-hidden="true" />
      <aside
        role="dialog"
        aria-modal="true"
        aria-label="Alerts"
        className="fixed right-0 top-0 z-50 flex h-full w-full max-w-lg flex-col border-l border-border bg-surface shadow-raised"
      >
        <header className="flex h-10 shrink-0 items-center justify-between border-b border-border px-3">
          <h2 className="flex items-center gap-2 text-lead font-bold tracking-tight text-foreground">
            <Bell className="h-4 w-4 text-primary" aria-hidden="true" />
            Alerts
          </h2>
          <div className="flex items-center gap-1">
            {canManage && (
              <Button
                size="xs"
                disabled={busy}
                onClick={() => void act(evaluateAlerts)}
                title="Evaluate every active watch now instead of waiting for the next pass."
              >
                <RefreshCw className="h-3 w-3" aria-hidden="true" />
                Check now
              </Button>
            )}
            <button
              type="button"
              onClick={onClose}
              aria-label="Close alerts"
              className="inline-flex h-6 w-6 cursor-pointer items-center justify-center rounded-sm text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
            >
              <X className="h-4 w-4" aria-hidden="true" />
            </button>
          </div>
        </header>

        <div className="min-h-0 flex-1 space-y-4 overflow-auto p-3">
          {error && (
            <p role="alert" className="panel-note border-risk/40 text-risk">
              {error}
            </p>
          )}

          {/* The one promise this panel must never make. */}
          <p className="panel-note">
            Alerts are recorded and shown here. Nothing is sent — no email, no message, no push.
            {data?.evaluation_interval_seconds
              ? ` Watches are checked about every ${Math.round(data.evaluation_interval_seconds / 60)} minutes; the market data behind them only changes when a harvester is run.`
              : ' Evaluation is driven externally on this deployment.'}
          </p>

          <section className="space-y-2">
            <h3 className="stat-label">What has happened</h3>
            {data && data.firings.length === 0 ? (
              <div className="panel-state">
                <p className="panel-state-title">Nothing has fired</p>
                <p className="panel-state-hint">
                  An empty list is the normal, healthy result — it means every watched condition is
                  where it was last time.
                </p>
              </div>
            ) : (
              <ul className="divide-y divide-border/60">
                {data?.firings.map((f) => (
                  <li key={f.firing_id} className="px-1 py-2">
                    <p className="text-body leading-relaxed text-foreground">{f.message}</p>
                    <p className="mt-0.5 text-micro text-muted-foreground">
                      {watchById.get(f.watch_id)?.label ?? 'a deleted watch'}
                      {' · noticed '}
                      {formatIsoShort(f.fired_at.slice(0, 10))}
                      {/* Both dates, never conflated: a rate published Friday
                          and noticed Monday fired on Monday about Friday's
                          number. */}
                      {f.observed_on && f.observed_on !== f.fired_at.slice(0, 10) && (
                        <> · observed {formatIsoShort(f.observed_on)}</>
                      )}
                    </p>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className="space-y-2 border-t border-border pt-3">
            <div className="flex items-center justify-between">
              <h3 className="stat-label">What you are watching</h3>
              {canManage && (
                <Button size="xs" onClick={() => setAdding((v) => !v)}>
                  <Plus className="h-3 w-3" aria-hidden="true" />
                  {adding ? 'Cancel' : 'Add a watch'}
                </Button>
              )}
            </div>

            {data && data.watches.length === 0 && !adding && (
              <p className="text-caption leading-relaxed text-muted-foreground">
                No watches yet.
                {canManage
                  ? ' Add one to be told when a published rate crosses a level, moves sharply, or when ledger entries go unsettled.'
                  : ' A chartering manager can add one.'}
              </p>
            )}

            <ul className="divide-y divide-border/60">
              {data?.watches.map((w) => (
                <li key={w.watch_id} className="flex items-start gap-2 px-1 py-2">
                  <div className="min-w-0 flex-1">
                    <p
                      className={cn(
                        'text-body font-semibold',
                        w.is_active ? 'text-foreground' : 'text-muted-foreground line-through',
                      )}
                    >
                      {w.label}
                    </p>
                    <p className="text-micro leading-relaxed text-muted-foreground">
                      {describe(w.kind, w)}
                      {w.created_by && ` · added by ${w.created_by}`}
                      {w.last_evaluated_at
                        ? ` · last checked ${formatIsoShort(w.last_evaluated_at.slice(0, 10))}`
                        : ' · not checked yet'}
                    </p>
                  </div>
                  {canManage && (
                    <div className="flex shrink-0 items-center gap-1">
                      <Button
                        size="xs"
                        disabled={busy}
                        onClick={() => void act(() => setWatchActive(w.watch_id, !w.is_active))}
                      >
                        {w.is_active ? 'Pause' : 'Resume'}
                      </Button>
                      <Button
                        size="icon"
                        variant="danger"
                        disabled={busy}
                        aria-label={`Delete the watch ${w.label}`}
                        onClick={() => void act(() => deleteWatch(w.watch_id))}
                      >
                        <Trash2 className="h-3 w-3" aria-hidden="true" />
                      </Button>
                    </div>
                  )}
                </li>
              ))}
            </ul>

            {adding && canManage && (
              <div className="space-y-2 rounded-sm border border-border bg-surface-2 p-2">
                <Field label="What to watch" hint={kindHelp.get(kind)}>
                  <select
                    value={kind}
                    onChange={(e) => setKind(e.target.value as WatchKind)}
                    className={cn(inputCls, 'cursor-pointer')}
                  >
                    {(Object.keys(KIND_LABEL) as WatchKind[]).map((k) => (
                      <option key={k} value={k}>
                        {KIND_LABEL[k]}
                      </option>
                    ))}
                  </select>
                </Field>

                <Field label="Call it" hint="How you will recognise it in this list.">
                  <input
                    value={label}
                    onChange={(e) => setLabel(e.target.value)}
                    placeholder="e.g. Supramax softening"
                    className={inputCls}
                  />
                </Field>

                {kind !== 'outcome_overdue' && (
                  <Field label="Vessel class">
                    <select
                      value={vesselClass}
                      onChange={(e) => setVesselClass(e.target.value as VesselClass)}
                      className={cn(inputCls, 'cursor-pointer')}
                    >
                      {CLASSES.map((c) => (
                        <option key={c} value={c}>
                          {c}
                        </option>
                      ))}
                    </select>
                  </Field>
                )}

                {kind === 'rate_crosses' && (
                  <div className="grid grid-cols-2 gap-2">
                    <Field label="Direction">
                      <select
                        value={direction}
                        onChange={(e) => setDirection(e.target.value as 'above' | 'below')}
                        className={cn(inputCls, 'cursor-pointer')}
                      >
                        <option value="below">falls below</option>
                        <option value="above">rises above</option>
                      </select>
                    </Field>
                    <Field label="USD per day">
                      <input
                        type="number"
                        value={threshold}
                        onChange={(e) => setThreshold(e.target.value)}
                        className={cn(inputCls, 'font-mono')}
                      />
                    </Field>
                  </div>
                )}

                {kind === 'rate_moves' && (
                  <div className="grid grid-cols-2 gap-2">
                    <Field label="Move of at least (%)">
                      <input
                        type="number"
                        value={movePct}
                        onChange={(e) => setMovePct(e.target.value)}
                        className={cn(inputCls, 'font-mono')}
                      />
                    </Field>
                    <Field label="Over (days)">
                      <input
                        type="number"
                        value={windowDays}
                        onChange={(e) => setWindowDays(e.target.value)}
                        className={cn(inputCls, 'font-mono')}
                      />
                    </Field>
                  </div>
                )}

                {kind === 'outcome_overdue' && (
                  <Field
                    label="Unsettled for more than (days)"
                    hint="Performance is scored only over ledger entries with a reported outcome, so entries nobody settles quietly shrink the evidence base."
                  >
                    <input
                      type="number"
                      value={overdueDays}
                      onChange={(e) => setOverdueDays(e.target.value)}
                      className={cn(inputCls, 'w-32 font-mono')}
                    />
                  </Field>
                )}

                <Button
                  variant="primary"
                  size="md"
                  disabled={busy || !label.trim()}
                  onClick={() =>
                    void act(async () => {
                      await createWatch({
                        kind,
                        label: label.trim(),
                        vessel_class: kind === 'outcome_overdue' ? null : vesselClass,
                        threshold_usd_per_day:
                          kind === 'rate_crosses' ? Number(threshold) : null,
                        direction: kind === 'rate_crosses' ? direction : null,
                        move_pct: kind === 'rate_moves' ? Number(movePct) : null,
                        window_days: kind === 'rate_moves' ? Number(windowDays) : null,
                        overdue_days:
                          kind === 'outcome_overdue' ? Number(overdueDays) : null,
                      })
                      setLabel('')
                      setAdding(false)
                    })
                  }
                >
                  Create watch
                </Button>
                <p className="text-micro leading-relaxed text-muted-foreground">
                  A watch fires on the change into its condition, not on every check while it
                  holds — a rate that sits below a level for a fortnight is one piece of news, not
                  fourteen.
                </p>
              </div>
            )}
          </section>
        </div>
      </aside>
    </>
  )
}

/** The watch's own parameters, in words. Reading a list of labels tells you
 *  what someone called each watch; this tells you what it actually does. */
function describe(kind: WatchKind, w: { vessel_class: string | null; threshold_usd_per_day: number | null; direction: string | null; move_pct: number | null; window_days: number | null; overdue_days: number | null }): string {
  if (kind === 'rate_crosses' && w.threshold_usd_per_day != null) {
    const verb = w.direction === 'above' ? 'rises above' : 'falls below'
    return `${w.vessel_class} spot TC average ${verb} $${w.threshold_usd_per_day.toLocaleString('en-US')}/day`
  }
  if (kind === 'rate_moves' && w.move_pct != null) {
    return `${w.vessel_class} spot TC average moves ${w.move_pct}% or more over ${w.window_days} days`
  }
  if (kind === 'outcome_overdue' && w.overdue_days != null) {
    return `Ledger entries unsettled for more than ${w.overdue_days} days`
  }
  return KIND_LABEL[kind]
}
