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
  const items: [string, string][] = [
    ['Route', `${portName(quote.origin_port)} to ${portName(quote.dest_port)}`],
    ['Commodity', quote.commodity],
    ['Cargo', `${formatNumber(quote.cargo_volume_dwt)} dwt`],
    ['Laycan', `${formatIsoShort(quote.laycan_start)} to ${formatIsoShort(quote.laycan_end)}`],
  ]
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-0.5 rounded-[3px] border border-border bg-surface px-2 py-0.5">
      {items.map(([label, value], i) => (
        <div key={label} className="flex items-baseline gap-1.5">
          {i > 0 && <span className="mr-2 h-3 w-px bg-border" />}
          <span className="text-[9.5px] font-semibold uppercase tracking-wide text-muted-foreground">
            {label}
          </span>
          <span className="desk-num font-semibold text-foreground">{value}</span>
        </div>
      ))}
    </div>
  )
}
