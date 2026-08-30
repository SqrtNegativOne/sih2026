import { useEffect, useState } from 'react'
import { Panel } from '@/components/desk/panel'
import { anchorageOverlayUrl, fetchAnchorageCensus } from '@/lib/api'
import { formatIsoShort, formatNumber, formatRelativeAge, prettyPort } from '@/lib/format'
import type { AnchorageConfidence, AnchoragePortCode, AnchorageCensus } from '@/lib/types'
import { cn } from '@/lib/utils'

// Same three-tone convention CIIPanel/FracturePanel already use -- calm/high
// confidence reads go, a rough-sea/low-confidence count reads risk, so a
// reader distrusts a low-confidence count on sight, not just from the label.
const CONFIDENCE_CLASS: Record<AnchorageConfidence, string> = {
  high: 'bg-go/15 text-go',
  medium: 'bg-wait/15 text-wait',
  low: 'bg-risk/15 text-risk',
}

export function AnchoragePanel({ port }: { port: AnchoragePortCode }) {
  const [census, setCensus] = useState<AnchorageCensus | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  // The overlay PNG is rendered by a separate, offline step from the
  // census pipeline (see backend's own /anchorage/{port}/overlay.png
  // docstring) -- it can genuinely 404 for a port whose latest scene
  // hasn't had one rendered yet, which is not an application error, just a
  // reason to fall back to the text-only view below.
  const [imgFailed, setImgFailed] = useState(false)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    setImgFailed(false)
    fetchAnchorageCensus(port)
      .then((c) => {
        if (!cancelled) setCensus(c)
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Could not load the anchorage census.')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [port])

  const hint =
    'A real Sentinel-1 SAR-derived vessel count for this anchorage (opt.anchorage.detect, a classical ' +
    'CFAR detector -- see that module for method and known failure modes). This is a SPARSE spot check, ' +
    'not a live signal -- real revisit is roughly every 4-11 days at this port -- so the count is only ' +
    'ever shown alongside its real age. The count itself is MODEL_DERIVED (a computation over the real, ' +
    'OBSERVED SAR scene); never blended into or replacing the live PortWatch-derived congestion signal.'

  if (loading) {
    return (
      <Panel id="anchorage" title="Anchorage Census (Satellite)" hint={hint}>
        <div className="flex h-full items-center justify-center text-center text-[12px] text-muted-foreground">
          Loading…
        </div>
      </Panel>
    )
  }

  if (error) {
    return (
      <Panel id="anchorage" title="Anchorage Census (Satellite)" hint={hint}>
        <div className="flex h-full items-center justify-center text-center text-[12px] text-risk">{error}</div>
      </Panel>
    )
  }

  if (!census) {
    return (
      <Panel id="anchorage" title="Anchorage Census (Satellite)" hint={hint}>
        <div className="flex h-full flex-col items-center justify-center gap-1 text-center text-[12px] text-muted-foreground">
          <span>No Sentinel-1 scene has been processed for {prettyPort(port)} yet.</span>
        </div>
      </Panel>
    )
  }

  return (
    <Panel
      id="anchorage"
      title="Anchorage Census (Satellite)"
      hint={hint}
      meta={prettyPort(census.port)}
      flush={!imgFailed}
    >
      {imgFailed ? (
        <div className="flex h-full flex-col gap-1.5 p-2">
          <div className="flex items-baseline justify-between">
            <span className="desk-num text-[26px] font-bold text-foreground">
              {formatNumber(census.vessel_count)}
            </span>
            <span className={cn('rounded-sm px-1.5 py-px text-[10px] font-bold uppercase tracking-wide', CONFIDENCE_CLASS[census.confidence])}>
              {census.confidence} confidence
            </span>
          </div>
          <div className="text-[11px] text-muted-foreground">
            vessels detected in the anchorage box -- no overlay image rendered for this scene yet
          </div>
          <div className="mt-1 flex items-center gap-1.5 rounded border border-wait/40 bg-wait-soft px-1.5 py-1 text-[11px] font-semibold text-wait">
            {formatRelativeAge(census.acquired_at)} ({formatIsoShort(census.acquired_at.slice(0, 10))})
          </div>
          <div className="mt-auto grid grid-cols-2 gap-1 text-[10px] text-muted-foreground">
            <div>
              <div className="uppercase tracking-wide">Scene</div>
              <div className="truncate font-mono text-foreground" title={census.scene_id}>
                {census.scene_id}
              </div>
            </div>
            <div>
              <div className="uppercase tracking-wide">Provenance</div>
              <div className="font-mono text-foreground">{census.provenance}</div>
            </div>
          </div>
        </div>
      ) : (
        <div className="relative h-full w-full overflow-hidden bg-black">
          <img
            src={anchorageOverlayUrl(census.port)}
            alt={`Sentinel-1 SAR crop of ${prettyPort(census.port)}'s anchorage, ${census.vessel_count} vessel(s) ringed in red`}
            className="h-full w-full object-cover object-top"
            onError={() => setImgFailed(true)}
          />
          <div className="absolute left-1.5 top-1.5 flex items-center gap-1.5">
            <span className="desk-num rounded-sm bg-black/70 px-1.5 py-0.5 text-[15px] font-bold text-white">
              {formatNumber(census.vessel_count)}
            </span>
            <span
              className={cn(
                'rounded-sm px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wide',
                CONFIDENCE_CLASS[census.confidence],
              )}
            >
              {census.confidence}
            </span>
          </div>
          <div className="absolute bottom-1.5 right-1.5 rounded-sm border border-wait/40 bg-black/70 px-1.5 py-0.5 text-[10px] font-semibold text-wait">
            {formatRelativeAge(census.acquired_at)} ({formatIsoShort(census.acquired_at.slice(0, 10))})
          </div>
        </div>
      )}
    </Panel>
  )
}
