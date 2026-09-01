import { OctagonX } from 'lucide-react'
import type { StructuralProblem } from '@/lib/types'

const FIELD_LABEL: Record<StructuralProblem['field'], string> = {
  cargo_volume_dwt: 'Cargo volume',
  laycan: 'Laycan window',
  route: 'Route',
  contract_term_days: 'Contract term',
}

/**
 * Shown in place of everything else when the request cannot be solved at all.
 * No forecast, no fleet mix, no savings render behind it.
 */
export function InfeasibilityPanel({ problems }: { problems: StructuralProblem[] }) {
  return (
    <div className="mx-auto max-w-2xl rounded border border-risk bg-surface">
      <div className="flex items-center gap-2 border-b border-risk bg-risk-soft px-3 py-2">
        <OctagonX className="h-4 w-4 text-risk" />
        <span className="text-lead font-bold uppercase tracking-wide text-risk">
          This request cannot be solved
        </span>
      </div>
      <ul className="divide-y divide-border">
        {problems.map((p, i) => (
          <li key={i} className="px-3 py-2">
            <div className="text-caption font-semibold uppercase tracking-wide text-muted-foreground">
              {FIELD_LABEL[p.field]}
            </div>
            <p className="mt-0.5 text-lead text-foreground">{p.message}</p>
            <div className="mt-2 grid grid-cols-2 gap-2">
              <div className="rounded bg-surface-2 px-2 py-1">
                <div className="text-micro font-semibold uppercase tracking-wide text-muted-foreground">
                  You asked for
                </div>
                <div className="desk-num text-body">{p.observed}</div>
              </div>
              <div className="rounded bg-surface-2 px-2 py-1">
                <div className="text-micro font-semibold uppercase tracking-wide text-muted-foreground">
                  Feasible limit
                </div>
                <div className="desk-num text-body">{p.limit}</div>
              </div>
            </div>
          </li>
        ))}
      </ul>
    </div>
  )
}
