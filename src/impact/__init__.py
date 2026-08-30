"""Footprint: pricing SAIL's own market impact and executing around it.

**STATUS (P6): EXPERIMENTAL, NOT CONNECTED.** Nothing in ``opt/``,
``backend/main.py``, or ``frontend/`` imports this package. It is kept in the
tree as a documented, honestly-labelled research artifact -- not deleted,
because the code is real and may become usable if its two upstream inputs are
later fixed -- but it must not be wired into any live decision path or product
claim until they are.

Turns the Tonnage Field's forecast from ``forecast(market_state)`` into
``forecast(market_state, our_plan)``: ``elasticity.py`` estimates local
d(rate)/d(demand), ``execution.py`` runs an Almgren-Chriss-style optimal
execution schedule against it, ``fixedpoint.py`` solves the damped fixed point
between the two. See ``docs/plan.md`` Part 2 Component 2 and Part 3 (Target
architecture) for the original design intent.

**Why experimental, precisely.** Both of this package's upstream inputs were
evaluated for real by the P3 identification gate and sign-diagnosis toolkit
(``tonnage/identification.py``, live-computed against real harvested data, not
asserted), and both came back invalidated for this use:

1. ``elasticity.local_elasticity`` divides by ``stockflow``'s ``stock_dwt`` to
   get a footprint fraction. The identification gate formally classifies
   ``stock_dwt`` as ``IndexType.RELATIVE`` (a physical supply-pressure index),
   not ``ABSOLUTE`` (a real free-tonnage headcount): no evidence source on
   disk distinguishes an available/ballasting vessel from a committed one, and
   the reconstruction's implied ballaster count runs 0.35x-26x real Signal
   Ocean ballaster counts across 10 real comparison points, with no single
   correction factor that fixes all of them (``tonnage.validate.SignalValidationSummary``).
   A footprint fraction computed against a index that is not on an absolute
   scale is not a real footprint fraction.
2. ``elasticity.local_elasticity`` also multiplies by ``supplycurve``'s fitted
   rate~tightness slope. The sign-diagnosis toolkit found real problems for
   every one of the four vessel classes, not just "two of four" as earlier
   estimated: Capesize (r=-0.022) and Handysize (r=-0.238) are wrong-signed at
   the pooled level, and first-differencing flips both back positive --
   consistent with a shared time trend confounding the level correlation
   rather than a genuine inverse physical relationship. Handysize additionally
   flips sign between the first half (r=+0.492) and second half (r=-0.300) of
   the sample outright. Panamax (r=0.311) and Supramax (r=0.746) are
   correctly signed but regime-unstable -- first-differencing collapses both
   toward zero (0.311->0.004, 0.746->0.093) and a first-half/second-half split
   swings materially (Panamax 0.300->0.651; Supramax 0.808->0.089) -- so even
   the two "clean-looking" classes are not stable enough to hang a normative
   execution schedule on. (Full per-class evidence:
   ``tonnage.identification.diagnose_all_signs``.)

``execution.py``'s own module docstring separately notes there is no
historical "correct answer" to validate a normative optimization framework
against the way there is for a forecast -- so even with a sound elasticity
input, the execution/fixed-point layer would still be unvalidated by
construction. Modules here continue to propagate confidence flags rather than
launder them into a single clean-looking number; that discipline is what
surfaced the findings above, and it is why the package is labelled
experimental instead of silently shipped.
"""
