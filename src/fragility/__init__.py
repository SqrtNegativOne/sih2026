"""Decision Fragility -- "how far is this recommendation from changing?"

A robustness layer over the existing decision engines (opt.voyage,
opt.fleetmix, opt.ceiling, opt.stopping, opt.quote), reached exclusively
through opt.quote.quote_envelope and a small number of already-existing
closed-form functions (opt.voyage._vessel_can_call,
opt.ceiling.select_vessel_class_for_cargo). This package computes no new
market number, no new feasibility rule, and no new decision of its own -- it
perturbs one input at a time and asks, of an engine that already exists,
"does the answer change." See engine.analyze_fragility for the entry point.

Not re-exported eagerly from this ``__init__``: importing ``engine`` pulls in
``opt.quote`` (and transitively ``ml.live_forecast``), the same reason
``opt/__init__.py`` itself doesn't re-export ``opt.quote`` -- see that
module's own docstring. Import what you need directly, e.g.
``from fragility.engine import analyze_fragility``.
"""
from __future__ import annotations
