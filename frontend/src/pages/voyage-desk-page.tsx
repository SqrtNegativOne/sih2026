import { Info, TriangleAlert } from 'lucide-react'
import { useState } from 'react'
import { BackhaulPanel } from '@/components/desk/backhaul-panel'
import { AnchoragePanel } from '@/components/desk/anchorage-panel'
import { CIIPanel } from '@/components/desk/cii-panel'
import { FleetMixTable } from '@/components/desk/fleet-mix-table'
import { FracturePanel } from '@/components/desk/fracture-panel'
import { InfeasibilityPanel } from '@/components/desk/infeasibility-panel'
import { LandedCostPanel } from '@/components/desk/landed-cost-panel'
import { PortChecksTable } from '@/components/desk/port-checks-table'
import { RateForecastTable } from '@/components/desk/rate-forecast-table'
import { RiskFeed } from '@/components/desk/risk-feed'
import { RouteList } from '@/components/desk/route-list'
import { RouteMap } from '@/components/desk/route-map'
import { SolveProgress } from '@/components/desk/solve-progress'
import { SummaryStrip } from '@/components/desk/summary-strip'
import { VerdictBlock } from '@/components/desk/verdict-block'
import { VoyageAssignmentsTable } from '@/components/desk/voyage-assignments-table'
import { anchoragePortForQuotePort } from '@/lib/anchorage-ports'
import type { ChokepointReference, PortListing, ProgressStage, QuoteEnvelope, VesselInput } from '@/lib/types'

interface VoyageDeskPageProps {
  envelope: QuoteEnvelope | null
  ports: PortListing[]
  /** 3.4: the static chokepoint reference table (id/name/centre/radius) --
   * joined against `quote.fracture.chokepoints` (band data, no geometry) to
   * place markers on the route map without hardcoding geography there. */
  chokepoints: ChokepointReference[]
  stages: ProgressStage[]
  solving: boolean
  error: string | null
  onNewQuote: () => void
  /** P6: the vessel(s) actually supplied on this quote, for a real backhaul
   * sweep -- QuoteResult never echoes full vessel specs back. */
  vessels: VesselInput[]
}

function EmptyPanel({ label }: { label: string }) {
  return (
    <div className="flex h-full items-center justify-center rounded border border-border bg-surface p-3 text-center text-[12px] text-muted-foreground">
      {label}
    </div>
  )
}

export function VoyageDeskPage({
  envelope,
  ports,
  chokepoints,
  stages,
  solving,
  error,
  onNewQuote,
  vessels,
}: VoyageDeskPageProps) {
  const [mapFocus, setMapFocus] = useState<string | null>(null)

  if (solving) return <SolveProgress stages={stages} />

  if (!envelope) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 text-center">
        {error ? (
          <div className="flex items-center gap-1 rounded border border-risk/40 bg-risk-soft px-3 py-2 text-[12px] text-risk">
            <TriangleAlert className="h-4 w-4" />
            {error}
          </div>
        ) : (
          <p className="text-[13px] text-muted-foreground">No voyage priced yet.</p>
        )}
        <button
          type="button"
          onClick={onNewQuote}
          className="rounded bg-primary px-3 py-1.5 text-[12px] font-semibold uppercase tracking-wide text-primary-foreground hover:bg-primary/90"
        >
          New Charter Quote
        </button>
      </div>
    )
  }

  if (envelope.status === 'structural_infeasible') {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3">
        <InfeasibilityPanel problems={envelope.structural_problems} />
        <button
          type="button"
          onClick={onNewQuote}
          className="rounded bg-primary px-3 py-1.5 text-[12px] font-semibold uppercase tracking-wide text-primary-foreground hover:bg-primary/90"
        >
          Change the request
        </button>
      </div>
    )
  }

  const quote = envelope.quote
  if (!quote) return null

  const isContingent = envelope.status === 'contingent_infeasible'
  const rec = quote.full_recommendation
  const hasAssignments =
    rec.voyage_assignments.length > 0 ||
    rec.rejected_options.length > 0 ||
    rec.repositioning_actions.length > 0

  return (
    <div className="flex flex-col gap-1">
      <SummaryStrip quote={quote} ports={ports} />

      {isContingent && (
        <div className="rounded border border-wait bg-wait-soft px-3 py-2">
          <div className="flex items-center gap-1 text-[12px] font-bold uppercase tracking-wide text-wait">
            <Info className="h-4 w-4" />
            Your original request had no solution. This is the nearest one that does.
          </div>
          <div className="mt-1.5 grid gap-1.5 sm:grid-cols-2">
            <div>
              <div className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
                What blocked it
              </div>
              <ul className="mt-0.5 list-inside list-disc text-[11px] text-foreground">
                {envelope.original_blockers.slice(0, 4).map((b, i) => (
                  <li key={i}>{b}</li>
                ))}
              </ul>
            </div>
            <div>
              <div className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
                What was loosened
              </div>
              <ul className="mt-0.5 space-y-0.5 text-[11px] text-foreground">
                {envelope.relaxations_applied.map((r, i) => (
                  <li key={i}>
                    {r.message}
                    <span className="desk-num ml-1 text-[10px] text-muted-foreground">
                      {r.before} to {r.after}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </div>
      )}

      {/*
        Desk layout, grouped by what each row actually answers rather than
        the order features were built in (the prior version's "Row 2.5" /
        "2.75" / "2.9" comments named build order, not purpose, and the
        panel heights drifted independently per addition -- both are fixed
        here: one height per group, and the group's own name says what it's
        for).

        1. THE DECISION   -- verdict, forecast, risk. The headline answer.
        2. THE ROUTE       -- map + list. Deliberately right under the
                              decision (not at the foot of the page) so the
                              verdict and the route it's for are both on
                              screen together without scrolling.
        3. FIT & SCHEDULE  -- fleet mix, port constraints, assignments.
        4. COMMERCIAL      -- landed cost, backhaul.
        5. EXPOSURE        -- carbon rating, chokepoint fracture, satellite
                              census -- three independent real-world signals
                              about this specific voyage, grouped together
                              on purpose.
      */}

      {/* 1. The Decision.
          Heights here are MEASURED, not guessed -- each panel's real
          rendered content height was read out of the running app against a
          real quote, and the container sized just above it. The verdict
          panel in particular was overflowing its old 340px by a real 34px
          (clipping the tail of its own "why this verdict" reasoning, the
          single most valuable text on the desk), which is why this row is
          380px rather than the 340px the other two would need on their
          own -- a level row beats two tight panels and one truncated one. */}
      <div className="grid grid-cols-1 gap-1 xl:grid-cols-12">
        <div className="h-[380px] xl:col-span-4">
          <VerdictBlock quote={quote} />
        </div>
        <div className="h-[380px] xl:col-span-4">
          <RateForecastTable rows={quote.rate_forecast} todayQuote={quote.today_quote_usd_per_day} />
        </div>
        <div className="h-[380px] xl:col-span-4">
          {quote.risk_assessment ? (
            <RiskFeed assessment={quote.risk_assessment} />
          ) : (
            <EmptyPanel label="No risk data on disk for this date" />
          )}
        </div>
      </div>

      {/* 2. The Route -- the map is the wider of the two on purpose: it's
          the single most immediately legible panel on the desk, and sitting
          right under the decision means a screenshot of the page's top
          shows the verdict and the route it's for in one frame. The map
          fills whatever height it is given (its SVG stretches), so this is
          sized for legibility rather than to fit content; the route list
          beside it measured ~175px of real content and simply scrolls when
          a route has more legs. */}
      <div className="grid grid-cols-1 gap-1 xl:grid-cols-12">
        <div className="h-[440px] xl:col-span-8">
          <RouteMap
            routes={quote.route_exploration}
            ports={ports}
            chokepoints={chokepoints}
            fracture={quote.fracture}
            focusId={mapFocus}
            onFocus={setMapFocus}
          />
        </div>
        <div className="h-[440px] xl:col-span-4">
          <RouteList routes={quote.route_exploration} focusId={mapFocus} onFocus={setMapFocus} />
        </div>
      </div>

      {/* 3. Fit & Schedule -- measured at ~151px (fleet mix) and ~165px
          (port constraints) of real content; 224px was generous, 200px
          still clears both with real room for a longer fleet-mix table. */}
      <div className="grid grid-cols-1 gap-1 xl:grid-cols-12">
        <div className="h-[200px] xl:col-span-6">
          {quote.fleet_mix ? (
            <FleetMixTable frontier={quote.fleet_mix} />
          ) : (
            <EmptyPanel label="No fleet-mix frontier" />
          )}
        </div>
        <div className="h-[200px] xl:col-span-3">
          <PortChecksTable
            origin={quote.origin_port_check}
            dest={quote.dest_port_check}
            ports={ports}
          />
        </div>
        <div className="h-[200px] xl:col-span-3">
          {hasAssignments ? (
            <VoyageAssignmentsTable rec={rec} ports={ports} />
          ) : (
            <EmptyPanel label="Add a vessel and a cargo revenue figure in the quote form to schedule and assign it." />
          )}
        </div>
      </div>

      {/* 4. Commercial.
          F-33: this row used to be h-[260px] -- five cost rows plus the
          total plus the "fill the gaps" assumptions form (5 inputs, a
          checkbox and a Recompute button) add up to roughly 300-330px on
          their own, so the form rendered entirely below the fold of an
          inner scroll container with no visible scrollbar -- the one
          control that turns "1/5 components real" into 5/5 was
          unreachable. Kept at 400px after re-measuring: landed cost's real
          content is ~321px (53px of real slack, the right side of tight for
          a panel whose row count grows once a war-risk line or a commodity
          price becomes available), and the map row above already carries
          this section of the page visually. */}
      <div className="grid grid-cols-1 gap-1 xl:grid-cols-12">
        <div className="h-[400px] xl:col-span-6">
          <LandedCostPanel
            breakdown={envelope.landed_cost}
            destPort={quote.dest_port}
            cargoVolumeMt={quote.cargo_volume_dwt}
            freightUsdPerDay={quote.today_quote_usd_per_day}
            voyageDays={quote.assumed_transit_days}
            vesselClass={quote.target_vessel_class}
          />
        </div>
        <div className="h-[400px] xl:col-span-6">
          <BackhaulPanel vessel={vessels[0]} dischargePort={quote.dest_port} />
        </div>
      </div>

      {/* 5. Exposure -- carbon rating and chokepoint fracture are always
          shown; the satellite census joins them only when this quote's
          route actually touches one of Sentinel-1's five covered ports
          (dest preferred -- a discharge-side anchorage is the more common
          real case for this network's own shape). The row reflows to 3
          even columns when it's present, 2 when it isn't, rather than
          leaving a half-width gap or a separate half-empty row. */}
      {(() => {
        const anchoragePort = anchoragePortForQuotePort(quote.dest_port) ?? anchoragePortForQuotePort(quote.origin_port)
        const span = anchoragePort ? 'xl:col-span-4' : 'xl:col-span-6'
        return (
          <div className="grid grid-cols-1 gap-1 xl:grid-cols-12">
            <div className={`h-[210px] ${span}`}>
              <CIIPanel emissions={quote.emissions} />
            </div>
            <div className={`h-[210px] ${span}`}>
              <FracturePanel fracture={quote.fracture} />
            </div>
            {anchoragePort && (
              <div className={`h-[210px] ${span}`}>
                <AnchoragePanel port={anchoragePort} />
              </div>
            )}
          </div>
        )
      })()}
    </div>
  )
}
