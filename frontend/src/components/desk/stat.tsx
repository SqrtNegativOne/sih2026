import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'

/**
 * The one value-presentation pattern on the desk: label left, value hard right,
 * same baseline. No panel is allowed to invent its own layout for a single
 * figure. `tone` maps onto the semantic colour scale.
 */
export function StatRow({
  label,
  value,
  tone = 'plain',
  className,
}: {
  label: ReactNode
  value: ReactNode
  tone?: 'plain' | 'go' | 'wait' | 'risk' | 'market' | 'muted'
  className?: string
}) {
  const toneCls =
    tone === 'go'
      ? 'text-go'
      : tone === 'wait'
        ? 'text-wait'
        : tone === 'risk'
          ? 'text-risk'
          : tone === 'market'
            ? 'text-market'
            : tone === 'muted'
              ? 'text-muted-foreground'
              : 'text-foreground'
  return (
    <div className={cn('stat-row', className)}>
      <span className="stat-label">{label}</span>
      <span className={cn('stat-value', toneCls)}>{value}</span>
    </div>
  )
}
