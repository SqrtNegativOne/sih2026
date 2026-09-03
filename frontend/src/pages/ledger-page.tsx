import { useEffect, useState } from 'react'
import { Panel, PanelError, PanelLoading } from '@/components/desk/panel'
import { StatRow } from '@/components/desk/stat'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { fetchLedgerLive, fetchLedgerPerformance, fetchLedgerReplay, postLedgerOutcome, resetLedgerLive } from '@/lib/api'
import { useAuth } from '@/lib/auth-context'
import { formatNumber, prettyPort } from '@/lib/format'
import type { LedgerLiveEntry, LedgerLiveResponse, LedgerPerformanceResponse, LedgerReplayResponse } from '@/lib/types'
import { cn } from '@/lib/utils'

// ---------------------------------------------------------------------------
// LIVE_DECISION_LEDGER -- real recommendations, forward-only.
// ---------------------------------------------------------------------------

function OutcomeForm({ entry, onRecorded }: { entry: LedgerLiveEntry; onRecorded: () => void }) {
  const [rate, setRate] = useState('')
  const [atDate, setAtDate] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  function submit() {
    const rateNum = Number(rate)
    if (!rateNum || rateNum <= 0 || !atDate) return
    setSubmitting(true)
    setErr(null)
    postLedgerOutcome({ entryId: entry.entry_id, realizedRateUsdPerDay: rateNum, realizedAtDate: atDate })
      .then(onRecorded)
      .catch((e: unknown) => setErr(e instanceof Error ? e.message : 'Failed to record outcome.'))
      .finally(() => setSubmitting(false))
  }

  // Every input here is one of a long column of identical pairs -- one per
  // open recommendation -- so a placeholder alone is not an accessible name
  // and, even where it were read, "realised $/day" repeated 140 times says
  // nothing about WHICH recommendation is being settled. The route and the
  // laycan are what identify a fixture to the person recording it.
  const which = `${prettyPort(entry.origin_port)} to ${prettyPort(entry.dest_port)}, laycan ${entry.laycan_start}`

  return (
    <div className="flex items-center gap-2">
      <Input
        placeholder="realised $/day"
        aria-label={`Realised rate in dollars per day for ${which}`}
        className="h-6 w-28 text-caption"
        value={rate}
        onChange={(e) => setRate(e.target.value)}
      />
      <Input
        type="date"
        aria-label={`Date fixed for ${which}`}
        className="h-6 w-32 text-caption"
        value={atDate}
        onChange={(e) => setAtDate(e.target.value)}
      />
      <Button
        size="sm"
        onClick={submit}
        disabled={submitting || !rate || !atDate}
        aria-label={`Record the outcome for ${which}`}
      >
        {submitting ? 'Recording…' : 'Record'}
      </Button>
      {/* The disabled reason, visible rather than in a native `title`: most
          platforms suppress `title` entirely on a disabled control. */}
      {!rate || !atDate ? (
        <span className="text-micro text-muted-foreground">
          Enter the realised rate and the date it was fixed.
        </span>
      ) : null}
      {err && <span className="text-micro text-risk">{err}</span>}
    </div>
  )
}

function LiveLedgerSection() {
  const [live, setLive] = useState<LedgerLiveResponse | null>(null)
  const [perf, setPerf] = useState<LedgerPerformanceResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [resetting, setResetting] = useState(false)
  // Recording an outcome is the act the chartering-manager role exists for:
  // compute_performance scores this system's own recommendations from these
  // lines, so if anyone who can read a quote can also write an outcome, the
  // performance figures mean nothing. Clearing the ledger is irreversible and
  // sits with the admin. On an open deployment `can` is true for everyone --
  // the server really does accept these, and greying out a control the
  // backend would honour would be the interface lying about its own security.
  const { can } = useAuth()
  const canRecord = can('chartering_manager')
  const canReset = can('admin')

  function reload() {
    Promise.all([fetchLedgerLive(), fetchLedgerPerformance()])
      .then(([l, p]) => {
        setLive(l)
        setPerf(p)
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : 'Failed to load the live ledger.'))
  }

  useEffect(reload, [])

  // F-38: every real quote auto-records here by design, so ordinary use or
  // testing had no way to start clean before a demo -- this clears the
  // whole log at once (never a selective per-entry deletion, see the
  // backend's own DELETE /ledger/live docstring for why that stays safe).
  function handleReset() {
    if (!window.confirm(`Clear all ${live?.total ?? 0} ledger entries? This cannot be undone.`)) return
    setResetting(true)
    setError(null)
    resetLedgerLive()
      .then(reload)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : 'Failed to reset the ledger.'))
      .finally(() => setResetting(false))
  }

  return (
    <Panel
      title="Live Decision Ledger"
        soWhat={'Every recommendation this system has made, kept so it can be marked right or wrong later. This is how you check whether to trust it — a model that will not record its own calls cannot be held to them.'}
      meta="Real recommendations this system made, forward-only — never seeded or historical"
      hint="Every real /quote call appends an entry here automatically. Starts empty and fills forward -- nothing here is seeded or historical."
      actions={
        <>
          <Badge variant="secondary" className="text-micro">real, forward-only</Badge>
          {live && live.total > 0 && canReset && (
            <Button
              variant="danger"
              size="xs"
              onClick={handleReset}
              disabled={resetting}
              title="Clear every entry -- for starting a clean demo, not for hiding unfavourable results (it's all-or-nothing)"
            >
              {resetting ? 'Clearing…' : 'Clear ledger'}
            </Button>
          )}
        </>
      }
    >
      {error && <p className="p-2 text-body text-risk">{error}</p>}
      {perf && (
        <div className="grid grid-cols-2 gap-x-4 border-b border-border p-2 md:grid-cols-4">
          <StatRow label="Entries" value={perf.n_entries_total} />
          <StatRow label="Pending" value={perf.n_pending} tone={perf.n_pending > 0 ? 'wait' : 'plain'} />
          <StatRow label="Scored" value={perf.n_scored} />
          <StatRow
            label="Mean regret $/day"
            value={perf.mean_realized_regret_usd_per_day != null ? formatNumber(perf.mean_realized_regret_usd_per_day, 2) : '—'}
            tone={perf.mean_realized_regret_usd_per_day != null && perf.mean_realized_regret_usd_per_day > 0 ? 'risk' : 'go'}
          />
          <StatRow label="Lock accuracy" value={perf.lock_accuracy != null ? `${formatNumber(perf.lock_accuracy * 100, 0)}%` : '—'} />
          <StatRow
            label="vs always-lock $/day"
            value={perf.mean_savings_vs_always_lock_usd_per_day != null ? formatNumber(perf.mean_savings_vs_always_lock_usd_per_day, 2) : '—'}
          />
          <StatRow
            label="vs always-wait $/day"
            value={perf.mean_savings_vs_always_wait_usd_per_day != null ? formatNumber(perf.mean_savings_vs_always_wait_usd_per_day, 2) : '—'}
          />
        </div>
      )}
      {live && live.total === 0 && (
        <p className="p-3 text-body text-muted-foreground">
          No real recommendations recorded yet. This ledger fills forward as the system is used — it is never
          seeded with historical or example entries.
        </p>
      )}
      {live && live.total > 0 && (
        <table className="desk-table w-full">
          <thead>
            <tr>
              <th>When</th>
              <th>Route</th>
              <th>Class</th>
              <th className="text-right">Quote $/day</th>
              <th className="text-right">Risk tol.</th>
              <th>Action</th>
              <th>Status</th>
              <th>Outcome</th>
            </tr>
          </thead>
          <tbody>
            {live.entries.map((e) => (
              <tr key={e.entry_id}>
                <td className="text-caption text-muted-foreground">{e.decision_timestamp.slice(0, 16)}</td>
                <td className="text-caption">
                  {prettyPort(e.origin_port)} → {prettyPort(e.dest_port)}
                </td>
                <td>{e.target_vessel_class}</td>
                <td className="desk-num text-right">{formatNumber(e.today_quote_usd_per_day)}</td>
                <td className="desk-num text-right text-muted-foreground">{formatNumber(e.risk_tolerance, 2)}</td>
                <td>
                  <Badge variant={e.lock_action === 'LOCK' ? 'secondary' : 'outline'} className="text-micro">
                    {e.lock_action}
                  </Badge>
                </td>
                <td>
                  <span className={cn('text-caption', e.status === 'pending' ? 'text-wait' : 'text-go')}>{e.status}</span>
                </td>
                <td>
                  {e.outcome ? (
                    <span className="desk-num text-caption">
                      {formatNumber(e.outcome.realized_rate_usd_per_day)} @ {e.outcome.realized_at_date}
                    </span>
                  ) : canRecord ? (
                    <OutcomeForm entry={e} onRecorded={reload} />
                  ) : (
                    // Not disabled inputs: a form you can fill in and cannot
                    // submit wastes someone's time and reads as a bug. The
                    // honest thing is to say who can do this and why the role
                    // exists at all.
                    <span className="text-micro text-muted-foreground">
                      pending — a chartering manager records the outcome
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Panel>
  )
}

// ---------------------------------------------------------------------------
// HISTORICAL_MODEL_REPLAY -- retrospective, clearly separate.
// ---------------------------------------------------------------------------

function ReplaySection() {
  const [replay, setReplay] = useState<LedgerReplayResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [requested, setRequested] = useState(false)

  function load() {
    setRequested(true)
    setLoading(true)
    setError(null)
    fetchLedgerReplay()
      .then(setReplay)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : 'Failed to load the replay.'))
      .finally(() => setLoading(false))
  }

  const pooled = replay?.summaries.filter((s) => s.vessel_class === 'ALL') ?? []

  return (
    <Panel
      title="Historical Model Replay"
        soWhat={'What this system would have advised on past dates, scored against what the market actually did. Read the regret figure as the money the advice would have cost or saved per day, versus fixing immediately.'}
      meta="A retrospective backtest — not real decisions this system made"
      actions={<Badge variant="destructive" className="text-micro">retrospective simulation</Badge>}
    >
      <div className="border-b-2 border-wait bg-wait-soft p-2 text-body text-wait">
        <span className="font-bold uppercase tracking-wide">{replay?.label ?? 'retrospective model simulation — not decisions this system actually made'}</span>
        <p className="mt-0.5 text-foreground">
          A real backtest over a frozen historical period, calibrated on a separate slice of data
          it was never scored against — never merged with the Live Decision Ledger's own
          statistics above.
        </p>
      </div>
      {!requested && (
        <div className="flex flex-col items-start gap-2 p-2">
          <p className="text-body text-muted-foreground">
            Runs a real PSO calibration + backtest on the first load (real minutes-scale cost). Cached after that.
          </p>
          <Button variant="primary" size="md" onClick={load}>
            Run replay
          </Button>
        </div>
      )}
      {loading && (
        <div className="p-2">
          <p className="mb-2 text-body text-muted-foreground" role="status" aria-live="polite">
            Calibrating on the validation split and scoring the real frozen test split…
          </p>
          <PanelLoading rows={4} label="Running the replay" />
        </div>
      )}
      {error && <PanelError message={error} onRetry={load} />}
      {replay && (
        <div className="flex flex-col gap-2 p-1">
          <div className="grid grid-cols-2 gap-x-4 md:grid-cols-4">
            <StatRow label="Test rows" value={replay.n_test_rows} />
            <StatRow label="Calibrated risk tolerance" value={formatNumber(replay.calibration.best_risk_tolerance, 3)} />
            <StatRow label="Compute time" value={`${formatNumber(replay.compute_seconds, 1)}s`} />
            {replay.stale && <StatRow label="Status" value="STALE (last-good)" tone="risk" />}
          </div>
          <table className="desk-table w-full">
            <thead>
              <tr>
                <th>Strategy</th>
                <th className="text-right">n</th>
                <th className="text-right">Mean savings $/day</th>
                <th className="text-right">P10</th>
                <th className="text-right">Decision value</th>
                <th className="text-right">Regret</th>
                <th className="text-right">Hit rate</th>
                <th className="text-right">Lock rate</th>
              </tr>
            </thead>
            <tbody>
              {pooled.map((s) => (
                <tr key={s.strategy}>
                  <td className="font-semibold">{s.strategy}</td>
                  <td className="desk-num text-right">{s.n_decisions}</td>
                  <td className="desk-num text-right">{formatNumber(s.savings_mean, 2)}</td>
                  <td className="desk-num text-right text-muted-foreground">{formatNumber(s.savings_p10, 2)}</td>
                  <td className="desk-num text-right">{s.decision_value != null ? formatNumber(s.decision_value, 2) : '—'}</td>
                  <td className="desk-num text-right">{s.regret != null ? formatNumber(s.regret, 2) : '—'}</td>
                  <td className="desk-num text-right">{s.hit_rate != null ? `${formatNumber(s.hit_rate * 100, 0)}%` : '—'}</td>
                  <td className="desk-num text-right">{formatNumber(s.lock_rate * 100, 0)}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  )
}

export function LedgerPage() {
  return (
    // Grid with content-sized rows, not `flex flex-col` -- the same fix the
    // other four secondary pages needed (F-57), arrived at here for the
    // matching reason. A flex item defaults to `flex-shrink: 1`, so once the
    // live ledger had accumulated enough real entries to exceed the viewport,
    // both panels were compressed below their own content instead of the page
    // scrolling: measured at +53px and +12px of hidden overflow, with the
    // oldest rows and the replay panel's footer simply not reachable. Auto
    // rows size to content and the container scrolls, which is what a growing
    // append-only log needs.
    <div
      className="grid h-full auto-rows-min content-start gap-2 overflow-auto p-2"
      id="ledger"
    >
      <LiveLedgerSection />
      <ReplaySection />
    </div>
  )
}
