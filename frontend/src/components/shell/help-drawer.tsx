import { AnimatePresence, motion } from 'motion/react'
import { X } from 'lucide-react'
import { useEffect } from 'react'
import { GLOSSARY } from '@/lib/vocabulary'

/**
 * Help: what the desk does, what its words mean, and how it treats its own
 * numbers.
 *
 * Replaces a "Help — not implemented" button. The glossary is generated from
 * `lib/vocabulary`'s GLOSSARY rather than retyped, so the panel and the inline
 * term tooltips can never drift apart — one map, two surfaces.
 *
 * The last section is deliberate. "How this desk treats numbers" is the thing
 * that most distinguishes this product, it is enforced by a test that breaks
 * the build, and it belongs somewhere a reader can find it on purpose rather
 * than only meeting it as a chip in the corner of a panel.
 */
export function HelpDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  const terms = Object.entries(GLOSSARY).sort(([a], [b]) => a.localeCompare(b))

  return (
    <AnimatePresence>
      {open && (
        <>
          <motion.div
            className="fixed inset-0 z-50 bg-black/30"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={onClose}
            aria-hidden="true"
          />
          <motion.aside
            role="dialog"
            aria-modal="true"
            aria-label="Help"
            className="fixed right-0 top-0 z-50 flex h-full w-[420px] max-w-[92vw] flex-col border-l border-border bg-surface shadow-raised"
            initial={{ x: '100%' }}
            animate={{ x: 0 }}
            exit={{ x: '100%' }}
            transition={{ type: 'spring', stiffness: 320, damping: 34 }}
          >
            <div className="flex h-10 shrink-0 items-center justify-between border-b border-border bg-navbar px-3 text-navbar-foreground">
              <span className="text-lead font-bold uppercase tracking-wide">Help</span>
              <button
                type="button"
                onClick={onClose}
                aria-label="Close help"
                title="Close (Esc)"
                className="rounded-sm p-1 transition-colors hover:bg-white/10 focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-white"
              >
                <X className="h-4 w-4" aria-hidden="true" />
              </button>
            </div>

            <div className="min-h-0 flex-1 space-y-5 overflow-y-auto p-4">
              <section>
                <h3 className="text-caption font-bold uppercase tracking-[0.04em] text-primary">
                  What this desk does
                </h3>
                <p className="mt-2 text-body leading-relaxed text-muted-foreground">
                  It answers one question: <strong className="text-foreground">should you fix
                  this charter today, or wait?</strong> It prices today&apos;s rate against a
                  model of where the market is going, prices the option value of being able to
                  wait, checks the route and both ports are physically feasible, and shows the
                  evidence behind every part of that.
                </p>
              </section>

              <section>
                <h3 className="text-caption font-bold uppercase tracking-[0.04em] text-primary">
                  The screens
                </h3>
                <ul className="mt-2 space-y-2">
                  {[
                    ['Voyage Desk', 'The decision: verdict, walk-away line, forecast, route, timeline, cost and exposure.'],
                    ['Port Twin', 'One port in depth — berth limits, tide rules, and the real waiting-time record.'],
                    ['Tonnage Field', 'How tight vessel supply is by class and basin, as a relative index.'],
                    ['Fragility', 'How far the inputs can move before the recommendation changes.'],
                    ['Ledger', 'Every recommendation this system has made, scored against what actually happened.'],
                    ['Portfolio', 'The spot / period-TC / COA coverage mix against a stockout-risk penalty.'],
                  ].map(([name, what]) => (
                    <li key={name} className="flex gap-2">
                      <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-structure" aria-hidden="true" />
                      <span className="text-caption leading-relaxed">
                        <span className="font-semibold text-foreground">{name}</span>{' '}
                        <span className="text-muted-foreground">— {what}</span>
                      </span>
                    </li>
                  ))}
                </ul>
              </section>

              <section>
                <h3 className="text-caption font-bold uppercase tracking-[0.04em] text-primary">
                  How this desk treats numbers
                </h3>
                <p className="mt-2 text-caption leading-relaxed text-muted-foreground">
                  Nothing on screen is invented. Where a figure cannot be computed from real data
                  the desk says so and shows why, instead of substituting a plausible default —
                  which is why you will see &ldquo;1/5 components real&rdquo; or &ldquo;insufficient
                  sample (n=0)&rdquo; rather than a tidy total. Every value carries where it came
                  from: <em>measured</em> is read off a real source, <em>modelled</em> is the output
                  of a fitted model, <em>stated</em> is asserted by a port or entered by you. This
                  is enforced by a test that fails the build if any synthetic-data pattern appears
                  in the interface.
                </p>
              </section>

              <section>
                <h3 className="text-caption font-bold uppercase tracking-[0.04em] text-primary">
                  Glossary
                </h3>
                <dl className="mt-2 space-y-2">
                  {terms.map(([term, def]) => (
                    <div key={term}>
                      <dt className="text-caption font-semibold capitalize text-foreground">
                        {term}
                      </dt>
                      <dd className="text-caption leading-relaxed text-muted-foreground">{def}</dd>
                    </div>
                  ))}
                </dl>
              </section>
            </div>
          </motion.aside>
        </>
      )}
    </AnimatePresence>
  )
}
