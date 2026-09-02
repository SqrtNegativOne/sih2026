import { Disclosure } from '@/components/desk/disclosure'
import type { Explanation } from '@/lib/types'

/**
 * The backend writes a plain-English rationale for five separate decisions --
 * `explanations.lock_wait`, `.savings`, `.fleet_mix`, `.voyage_assignments[]`
 * and `.repositioning[]` -- each with a one-line summary, the factors that
 * drove it, and the method behind it.
 *
 * Four of the five were being fetched on every quote and discarded; only
 * lock_wait was rendered. That was the largest readability gap in the product
 * and the cheapest to close, because the hard part -- writing the reasoning in
 * language a charterer actually uses -- was already done server-side. Nothing
 * here paraphrases or re-derives; it only presents what the solver said.
 *
 * Rendered through the desk's single Disclosure so the gesture is identical
 * wherever a user meets it, and collapsed by default so a paragraph never
 * competes with the figure it explains.
 */
export function ExplanationBlock({
  explanation,
  label = 'Why this',
  className,
}: {
  explanation: Explanation | null | undefined
  label?: string
  className?: string
}) {
  if (!explanation) return null
  const { summary, factors, method } = explanation
  if (!summary && factors.length === 0) return null

  return (
    <Disclosure label={label} className={className}>
      <div className="space-y-2">
        {summary && (
          <p className="text-caption leading-relaxed text-foreground">{summary}</p>
        )}
        {factors.length > 0 && (
          <ul className="space-y-1 text-caption leading-relaxed text-muted-foreground">
            {factors.map((f, i) => (
              <li key={i} className="flex gap-1.5">
                <span
                  className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-market"
                  aria-hidden="true"
                />
                <span>{f}</span>
              </li>
            ))}
          </ul>
        )}
        {method && (
          <p className="border-t border-border/60 pt-1 text-micro italic leading-relaxed text-muted-foreground">
            {method}
          </p>
        )}
      </div>
    </Disclosure>
  )
}

/**
 * The list-valued explanations (`voyage_assignments`, `repositioning`) carry
 * one entry per decision made. Rendered as one disclosure holding all of them
 * rather than one disclosure each, so a schedule with six assignments does not
 * become six separate chevrons a user has to open in turn.
 */
export function ExplanationList({
  explanations,
  label,
  className,
}: {
  explanations: Explanation[] | null | undefined
  label: string
  className?: string
}) {
  if (!explanations || explanations.length === 0) return null

  return (
    <Disclosure label={`${label} (${explanations.length})`} className={className}>
      <div className="space-y-3">
        {explanations.map((e, i) => (
          <div key={i} className="space-y-1">
            {e.summary && (
              <p className="text-caption font-medium leading-relaxed text-foreground">
                {e.summary}
              </p>
            )}
            {e.factors.length > 0 && (
              <ul className="space-y-1 text-caption leading-relaxed text-muted-foreground">
                {e.factors.map((f, j) => (
                  <li key={j} className="flex gap-1.5">
                    <span
                      className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-market"
                      aria-hidden="true"
                    />
                    <span>{f}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        ))}
      </div>
    </Disclosure>
  )
}
