import { Panel } from '@/components/desk/panel'
import type { CongestionLabel, PortCheck, PortListing } from '@/lib/types'
import { formatNumber, prettyPort } from '@/lib/format'
import { cn } from '@/lib/utils'

const CONGESTION_CLASS: Record<CongestionLabel, string> = {
  LOW: 'bg-go/15 text-go-on-soft',
  MODERATE: 'bg-wait/15 text-wait-on-soft',
  HIGH: 'bg-risk/15 text-risk-on-soft',
}

function num(v: number | null, digits = 0, suffix = ''): string {
  return v == null ? '—' : `${formatNumber(v, digits)}${suffix}`
}

/**
 * Load vs discharge as two comparison columns (no horizontal scroll), the way a
 * chartering estimate sheet reads.
 */
export function PortChecksTable({
  origin,
  dest,
  ports,
}: {
  origin: PortCheck
  dest: PortCheck
  ports: PortListing[]
}) {
  const portName = (code: string) => prettyPort(ports.find((p) => p.code === code)?.name ?? code)

  const rows: Array<{ label: string; render: (pc: PortCheck) => React.ReactNode }> = [
    { label: 'Port', render: (pc) => portName(pc.port) },
    { label: 'Max DWT', render: (pc) => num(pc.max_dwt) },
    { label: 'Draft', render: (pc) => num(pc.max_draft_m, 1, ' m') },
    { label: 'LOA', render: (pc) => num(pc.max_loa_m, 0, ' m') },
    { label: 'Beam', render: (pc) => num(pc.max_beam_m, 1, ' m') },
    {
      label: 'Wait, days',
      render: (pc) => (
        <span title={pc.wait_days_is_real_data ? 'Real data' : 'Estimated'}>
          {pc.expected_wait_days.toFixed(1)}
          {!pc.wait_days_is_real_data && <span className="text-muted-foreground">*</span>}
        </span>
      ),
    },
    {
      label: 'Congestion',
      render: (pc) => (
        <span
          className={cn(
            'rounded-sm px-1 text-caption font-bold',
            CONGESTION_CLASS[pc.congestion_label],
          )}
        >
          {pc.congestion_label}
        </span>
      ),
    },
  ]

  return (
    <Panel
      className="h-full"
      id="ports"
      title="Port Constraints"
      hint="Berth limits and current queue at the load and discharge ports, shown side by side. Wait marked * is an estimate, not live data. Congestion buckets the live wait against the port's own normal."
      flush
    >
      <table className="desk-table">
        <thead>
          <tr>
            <th />
            <th className="text-right">Load</th>
            <th className="text-right">Disch</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(({ label, render }) => (
            <tr key={label}>
              <td className="text-caption font-semibold uppercase tracking-wide text-muted-foreground">
                {label}
              </td>
              <td className="desk-num text-right">{render(origin)}</td>
              <td className="desk-num text-right">{render(dest)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Panel>
  )
}
