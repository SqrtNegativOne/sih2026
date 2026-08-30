"""Tonnage Field: a physical supply-side twin of the dry bulk market.

Reconstructs, from free public data (IMF PortWatch port calls), a daily estimate of
how much dry-bulk carrying capacity of each vessel class is free in each ocean basin,
projects it forward through voyage physics, and prices rate as a function of the
resulting supply/demand tightness. See ``docs/plan.md`` Part 2 for the design and
Part 4 P2 for the milestone gates this package is built against.
"""
