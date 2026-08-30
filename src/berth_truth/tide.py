"""Tide authority separation -- P2 §2.

Two real, distinct tide facts already existed in ``registry.py`` before this
module, seeded from real documents, never previously read by any decision
path:

* **Gangavaram berths 4/5/6** carry a real ``tide_rule`` TEXT from the
  BPTS's own §21: *"Berthing/Un-berthing POB: any time except for loaded
  Cape size vessels, which are subject to tidal restriction."* This is
  qualitative and vessel-state-conditional -- it does not apply at all to a
  non-Capesize or empty vessel, and for a laden Capesize it names a real
  restriction this codebase has no tide-timetable data to resolve into an
  actual window. That is exactly the case the spec calls out: a real
  ``PORT_RULE`` restriction with no valid window available -> ``CONDITIONAL``
  (this repo's spelling of "cannot silently pass"), never a silent clear.
* **Vizag's inner/outer harbour berths** carry real ``tide_allowance_m``
  NUMBERS from the same authoritative document (VPA's own 2021 table) --
  unconditional, already-published additional draft margin, not a live
  tide-state check. Structurally different from Gangavaram's case: this is
  simply part of the declared limit, safe to treat as ``NONE`` impact
  (nothing further needs verifying) *provided* the source document itself is
  still current -- which Vizag's is not (``LimitStatus.SUPERSEDED``), so it
  additionally demonstrates the "real PORT_RULE authority, but stale" case.

No ``ADVISORY_MODEL`` (FES/pyTMD/Open-Meteo/...) source exists anywhere in
this repo. The hard separation below is enforced at the function level: an
``ADVISORY_MODEL`` input can never produce ``TideImpact.NONE`` -- it can only
ever widen a verdict toward ``CONDITIONAL``, proven in
tests/berth_truth/test_tide.py by constructing one and showing it cannot
clear a vessel a PORT_RULE-only assessment would otherwise clear.
"""
from __future__ import annotations

import re
from enum import Enum

from pydantic import BaseModel, ConfigDict

from berth_truth.models import BerthConstraint, LimitStatus

__all__ = [
    "TideAssessment",
    "TideAuthority",
    "TideImpact",
    "assess_tide",
]


class TideAuthority(str, Enum):
    PORT_RULE = "PORT_RULE"
    """A port-authority operational rule or notice -- cited to a real
    document (BerthConstraint.source_doc_id). Authoritative: may gate
    feasibility."""

    ADVISORY_MODEL = "ADVISORY_MODEL"
    """A scientific tide model (FES, pyTMD, Open-Meteo, or similar).
    Advisory only -- can never satisfy or override a navigational check on
    its own. May only widen uncertainty."""


class TideImpact(str, Enum):
    NONE = "NONE"
    """No tide consideration applies, or a PORT_RULE-sourced allowance is
    already fully resolved and current."""

    CONDITIONAL = "CONDITIONAL"
    """A real PORT_RULE restriction applies to this vessel/state and this
    codebase has no tide-timetable data to resolve an actual window --
    or the sourcing document itself is stale. Renders as CANNOT_VERIFY at
    the PortRealityReport level, never a silent pass."""

    BLOCKING = "BLOCKING"
    """Reserved for a restriction resolvable as a hard no (e.g. a berth
    stated as tide-inaccessible outright). Not reached by any real data
    currently in the register -- included for completeness of the 3-value
    contract the spec asks for, not fabricated to have a live example."""


class TideAssessment(BaseModel):
    model_config = ConfigDict(frozen=True)

    impact: TideImpact
    authority: TideAuthority | None
    rule_text: str | None = None
    allowance_m: float | None = None
    source_doc_id: str | None = None
    source_is_current: bool | None = None
    reason: str | None = None


# Vessel classes the real Gangavaram BPTS §21 text names explicitly --
# "loaded Cape size vessels". Matched case-insensitively against the class
# name a caller supplies; nothing else in the rule text names a class, so
# no other class is treated as conditional by it.
_CAPESIZE_MARKER_RE = re.compile(r"\bcape\s*size\b", re.IGNORECASE)
_LOADED_MARKER_RE = re.compile(r"\bloaded\b", re.IGNORECASE)


def assess_tide(
    constraint: BerthConstraint,
    *,
    vessel_class: str | None = None,
    vessel_is_laden: bool | None = None,
) -> TideAssessment:
    """Real tide assessment for one resolved berth constraint. Reads
    ``constraint.tide_rule`` / ``tide_allowance_m`` -- the fields have
    existed on ``BerthConstraint`` since BT-1 but were never read by any
    decision path before this function.

    Authority is always ``PORT_RULE`` here: every tide field on
    ``BerthConstraint`` is sourced from the same cited document as the
    berth's other limits (``source_doc_id``) -- there is no advisory-model
    input into the register. ``assess_tide_advisory_only`` below is the
    enforcement boundary for a hypothetical advisory value.
    """
    if constraint.tide_rule is None and constraint.tide_allowance_m is None:
        return TideAssessment(impact=TideImpact.NONE, authority=None)

    source_is_current = constraint.limit_status is LimitStatus.PUBLISHED

    if constraint.tide_rule is not None:
        applies_to_this_vessel = _LOADED_MARKER_RE.search(constraint.tide_rule) and _CAPESIZE_MARKER_RE.search(
            constraint.tide_rule
        )
        vessel_matches = (
            applies_to_this_vessel
            and vessel_class is not None
            and "cape" in vessel_class.lower()
            and vessel_is_laden is True
        )
        if vessel_matches:
            return TideAssessment(
                impact=TideImpact.CONDITIONAL,
                authority=TideAuthority.PORT_RULE,
                rule_text=constraint.tide_rule,
                source_doc_id=constraint.source_doc_id,
                source_is_current=source_is_current,
                reason=(
                    f"berth {constraint.berth_id}: real tidal restriction applies to this "
                    f"vessel (laden Capesize) per {constraint.source_doc_id!r}, but no "
                    f"tide-timetable data exists in this system to resolve an actual "
                    f"window -- cannot silently clear."
                ),
            )
        # Real rule exists but does not name this vessel's state (e.g. a
        # ballast vessel, or a vessel class the rule text does not mention)
        # -- the rule is disclosed but does not gate this specific query.
        return TideAssessment(
            impact=TideImpact.NONE,
            authority=TideAuthority.PORT_RULE,
            rule_text=constraint.tide_rule,
            source_doc_id=constraint.source_doc_id,
            source_is_current=source_is_current,
            reason=f"berth {constraint.berth_id}: tidal rule on record does not apply to this vessel/state.",
        )

    # tide_allowance_m only (Vizag): an unconditional, already-published
    # additional draft margin, not a live tide-state check -- NONE impact
    # if the source is still current; CONDITIONAL (stale sourcing) if not.
    if not source_is_current:
        return TideAssessment(
            impact=TideImpact.CONDITIONAL,
            authority=TideAuthority.PORT_RULE,
            allowance_m=constraint.tide_allowance_m,
            source_doc_id=constraint.source_doc_id,
            source_is_current=False,
            reason=(
                f"berth {constraint.berth_id}: tide allowance of {constraint.tide_allowance_m}m is "
                f"real and PORT_RULE-sourced, but its document ({constraint.source_doc_id!r}) is "
                f"{constraint.limit_status.value}, not current -- cannot rely on it without "
                f"confirming it still holds."
            ),
        )
    return TideAssessment(
        impact=TideImpact.NONE,
        authority=TideAuthority.PORT_RULE,
        allowance_m=constraint.tide_allowance_m,
        source_doc_id=constraint.source_doc_id,
        source_is_current=True,
    )


def assess_tide_advisory_only(*, model_name: str, suggests_clear: bool) -> TideAssessment:
    """The enforcement boundary for a hypothetical advisory-model tide input
    (FES/pyTMD/Open-Meteo/...) -- no such source is wired into this repo's
    register; this function exists so the authority separation is provable
    even though no real advisory data exists to assess. However confident
    the model is that conditions are clear, an ADVISORY_MODEL input can
    never produce TideImpact.NONE on its own -- it may only ever widen
    toward CONDITIONAL, because it cannot satisfy or override a
    navigational check by itself (see the module docstring)."""
    return TideAssessment(
        impact=TideImpact.CONDITIONAL,
        authority=TideAuthority.ADVISORY_MODEL,
        reason=(
            f"{model_name} is an advisory model, not a port-authority rule -- even reporting "
            f"suggests_clear={suggests_clear}, it cannot by itself clear a vessel. Widens "
            f"uncertainty only; a PORT_RULE source or direct port confirmation is required "
            f"to resolve this to NONE."
        ),
    )
