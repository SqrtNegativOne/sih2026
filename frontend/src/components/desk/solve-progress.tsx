import { Check, Loader2 } from 'lucide-react'
import type { ProgressStage } from '@/lib/types'
import { cn } from '@/lib/utils'

const PIPELINE: { key: string; label: string }[] = [
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

export function SolveProgress({ stages }: { stages: ProgressStage[] }) {
  const byKey = new Map<string, { state: StageState; ms: number }>()
  for (const s of stages) {
    const cur = byKey.get(s.key)
    if (s.status === 'start') {
      if (!cur) byKey.set(s.key, { state: 'running', ms: 0 })
    } else {
      byKey.set(s.key, { state: 'done', ms: s.elapsed_ms })
    }
  }

  // A real solve runs eight stages and takes seconds; a bare list of ticks
  // gives no sense of how far through it is. The bar is derived from the same
  // stage events the list already renders — never a timer, never an estimate.
  const doneCount = PIPELINE.filter((p) => byKey.get(p.key)?.state === 'done').length
  const pct = Math.round((doneCount / PIPELINE.length) * 100)

  return (
    <div className="flex h-full items-center justify-center p-4">
      <div
        className="w-full max-w-sm rounded-md border border-border bg-surface shadow-panel"
        role="status"
        aria-live="polite"
        aria-busy={doneCount < PIPELINE.length}
      >
        <div className="border-b border-border bg-surface-2 px-3 py-2">
          <div className="flex items-baseline justify-between">
            <span className="text-caption font-bold uppercase tracking-[0.04em] text-primary">
              Solving
            </span>
            <span className="desk-num text-caption text-muted-foreground">
              {doneCount} / {PIPELINE.length}
            </span>
          </div>
          <div className="mt-2 h-1 overflow-hidden rounded-full bg-border">
            <div
              className="h-full rounded-full bg-primary transition-[width] duration-300"
              style={{ width: `${pct}%` }}
            />
          </div>
        </div>
        <ul className="space-y-1 p-3">
          {PIPELINE.map(({ key, label }) => {
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
