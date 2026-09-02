import { GLOSSARY, PROVENANCE, type ProvenanceKind } from '@/lib/vocabulary'
import { cn } from '@/lib/utils'

/**
 * A domain term with its definition available on demand.
 *
 * Rendered as a real `<abbr>` with a dotted underline rather than a bare
 * `title` on a span: `<abbr>` is what assistive technology announces as "has a
 * definition", the underline is the only affordance telling a sighted reader
 * there is something to hover, and `tabIndex` means a keyboard user can reach
 * it at all. A `title` attribute alone is invisible and unreachable — which is
 * how most of this desk's best explanatory text was hidden.
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
    <abbr
      title={definition}
      tabIndex={0}
      className={cn(
        'cursor-help underline decoration-dotted decoration-muted-foreground/60 underline-offset-2',
        'transition-colors hover:decoration-foreground focus-visible:outline-2',
        'focus-visible:outline-offset-1 focus-visible:outline-ring',
        className,
      )}
    >
      {children}
    </abbr>
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
    <span
      className={cn('desk-chip desk-chip-neutral cursor-help', className)}
      title={`${t.definition} (${kind})`}
    >
      {t.label}
    </span>
  )
}
