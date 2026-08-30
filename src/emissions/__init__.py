"""IMO Carbon Intensity Indicator (CII) arithmetic and per-voyage projection.

``cii`` implements the pure formulas (fuel-to-CO2 conversion, attained/required
CII, A-E rating) straight from the IMO resolutions -- no vessel, no route, no
Vessel/PortEnum types. ``projection`` turns that arithmetic into a projection
for a real vessel on a real quoted route (real distances via
``opt.geography``), producing the ``VesselCIIProjection``/``VoyageEmissions``
types defined on ``opt.types``.

Scope: bulk carriers only (the only ship type this fleet optimizer prices),
years 2023-2026 only (the only years the IMO has published a reduction
factor). See ``cii``'s own module docstring for the primary-source citation
for every constant.
"""
from __future__ import annotations
