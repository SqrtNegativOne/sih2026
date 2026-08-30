import { Panel } from '@/components/desk/panel'
import { formatUsdCompact } from '@/lib/format'
import { assignRouteColors } from '@/lib/route-colors'
import type { SolverRoute } from '@/lib/types'
import { cn } from '@/lib/utils'
import { useMemo } from 'react'

const STATUS_STYLE: Record<SolverRoute['status'], string> = {
  chosen: 'bg-go-soft text-go',
  considered: 'bg-accent text-accent-foreground',
  rejected: 'bg-risk-soft text-risk',
}

/**
 * The route-exploration list beside the map: one row per routing the solver
 * evaluated. Clicking a row zooms the map to that route.
 */
export function RouteList({
  routes,
  focusId,
  onFocus,
}: {
  routes: SolverRoute[]
  focusId: string | null
  onFocus: (id: string | null) => void
}) {
  const colors = useMemo(() => assignRouteColors(routes), [routes])
  const order = { chosen: 0, considered: 1, rejected: 2 }
  const sorted = [...routes].sort((a, b) => order[a.status] - order[b.status])

  return (
    <Panel
      title="Routes"
      hint="Every routing the solver evaluated. Click one to zoom the map to it."
      meta={`${routes.length}`}
      flush
    >
      <ul className="divide-y divide-border/60">
        {sorted.map((r) => (
          <li key={r.id}>
            <button
              type="button"
              onClick={() => onFocus(focusId === r.id ? null : r.id)}
              className={cn(
                'flex w-full flex-col gap-0.5 px-2 py-1.5 text-left',
                focusId === r.id ? 'bg-accent' : 'hover:bg-surface-2',
              )}
            >
              <div className="flex items-center gap-1.5">
                <span
                  className="h-2 w-2 shrink-0 rounded-full"
                  style={{
                    background:
                      r.status === 'rejected'
                        ? 'var(--risk)'
                        : (colors.get(r.id) ?? 'var(--structure)'),
                  }}
                />
                <span className="flex-1 truncate text-[11px] font-semibold text-foreground">
                  {r.label}
                </span>
                <span
                  className={cn(
                    'rounded-sm px-1 text-[8px] font-bold uppercase tracking-wide',
                    STATUS_STYLE[r.status],
                  )}
                >
                  {r.status}
                </span>
              </div>
              <div className="flex items-center justify-between pl-3.5 text-[10px] text-muted-foreground">
                <span className="truncate">
                  {r.reason ??
                    r.legs
                      .map((l) => l.from_port)
                      .concat(r.legs[r.legs.length - 1]?.to_port ?? '')
                      .join(' → ')}
                </span>
                {r.metric_usd != null && (
                  <span className="desk-num shrink-0 pl-2">
                    {r.metric_label} {formatUsdCompact(r.metric_usd)}
                  </span>
                )}
              </div>
            </button>
          </li>
        ))}
      </ul>
    </Panel>
  )
}
