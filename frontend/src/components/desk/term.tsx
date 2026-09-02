import { Tooltip } from '@/components/ui/tooltip'
import { GLOSSARY, PROVENANCE, type ProvenanceKind } from '@/lib/vocabulary'
import { cn } from '@/lib/utils'

/**
 * A domain term with its definition available on demand.
 *
 * The dotted underline is the only affordance telling a sighted reader there
 * is something here to ask about, and going through the shared Tooltip means
 * the definition opens on hover AND on keyboard focus, in the desk's own
 * surface rather than OS chrome. A bare `title` attribute is invisible,
 * slow and unreachable by keyboard — which is how most of this desk's best
 * explanatory text stayed hidden.
 *
 * Domain vocabulary is explained, never replaced. "Laycan" is the correct word
 * and the people using this tool use it; the goal is that someone new is not
 * stopped by it, not that the desk stops speaking the language of the trade.
 */
export function Term({
  children,
  term,
  className,
}: {
  children?: React.ReactNode
  /** Key into GLOSSARY. Defaults to the rendered text, lowercased. */
  term?: string
  className?: string
}) {
  const key = (term ?? (typeof children === 'string' ? children : '')).toLowerCase()
  const definition = GLOSSARY[key] ?? GLOSSARY[term ?? '']
  if (!definition) return <>{children}</>

  return (
    <Tooltip
      content={
        <>
          <span className="font-semibold text-foreground">{children}</span>
          <span className="mt-1 block">{definition}</span>
        </>
      }
      className={cn('align-baseline', className)}
    >
      <span className="underline decoration-dotted decoration-muted-foreground/60 underline-offset-2 transition-colors hover:decoration-foreground">
        {children}
      </span>
    </Tooltip>
  )
}

/**
 * The provenance chip: how a number came to exist.
 *
 * These read as clutter to a designer and as the whole point to anyone
 * deciding whether to trust a figure, so the treatment is deliberate rather
 * than apologetic — and the label is the plain-English word, with the enum the
 * codebase actually uses kept in the tooltip so nothing is lost.
 */
export function ProvenanceChip({
  kind,
  className,
}: {
  kind: ProvenanceKind
  className?: string
}) {
  const t = PROVENANCE[kind]
  if (!t) return null
  return (
    <Tooltip
      content={
        <>
          <span className="font-semibold text-foreground">{t.label}</span>
          <span className="mt-1 block">{t.definition}</span>
          <span className="mt-1 block font-mono text-micro text-muted-foreground">{kind}</span>
        </>
      }
    >
      <span className={cn('desk-chip desk-chip-neutral', className)}>{t.label}</span>
    </Tooltip>
  )
}
