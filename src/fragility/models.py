"""Data model for the fragility sweep. No logic lives here -- see search.py
(the ladder+bisection algorithm), tiers.py (the three evaluators) and
engine.py (the public entry point that ties them together).
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict


class Provenance(str, Enum):
    """Where a variable's *base* value (the one actually being perturbed
    away from) comes from -- not to be confused with berth_truth's own
    LimitStatus/DraftStatus, which describe a *constraint's* provenance, not
    a decision variable's. Defined fresh here because MAY CHANGE for this
    task is scoped to src/fragility/ only; not shared with berth_truth."""

    USER_INPUT = "USER_INPUT"
    """Supplied directly by the caller of analyze_fragility -- cargo_volume_dwt,
    laycan dates, risk_tolerance, contract_term_days."""

    DERIVED = "DERIVED"
    """Computed deterministically from other real inputs -- e.g. the
    representative vessel's draft for the cargo's derived target class."""

    OBSERVED = "OBSERVED"
    """Read from real, current data -- opt.congestion.dynamic_wait_days with
    is_real_data=True, or a berth_truth register value with limit_status
    PUBLISHED and a current draft declaration."""

    ASSUMPTION = "ASSUMPTION"
    """A static fallback standing in for missing real data -- wait days from
    a port's static PortEnum baseline (dynamic_wait_days with
    is_real_data=False), or a register value that is NOT_PUBLISHED /
    SUPERSEDED / STALE_OR_UNAVAILABLE."""


class VariableId(str, Enum):
    """The eight v1 variables. Fixed, closed set -- see
    FragilityReport.limitations for why (single-variable only, this task)."""

    CARGO_VOLUME_DWT = "cargo_volume_dwt"
    ORIGIN_WAIT_DAYS = "origin_wait_days"
    DEST_WAIT_DAYS = "dest_wait_days"
    VESSEL_DRAFT_M = "vessel_draft_m"
    PERMISSIBLE_DRAFT_M = "permissible_draft_m"
    LAYCAN_WIDTH_DAYS = "laycan_width_days"
    RISK_TOLERANCE = "risk_tolerance"
    CONTRACT_TERM_DAYS = "contract_term_days"


class Tier(str, Enum):
    """Which evaluator answered a query -- reported so a caller can see how
    cheaply (or expensively) a flip point was actually established."""

    TIER1_CLOSED_FORM = "TIER1_CLOSED_FORM"
    """No solver: register arithmetic (opt.voyage._vessel_can_call) or the
    class-boundary rule (opt.ceiling.select_vessel_class_for_cargo)."""

    TIER2_FLEET_MIX = "TIER2_FLEET_MIX"
    """opt.fleetmix.enumerate_fleet_mix alone -- feasibility and the cheapest
    configuration, no CP-SAT voyage scheduling, no LSMC."""

    TIER3_FULL_QUOTE = "TIER3_FULL_QUOTE"
    """opt.quote.quote_envelope -- the complete decision, including
    LOCK/WAIT and the relaxation ladder."""


class DecisionSignature(BaseModel):
    """The parts of a decision a flip search compares before/after. Fields
    are populated only for what the tier that computed this signature can
    actually determine -- a Tier 1 signature has target_vessel_class and
    nothing else; a Tier 3 signature has everything. Two signatures are only
    ever compared when both came from the SAME tier (search.py never mixes
    tiers within one variable's search), so "populated in one, None in the
    other" never arises in a real comparison -- see search.py.
    """

    model_config = ConfigDict(frozen=True)

    envelope_status: str | None = None
    """"feasible" | "structural_infeasible" | "contingent_infeasible" --
    Tier 3 only. A feasible -> contingent_infeasible transition is a flip in
    its own right (requirement 1), not merely a side effect of some other
    field changing."""

    lock_action: str | None = None
    """"LOCK" | "WAIT" -- Tier 3 only."""

    target_vessel_class: str | None = None
    """VesselClass.value -- available from Tier 1 upward (a pure function of
    cargo_volume_dwt via opt.ceiling.select_vessel_class_for_cargo)."""

    chosen_config_id: str | None = None
    """A stable id for the cheapest feasible opt.fleetmix configuration
    (f"{class}:{n_vessels}:{'T' if transshipment else 'D'}") -- Tier 2
    upward. None when no configuration is feasible."""

    feasibility_set: frozenset[str] | None = None
    """VesselClass.value for every class opt.fleetmix found feasible -- Tier
    2 upward. At Tier 1, a draft/permissible-draft search instead reports
    is_feasible_at_binding_port (see FlipPoint) directly, since Tier 1 only
    ever checks one class at one port, not the full frontier."""

    def changed_fields(self, other: DecisionSignature) -> tuple[str, ...]:
        """Field names present (non-None) on both sides that differ. Never
        counts a field populated on only one side as changed -- that would
        conflate 'the tier changed' with 'the decision changed'."""
        changed = []
        for field in type(self).model_fields:
            mine = getattr(self, field)
            theirs = getattr(other, field)
            if mine is not None and theirs is not None and mine != theirs:
                changed.append(field)
        return tuple(changed)


class BerthTruthContext(BaseModel):
    """P5: real P2 (Berth Truth / M4) evidence attached to a finding --
    filling in what this field was always meant to hold (see its own
    docstring history: originally left null pending "Track B1/B2 data",
    which now exists). Every field is independently nullable -- a port with
    draft data but no wait sample still gets an honest partial context, and
    a variable with no berth-truth relevance at all (cargo_volume_dwt,
    laycan_width_days, risk_tolerance, contract_term_days) gets none of
    this populated, not zeros standing in for absence."""

    model_config = ConfigDict(frozen=True)

    draft_status: str | None = None
    """berth_truth.models.DraftStatus.value at the binding berth, as of the
    query date -- DECLARED / STALE_OR_UNAVAILABLE / etc."""
    draft_source: str | None = None
    """berth_truth.models.DraftSource.value -- which document class the
    draft figure traces to."""
    draft_as_of: str | None = None
    """ISO date the draft declaration was resolved against, when DECLARED."""

    tide_impact: str | None = None
    """berth_truth.tide.TideImpact.value -- NONE / CONDITIONAL / BLOCKING."""
    tide_authority: str | None = None
    """berth_truth.tide.TideAuthority.value -- PORT_RULE / ADVISORY_MODEL.
    Never populated as ADVISORY_MODEL feeding a feasibility flip (P2's own
    enforcement: advisory tide input can never gate a verdict)."""
    tide_reason: str | None = None

    empirical_wait_p50_hours: float | None = None
    empirical_wait_p90_hours: float | None = None
    empirical_wait_sample_n: int | None = None
    empirical_wait_is_sufficient: bool | None = None
    """False (not None) when a real sample exists but is too thin to trust
    (berth_truth.empirical.MINIMUM_SAMPLE_SIZE) -- distinct from no sample
    at all, where every empirical_wait_* field above stays None."""


class FlipPoint(BaseModel):
    """One variable's search result."""

    model_config = ConfigDict(frozen=True)

    variable: VariableId
    tier: Tier
    """Which evaluator actually found (or ruled out) the flip -- the
    cheapest one that could answer, per the escalation rule."""

    provenance: Provenance
    base_value: float
    unit: str

    flip_found: bool
    flip_value: float | None = None
    absolute_delta: float | None = None
    percent_delta: float | None = None
    direction: str | None = None
    """"increase" | "decrease" -- which way the variable moved to flip."""

    base_signature: DecisionSignature | None = None
    flipped_signature: DecisionSignature | None = None
    changed_components: tuple[str, ...] = ()

    range_searched_low: float | None = None
    range_searched_high: float | None = None
    """Populated on flip_found=False -- requirement 7: 'plus the range
    actually searched', never a silent empty result."""

    evaluations_used: int = 0

    unavailable_reason: str | None = None
    """Populated instead of everything above when this variable's base
    limit is NOT_PUBLISHED or STALE_OR_UNAVAILABLE (requirement 3) -- no
    search was attempted, so flip_found is meaningless here (left False by
    convention, but unavailable_reason is what a caller should actually
    check)."""

    berth_truth_context: BerthTruthContext | None = None
    """P5: real P2 evidence (draft resolution detail, tide sensitivity,
    empirical wait percentiles) where it exists for this variable/binding
    port -- see BerthTruthContext. Still null for variables with no
    berth-truth relevance (cargo_volume_dwt, laycan_width_days,
    risk_tolerance, contract_term_days) and for a binding port with no
    register coverage at all -- populated only where real evidence backs
    it, never a placeholder."""


class FragilityReport(BaseModel):
    """Everything analyze_fragility produced for one decision query."""

    model_config = ConfigDict(frozen=True)

    current_decision: DecisionSignature
    """The full Tier 3 signature at the base point -- always computed once,
    since the caller needs to know what decision is even being tested for
    fragility."""

    findings: tuple[FlipPoint, ...]
    evaluations_used: int
    """Total across every variable searched, including the one Tier 3 call
    for current_decision itself."""

    # F-30 fix: these render verbatim on the Fragility screen's Limitations
    # panel, so they must read as plain English -- the second and third
    # entries used to name internal modules/functions and an internal
    # build-phase label ("P5", "P2"), the exact anti-pattern this fix
    # removes elsewhere in this module (see _WAIT_DAYS_UNAVAILABLE_REASON
    # in fragility/engine.py for the fuller rationale, which still applies
    # to the third point here).
    limitations: tuple[str, ...] = (
        (
            "Single-variable only: each finding perturbs exactly one input with "
            "every other input held at its base value. Interaction effects "
            "between variables (e.g. wait days AND draft moving together) are "
            "not searched."
        ),
        (
            "The real berth/tide/wait context shown alongside a finding is only "
            "available for draft- and tide-related variables at a port with real "
            "register or empirical coverage -- it is a few specific figures, not "
            "a full comparison against everything that port's data could show."
        ),
        (
            "Wait-time variables (how long a vessel queues at the origin or "
            "destination port) can't be tested for sensitivity: nothing in the "
            "pricing or scheduling logic currently takes a wait-day figure as an "
            "input, so there is no lever to search for a flip point. The real "
            "wait data is still shown as context where available -- it just "
            "doesn't change the recommendation on its own yet."
        ),
    )
