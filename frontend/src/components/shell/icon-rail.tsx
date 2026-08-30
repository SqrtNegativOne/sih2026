import {
  Anchor,
  ArrowLeftRight,
  Boxes,
  CalendarClock,
  FileText,
  Gauge,
  Globe,
  LayoutGrid,
  LogIn,
  LogOut,
  type LucideIcon,
  PieChart,
  ScrollText,
  Zap,
} from 'lucide-react'
import type { DeskView } from '@/App'
import { cn } from '@/lib/utils'

interface RailItem {
  icon: LucideIcon
  label: string
  /** Panel id to scroll to, when the module maps to a section on the desk
   * (only meaningful for the 'desk' view). */
  target?: string
  /** Switches the whole main-content view instead of scrolling within it --
   * P2: there is no client-side router in this app, so Port Twin is a
   * sibling view swapped in by App.tsx, the same pattern QuoteDrawer's own
   * open/close state already uses for the quote form. */
  view?: DeskView
  /** P7: true for a rail item with no real destination (neither `target`
   * nor `view`) -- Time Charter In/Out contract management is a real
   * chartering concept this system does not implement (everything here is
   * voyage/spot decision support, not TC contract book-keeping). Rendered
   * visibly disabled with an honest tooltip instead of silently doing
   * nothing on click, which read as a broken link, not a real feature. */
  notImplemented?: boolean
}

const ITEMS: RailItem[] = [
  { icon: Boxes, label: 'Cargoes', target: 'summary', view: 'desk' },
  { icon: LayoutGrid, label: 'Estimates', target: 'fleet', view: 'desk' },
  { icon: FileText, label: 'Fixtures', target: 'decision', view: 'desk' },
  { icon: Globe, label: 'Market', target: 'forecast', view: 'desk' },
  { icon: ArrowLeftRight, label: 'Matching', target: 'assignments', view: 'desk' },
  { icon: CalendarClock, label: 'Scheduling', target: 'ports', view: 'desk' },
  { icon: Anchor, label: 'Port Twin', view: 'port-twin' },
  { icon: Gauge, label: 'Tonnage Field', view: 'tonnage-field' },
  { icon: Zap, label: 'Fragility', view: 'fragility' },
  { icon: ScrollText, label: 'Ledger', view: 'ledger' },
  { icon: PieChart, label: 'Portfolio', view: 'portfolio' },
  { icon: LogIn, label: 'TC In', notImplemented: true },
  { icon: LogOut, label: 'TC Out', notImplemented: true },
]

function scrollTo(id?: string) {
  if (!id) return
  document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

export function IconRail({
  active,
  onSelectView,
}: {
  active?: string
  onSelectView?: (view: DeskView) => void
}) {
  return (
    // relative z-50: the New Quote drawer's own backdrop is `fixed inset-0
    // z-40` -- a positioned+z-indexed element always paints above static
    // content regardless of DOM order, so without an explicit z-index here
    // this rail (a plain static <nav>) sat BEHIND that backdrop whenever the
    // drawer was open, including on first load (drawerOpen defaults true).
    // A click on Portfolio/Fragility/Tonnage Field then hit the backdrop
    // instead and just closed the drawer -- the nav item never activated,
    // which read as "this page doesn't work" even though the page itself
    // was fine. z-50 matches the drawer panel's own z-index; DOM order (the
    // drawer renders after this rail) keeps the panel on top where the two
    // would otherwise overlap, so this only fixes the backdrop coverage.
    <nav className="relative z-50 hidden w-16 shrink-0 flex-col border-r border-sidebar-border bg-sidebar py-1 md:flex">
      {ITEMS.map(({ icon: Icon, label, target, view, notImplemented }) => {
        // F-34 fix: this used to default `active` to the literal string
        // 'Estimates', so that item was highlighted as "current" any time
        // the caller had nothing real to report -- which was always true on
        // the Voyage Desk itself, since it has six of these targets and
        // nothing here tracks which one is actually scrolled into view.
        // No default now: on the desk, none of the six section links claims
        // to be "the" current one (honest, since none of them is), and the
        // four real secondary screens (Port Twin, Tonnage Field, Fragility,
        // Ledger) still highlight correctly via the real view name passed in.
        const isActive = active != null && label === active
        return (
          <button
            key={label}
            type="button"
            disabled={notImplemented}
            aria-disabled={notImplemented}
            onClick={() => {
              if (notImplemented) return
              if (view === 'desk') {
                onSelectView?.('desk')
                // Scrolling to a desk section only makes sense once the desk
                // view is actually mounted -- next tick, after the swap.
                requestAnimationFrame(() => scrollTo(target))
              } else if (view) onSelectView?.(view)
              else scrollTo(target)
            }}
            title={notImplemented ? `${label} — not implemented` : label}
            className={cn(
              'flex flex-col items-center gap-0.5 px-0.5 py-2 text-center text-[8.5px] font-semibold uppercase leading-tight transition-colors',
              notImplemented
                ? 'cursor-not-allowed border-l-2 border-transparent text-muted-foreground/40'
                : isActive
                  ? 'border-l-2 border-primary bg-accent text-primary'
                  : 'border-l-2 border-transparent text-muted-foreground hover:bg-accent hover:text-foreground',
            )}
          >
            <Icon className="h-[18px] w-[18px]" strokeWidth={1.75} />
            <span className="break-words">{label}</span>
          </button>
        )
      })}
    </nav>
  )
}
