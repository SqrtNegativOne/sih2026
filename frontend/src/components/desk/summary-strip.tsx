import { Term } from '@/components/desk/term'
import type { PortListing, QuoteResult } from '@/lib/types'
import { formatIsoShort, formatNumber, prettyPort } from '@/lib/format'

/**
 * A single-line context bar. Only facts that appear nowhere else on the desk:
 * the route, what is moving, and the loading window. Vessel class lives in the
 * verdict, the charter date in the top bar, the term in the verdict.
 */
export function SummaryStrip({ quote, ports }: { quote: QuoteResult; ports: PortListing[] }) {
  const portName = (code: string) =>
    prettyPort(ports.find((p) => p.code === code)?.name ?? code)
  // "Laycan" and "dwt" are the right words and stay; `term` attaches their
  // definition so the strip is readable by someone who has not spent a career
  // in chartering, without translating the trade out of the interface.
  const items: { key: string; label: React.ReactNode; value: string }[] = [
    {
      key: 'route',
      label: 'Route',
      value: `${portName(quote.origin_port)} to ${portName(quote.dest_port)}`,
    },
    { key: 'commodity', label: 'Commodity', value: quote.commodity },
    {
      key: 'cargo',
      label: <Term term="DWT">Cargo</Term>,
      value: `${formatNumber(quote.cargo_volume_dwt)} dwt`,
    },
    {
      key: 'laycan',
      label: <Term term="laycan">Laycan</Term>,
      value: `${formatIsoShort(quote.laycan_start)} to ${formatIsoShort(quote.laycan_end)}`,
    },
  ]
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-0.5 rounded-sm border border-border bg-surface px-2 py-0.5">
      {items.map(({ key, label, value }, i) => (
        <div key={key} className="flex items-baseline gap-2">
          {i > 0 && <span className="mr-2 h-3 w-px bg-border" aria-hidden="true" />}
          <span className="text-caption font-semibold uppercase tracking-wide text-muted-foreground">
            {label}
          </span>
          <span className="desk-num font-semibold text-foreground">{value}</span>
        </div>
      ))}
    </div>
  )
}
