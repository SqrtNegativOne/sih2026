"""Berth Truth -- primary-source port data the optimizer's PortEnum can't see.

This first slice (BT-0) is a snapshot archiver only: it captures Adani's live
Dhamra and Gangavaram vessel-schedule pages, which expose no historical
archive of their own, so every day uncaptured is history permanently lost.
Nothing here computes a berth constraint, a wait time, or a feasibility
verdict -- that is BT-1 (constraint register) and later derive.py.

Deliberately not re-exported from this ``__init__``: importing ``archiver``
pulls in ``requests`` at package-import time, which every other module in
this package (``models``, most of the future registry) has no reason to pay
for. Import what you need directly, e.g. ``from berth_truth import archiver``.
"""
from __future__ import annotations
