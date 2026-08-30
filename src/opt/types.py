"""Shared types and Pydantic models for the optimizer."""
from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from berth_truth.models import BerthConstraint, DraftSource, DraftStatus
from data_builders.provenance import Provenance
from opt.network import PortEnum, RouteEnum, RouteFamily
from opt.weather_window import TransitBuffer


class VesselClass(str, Enum):
    CAPESIZE = "Capesize"
    PANAMAX = "Panamax"
    SUPRAMAX = "Supramax"
    HANDYSIZE = "Handysize"

#: Representative single-shipment carrying capacity per class, in DWT -- the
#: same reference figures opt.fleetmix._CLASS_SPECS uses for port-viability
#: checks. Used to derive the vessel class a cargo lot of a given size would
#: naturally move on (opt.ceiling.select_vessel_class_for_cargo), so callers
#: describe the cargo they actually have rather than a vessel class they'd
#: otherwise have to already know.
CLASS_REFERENCE_DWT: dict[VesselClass, float] = {
    VesselClass.HANDYSIZE: 32_000.0,
    VesselClass.SUPRAMAX: 58_000.0,
    VesselClass.PANAMAX: 82_000.0,
    VesselClass.CAPESIZE: 180_000.0,
}

class ForecastFan(BaseModel):
    model_config = ConfigDict(frozen=True)

    vessel_class: VesselClass
    horizon_days: Literal[7, 30, 90]
    p10: float
    p50: float
    p90: float

    @model_validator(mode='after')
    def check_quantiles(self) -> ForecastFan:
        if not (self.p10 <= self.p50 <= self.p90):
            raise ValueError(f"Quantile order violated: p10={self.p10} p50={self.p50} p90={self.p90}")
        if self.p10 <= 0:
            raise ValueError("TCE forecasts must be positive USD/day values.")
        return self

class BasisEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    route_family: RouteFamily
    basis_mean: float
    basis_std: float


class RouteEvidenceLevel(str, Enum):
    """P4 API contract, verbatim -- mirrors opt.basis.RouteEvidence (kept as
    a separate enum here rather than importing opt.basis into opt.types, to
    avoid opt.types depending on a heavier module purely for a 3-value
    label; opt.quote is responsible for keeping the two in sync, asserted by
    tests/opt/test_basis.py)."""

    OBSERVED = "OBSERVED"
    MODELLED = "MODELLED"
    ROUTE_RATE_BASIS_UNAVAILABLE = "ROUTE_RATE_BASIS_UNAVAILABLE"

class Vessel(BaseModel):
    model_config = ConfigDict(frozen=True)

    vessel_id: str
    vessel_class: VesselClass
    current_port: PortEnum
    status: Literal["idle", "on_tc", "on_voyage"]
    available_from: date
    available_until: date | None = None
    dwt: float
    draft_m: float
    loa_m: float
    beam_m: float
    speed_kn: float
    laden_fuel_consumption_tpd: float
    ballast_fuel_consumption_tpd: float

class VesselCIIProjection(BaseModel):
    """Projected IMO Carbon Intensity Indicator rating for one real vessel on
    one real quoted route -- emissions.projection.project_vessel_cii. Every
    figure here is computed from real vessel/route inputs (emissions.cii,
    against real IMO resolution constants); nothing is a placeholder."""
    model_config = ConfigDict(frozen=True)

    vessel_id: str
    vessel_class: VesselClass
    dwt: float
    laden_distance_nm: float
    ballast_distance_nm: float
    sea_days: float
    fuel_tonnes: float
    co2_tonnes: float
    attained_cii: float
    required_cii: float
    rating: Literal["A", "B", "C", "D", "E"]
    rating_year: int
    margin_pct: float  # (required - attained)/required * 100; positive = better than required
    is_distance_fallback: bool  # true if any leg used a great-circle approximation, not a real sea route
    provenance: str  # "MODEL_DERIVED" -- see data_builders.provenance


class VoyageEmissions(BaseModel):
    """CII projections for every real vessel on a quote's route --
    emissions.projection.project_voyage_emissions. None on QuoteResult
    exactly when there is no vessel to rate or the rating year isn't
    published (see that function's docstring)."""
    model_config = ConfigDict(frozen=True)

    as_of: date
    rating_year: int
    projections: tuple[VesselCIIProjection, ...]
    fleet_worst_rating: Literal["A", "B", "C", "D", "E"] | None


class CargoParcel(BaseModel):
    model_config = ConfigDict(frozen=True)

    parcel_id: str
    origin_port: PortEnum
    dest_port: PortEnum
    alternative_dest_ports: list[PortEnum] | None = None
    commodity: str
    volume_dwt: float
    laycan_start: date
    laycan_end: date
    route_family: RouteFamily
    revenue_usd: float
    demurrage_usd_per_day: float = 0.0
    # Allowed free time in port (loading + discharging combined) before
    # demurrage starts accruing. Real charter parties define laytime with more
    # nuance (separate load/discharge allowances, reversible laytime, whether
    # waiting-for-berth counts) than this model attempts -- this is deliberately
    # the simplified version: total time in port beyond this allowance costs
    # demurrage_usd_per_day. 0.0 means demurrage is off unless set explicitly.
    demurrage_wait_days: float = 0.0

class WeatherSeverity(str, Enum):
    CYCLONE = "CYCLONE"
    GALE = "GALE"
    STORM = "STORM"
    TROPICAL_DEPRESSION = "TROPICAL_DEPRESSION"

class WeatherEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_id: str
    route: RouteEnum
    start_time: datetime
    end_time: datetime
    delay_hours: int  # Additional transit time caused by routing around or slowing down
    severity: WeatherSeverity

class PortLogisticsStatus(BaseModel):
    model_config = ConfigDict(frozen=True)

    status_id: str
    port: PortEnum
    start_time: datetime
    end_time: datetime
    additional_wait_hours: int      # Added to anchorage/queue time
    handling_rate_multiplier: float # e.g., 0.8 means 20% slower loading due to rail/truck congestion

class OptimizerInputs(BaseModel):
    model_config = ConfigDict(frozen=True)

    parcels: list[CargoParcel]
    vessels: list[Vessel]
    tc_quotes: dict[VesselClass, float]
    planning_horizon_days: int
    contract_term_days: int
    forecasts: list[ForecastFan]
    basis: dict[RouteFamily, BasisEntry]
    
    weather_events: list[WeatherEvent] = []
    port_events: list[PortLogisticsStatus] = []
    
    risk_tolerance: float = 0.0
    # F-40 fix: the real vessel daily running cost (crew, insurance,
    # maintenance/stores -- NOT fuel, which is already separately costed
    # via laden/ballast_fuel_consumption_tpd x the port's own bunker
    # price) that prices idle time, queue wait, and early arrival in the
    # CP-SAT scheduler's objective and the repositioning score. The old
    # 500.0 default was off by roughly an order of magnitude against any
    # real bulk-carrier OPEX figure -- confirmed live: industry-reported
    # average Panamax daily operating cost is approximately $5,500/day
    # (a web-aggregated figure, not a single primary-source citation the
    # way the port register data is -- disclosed as the weaker sourcing it
    # is, same convention this codebase already uses for e.g. Vostochny's
    # port data). Used as one blended figure across every vessel class
    # here (this field is not currently class-specific); a caller with a
    # real, class-specific OPEX figure should still pass it explicitly.
    opex_usd_per_day: float = 5_500.0

class LockWaitResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    vessel_class: VesselClass
    contract_term_days: int
    ceiling_usd_per_day: float
    today_quote_usd_per_day: float
    action: Literal["LOCK", "WAIT"]
    expected_spot_cost_usd_per_day: float
    savings_p50_usd_per_day: float
    savings_p10_usd_per_day: float
    route_adjusted: bool
    # Populated by opt.ceiling.lock_or_wait_for_cargo (the cargo/route-driven
    # entry point); left None for direct opt.ceiling.lock_or_wait calls that
    # only ever knew a vessel class (e.g. opt.backtest's historical replay,
    # which has no single cargo lot to point at).
    origin_port: PortEnum | None = None
    dest_port: PortEnum | None = None
    cargo_volume_dwt: float | None = None

class RejectedOption(BaseModel):
    model_config = ConfigDict(frozen=True)
    
    vessel_id: str
    parcel_id: str
    reason: str

class VoyageAssignment(BaseModel):
    model_config = ConfigDict(frozen=True)
    
    vessel_id: str
    parcel_id: str
    dest_port: PortEnum
    arrival_hours: int
    wait_hours: int
    start_operation_hours: int
    finish_hours: int
    ballast_hours: int
    inter_cargo_gap_hours: int
    profit_usd: float

class RepositioningAction(BaseModel):
    model_config = ConfigDict(frozen=True)

    vessel_id: str
    current_port: PortEnum
    recommended_port: PortEnum
    is_staying: bool
    # Real numbers behind the recommendation, from opt.repositioning's own
    # RepositionOption -- previously computed there and then discarded before
    # reaching this type, which meant the "why" behind a repositioning call
    # existed internally but was never actually surfaced to a caller.
    cargo_probability_within_window: float  # P(cargo appears at recommended_port), for the chosen option
    probability_is_real_data: bool  # False -> a neutral 0.5 default was used, no tonnage-field coverage there
    # P4: PortWatch's export_dry_bulk (which this probability is ultimately
    # built from, via tonnage.classmix) is PortWatch's own model ESTIMATE,
    # not a measurement -- see data_builders.provenance. None when
    # probability_is_real_data is False.
    data_provenance: Provenance | None = None
    recommended_score_usd: float  # hazard-weighted expected net value of recommended_port
    current_port_score_usd: float  # same score for current_port, for a direct "why move" comparison

class RepositioningOption(BaseModel):
    """One candidate port the repositioning engine scored for an idle vessel --
    the alternatives behind a RepositioningAction, surfaced so a caller (and the
    route-exploration map) can see what was considered, not just what won. Mirrors
    the real numbers on opt.repositioning.RepositionOption."""
    model_config = ConfigDict(frozen=True)

    vessel_id: str
    port: PortEnum
    is_recommended: bool
    is_current_location: bool
    ballast_distance_nm: float
    cargo_probability_within_window: float
    probability_is_real_data: bool
    data_provenance: Provenance | None = None
    score_usd: float


class ReviewTrigger(BaseModel):
    model_config = ConfigDict(frozen=True)

    schedule: Literal["WEEKLY", "DAILY", "MONTHLY"]
    conditions: list[str]


class NoFeasibleConfigurationError(RuntimeError):
    """Raised when every vessel class is physically or commercially ruled out
    for a fleet-mix requirement (opt.fleetmix)."""


class FleetConfiguration(BaseModel):
    """One vessel-class configuration for a tonnage requirement -- opt.fleetmix.
    Defined here, not in opt.fleetmix, so OptimizerRecommendation can carry it
    without opt.types importing opt.fleetmix (which itself imports opt.types)."""
    model_config = ConfigDict(frozen=True)

    vessel_class: VesselClass
    n_vessels: int
    dwt_per_vessel: float
    total_capacity_dwt: float
    requires_transshipment: bool
    transshipment_hub: PortEnum | None
    voyage_days_per_vessel: float
    cost_p10_usd: float
    cost_p50_usd: float
    cost_p90_usd: float
    reliability_score: float  # (0, 1], higher = fewer moving parts / less coordination risk
    infeasible_reason: str | None = None

    @property
    def is_feasible(self) -> bool:
        return self.infeasible_reason is None


class FleetMixFrontier(BaseModel):
    model_config = ConfigDict(frozen=True)

    origin: PortEnum
    dest: PortEnum
    requirement_dwt: float
    configurations: tuple[FleetConfiguration, ...]  # feasible only, sorted by cost_p50
    # Infeasible classes, each with its real infeasible_reason -- computed by
    # opt.fleetmix.enumerate_fleet_mix in the same pass as the above but
    # previously dropped before construction, so "why wasn't a Capesize
    # recommended" had a real, already-computed answer that never reached the
    # caller. Not merged into `configurations` (which existing callers rely on
    # staying "feasible only, sorted by cost_p50") -- kept as a separate,
    # additive field instead.
    rejected_configurations: tuple[FleetConfiguration, ...] = ()

    @property
    def cheapest(self) -> FleetConfiguration:
        if not self.configurations:
            raise NoFeasibleConfigurationError(
                f"No vessel class can physically and commercially move {self.requirement_dwt:,.0f} "
                f"dwt from {self.origin.value.id} to {self.dest.value.id}."
            )
        return self.configurations[0]

    @property
    def most_reliable(self) -> FleetConfiguration:
        if not self.configurations:
            raise NoFeasibleConfigurationError(
                f"No vessel class can physically and commercially move {self.requirement_dwt:,.0f} "
                f"dwt from {self.origin.value.id} to {self.dest.value.id}."
            )
        return max(self.configurations, key=lambda c: c.reliability_score)


class RiskAlert(BaseModel):
    """A single real, triggered early-warning signal -- opt.risk. Defined here
    for the same reason as FleetConfiguration above."""
    model_config = ConfigDict(frozen=True)

    category: Literal["rate_regime", "port_congestion", "chokepoint_disruption", "cyclone_season"]
    severity: Literal["info", "warning", "critical"]
    message: str
    metric_value: float
    threshold: float
    subject: str  # class name, port label, or chokepoint name this alert is about


class RiskAssessment(BaseModel):
    model_config = ConfigDict(frozen=True)

    as_of: date
    alerts: tuple[RiskAlert, ...]

    def to_review_trigger(self) -> ReviewTrigger:
        """Collapse the real alerts into the existing ReviewTrigger shape, so
        opt.api's output schema doesn't need to change to carry this."""
        if any(a.severity == "critical" for a in self.alerts):
            schedule: Literal["WEEKLY", "DAILY", "MONTHLY"] = "DAILY"
        elif self.alerts:
            schedule = "WEEKLY"
        else:
            schedule = "MONTHLY"
        conditions = [a.message for a in self.alerts] or ["No active risk signals as of last solve"]
        return ReviewTrigger(schedule=schedule, conditions=conditions)


class StoppingResult(BaseModel):
    """LSMC optimal-stopping output -- opt.stopping. Defined here for the same
    reason as FleetConfiguration/RiskAlert above."""
    model_config = ConfigDict(frozen=True)

    vessel_class: VesselClass
    today_quote_usd_per_day: float
    strike_usd_per_day: float  # K: the ceiling-implied expected spot cost being priced against
    planning_horizon_days: int
    exercise_boundary_usd_per_day: tuple[float, ...]  # index d-1 = boundary for day d, 1..horizon
    option_value_usd_per_day: float  # value of the optimal (adaptive) strategy over "decide on day 1"
    recommended_action_today: Literal["LOCK", "WAIT"]
    n_paths: int


class PortfolioMix(BaseModel):
    """Spot/period-TC/COA coverage mix -- opt.portfolio. Defined here for the
    same reason as FleetConfiguration/RiskAlert/StoppingResult above."""
    model_config = ConfigDict(frozen=True)

    spot_fraction: float
    tc_fraction: float
    coa_fraction: float
    expected_cost_usd: float
    cost_variance_usd2: float
    stockout_probability: float
    stockout_penalty_usd: float

    @property
    def cost_std_usd(self) -> float:
        import math

        return math.sqrt(max(self.cost_variance_usd2, 0.0))


class Explanation(BaseModel):
    """A deterministic, real-numbers-only explanation of one output --
    opt.explain. Every field is built from values already computed elsewhere
    in a solve, never a separately-generated (e.g. LLM) narrative: the goal is
    to surface the "why" that's already there, not author a new one that
    could say something the numbers don't actually support. Same shape used
    for every explained output (lock/wait, a voyage assignment, a
    repositioning call, the savings estimate, a fleet-mix pick) so a caller
    or a frontend can render them uniformly."""
    model_config = ConfigDict(frozen=True)

    summary: str  # one sentence, plain language
    factors: tuple[str, ...]  # the specific real numbers behind the summary, most important first
    method: str  # which real model/computation produced this output


class RecommendationExplanations(BaseModel):
    """One Explanation per explainable output on OptimizerRecommendation --
    everything except opt.risk's alerts, which already carry a real per-alert
    reason (RiskAlert.message/metric_value/threshold) rather than only raw
    numbers, so a further wrapper here would just restate them."""
    model_config = ConfigDict(frozen=True)

    lock_wait: Explanation
    voyage_assignments: tuple[Explanation, ...]  # same order as OptimizerRecommendation.voyage_assignments
    repositioning: tuple[Explanation, ...]  # same order as OptimizerRecommendation.repositioning_actions
    savings: Explanation
    fleet_mix: Explanation | None  # None exactly when OptimizerRecommendation.fleet_mix is None


class OptimizerRecommendation(BaseModel):
    model_config = ConfigDict(frozen=True)

    # 1. Lock/wait + ceiling -- vessel class is derived from cargo_volume_dwt,
    # not a caller-supplied input; origin/dest/cargo_volume_dwt are the real
    # inputs the decision is priced from (see opt.ceiling.lock_or_wait_for_cargo).
    target_vessel_class: VesselClass
    origin_port: PortEnum
    dest_port: PortEnum
    cargo_volume_dwt: float
    lock_action: Literal["LOCK", "WAIT"]
    ceiling_usd_per_day: float
    tc_quote_usd_per_day: float
    contract_term_days: int
    optimal_entry_window_start_day: int | None = None
    optimal_entry_window_end_day: int | None = None
    optimal_entry_window_p50_usd: float | None = None
    
    # 2. Voyage schedule
    voyage_assignments: list[VoyageAssignment]
    rejected_options: list[RejectedOption]
    total_voyage_profit_usd: float
    
    # 3. Repositioning
    repositioning_actions: list[RepositioningAction]
    # The other candidate ports each idle vessel was scored against (the chosen
    # one plus its alternates), so "what did the solver consider" is answerable.
    # Additive/optional -- defaults to empty for callers/tests that predate it.
    repositioning_options: list[RepositioningOption] = []
    
    # 4. Savings, as a range -- p90/prob_positive were already computed by
    # opt.monte_carlo.estimate_savings_distribution but previously discarded
    # before reaching this type.
    expected_savings_usd_per_day: float
    p10_savings_usd_per_day: float
    p90_savings_usd_per_day: float
    prob_savings_positive: float  # fraction of simulated futures where locking beat staying spot
    
    # 5. Review trigger
    review_trigger: ReviewTrigger

    # 6. Fleet-mix frontier (PS deliverable b) -- optional, defaults to unset so
    # existing callers/tests that construct OptimizerRecommendation without it
    # keep working unchanged.
    fleet_mix: FleetMixFrontier | None = None

    # 7. Full risk assessment (PS deliverable d) -- optional, same reason.
    # review_trigger above is always populated (from this when available, or
    # the pre-P3 default otherwise); this carries the richer per-alert detail.
    risk_assessment: RiskAssessment | None = None

    # 8. LSMC exercise boundary (PS deliverable a) -- optional (None exactly
    # when there wasn't enough forecast data to calibrate a price path; see
    # opt.stopping.solve_lock_or_wait). Fused into lock_action/ceiling_usd_per_day
    # above when present -- see opt.stopping's module docstring.
    stopping_result: StoppingResult | None = None

    # 8b. Weather/cyclone transit buffer (2.4) -- optional, additive. None
    # when opt.weather_window couldn't produce one (no network, cold cache,
    # or no laycan/route context) -- see opt.quote.quote()'s own try/except
    # around this. Its expected_delay_days, when present, taxes the WAIT
    # branch of the fused lock/wait decision above -- see
    # opt.stopping.solve_lock_or_wait's weather_delay_days parameter.
    transit_buffer: TransitBuffer | None = None

    # 9. Deterministic, real-numbers-only explanations for the outputs above --
    # "why was this shown," not a black box. See opt.explain.
    explanations: RecommendationExplanations


class ProgressStage(BaseModel):
    """One step of the solve pipeline, emitted as it starts and again as it
    finishes -- opt.api.run_optimizer threads a callback that yields these so a
    caller can show what the optimizer is doing instead of a dead wait."""
    model_config = ConfigDict(frozen=True)

    key: str  # stable id: "forecast_load", "lock_wait", "voyage_schedule", ...
    label: str  # human-readable
    status: Literal["start", "done"]
    elapsed_ms: float  # 0 on "start"; wall time for the step on "done"
    detail: str | None = None


class RouteLeg(BaseModel):
    """One port-to-port hop of a solver route, with a real navigable polyline
    (searoute where it resolves, a great-circle interpolation otherwise)."""
    model_config = ConfigDict(frozen=True)

    from_port: PortEnum
    to_port: PortEnum
    distance_nm: float
    is_great_circle_fallback: bool
    polyline: tuple[tuple[float, float], ...]  # (lon, lat) pairs, GeoJSON order


class SolverRoute(BaseModel):
    """One routing the solver evaluated. `status` says whether it was taken
    (chosen), priced but not taken (considered), or ruled out (rejected, with a
    real `reason`). `kind` says which sub-engine produced it."""
    model_config = ConfigDict(frozen=True)

    id: str
    kind: Literal["fleet_mix", "repositioning", "voyage_assignment"]
    label: str
    vessel_class: VesselClass | None
    status: Literal["chosen", "considered", "rejected"]
    reason: str | None  # populated for rejected
    metric_label: str | None  # e.g. "cost p50", "score", "profit"
    metric_usd: float | None
    legs: tuple[RouteLeg, ...]


class StructuralProblem(BaseModel):
    """A reason the request itself is impossible -- no amount of loosening
    constraints makes 1e9 dwt of cargo arrive in two days. The caller is told
    exactly which input is at fault and by how much."""
    model_config = ConfigDict(frozen=True)

    field: Literal["cargo_volume_dwt", "laycan", "route", "contract_term_days"]
    message: str
    observed: str
    limit: str


class Relaxation(BaseModel):
    """One constraint the solver loosened to turn an unsolvable request into a
    solvable nearby one. `before`/`after` are shown to the caller so they know
    exactly what changed."""
    model_config = ConfigDict(frozen=True)

    kind: Literal[
        "laycan_widened", "transshipment_allowed", "chokepoint_ignored", "draft_tolerance"
    ]
    message: str
    before: str
    after: str


class RateHorizon(BaseModel):
    """One real forecast horizon, restated for display -- opt.present. p50 is
    given in both units so a caller doesn't have to re-derive the $/MT
    conversion itself; p50_usd_per_mt is None exactly when opt.present
    couldn't get a real transit-day estimate for this route (see
    opt.present.estimate_transit_days).

    ``p50_usd_per_mt`` is ALWAYS ``class_rate / transit_days`` (a display
    conversion, per its own docstring) -- it does not become "more
    route-aware" just because ``route_evidence`` is OBSERVED/MODELLED for
    this route; only ``p10/p50/p90_usd_per_day`` themselves are route-
    adjusted when that happens (opt.ceiling._apply_basis runs before this
    horizon is built). ``route_evidence``/``route_adjustment`` say whether
    that adjustment happened, so a caller (and the UI) can label the $/MT
    figure honestly either way -- see opt.basis for what each evidence level
    means and why every route resolves ROUTE_RATE_BASIS_UNAVAILABLE today."""
    model_config = ConfigDict(frozen=True)

    horizon_days: Literal[7, 30, 90]
    p10_usd_per_day: float
    p50_usd_per_day: float
    p90_usd_per_day: float
    p50_usd_per_mt: float | None
    direction: Literal["up", "down", "flat"]
    confidence_pct: float
    route_evidence: RouteEvidenceLevel
    route_adjustment: float | None = None  # basis_mean applied, None when UNAVAILABLE


class PortCheck(BaseModel):
    """Real infrastructure limits + real current congestion for one port --
    opt.present.congestion_label + opt.network.Port. None fields mean "no
    limit on record" (opt.voyage's own _vessel_can_call convention), not
    "unconstrained is confirmed.\""""
    model_config = ConfigDict(frozen=True)

    port: PortEnum
    max_dwt: float | None
    max_draft_m: float | None
    max_loa_m: float | None
    max_beam_m: float | None
    expected_wait_days: float
    wait_days_is_real_data: bool
    congestion_label: Literal["LOW", "MODERATE", "HIGH"]

    empirical_wait_p50_hours: float | None = None
    empirical_wait_p90_hours: float | None = None
    empirical_wait_sample_n: int = 0
    """P2: real arrival-to-berth percentiles from berth_truth.fact_port_call
    (berth_truth.empirical.compute_wait_distribution), where the real sample
    clears the sufficiency floor -- additive alongside expected_wait_days
    (the existing PortWatch-ratio-scaled scalar CP-SAT itself consumes,
    unchanged), not a replacement for it: this is the "P50 vs P90, not one
    scalar" picture at the reporting layer the empirical data supports,
    without touching the CP-SAT encoding's own single wait-cost input.
    empirical_wait_p50_hours/p90_hours are None, sample_n is 0, whenever the
    real sample did not clear berth_truth.empirical.MINIMUM_SAMPLE_SIZE."""


class QuoteResult(BaseModel):
    """The single-cargo "quote" front door's response -- opt.quote.quote().
    Wraps an OptimizerRecommendation (carried whole as full_recommendation,
    nothing it computes is duplicated or re-derived here) with the display
    polish the final-lap sub-plan asked for: $/MT alongside $/day, a
    per-horizon confidence%, and LOW/MODERATE/HIGH congestion labels for BOTH
    origin and destination -- the PS's own "for both origin and destination"
    port-constraint ask, not just the origin side opt.voyage's cost term
    already covered."""
    model_config = ConfigDict(frozen=True)

    # Echo of the caller's real inputs.
    cargo_volume_dwt: float
    commodity: str
    origin_port: PortEnum
    dest_port: PortEnum
    laycan_start: date
    laycan_end: date
    contract_term_days: int
    as_of: date

    # Rate forecast, both units.
    today_quote_usd_per_day: float
    today_quote_usd_per_mt: float | None
    assumed_transit_days: float | None
    rate_forecast: tuple[RateHorizon, ...]

    # P4: route-basis summary for this quote's route_family -- every
    # RateHorizon above carries the same route_evidence/route_adjustment;
    # duplicated here so a caller doesn't have to dig into rate_forecast[0]
    # for the one-line answer "is this route-aware or class-only."
    route_evidence: RouteEvidenceLevel
    route_adjustment: float | None = None

    # Recommendation (LOCK/WAIT, option-value-aware -- see opt.stopping).
    target_vessel_class: VesselClass
    lock_action: Literal["LOCK", "WAIT"]
    ceiling_usd_per_day: float
    ceiling_usd_per_mt: float | None
    optimal_entry_window_start_day: int | None
    optimal_entry_window_end_day: int | None
    expected_savings_usd_per_day: float
    expected_savings_usd_total: float
    prob_savings_positive: float

    # Vessel type optimization (PS b).
    fleet_mix: FleetMixFrontier | None

    # Port constraint check, both ends (PS's named ask).
    origin_port_check: PortCheck
    dest_port_check: PortCheck

    # Risk mitigation / early warning (PS d).
    risk_assessment: RiskAssessment | None

    # "Why," not a black box.
    explanations: RecommendationExplanations

    # Escape hatch: the full, unpolished recommendation (voyage assignments,
    # repositioning, review_trigger, stopping_result, etc.) for a caller that
    # wants everything opt.api.run_optimizer computed, not just the polish.
    full_recommendation: OptimizerRecommendation

    # Every route the solver actually walked -- fleet-mix configurations
    # (chosen/considered/rejected), repositioning candidates, and voyage legs --
    # each with a real port-to-port polyline, for the route-inspection map.
    # Additive/optional (opt.route_trace populates it in opt.quote); empty for
    # callers/tests that predate it.
    route_exploration: tuple[SolverRoute, ...] = ()

    # IMO CII projection for every real vessel on this route -- optional/
    # additive (emissions.projection populates it in opt.quote). None for a
    # cargo-only quote (no vessel to rate), an unpublished rating_year, or a
    # projection failure (opt.quote degrades to None rather than raising --
    # same defensive pattern as risk_assessment above).
    emissions: VoyageEmissions | None = None

    # Weather/cyclone transit buffer (2.4) -- optional/additive, mirrors
    # full_recommendation.transit_buffer (same value; surfaced here too so a
    # caller doesn't have to dig into full_recommendation for it, same
    # reasoning as route_evidence/route_adjustment above duplicating
    # rate_forecast[0]'s own fields). None when opt.weather_window couldn't
    # produce one -- see opt.quote.quote()'s own try/except around this.
    transit_buffer: TransitBuffer | None = None


class QuoteEnvelope(BaseModel):
    """What opt.quote.quote_envelope() returns and the HTTP layer serialises.

    - ``feasible``: ``quote`` is the real answer for the request as given.
    - ``structural_infeasible``: the request cannot be satisfied at all;
      ``structural_problems`` says which input is impossible; ``quote`` is None.
    - ``contingent_infeasible``: the request as given had no solution, but a
      loosened version does; ``quote`` is that loosened solve, ``original_blockers``
      is why the original failed, and ``relaxations_applied`` is what was changed.
    """
    model_config = ConfigDict(frozen=True)

    status: Literal["feasible", "structural_infeasible", "contingent_infeasible"]
    quote: QuoteResult | None = None
    structural_problems: tuple[StructuralProblem, ...] = ()
    original_blockers: tuple[str, ...] = ()
    relaxations_applied: tuple[Relaxation, ...] = ()


class LimitSource(str, Enum):
    """Which system answered a port-clearance check -- an opt-side concept,
    not a berth_truth one: berth_truth's own registry has no notion of
    PortEnum or a "fallback" to it. Only opt.voyage._vessel_can_call sets
    PORTENUM_FALLBACK, and only when berth_truth.service reports it has
    nothing at all for the port being checked."""

    REGISTER = "REGISTER"
    PORTENUM_FALLBACK = "PORTENUM_FALLBACK"


class FeasibilityMargins(BaseModel):
    """Signed clearance, in metres, for the three dimensions the register can
    compare like-for-like (same unit, no ambiguity): positive means the
    vessel fits with that much room, negative means it exceeds the limit by
    that much. None means the dimension was not tested -- see
    FeasibilityVerdict.untested_checks for why, never inferred from a null
    margin here. DWT/displacement has no margin field on purpose: the two are
    different quantities (a headline weight vs. everything a hull water-
    displaces at that weight) and are never safely differenced into one
    number -- see FeasibilityVerdict.untested_checks for that comparison's
    own disclosure instead.
    """

    model_config = ConfigDict(frozen=True)

    draft_margin_m: float | None = None
    loa_margin_m: float | None = None
    beam_margin_m: float | None = None


class FeasibilityVerdict(BaseModel):
    """What opt.voyage._vessel_can_call and opt.fleetmix._can_call return --
    replacing the previous bare ``tuple[bool, str | None]``: "is this vessel
    allowed to call this port" is no longer a single fact with one source.
    It is now which specific berth (if the port is commodity/berth-routed),
    resolved against which document, current as of when, with which checks
    the register genuinely could not run -- not silently passed, not
    silently failed.

    ``binding_constraint`` is the actual berth_truth.models.BerthConstraint
    the verdict rests on when ``limit_source`` is REGISTER (so a caller with
    tolerance logic of its own -- opt.fleetmix's relaxed pass -- can re-run
    its own comparison against the SAME resolved numbers, never against the
    legacy PortEnum literal). It is None whenever limit_source is
    PORTENUM_FALLBACK: there is no register row to hand back, only
    ``opt.network.Port`` values the caller already has direct access to.
    """

    model_config = ConfigDict(frozen=True)

    is_feasible: bool
    berth_id: str | None = None
    binding_constraint: BerthConstraint | None = None
    limit_source: LimitSource

    draft_source: DraftSource | None = None
    draft_status: DraftStatus | None = None
    draft_as_of: date | None = None

    margins: FeasibilityMargins = FeasibilityMargins()
    is_soft_limit: bool = False
    staleness_days: int | None = None

    untested_checks: tuple[str, ...] = ()
    """Every dimension that could not be evaluated, with why -- a berth
    whose only published size figure is displacement (not DWT), a draft that
    is STALE_OR_UNAVAILABLE, a beam the source never states. Populated
    whenever such a check exists, whether or not the overall verdict is
    feasible: an untested dimension on an otherwise-feasible berth is real,
    disclosable information, not a reason to withhold the verdict."""

    observed_only_berths: tuple[str, ...] = ()
    """Berths this port is confirmed to operate (BT-0's live schedules) with
    no citable limit anywhere -- disclosed so a caller can see the port
    operates more berths than are published, never used to clear a vessel."""

    reason: str | None = None
    """Human-readable explanation, populated whenever is_feasible is False
    (why) or limit_source is PORTENUM_FALLBACK with no register data at all
    (that there was nothing to check against) -- kept as a plain string,
    matching the field name every existing caller of the old tuple return
    already expected as its second element."""
