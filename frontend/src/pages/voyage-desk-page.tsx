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
import { WalkAwayCurve } from '@/components/desk/walk-away-curve'
import { VoyageAssignmentsTable } from '@/components/desk/voyage-assignments-table'
import { Button } from '@/components/ui/button'
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

/** A slot on the desk grid with no panel to put in it. Matches Panel's own
 *  border, radius and hairline so a missing signal reads as a deliberate gap
 *  in the layout rather than as a broken box. */
function EmptyPanel({ label, hint }: { label: string; hint?: string }) {
  return (
    <div className="h-full rounded-md border border-border bg-surface shadow-panel">
      <div className="panel-state">
        <p className="panel-state-title">{label}</p>
        {hint && <p className="panel-state-hint">{hint}</p>}
      </div>
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
      <div className="flex h-full items-center justify-center p-4">
        {/*
          The first thing a new user meets. The old version was the single
          sentence "No voyage priced yet." above a button -- true, but it
          named the absence rather than the product, so nothing on a cold
          desk said what pricing a voyage actually gets you. This states the
          four real outputs (the same four this page then renders), keeps
          exactly one call to action, and stays in the desk's own register:
          no illustration, no marketing line, no invented statistics.
        */}
        <div className="w-full max-w-md">
          {error && (
            <div className="mb-4 flex items-start gap-2 rounded-md border border-risk/40 bg-risk-soft px-3 py-2 text-left">
              <TriangleAlert className="mt-px h-4 w-4 shrink-0 text-risk" aria-hidden="true" />
              <div>
                <div className="text-caption font-bold uppercase tracking-wide text-risk">
                  The last quote failed
                </div>
                <p className="mt-1 text-body leading-relaxed text-risk">{error}</p>
              </div>
            </div>
          )}

          <div className="rounded-md border border-border bg-surface shadow-panel">
            <div className="border-b border-border bg-surface-2 px-4 py-3">
              <h1 className="text-figure font-bold tracking-tight text-foreground">
                Price a dry-bulk voyage
              </h1>
              <p className="mt-1 text-body leading-relaxed text-muted-foreground">
                Enter a route, a cargo and a laycan window. The desk returns a timing
                verdict and the evidence behind it.
              </p>
            </div>

            <ul className="divide-y divide-border/60">
              {[
                ['Lock or wait', 'Today’s rate against the projected trough, with the option value of waiting priced in.'],
                ['A 30-day rate forecast', 'With the walk-away ceiling this charter should not cross.'],
                ['Route and port feasibility', 'Draft, LOA and beam checked against the real limits at both ends.'],
                ['Cost, carbon and exposure', 'Landed cost per tonne, CII rating, and chokepoint risk on the legs you transit.'],
              ].map(([label, detail]) => (
                <li key={label} className="flex gap-3 px-4 py-2">
                  <span className="mt-2 h-1 w-1 shrink-0 rounded-full bg-structure" aria-hidden="true" />
                  <div>
                    <div className="text-body font-semibold text-foreground">{label}</div>
                    <div className="mt-0.5 text-caption leading-relaxed text-muted-foreground">
                      {detail}
                    </div>
                  </div>
                </li>
              ))}
            </ul>

            <div className="border-t border-border px-4 py-3">
              <Button variant="primary" size="lg" className="w-full" onClick={onNewQuote}>
                New charter quote
              </Button>
              <p className="mt-2 text-center text-micro text-muted-foreground">
                Every figure is computed from data on disk. Nothing here is simulated.
              </p>
            </div>
          </div>
        </div>
      </div>
    )
  }

  if (envelope.status === 'structural_infeasible') {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 p-4">
        <InfeasibilityPanel problems={envelope.structural_problems} />
        <Button variant="primary" size="md" onClick={onNewQuote}>
          Change the request
        </Button>
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
          <div className="flex items-center gap-1 text-lead font-bold uppercase tracking-wide text-wait">
            <Info className="h-4 w-4" />
            Your original request had no solution. This is the nearest one that does.
          </div>
          <div className="mt-2 grid gap-2 sm:grid-cols-2">
            <div>
              <div className="text-caption font-semibold uppercase tracking-wide text-muted-foreground">
                What blocked it
              </div>
              <ul className="mt-0.5 list-inside list-disc text-body text-foreground">
                {envelope.original_blockers.slice(0, 4).map((b, i) => (
                  <li key={i}>{b}</li>
                ))}
              </ul>
            </div>
            <div>
              <div className="text-caption font-semibold uppercase tracking-wide text-muted-foreground">
                What was loosened
              </div>
              <ul className="mt-0.5 space-y-0.5 text-body text-foreground">
                {envelope.relaxations_applied.map((r, i) => (
                  <li key={i}>
                    {r.message}
                    <span className="desk-num ml-1 text-caption text-muted-foreground">
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
        <div className="h-95 xl:col-span-4">
          <VerdictBlock quote={quote} />
        </div>
        <div className="h-95 xl:col-span-4">
          <RateForecastTable rows={quote.rate_forecast} todayQuote={quote.today_quote_usd_per_day} />
        </div>
        <div className="h-95 xl:col-span-4">
          {quote.risk_assessment ? (
            <RiskFeed assessment={quote.risk_assessment} />
          ) : (
            <EmptyPanel
              label="No risk data for this date"
              hint="The risk feed reads from harvested files on disk. Nothing covers this charter date yet."
            />
          )}
        </div>
      </div>

      {/* 1b. Why that verdict.
          The optimal-stopping boundary was computed on every quote, typed in
          the frontend, and rendered nowhere -- 90 real numbers per quote,
          discarded. It is the actual decision rule (the solver's own test is
          `LOCK if today_quote <= boundary[0] + weather`), so it belongs
          immediately under the verdict it produces rather than filed with the
          supporting panels. Full width because 90 points need it to be
          readable, and because the shape of the line -- where it troughs, how
          far today sits above it -- is the single most informative object on
          the page once you can actually see it. */}
      {/* 336px, measured against the TALLEST variant rather than the one on
          screen at the time: the panel grows a "weather buffer raises it by"
          row and an extra sentence whenever the transit buffer moves the
          decision line, which 304px clipped by 25px. Sized for that case so a
          route with a large buffer -- exactly the case where the extra
          explanation matters most -- is not the one that gets cut off. */}
      <div className="h-84">
        <WalkAwayCurve quote={quote} />
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
        <div className="h-110 xl:col-span-8">
          <RouteMap
            routes={quote.route_exploration}
            ports={ports}
            chokepoints={chokepoints}
            fracture={quote.fracture}
            focusId={mapFocus}
            onFocus={setMapFocus}
          />
        </div>
        <div className="h-110 xl:col-span-4">
          <RouteList routes={quote.route_exploration} focusId={mapFocus} onFocus={setMapFocus} />
        </div>
      </div>

      {/* 3. Fit & Schedule -- measured at ~151px (fleet mix) and ~165px
          (port constraints) of real content; 224px was generous, 200px
          still clears both with real room for a longer fleet-mix table. */}
      <div className="grid grid-cols-1 gap-1 xl:grid-cols-12">
        <div className="h-60 xl:col-span-6">
          {quote.fleet_mix ? (
            <FleetMixTable frontier={quote.fleet_mix} explanation={quote.explanations.fleet_mix} />
          ) : (
            <EmptyPanel
              label="No fleet-mix frontier"
              hint="The optimizer found no alternative split of this cargo across vessel classes to compare."
            />
          )}
        </div>
        <div className="h-60 xl:col-span-3">
          <PortChecksTable
            origin={quote.origin_port_check}
            dest={quote.dest_port_check}
            ports={ports}
          />
        </div>
        <div className="h-60 xl:col-span-3">
          {hasAssignments ? (
            <VoyageAssignmentsTable
              rec={rec}
              ports={ports}
              assignmentExplanations={quote.explanations.voyage_assignments}
              repositioningExplanations={quote.explanations.repositioning}
            />
          ) : (
            <EmptyPanel
              label="Nothing scheduled"
              hint="Add a vessel and a cargo revenue figure to the quote form, and the optimizer will assign and sequence it here."
            />
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
        <div className="h-100 xl:col-span-6">
          <LandedCostPanel
            breakdown={envelope.landed_cost}
            destPort={quote.dest_port}
            cargoVolumeMt={quote.cargo_volume_dwt}
            freightUsdPerDay={quote.today_quote_usd_per_day}
            voyageDays={quote.assumed_transit_days}
            vesselClass={quote.target_vessel_class}
          />
        </div>
        <div className="h-100 xl:col-span-6">
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
        // F-51 fix: this used to fall back to the ORIGIN port's census when
        // the destination wasn't one of Sentinel-1's five covered ports --
        // silently showing a different port's congestion under a panel that
        // gives no origin/destination label, which read as "the wrong port"
        // (a real user report). Discharge-side congestion is what actually
        // matters for a chartering decision (how fast the vessel gets off
        // demurrage at the far end), so this now keys on dest_port only --
        // real destination congestion, or the panel doesn't render at all.
        const anchoragePort = anchoragePortForQuotePort(quote.dest_port)
        const span = anchoragePort ? 'xl:col-span-4' : 'xl:col-span-6'
        return (
          <div className="grid grid-cols-1 gap-1 xl:grid-cols-12">
            <div className={`h-52.5 ${span}`}>
              <CIIPanel emissions={quote.emissions} />
            </div>
            <div className={`h-52.5 ${span}`}>
              <FracturePanel fracture={quote.fracture} />
            </div>
            {anchoragePort && (
              <div className={`h-52.5 ${span}`}>
                <AnchoragePanel port={anchoragePort} />
              </div>
            )}
          </div>
        )
      })()}
    </div>
  )
}
