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

  return (
    <div className="flex h-full items-center justify-center">
      <div className="w-full max-w-sm rounded border border-border bg-surface p-3">
        <div className="mb-2 text-[11px] font-bold uppercase tracking-wide text-primary">
          Solving
        </div>
        <ul className="space-y-1">
          {PIPELINE.map(({ key, label }) => {
            const entry = byKey.get(key)
            const state: StageState = entry?.state ?? 'pending'
            return (
              <li key={key} className="flex items-center gap-2 text-[12px]">
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
                  <span className="desk-num text-[10px] text-muted-foreground">
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
