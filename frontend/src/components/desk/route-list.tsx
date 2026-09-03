import { Panel } from '@/components/desk/panel'

import { assignRouteColors } from '@/lib/route-colors'
import type { SolverRoute } from '@/lib/types'
import { cn } from '@/lib/utils'
import { useMemo } from 'react'
import { useMoney } from '@/lib/money-context'

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
  const { moneyCompact } = useMoney()
  const colors = useMemo(() => assignRouteColors(routes), [routes])
  const order = { chosen: 0, considered: 1, rejected: 2 }
  const sorted = [...routes].sort((a, b) => order[a.status] - order[b.status])

  return (
    <Panel
      className="h-full"
      title="Routes"
      soWhat={'Every sea route this cargo could take, and what each costs. If the cheapest route is one you cannot actually use — a canal that is closed, a port your shipper will not call — pick the next one and re-price.'}
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
                'flex w-full flex-col gap-0.5 px-2 py-2 text-left',
                focusId === r.id ? 'bg-accent' : 'hover:bg-surface-2',
              )}
            >
              <div className="flex items-center gap-2">
                <span
                  className="h-2 w-2 shrink-0 rounded-full"
                  style={{
                    background:
                      r.status === 'rejected'
                        ? 'var(--risk)'
                        : (colors.get(r.id) ?? 'var(--structure)'),
                  }}
                />
                <span className="flex-1 truncate text-body font-semibold text-foreground">
                  {r.label}
                </span>
                <span
                  className={cn(
                    'rounded-sm px-1 text-micro font-bold uppercase tracking-wide',
                    STATUS_STYLE[r.status],
                  )}
                >
                  {r.status}
                </span>
              </div>
              <div className="flex items-center justify-between pl-3.5 text-caption text-muted-foreground">
                <span className="truncate">
                  {r.reason ??
                    r.legs
                      .map((l) => l.from_port)
                      .concat(r.legs[r.legs.length - 1]?.to_port ?? '')
                      .join(' → ')}
                </span>
                {r.metric_usd != null && (
                  <span className="desk-num shrink-0 pl-2">
                    {r.metric_label} {moneyCompact(r.metric_usd)}
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
