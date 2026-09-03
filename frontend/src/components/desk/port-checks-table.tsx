import { Panel } from '@/components/desk/panel'
import { Term } from '@/components/desk/term'
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

  // DWT / draft / LOA / beam are the correct trade words and stay -- `term`
  // attaches the definition so someone new to chartering is not stopped by
  // four abbreviations in a row, without the desk having to stop speaking the
  // language of the people using it.
  const rows: Array<{ label: React.ReactNode; key: string; render: (pc: PortCheck) => React.ReactNode }> = [
    { key: 'port', label: 'Port', render: (pc) => portName(pc.port) },
    {
      key: 'dwt',
      label: <Term term="DWT">Max DWT</Term>,
      render: (pc) => num(pc.max_dwt),
    },
    {
      key: 'draft',
      label: <Term term="draft">Draft</Term>,
      render: (pc) => num(pc.max_draft_m, 1, ' m'),
    },
    {
      key: 'loa',
      label: <Term term="LOA">LOA</Term>,
      render: (pc) => num(pc.max_loa_m, 0, ' m'),
    },
    {
      key: 'beam',
      label: <Term term="beam">Beam</Term>,
      render: (pc) => num(pc.max_beam_m, 1, ' m'),
    },
    {
      key: 'wait',
      label: 'Wait, days',
      render: (pc) => (
        <span title={pc.wait_days_is_real_data ? 'Real data' : 'Estimated'}>
          {pc.expected_wait_days.toFixed(1)}
          {!pc.wait_days_is_real_data && <span className="text-muted-foreground">*</span>}
        </span>
      ),
    },
    {
      key: 'congestion',
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
      soWhat={'Whether the chosen ship can physically enter and work at both ports — depth, length, beam, air draft. A failed check is not a warning: that ship cannot call there, so change the ship or the port.'}
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
          {rows.map(({ key, label, render }) => (
            <tr key={key}>
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
