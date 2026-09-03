import { Check, Loader2 } from 'lucide-react'
import type { ProgressStage } from '@/lib/types'
import { cn } from '@/lib/utils'

/** The quote pipeline's eight steps, in the order the solver runs them. */
export const QUOTE_PIPELINE: { key: string; label: string }[] = [
  { key: 'forecast_load', label: 'Loading real forecast' },
  { key: 'lock_wait', label: 'Lock / wait decision' },
  { key: 'voyage_schedule', label: 'Voyage scheduling' },
  { key: 'repositioning', label: 'Repositioning idle vessels' },
  { key: 'monte_carlo', label: 'Savings simulation' },
  { key: 'risk', label: 'Risk assessment' },
  { key: 'fleet_mix', label: 'Fleet-mix frontier' },
  { key: 'explanations', label: 'Building explanations' },
]

type StageState = 'pending' | 'running' | 'done'

/**
 * The live checklist for any streamed computation on this desk.
 *
 * Two callers, two shapes. A quote runs a KNOWN eight steps every time, so it
 * passes nothing and gets the fixed list below with real N/8 progress. The
 * fragility sweep does not: `fragility.engine` skips a variable outright when
 * the inputs make it meaningless (there is no vessel-draft flip point to find
 * when the quote carries no vessel), so the set of steps is not known until
 * the engine has run them.
 *
 * That difference is why `pipeline` is optional rather than always supplied.
 * Given a fixed list, this renders the un-started steps greyed as "pending",
 * which is a promise that they WILL run — true for a quote, false for a sweep.
 * Left out, the list instead grows as the engine announces each step, and the
 * header counts what has finished without claiming a total it cannot know.
 * Showing a caller a step that never runs, or a denominator that turns out to
 * be wrong, is the kind of small lie this desk does not tell.
 */
export function SolveProgress({
  stages,
  pipeline,
  title = 'Solving',
}: {
  stages: ProgressStage[]
  /** Omit when the set of steps is only known as they happen. */
  pipeline?: { key: string; label: string }[]
  title?: string
}) {
  const byKey = new Map<string, { state: StageState; ms: number }>()
  for (const s of stages) {
    const cur = byKey.get(s.key)
    if (s.status === 'start') {
      if (!cur) byKey.set(s.key, { state: 'running', ms: 0 })
    } else {
      byKey.set(s.key, { state: 'done', ms: s.elapsed_ms })
    }
  }

  // Without a fixed pipeline, the rows ARE the stages seen so far, in the
  // order they first arrived -- deduplicated, because every step emits a
  // start and a done under the same key.
  const dynamicRows: { key: string; label: string }[] = []
  if (!pipeline) {
    const seen = new Set<string>()
    for (const s of stages) {
      if (seen.has(s.key)) continue
      seen.add(s.key)
      dynamicRows.push({ key: s.key, label: s.label })
    }
  }
  const rows = pipeline ?? dynamicRows

  // A real solve takes seconds; a bare list of ticks gives no sense of how far
  // through it is. Both readings below come from the same stage events the
  // list renders — never a timer, never an estimate.
  const doneCount = rows.filter((p) => byKey.get(p.key)?.state === 'done').length
  // An indeterminate bar when the total is unknown, rather than a percentage
  // computed against a denominator that is still growing — which would run
  // backwards on screen as new steps arrived.
  const pct = pipeline ? Math.round((doneCount / Math.max(pipeline.length, 1)) * 100) : null

  return (
    <div className="flex h-full items-center justify-center p-4">
      <div
        className="w-full max-w-sm rounded-md border border-border bg-surface shadow-panel"
        role="status"
        aria-live="polite"
        aria-busy={pipeline ? doneCount < pipeline.length : true}
      >
        <div className="border-b border-border bg-surface-2 px-3 py-2">
          <div className="flex items-baseline justify-between">
            <span className="text-caption font-bold uppercase tracking-[0.04em] text-primary">
              {title}
            </span>
            <span className="desk-num text-caption text-muted-foreground">
              {pipeline ? `${doneCount} / ${pipeline.length}` : `${doneCount} done`}
            </span>
          </div>
          <div className="mt-2 h-1 overflow-hidden rounded-full bg-border">
            {pct != null ? (
              <div
                className="h-full rounded-full bg-primary transition-[width] duration-300"
                style={{ width: `${pct}%` }}
              />
            ) : (
              // Indeterminate: a sliding sliver, because there is no honest
              // percentage to draw when the number of steps is not yet known.
              <div className="h-full w-1/3 animate-[indeterminate_1.4s_ease-in-out_infinite] rounded-full bg-primary" />
            )}
          </div>
        </div>
        <ul className="space-y-1 p-3">
          {rows.map(({ key, label }) => {
            const entry = byKey.get(key)
            const state: StageState = entry?.state ?? 'pending'
            return (
              <li key={key} className="flex items-center gap-2 text-lead">
                <span className="flex h-4 w-4 shrink-0 items-center justify-center">
                  {state === 'done' && <Check className="h-3.5 w-3.5 text-go" />}
                  {state === 'running' && (
                    <Loader2 className="h-3.5 w-3.5 animate-spin text-primary" />
                  )}
                  {state === 'pending' && (
                    <span className="h-1.5 w-1.5 rounded-full bg-border" />
                  )}
                </span>
                <span
                  className={cn(
                    'flex-1',
                    state === 'pending' ? 'text-muted-foreground' : 'text-foreground',
                  )}
                >
                  {label}
                </span>
                {state === 'done' && entry != null && (
                  <span className="desk-num text-caption text-muted-foreground">
                    {entry.ms < 1 ? '<1' : Math.round(entry.ms)} ms
                  </span>
                )}
              </li>
            )
          })}
        </ul>
      </div>
    </div>
  )
}
