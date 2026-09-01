import { useEffect, useRef, useState } from 'react'
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

/** The same three tones as CONFIDENCE_CLASS, re-cut as solid fills for the
 *  dark SAR overlay, where a 15%-alpha tint on black reads as black. */
const CONFIDENCE_CLASS_ON_DARK: Record<AnchorageConfidence, string> = {
  high: 'bg-go text-white',
  medium: 'bg-wait text-white',
  low: 'bg-risk text-white',
}

const ZOOM_MIN = 1
const ZOOM_MAX = 8
const IDENTITY_TF = { k: 1, x: 0, y: 0 }

/** A real vessel ring can be a handful of pixels wide against a scene
 * downscaled to a 900px edge (see anchorage.render's own docstring) --
 * legible at panel size but not enough to distinguish nearby detections at
 * a crowded anchorage. Cursor-centred wheel zoom + drag-to-pan (same
 * mechanics as route-map.tsx's own free zoom) so a viewer can actually
 * separate them, rather than shipping the fixed-scale crop as the ceiling
 * on what's checkable. */
function ZoomableImage({
  src,
  alt,
  onError,
}: {
  src: string
  alt: string
  onError: () => void
}) {
  const wrapRef = useRef<HTMLDivElement>(null)
  const [tf, setTf] = useState(IDENTITY_TF)
  const dragRef = useRef<{ startX: number; startY: number; tfX: number; tfY: number } | null>(null)
  const [dragging, setDragging] = useState(false)

  useEffect(() => {
    setTf(IDENTITY_TF)
  }, [src])

  useEffect(() => {
    const el = wrapRef.current
    if (!el) return
    function onWheel(e: WheelEvent) {
      e.preventDefault()
      const rect = el!.getBoundingClientRect()
      const mx = e.clientX - rect.left
      const my = e.clientY - rect.top
      setTf((prev) => {
        const factor = Math.exp(-e.deltaY * 0.0018)
        const k = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, prev.k * factor))
        const dataX = (mx - prev.x) / prev.k
        const dataY = (my - prev.y) / prev.k
        return { k, x: mx - dataX * k, y: my - dataY * k }
      })
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => el.removeEventListener('wheel', onWheel)
  }, [])

  function zoomBy(factor: number) {
    setTf((prev) => {
      const el = wrapRef.current
      const rect = el?.getBoundingClientRect()
      const mx = rect ? rect.width / 2 : 0
      const my = rect ? rect.height / 2 : 0
      const k = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, prev.k * factor))
      const dataX = (mx - prev.x) / prev.k
      const dataY = (my - prev.y) / prev.k
      return { k, x: mx - dataX * k, y: my - dataY * k }
    })
  }

  const zoomed = tf.k !== 1 || tf.x !== 0 || tf.y !== 0

  return (
    <div
      ref={wrapRef}
      className={cn('relative h-full w-full touch-none overflow-hidden bg-black', dragging ? 'cursor-grabbing' : tf.k > 1 ? 'cursor-grab' : 'cursor-zoom-in')}
      onPointerDown={(e) => {
        if (tf.k <= 1) return
        ;(e.target as Element).setPointerCapture(e.pointerId)
        dragRef.current = { startX: e.clientX, startY: e.clientY, tfX: tf.x, tfY: tf.y }
        setDragging(true)
      }}
      onPointerMove={(e) => {
        if (!dragRef.current) return
        const dx = e.clientX - dragRef.current.startX
        const dy = e.clientY - dragRef.current.startY
        setTf((prev) => ({ ...prev, x: dragRef.current!.tfX + dx, y: dragRef.current!.tfY + dy }))
      }}
      onPointerUp={() => {
        dragRef.current = null
        setDragging(false)
      }}
      onDoubleClick={() => setTf(IDENTITY_TF)}
    >
      <img
        src={src}
        alt={alt}
        draggable={false}
        onError={onError}
        className="h-full w-full origin-top-left select-none object-contain"
        style={{ transform: `translate(${tf.x}px, ${tf.y}px) scale(${tf.k})` }}
      />
      <div className="pointer-events-none absolute bottom-1.5 left-1.5 flex items-center gap-1">
        <button
          type="button"
          onClick={() => zoomBy(1 / 1.5)}
          className="pointer-events-auto flex h-5 w-5 items-center justify-center rounded-sm bg-black/70 text-lead font-bold leading-none text-white hover:bg-black/90"
          title="Zoom out"
        >
          −
        </button>
        <button
          type="button"
          onClick={() => zoomBy(1.5)}
          className="pointer-events-auto flex h-5 w-5 items-center justify-center rounded-sm bg-black/70 text-lead font-bold leading-none text-white hover:bg-black/90"
          title="Zoom in"
        >
          +
        </button>
        {zoomed && (
          <button
            type="button"
            onClick={() => setTf(IDENTITY_TF)}
            className="pointer-events-auto rounded-sm bg-black/70 px-2 py-0.5 text-micro font-bold uppercase tracking-wide text-white hover:bg-black/90"
            title="Reset zoom"
          >
            Reset
          </button>
        )}
      </div>
    </div>
  )
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
    'The real Sentinel-1 SAR-derived vessel count for this quote\'s DESTINATION port anchorage ' +
    '(opt.anchorage.detect, a classical CFAR detector -- see that module for method and known failure ' +
    'modes) -- discharge-side congestion, never the origin/load port. This is a SPARSE spot check, not ' +
    'a live signal -- real revisit is roughly every 4-11 days at this port -- so the count is only ever ' +
    'shown alongside its real age. Scroll or pinch on the image to zoom in on individual detections, ' +
    'drag to pan, double-click to reset. The count itself is MODEL_DERIVED (a computation over the ' +
    'real, OBSERVED SAR scene); never blended into or replacing the live PortWatch-derived congestion ' +
    'signal.'

  if (loading) {
    return (
      <Panel className="h-full" id="anchorage" title="Anchorage Census (Satellite)" hint={hint}>
        <div className="flex h-full items-center justify-center text-center text-lead text-muted-foreground">
          Loading…
        </div>
      </Panel>
    )
  }

  if (error) {
    return (
      <Panel className="h-full" id="anchorage" title="Anchorage Census (Satellite)" hint={hint}>
        <div className="flex h-full items-center justify-center text-center text-lead text-risk">{error}</div>
      </Panel>
    )
  }

  if (!census) {
    return (
      <Panel className="h-full" id="anchorage" title="Anchorage Census (Satellite)" hint={hint}>
        <div className="flex h-full flex-col items-center justify-center gap-1 text-center text-lead text-muted-foreground">
          <span>No Sentinel-1 scene has been processed for {prettyPort(port)} yet.</span>
        </div>
      </Panel>
    )
  }

  return (
    <Panel
      className="h-full"
      id="anchorage"
      title="Anchorage Census (Satellite)"
      hint={hint}
      meta={`${prettyPort(census.port)} · destination`}
      flush={!imgFailed}
    >
      {imgFailed ? (
        <div className="flex h-full flex-col gap-2 p-2">
          <div className="flex items-baseline justify-between">
            <span className="desk-num text-figure-lg font-bold text-foreground">
              {formatNumber(census.vessel_count)}
            </span>
            <span className={cn('rounded-sm px-2 py-px text-caption font-bold uppercase tracking-wide', CONFIDENCE_CLASS[census.confidence])}>
              {census.confidence} confidence
            </span>
          </div>
          <div className="text-body text-muted-foreground">
            vessels detected in the anchorage box -- no overlay image rendered for this scene yet
          </div>
          <div className="mt-1 flex items-center gap-2 rounded border border-wait/40 bg-wait-soft px-2 py-1 text-body font-semibold text-wait">
            {formatRelativeAge(census.acquired_at)} ({formatIsoShort(census.acquired_at.slice(0, 10))})
          </div>
          <div className="mt-auto grid grid-cols-2 gap-1 text-caption text-muted-foreground">
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
          <ZoomableImage
            src={anchorageOverlayUrl(census.port)}
            alt={`Sentinel-1 SAR crop of ${prettyPort(census.port)}'s anchorage, ${census.vessel_count} vessel(s) ringed in red`}
            onError={() => setImgFailed(true)}
          />
          <div className="pointer-events-none absolute left-2 top-2 flex items-center gap-2">
            <span className="desk-num rounded-sm bg-black/75 px-2 py-0.5 text-figure font-bold text-white">
              {formatNumber(census.vessel_count)}
            </span>
            {/* The panel-body confidence tones (a 15% tint behind coloured
                text) are built for a white ground; laid over the near-black
                SAR plate the tint all but vanishes and the text drops to
                roughly 1:1 -- a low-confidence count, the one a reader most
                needs to distrust on sight, became the least legible thing on
                the image. On the overlay the chips are solid instead. */}
            <span
              className={cn(
                'rounded-sm px-2 py-0.5 text-micro font-bold uppercase tracking-wide',
                CONFIDENCE_CLASS_ON_DARK[census.confidence],
              )}
            >
              {census.confidence}
            </span>
          </div>
          {/* --wait (#a85a00) is tuned for dark-on-light and measures 4.13:1
              against this plate -- just under the floor. The scene's age is
              the single most important caveat on this panel, so it gets a
              light amber that clears AA on black. */}
          <div className="pointer-events-none absolute bottom-2 right-2 rounded-sm border border-[#f2b45c]/50 bg-black/75 px-2 py-0.5 text-caption font-semibold text-[#f2b45c]">
            {formatRelativeAge(census.acquired_at)} ({formatIsoShort(census.acquired_at.slice(0, 10))})
          </div>
        </div>
      )}
    </Panel>
  )
}
