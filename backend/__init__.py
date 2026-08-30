"""Thin FastAPI service wrapping ``opt.quote.quote()`` -- the product layer
named in ``docs/plan.md``'s own target architecture (Part 3: "backend/
FastAPI service"). Deliberately thin: every real computation lives in
``src/opt``/``src/ml``/``src/tonnage``/``src/impact`` and is fully testable
without this package; this package only translates HTTP <-> those real
Python calls and shapes JSON a frontend can consume (port codes as strings,
not embedded objects -- see ``backend.serialize``).
"""
