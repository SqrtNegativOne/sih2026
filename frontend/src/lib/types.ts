export type PortCode =
  | 'PARADIP'
  | 'VIZAG'
  | 'GANGAVARAM'
  | 'GOPALPUR'
  | 'DHAMRA'
  | 'SAGAR_SANDHEADS'
  | 'HALDIA'
  | 'NEWCASTLE_AU'
  | 'GLADSTONE_AU'
  | 'RICHARDS_BAY'
  | 'BEIRA'
  | 'MUARA_PANTAI'
  | 'BALIKPAPAN'
  | 'HAMPTON_ROADS'
  | 'SINGAPORE'
  | 'VOSTOCHNY_RU'

export type VesselClass = 'Capesize' | 'Panamax' | 'Supramax' | 'Handysize'
export type LockAction = 'LOCK' | 'WAIT'
export type CongestionLabel = 'LOW' | 'MODERATE' | 'HIGH'
export type RiskSeverity = 'info' | 'warning' | 'critical'
export type RiskCategory =
  | 'rate_regime'
  | 'port_congestion'
  | 'chokepoint_disruption'
  | 'cyclone_season'

export interface PortListing {
  code: string
  name: string
  lat: number | null
  lon: number | null
}

export interface Meta {
  latest_data_date: string
}

export interface VesselInput {
  vessel_id: string
  vessel_class: VesselClass
  current_port: PortCode
  status?: 'idle' | 'on_tc' | 'on_voyage'
  available_from: string
  available_until?: string | null
  dwt: number
  draft_m: number
  loa_m: number
  beam_m: number
  speed_kn: number
  laden_fuel_consumption_tpd: number
  ballast_fuel_consumption_tpd: number
}

export interface QuoteRequest {
  cargo_volume_dwt: number
  origin_port: PortCode
  dest_port: PortCode
  laycan_start: string
  laycan_end: string
  contract_term_days: number
  commodity: string
  as_of?: string | null
  risk_tolerance: number
  vessels?: VesselInput[]
  revenue_usd?: number
}

/** P4: whether a route's $/day figure reflects real route-level rate
 * evidence, a thin real-evidence estimate, or the class benchmark
 * unadjusted -- see opt.basis. Every route resolves
 * ROUTE_RATE_BASIS_UNAVAILABLE today (no route family has >= 5 real
 * USD/day-denominated observations); the field exists so the UI never
 * implies route-awareness that isn't real. */
export type RouteEvidence = 'OBSERVED' | 'MODELLED' | 'ROUTE_RATE_BASIS_UNAVAILABLE'

export interface RateHorizon {
  horizon_days: 7 | 30 | 90
  p10_usd_per_day: number
  p50_usd_per_day: number
  p90_usd_per_day: number
  /** class_rate / transit_days -- a display conversion, not itself a route
   * quote (see opt.present.usd_per_day_to_usd_per_mt). Whether the $/day
   * feeding it is route-adjusted is `route_evidence`, not this field. */
  p50_usd_per_mt: number | null
  direction: 'up' | 'down' | 'flat'
  confidence_pct: number
  route_evidence: RouteEvidence
  route_adjustment: number | null
}

export interface Explanation {
  summary: string
  factors: string[]
  method: string
}

export interface FleetConfiguration {
  vessel_class: VesselClass
  n_vessels: number
  dwt_per_vessel: number
  total_capacity_dwt: number
  requires_transshipment: boolean
  transshipment_hub: PortCode | null
  voyage_days_per_vessel: number
  cost_p10_usd: number
  cost_p50_usd: number
  cost_p90_usd: number
  reliability_score: number
  infeasible_reason: string | null
}

export interface FleetMixFrontier {
  origin: PortCode
  dest: PortCode
  requirement_dwt: number
  configurations: FleetConfiguration[]
  rejected_configurations: FleetConfiguration[]
}

export interface PortCheck {
  port: PortCode
  max_dwt: number | null
  max_draft_m: number | null
  max_loa_m: number | null
  max_beam_m: number | null
  expected_wait_days: number
  wait_days_is_real_data: boolean
  congestion_label: CongestionLabel
  empirical_wait_p50_hours: number | null
  empirical_wait_p90_hours: number | null
  empirical_wait_sample_n: number
}

export interface RiskAlert {
  category: RiskCategory
  severity: RiskSeverity
  message: string
  metric_value: number
  threshold: number
  subject: string
}

export interface RiskAssessment {
  as_of: string
  alerts: RiskAlert[]
}

export interface StoppingResult {
  vessel_class: VesselClass
  today_quote_usd_per_day: number
  strike_usd_per_day: number
  planning_horizon_days: number
  exercise_boundary_usd_per_day: number[]
  option_value_usd_per_day: number
  recommended_action_today: LockAction
  n_paths: number
}

export interface RecommendationExplanations {
  lock_wait: Explanation
  voyage_assignments: Explanation[]
  repositioning: Explanation[]
  savings: Explanation
  fleet_mix: Explanation | null
}

export interface VoyageAssignment {
  vessel_id: string
  parcel_id: string
  dest_port: PortCode
  arrival_hours: number
  wait_hours: number
  start_operation_hours: number
  finish_hours: number
  ballast_hours: number
  inter_cargo_gap_hours: number
  profit_usd: number
}

export interface RejectedOption {
  vessel_id: string
  parcel_id: string
  reason: string
}

/** P4: the five-value provenance contract, shared across every module that
 * carries a PortWatch-derived (or other real-but-not-directly-measured)
 * figure through to the UI. */
export type DataProvenance = 'OBSERVED' | 'ESTIMATED' | 'INFERRED' | 'MODEL_DERIVED' | 'DECLARED'

export interface RepositioningAction {
  vessel_id: string
  current_port: PortCode
  recommended_port: PortCode
  is_staying: boolean
  cargo_probability_within_window: number
  probability_is_real_data: boolean
  /** ESTIMATED when probability_is_real_data is true (built from
   * PortWatch's own export_dry_bulk model estimate, not a measurement);
   * null when a neutral 0.5 default was used (no real data at all). */
  data_provenance: DataProvenance | null
  recommended_score_usd: number
  current_port_score_usd: number
}

export interface ReviewTrigger {
  schedule: 'WEEKLY' | 'DAILY' | 'MONTHLY'
  conditions: string[]
}

export interface RepositioningOption {
  vessel_id: string
  port: PortCode
  is_recommended: boolean
  is_current_location: boolean
  ballast_distance_nm: number
  cargo_probability_within_window: number
  probability_is_real_data: boolean
  data_provenance: DataProvenance | null
  score_usd: number
}

export interface OptimizerRecommendation {
  target_vessel_class: VesselClass
  origin_port: PortCode
  dest_port: PortCode
  cargo_volume_dwt: number
  lock_action: LockAction
  ceiling_usd_per_day: number
  tc_quote_usd_per_day: number
  contract_term_days: number
  optimal_entry_window_start_day: number | null
  optimal_entry_window_end_day: number | null
  optimal_entry_window_p50_usd: number | null
  voyage_assignments: VoyageAssignment[]
  rejected_options: RejectedOption[]
  total_voyage_profit_usd: number
  repositioning_actions: RepositioningAction[]
  repositioning_options: RepositioningOption[]
  expected_savings_usd_per_day: number
  p10_savings_usd_per_day: number
  p90_savings_usd_per_day: number
  prob_savings_positive: number
  review_trigger: ReviewTrigger
  fleet_mix: FleetMixFrontier | null
  risk_assessment: RiskAssessment | null
  stopping_result: StoppingResult | null
  transit_buffer: TransitBuffer | null
  explanations: RecommendationExplanations
}

export type SolverRouteKind = 'fleet_mix' | 'repositioning' | 'voyage_assignment'
export type SolverRouteStatus = 'chosen' | 'considered' | 'rejected'

export interface RouteLeg {
  from_port: PortCode
  to_port: PortCode
  distance_nm: number
  is_great_circle_fallback: boolean
  polyline: [number, number][] // [lon, lat]
}

export interface SolverRoute {
  id: string
  kind: SolverRouteKind
  label: string
  vessel_class: VesselClass | null
  status: SolverRouteStatus
  reason: string | null
  metric_label: string | null
  metric_usd: number | null
  legs: RouteLeg[]
}

export interface ProgressStage {
  key: string
  label: string
  status: 'start' | 'done'
  elapsed_ms: number
  detail: string | null
}

export interface StructuralProblem {
  field: 'cargo_volume_dwt' | 'laycan' | 'route' | 'contract_term_days'
  message: string
  observed: string
  limit: string
}

export interface Relaxation {
  kind: 'laycan_widened' | 'transshipment_allowed' | 'chokepoint_ignored' | 'draft_tolerance'
  message: string
  before: string
  after: string
}

export type QuoteStatus = 'feasible' | 'structural_infeasible' | 'contingent_infeasible'

export interface QuoteEnvelope {
  status: QuoteStatus
  quote: QuoteResult | null
  structural_problems: StructuralProblem[]
  original_blockers: string[]
  relaxations_applied: Relaxation[]
  /** P6: freight-only partial breakdown, additive -- null when quote is
   * null or opt.present had no real transit-day estimate. Wait/handling/
   * demurrage/commodity/FX are never auto-computed here (see
   * backend/main.py's _quote_partial_landed_cost) -- POST /landed-cost
   * for the full, assumption-informed breakdown. */
  landed_cost: LandedCostBreakdown | null
}

/** opt.emissions.cii's A-E IMO Carbon Intensity Indicator rating -- A/B best,
 * D/E worst. See VesselCIIProjection.rating. */
export type CIIRating = 'A' | 'B' | 'C' | 'D' | 'E'

/** emissions.projection.project_vessel_cii's output for one real vessel on
 * this quote's route. Every figure is computed from the vessel's own real
 * inputs (speed, fuel consumption, DWT) and the real quoted route/laycan
 * year -- see that function's docstring for the ballast/laden-leg method,
 * including the documented conservative reading of the AER denominator. */
export interface VesselCIIProjection {
  vessel_id: string
  vessel_class: VesselClass
  dwt: number
  laden_distance_nm: number
  ballast_distance_nm: number
  sea_days: number
  fuel_tonnes: number
  co2_tonnes: number
  /** gCO2 / dwt-nautical-mile. */
  attained_cii: number
  /** gCO2 / dwt-nautical-mile. */
  required_cii: number
  rating: CIIRating
  rating_year: number
  /** (required - attained) / required * 100 -- positive means better than
   * required. */
  margin_pct: number
  /** True if any leg used a great-circle approximation rather than a real
   * sea route (opt.geography had no matrix entry for that port pair). */
  is_distance_fallback: boolean
  provenance: DataProvenance
}

/** emissions.projection.project_voyage_emissions's output, attached to
 * QuoteResult.emissions. Null on the Python side (never a placeholder rating)
 * when there's no vessel to rate or the rating year isn't published --
 * render the panel's empty state in that case, not a fabricated grade. */
export interface VoyageEmissions {
  as_of: string
  rating_year: number
  projections: VesselCIIProjection[]
  fleet_worst_rating: CIIRating | null
}

/** opt.weather_window.TransitBuffer (2.4): the combined long-run cyclone
 * climatology + live 7-day marine forecast expected-delay figure for this
 * quote's laycan, and the one-directional tax it applies to the WAIT branch
 * of the lock/wait decision -- see opt.stopping.solve_lock_or_wait's own
 * weather_delay_days parameter. Null when opt.weather_window couldn't
 * produce one (no network, cold cache, or no real laycan/route context);
 * never a fabricated sea state. */
export interface TransitBuffer {
  origin: PortCode
  dest: PortCode
  laycan_start: string
  laycan_end: string
  climatology_delay_days: number
  forecast_delay_days: number
  expected_delay_days: number
  forecast_covers_laycan: boolean
  basins: string[]
  /** One plain sentence a broker can read -- how the covered/uncovered
   * portions of the laycan were priced, without double-counting. */
  explanation: string
}

// ---------------------------------------------------------------------------
// 3.4 -- Chokepoint Fracture Index + Joint War Committee war-risk premium
// ---------------------------------------------------------------------------

/** opt.chokepoints's static reference table -- id, real name, real centre,
 * and the documented judgement-call radius approximating its transit lane.
 * GET /chokepoints; changes only when opt.chokepoints itself is edited, so
 * safe to fetch once and cache client-side. */
export interface ChokepointReference {
  id: string
  name: string
  lat: number
  lon: number
  radius_nm: number
}

/** opt.fracture's four bands, in ascending severity. A fracture computed
 * from an incomplete signal set (missing transit_z or conflict_z) is capped
 * at 'watch' however high its raw index -- see inputs_available below. */
export type FractureBand = 'calm' | 'watch' | 'elevated' | 'critical'

/** opt.fracture.ChokepointFracture: one real chokepoint's fused 0-100
 * disruption index. `inputs_available` names exactly which of the four
 * signals (transit_z, conflict_z, jwc_listed, draft_restricted) fed the
 * number -- a reader must never mistake a one-signal score for a
 * four-signal one, so render this list, don't just show the index. */
export interface ChokepointFracture {
  chokepoint_id: string
  chokepoint_name: string
  transit_z: number | null
  conflict_z: number | null
  draft_restricted: boolean
  jwc_listed: boolean
  index: number
  band: FractureBand
  inputs_available: string[]
  explanation: string
}

/** opt.war_risk.WarRiskPremium -- an illustrative, labelled ASSUMPTION, not
 * a market rate (see `basis` and `rate_is_caller_supplied`). Only present
 * when a caller supplied a real hull value. */
export interface WarRiskPremium {
  premium_usd: number
  hull_value_usd: number
  areas: string[]
  transit_days: number
  periods_charged: number
  rate_pct_per_7_days: number
  rate_is_caller_supplied: boolean
  provenance: DataProvenance
  basis: string
}

/** POST /fracture's response shape. */
export interface FractureResponse {
  origin_port: PortCode
  dest_port: PortCode
  as_of: string
  chokepoints: ChokepointFracture[]
  jwc_listed_areas: string[]
  transit_days: number | null
  war_risk_premium: WarRiskPremium | null
}

/** The route's Fracture Index, attached to QuoteResult at serialization
 * time (backend/serialize.py's own `_fracture_summary`) so the desk shows
 * it without a second round trip to POST /fracture. Never carries a
 * war-risk premium -- a plain quote supplies no hull value; POST /fracture
 * directly is how to price one. Null only if the underlying computation
 * itself failed (never a fabricated empty summary). */
export interface QuoteFractureSummary {
  chokepoints: ChokepointFracture[]
  jwc_listed_areas: string[]
}

export interface QuoteResult {
  cargo_volume_dwt: number
  commodity: string
  origin_port: PortCode
  dest_port: PortCode
  laycan_start: string
  laycan_end: string
  contract_term_days: number
  as_of: string

  today_quote_usd_per_day: number
  today_quote_usd_per_mt: number | null
  assumed_transit_days: number | null
  rate_forecast: RateHorizon[]
  route_evidence: RouteEvidence
  route_adjustment: number | null

  target_vessel_class: VesselClass
  lock_action: LockAction
  ceiling_usd_per_day: number
  ceiling_usd_per_mt: number | null
  optimal_entry_window_start_day: number | null
  optimal_entry_window_end_day: number | null
  expected_savings_usd_per_day: number
  expected_savings_usd_total: number
  prob_savings_positive: number

  fleet_mix: FleetMixFrontier | null
  origin_port_check: PortCheck
  dest_port_check: PortCheck
  risk_assessment: RiskAssessment | null
  explanations: RecommendationExplanations
  full_recommendation: OptimizerRecommendation
  route_exploration: SolverRoute[]
  emissions: VoyageEmissions | null
  transit_buffer: TransitBuffer | null
  fracture: QuoteFractureSummary | null
}

export interface ApiError {
  detail: string
}

// ---------------------------------------------------------------------------
// P2 -- M4 Berth Reality Engine / Port Twin
// ---------------------------------------------------------------------------

export type RealityVerdict = 'FEASIBLE' | 'INFEASIBLE' | 'CANNOT_VERIFY'
export type TideImpact = 'NONE' | 'CONDITIONAL' | 'BLOCKING'
export type TideAuthority = 'PORT_RULE' | 'ADVISORY_MODEL'
export type SourceQuality =
  | 'OFFICIAL_PORT_AUTHORITY'
  | 'OFFICIAL_TERMINAL_OPERATOR'
  | 'GOVERNMENT_OTHER'
  | 'PUBLIC_AGGREGATOR'
export type LimitStatus = 'PUBLISHED' | 'NOT_PUBLISHED' | 'ASSUMED' | 'SUPERSEDED'
export type WaitIntervalKind = 'ARRIVAL_TO_READY' | 'READY_TO_BERTH' | 'ARRIVAL_TO_BERTH' | 'BERTH_TO_SAIL'

export interface BerthConstraint {
  port_id: string
  berth_id: string
  is_published_constraint_berth: boolean
  is_observed_operational_berth: boolean
  limit_status: LimitStatus
  channel_depth_m: number | null
  designed_depth_m: number | null
  permissible_draft_m: number | null
  tide_allowance_m: number | null
  tide_rule: string | null
  max_loa_m: number | null
  max_beam_m: number | null
  max_displacement_t: number | null
  max_dwt: number | null
  berth_function: string | null
  commodity_class: string | null
  is_soft_limit: boolean
  promulgation_cycle: string | null
  draft_source: string
  source_url: string | null
  source_doc_id: string
  source_page: string | null
  doc_internal_date: string | null
  effective_from: string
  effective_to: string | null
  retrieved_at: string
  internal_conflict: string | null
}

export interface TideAssessment {
  impact: TideImpact
  authority: TideAuthority | null
  rule_text: string | null
  allowance_m: number | null
  source_doc_id: string | null
  source_is_current: boolean | null
  reason: string | null
}

export interface WaitDistribution {
  interval: WaitIntervalKind
  n: number
  p50_hours: number | null
  p75_hours: number | null
  p90_hours: number | null
  mean_hours: number | null
  source_level: string
  is_sufficient: boolean
}

export interface HandlingDistribution {
  n: number
  norm_tpd_median: number | null
  actual_tpd_median: number | null
  actual_over_norm_ratio: number | null
  source_level: string
  is_sufficient: boolean
}

export interface ObservedEnvelope {
  max_loa_m: number | null
  max_beam_m: number | null
  max_draft_m: number | null
  n_calls: number
}

export interface DeclaredVsObservedConflict {
  dimension: string
  declared_value: number
  observed_value: number
  note: string
}

export interface PortRealityReport {
  port: PortCode
  as_of: string
  verdict: RealityVerdict
  berth_id: string | null
  binding_constraint: BerthConstraint | null
  limit_source: 'REGISTER' | 'PORTENUM_FALLBACK' | 'NONE'
  margin_draft_m: number | null
  margin_loa_m: number | null
  margin_beam_m: number | null
  tide: TideAssessment
  wait_arrival_to_berth: WaitDistribution | null
  handling: HandlingDistribution | null
  untested_checks: string[]
  stale_inputs: string[]
  observed_envelope: ObservedEnvelope
  declared_vs_observed_conflicts: DeclaredVsObservedConflict[]
  source_quality: SourceQuality | null
  confidence: number
  reason: string | null
}

export interface PortBerthsResponse {
  port: PortCode
  status: 'OK' | 'NOT_AVAILABLE'
  reason?: string
  berths: BerthConstraint[]
}

export interface FactPortCallRow {
  port: PortCode
  terminal: string | null
  berth_or_point: string | null
  vessel_name: string | null
  loa_m: number | null
  beam_m: number | null
  arrival_draft_m: number | null
  vessel_class_inferred: string | null
  cargo_raw: string | null
  commodity_class: string | null
  load_discharge: 'LOAD' | 'DISCHARGE' | null
  shipper: string | null
  arrival_ts: string | null
  ready_ts: string | null
  berth_ts: string | null
  sail_ts: string | null
  total_qty_t: number | null
  handled_qty_t: number | null
  balance_qty_t: number | null
  norm_tpd: number | null
  actual_tpd: number | null
  source_url: string
  source_doc_date: string | null
  source_quality: SourceQuality
  quarantine_reason: string | null
}

export interface PortCallsResponse {
  port: PortCode
  status: 'OK' | 'NOT_AVAILABLE'
  total: number
  rows: FactPortCallRow[]
}

export interface PortWaitsResponse {
  port: PortCode
  status: 'OK' | 'BASELINE_ONLY'
  distributions: Record<WaitIntervalKind, WaitDistribution>
  portwatch_mapping_status: 'IDENTITY_CONFIRMED' | 'PROXY' | 'UNAVAILABLE' | null
  portwatch_mapping_note: string | null
}

// ---------------------------------------------------------------------------
// P3 -- M1 Tonnage Field
// ---------------------------------------------------------------------------

/** ABSOLUTE would mean a validated free/available-tonnage DWT figure exists;
 * RELATIVE (the real, current, live-verified state) means the index is a
 * dimensionless pressure/tightness signal only -- never render it as if it
 * were an absolute DWT number when this is RELATIVE. */
export type IndexType = 'ABSOLUTE' | 'RELATIVE'
export type MethodStatus = 'NOT_ATTEMPTED' | 'REJECTED_ALTERNATIVE_SHIPPED' | 'IMPLEMENTED_VALIDATED'
export type TonnageBasin = 'pacific' | 'atlantic' | 'indian_ocean'

export interface TightnessByClass {
  vessel_class: string
  tightness: number
  n_obs: number | null
  low_confidence: boolean | null
}

export interface TightnessByBasinClass {
  basin: TonnageBasin
  vessel_class: string
  tightness: number
}

export interface SignalValidationSummary {
  n_points: number
  min_ratio: number
  median_ratio: number
  max_ratio: number
  absolute_scale_validated: boolean
}

export interface TonnageFieldResponse {
  as_of: string
  index_type: IndexType
  index_type_reasoning: string
  calibration_factor_applied: boolean
  computed_at: string
  compute_seconds: number
  stale: boolean
  evidence_quality: {
    n_ports_used: number
    n_ports_skipped: number
    clipped_fraction: number
    signal_validation: SignalValidationSummary
  }
  tightness_by_class: TightnessByClass[]
  tightness_by_basin_class: TightnessByBasinClass[]
}

export interface TightnessForwardPoint {
  date: string
  vessel_class: string
  horizon_days: number
  p10: number
  p50: number
  p90: number
}

export interface TonnageFieldForwardResponse {
  as_of_date: string
  index_type: IndexType
  trend_window_days: number
  computed_at: string
  stale: boolean
  projections: TightnessForwardPoint[]
}

export interface SignDiagnosis {
  vessel_class: string
  pearson_r: number
  n_obs: number
  low_confidence: boolean
  weak_signal: boolean
  first_difference: { pearson_r_level: number; pearson_r_diff: number }
  regime_split: { first_half_r: number; second_half_r: number; split_date: string }
  lead_lag: { by_lag_days: Record<string, number>; best_lag_days: number }
  basin_aggregation: { pooled_r: number; by_basin_r: Record<string, number>; best_basin_beats_pooled: boolean }
  target_transform: { pearson_r_raw: number; pearson_r_log: number }
  explanation: string
}

export interface AblationMetricRow {
  split: 'valid' | 'test'
  scope: string
  h: number
  n: number
  pinball_0_1_a: number
  pinball_0_5_a: number
  pinball_0_9_a: number
  mae_p50_a: number
  dir_hit_a: number
  pinball_0_1_b: number
  pinball_0_5_b: number
  pinball_0_9_b: number
  mae_p50_b: number
  dir_hit_b: number
}

export interface AblationResult {
  computed_at: string
  compute_seconds: number
  stale: boolean
  m1_valid_coverage: number
  m1_test_coverage: number
  test_pooled_mean_relative_improvement: number
  valid_pooled_mean_relative_improvement: number
  adopt_b: boolean
  reasoning: string
  rows: AblationMetricRow[]
}

export interface TonnageFieldValidationResponse {
  computed_at: string
  index_type: IndexType
  iv_verdict: { status: MethodStatus; candidate_instruments_considered: string[]; reason: string }
  kalman_verdict: { status: MethodStatus; implemented_alternative: string; reason: string }
  sign_diagnoses: SignDiagnosis[]
  ablation: AblationResult
}

// ---------------------------------------------------------------------------
// P5 -- Decision Fragility (DF)
// ---------------------------------------------------------------------------

export type FragilityVariable =
  | 'cargo_volume_dwt'
  | 'origin_wait_days'
  | 'dest_wait_days'
  | 'vessel_draft_m'
  | 'permissible_draft_m'
  | 'laycan_width_days'
  | 'risk_tolerance'
  | 'contract_term_days'

export type FragilityTier = 'TIER1_CLOSED_FORM' | 'TIER2_FLEET_MIX' | 'TIER3_FULL_QUOTE'
export type FragilityProvenance = 'USER_INPUT' | 'DERIVED' | 'OBSERVED' | 'ASSUMPTION'
export type FragilityCategory = 'FRAGILE' | 'STABLE' | 'UNAVAILABLE'

export interface DecisionSignature {
  envelope_status: string | null
  lock_action: string | null
  target_vessel_class: string | null
  chosen_config_id: string | null
  feasibility_set: string[] | null
}

export interface BerthTruthContext {
  draft_status: string | null
  draft_source: string | null
  draft_as_of: string | null
  tide_impact: string | null
  tide_authority: string | null
  tide_reason: string | null
  empirical_wait_p50_hours: number | null
  empirical_wait_p90_hours: number | null
  empirical_wait_sample_n: number | null
  empirical_wait_is_sufficient: boolean | null
}

export interface FlipPoint {
  variable: FragilityVariable
  tier: FragilityTier
  provenance: FragilityProvenance
  base_value: number
  unit: string
  flip_found: boolean
  flip_value: number | null
  absolute_delta: number | null
  percent_delta: number | null
  direction: 'increase' | 'decrease' | null
  base_signature: DecisionSignature | null
  flipped_signature: DecisionSignature | null
  changed_components: string[]
  range_searched_low: number | null
  range_searched_high: number | null
  evaluations_used: number
  unavailable_reason: string | null
  berth_truth_context: BerthTruthContext | null
  fragility_category: FragilityCategory
  fragility_rank: number
  fragility_score: number | null
}

export interface FragilityReport {
  current_decision: DecisionSignature
  findings: FlipPoint[]
  evaluations_used: number
  limitations: string[]
}

// ---------------------------------------------------------------------------
// P5 -- Live Decision Ledger + Historical Model Replay
// ---------------------------------------------------------------------------

export interface LedgerLiveOutcome {
  realized_rate_usd_per_day: number
  realized_at_date: string
  recorded_at: string
  note: string | null
}

export interface LedgerLiveEntry {
  entry_id: string
  decision_timestamp: string
  cargo_volume_dwt: number
  origin_port: PortCode
  dest_port: PortCode
  laycan_start: string
  laycan_end: string
  contract_term_days: number
  /** P7: previously stored but not surfaced by GET /ledger/live. */
  commodity: string
  as_of: string
  risk_tolerance: number
  target_vessel_class: VesselClass
  today_quote_usd_per_day: number
  lock_action: LockAction
  alternative_action: LockAction
  ceiling_usd_per_day: number
  /** The LSMC exercise boundary behind the decision, when opt.stopping's
   * solve ran -- null exactly when it fell back to the plain ceiling rule. */
  exercise_boundary_usd_per_day: number[] | null
  model_version: string
  data_version: string
  status: 'scored' | 'pending'
  outcome: LedgerLiveOutcome | null
}

export interface LedgerLiveResponse {
  kind: 'LIVE_DECISION_LEDGER'
  total: number
  entries: LedgerLiveEntry[]
}

export interface LedgerPerformanceResponse {
  kind: 'LIVE_DECISION_LEDGER_PERFORMANCE'
  n_entries_total: number
  n_pending: number
  n_scored: number
  mean_realized_regret_usd_per_day: number | null
  lock_accuracy: number | null
  mean_savings_vs_always_lock_usd_per_day: number | null
  mean_savings_vs_always_wait_usd_per_day: number | null
}

export interface ReplayStrategySummary {
  strategy: 'always_spot' | 'always_lock' | 'calendar_lock' | 'optimizer' | 'oracle'
  vessel_class: string
  n_decisions: number
  savings_mean: number
  savings_p10: number
  savings_p50: number
  savings_p90: number
  hit_rate: number | null
  lock_rate: number
  decision_value: number | null
  regret: number | null
}

export interface LedgerReplayResponse {
  kind: 'HISTORICAL_MODEL_REPLAY'
  label: string
  computed_at: string
  compute_seconds: number
  stale: boolean
  contract_term_days: number
  broker_spread: number
  n_test_rows: number
  calibration: { best_theta: number; best_sigma_long: number; best_risk_tolerance: number; calibrated_on: string }
  summaries: ReplayStrategySummary[]
}

// --- P6: commercial upgrades (landed cost + backhaul), feasibility-gated ---

/** opt.landed_cost.LandedCostBreakdown. Every *_usd_per_mt field is `null`,
 * never a silent 0, when that component is unavailable -- check the
 * matching *_reason / components_missing before rendering a total as
 * complete. Commercial-term components (handling/demurrage) are `DECLARED`
 * only when the caller supplied them -- see LandedCostRequest below. */
export interface LandedCostBreakdown {
  dest_port: PortCode
  cargo_volume_mt: number

  freight_usd_per_mt: number
  freight_provenance: DataProvenance

  wait_cost_usd_per_mt: number | null
  wait_cost_provenance: DataProvenance | null
  wait_cost_reason: string
  wait_p50_hours: number | null
  wait_sample_n: number

  handling_cost_usd_per_mt: number | null
  handling_cost_provenance: DataProvenance | null
  handling_cost_reason: string

  demurrage_cost_usd_per_mt: number | null
  demurrage_cost_provenance: DataProvenance | null
  demurrage_cost_reason: string

  commodity_price_usd_per_mt: number | null
  commodity_price_provenance: DataProvenance | null
  commodity_price_reason: string
  commodity_price_raw_value: number | null
  commodity_price_raw_unit: string | null
  commodity_price_as_of: string | null

  fx_inr_per_usd: number | null
  fx_provenance: DataProvenance | null
  fx_as_of: string | null
  fx_reason: string

  /** Which of freight/wait_cost/handling_cost/demurrage_cost/commodity_price
   * actually fed partial_total_usd_per_mt -- the other side of
   * components_missing, so the gap is visible in the shape of the response
   * itself, not just in prose. */
  components_included: string[]
  components_missing: string[]
  /** Sum of components_included ONLY. Named "partial", not "landed_cost",
   * deliberately -- with any real component missing (the common case
   * without user-supplied commercial terms) this is a known lower bound,
   * not a complete total. */
  partial_total_usd_per_mt: number
  partial_total_inr_per_mt: number | null
}

/** POST /landed-cost's request body. Commercial terms have no default --
 * omit means unavailable server-side too, never a silently-assumed number. */
export interface LandedCostRequest {
  dest_port: PortCode
  cargo_volume_mt: number
  freight_usd_per_day: number
  voyage_days: number
  vessel_class?: VesselClass
  commodity_class?: string
  opex_usd_per_day?: number
  as_of?: string
  handling_rate_usd_per_mt?: number
  demurrage_usd_per_day?: number
  laytime_allowance_days?: number
  commodity?: 'iron_ore' | 'coal'
  convert_to_inr?: boolean
}

/** Minimal subset of opt.types.FeasibilityVerdict actually rendered on the
 * backhaul card -- the API response carries more fields (berth_id,
 * binding_constraint, margins, ...) than are typed/used here. */
export interface BackhaulClassFeasibility {
  is_feasible: boolean
  reason: string | null
}

/** opt.backhaul.PairingEvidence. See BackhaulOpportunityScore's own comment
 * on why this is never folded into `score`. */
export interface BackhaulPairingEvidence {
  discharge_port: PortCode
  load_port: PortCode
  cross_port: boolean
  n_total_vessels: number
  n_paired_vessels: number
  load_port_has_coverage: boolean
  pairing_rate: number | null
  is_sufficient: boolean
}

/** opt.backhaul.BackhaulOpportunityScore. Informational only --
 * `credit_usd_per_mt` is always `null` today (see `credit_evidence_reason`:
 * structural, FactPortCall carries no rate field to validate a $/MT credit
 * against). Never moves any recommendation elsewhere in this app. */
export interface BackhaulOpportunityScore {
  vessel_id: string
  discharge_port: PortCode
  candidate_load_port: PortCode
  vessel_class: string
  ballast_distance_nm: number
  ballast_days: number
  assumed_window_days: number
  timing_feasible: boolean
  cargo_probability: number
  cargo_probability_is_real_data: boolean
  class_feasibility: BackhaulClassFeasibility
  pairing_evidence: BackhaulPairingEvidence
  /** Bounded [0, 1]: cargo_probability, zeroed if the vessel can't reach the
   * port in time, fails class feasibility, or sufficient real evidence shows
   * zero turnaround at this exact port. */
  score: number
  /** Real bunker cost of the ballast leg to this candidate port. */
  ballast_cost_usd: number
  /** Today's real TC quote for this vessel's class -- null when none exists
   * for the requested date. Identical at every candidate port (no
   * route-level geography in the forecast yet); see score_usd. */
  base_tce_usd_per_day: number | null
  /** F-18: cargo_probability * base_tce_usd_per_day * assumed_window_days -
   * ballast_cost_usd, zeroed under the same conditions as `score` -- a real
   * dollar ranking signal, not just a probability. Null exactly when
   * base_tce_usd_per_day is null; fall back to `score` in that case. */
  score_usd: number | null
  credit_usd_per_mt: null
  credit_evidence_reason: string
  limitations: string[]
}

export interface BackhaulResponse {
  vessel_id: string
  discharge_port: PortCode
  results: BackhaulOpportunityScore[]
}

/** F-16: opt.types.PortfolioMix, JSON-shaped, plus the risk_aversion_k/
 * risk_aversion setting it was solved at (a frontier is a list of these at
 * different settings). */
export interface PortfolioMixResult {
  spot_fraction: number
  tc_fraction: number
  coa_fraction: number
  expected_cost_usd: number
  cost_std_usd: number
  stockout_probability: number
  stockout_penalty_usd: number
  risk_aversion_k: number
  risk_aversion: number
}

export interface PortfolioResponse {
  vessel_class: VesselClass
  as_of: string
  contract_term_days: number
  today_quote_usd_per_day: number
  spot_cost_usd: number
  spot_cost_std_usd: number
  /** True only when a real, evidence-gated route-specific rate basis was
   * found and used (same OBSERVED/MODELLED bar POST /quote applies) --
   * false means the class-level benchmark was used unadjusted, same as
   * every other screen's "Class-only" badge. */
  route_basis_applied: boolean
  recommended: PortfolioMixResult
  /** Always 8 points, risk_aversion_k = 0, 0.1, 0.25, 0.5, 1, 2, 4, 8. */
  frontier: PortfolioMixResult[]
}

// ---------------------------------------------------------------------------
// 4.3 -- Anchorage vessel census (Sentinel-1 SAR spot check) + calibration
// ---------------------------------------------------------------------------

/** The five real anchorage port labels anchorage.detect covers today --
 * NOT the same set as PortCode (HAY_POINT_AU is outside opt.network's core
 * 16-port network; see data_builders.harvest_sentinel1's own docstring). */
export type AnchoragePortCode = 'PARADIP' | 'VISAKHAPATNAM' | 'NEWCASTLE_AU' | 'HAY_POINT_AU' | 'RICHARDS_BAY_ZA'

export type AnchorageConfidence = 'high' | 'medium' | 'low'

/** anchorage.detect.Detection -- one CFAR blob after post-filtering. */
export interface AnchorageDetection {
  centroid_row: number
  centroid_col: number
  area_px: number
  peak_intensity: number
}

/** anchorage.detect.AnchorageCensus -- a real Sentinel-1-derived vessel
 * count for one anchorage scene. `provenance` is always 'MODEL_DERIVED': the
 * raw SAR backscatter a scene contains is OBSERVED, but a vessel COUNT is a
 * computation over that observation, never itself an observation -- see
 * anchorage.calibrate's own module docstring. This is a SPARSE, spot-check
 * signal (Sentinel-1's real revisit cadence at these ports is 6-12 days),
 * never a live one -- always render `acquired_at`'s age alongside the count. */
export interface AnchorageCensus {
  port: AnchoragePortCode
  scene_id: string
  acquired_at: string
  vessel_count: number
  detections: AnchorageDetection[]
  mean_sea_state_proxy: number
  confidence: AnchorageConfidence
  provenance: DataProvenance
}

/** anchorage.calibrate.PortCalibrationResult -- a spot-check comparison
 * between real satellite counts and PortWatch's own real daily call counts.
 * spearman_r/pearson_r are null whenever n is below the endpoint's own
 * min_n_for_correlation -- never report a correlation on too few points. */
export interface AnchorageCalibrationRow {
  port: AnchoragePortCode
  n: number
  date_from: string | null
  date_to: string | null
  spearman_r: number | null
  pearson_r: number | null
  mean_abs_diff: number | null
  finding: string
}

export interface AnchorageCalibrationResponse {
  min_n_for_correlation: number
  results: AnchorageCalibrationRow[]
}

// ---------------------------------------------------------------------------
// Season plan -- POST /season-plan. A book of cargo lots scheduled across a
// fleet in one CP-SAT solve, rather than priced one at a time.
// ---------------------------------------------------------------------------

export interface SeasonParcelInput {
  parcel_id: string
  origin_port: PortCode
  dest_port: PortCode
  commodity: string
  volume_dwt: number
  laycan_start: string
  laycan_end: string
  /** No honest default exists -- revenue is a business fact. 0 means the
   *  scheduler will correctly never assign a vessel to this lot. */
  revenue_usd: number
}

export interface SeasonAssignment {
  vessel_id: string
  parcel_id: string
  dest_port: string
  arrival_hours: number
  wait_hours: number
  start_operation_hours: number
  finish_hours: number
  ballast_hours: number
  inter_cargo_gap_hours: number
  profit_usd: number
}

export interface SeasonUnassigned {
  parcel_id: string
  reason: string
}

export interface SeasonInfeasiblePair {
  vessel_id: string
  parcel_id: string
  reason: string
}

/**
 * Break-even hire for one vessel class in a solved season plan — the highest
 * daily rate at which chartering in the tonnage to cover the plan still
 * breaks even.
 *
 * `spot_tc_average_usd_per_day` is the real published Baltic class TC average
 * for the class, read at the pricing date. It is a SPOT index, not a period
 * quote: no period charter rate exists anywhere in this system's data, and
 * `opt/period_cover.py` documents why nothing here invents one.
 */
export interface SeasonPeriodCover {
  vessel_class: VesselClass
  n_vessels: number
  profit_usd: number
  ship_days: number
  break_even_hire_usd_per_day: number
  /** null when no real observation exists at the pricing date. */
  spot_tc_average_usd_per_day: number | null
  spot_tc_series_id: string
  spot_tc_as_of: string | null
  verdict: 'cover_beats_spot' | 'spot_beats_cover' | 'no_benchmark'
  margin_over_spot_usd_per_day: number | null
}

export interface SeasonPlanResponse {
  as_of: string
  solver_status: string
  total_profit_usd: number
  n_parcels: number
  n_vessels: number
  n_assigned: number
  assignments: SeasonAssignment[]
  unassigned: SeasonUnassigned[]
  infeasible_pairs: SeasonInfeasiblePair[]
  /** Empty when nothing was scheduled — no voyages means no earnings to
   *  break even on, and a break-even of zero would read as a market finding
   *  rather than as "there is no plan". */
  period_cover: SeasonPeriodCover[]
}

// ---------------------------------------------------------------------------
// Accounts, roles and sessions
// ---------------------------------------------------------------------------

/**
 * What an account may do. Ordered: admin outranks chartering_manager outranks
 * viewer. `src/auth/models.py` carries the reasoning for why there are three
 * rather than two — the middle one exists because recording a realised
 * outcome has to be an attributable act, or the performance record computed
 * from the ledger means nothing.
 */
export type DeskRole = 'viewer' | 'chartering_manager' | 'admin'

export interface DeskUser {
  user_id: string
  username: string
  display_name: string
  role: DeskRole
  is_active: boolean
  created_at: string
  last_login_at: string | null
}

export interface AuthStatus {
  /** True when this deployment requires a sign-in for every route. False is
   *  a real, deliberate mode — not a bug — and the UI says which one it is
   *  in rather than showing a padlock over an open door. */
  enforced: boolean
  /** True when no account exists yet, so the first admin still has to be
   *  created. There is no seeded account and no default password anywhere. */
  needs_bootstrap: boolean
  user: DeskUser | null
  roles: { value: DeskRole; description: string }[]
  session_hours: number
}

// ---------------------------------------------------------------------------
// Standing alerts
// ---------------------------------------------------------------------------

/** Only conditions the backend can evaluate honestly from data already on
 *  disk. `src/alerts/models.py` documents what is deliberately absent and
 *  why — a condition with no falsifiable trigger is a feeling, not an alert. */
export type WatchKind = 'rate_crosses' | 'rate_moves' | 'outcome_overdue'

export interface Watch {
  watch_id: string
  kind: WatchKind
  label: string
  is_active: boolean
  created_at: string
  /** Null on an open deployment where nobody was signed in — recorded rather
   *  than invented. */
  created_by: string | null
  vessel_class: VesselClass | null
  threshold_usd_per_day: number | null
  direction: 'above' | 'below' | null
  move_pct: number | null
  window_days: number | null
  overdue_days: number | null
  last_evaluated_at: string | null
}

export interface Firing {
  firing_id: string
  watch_id: string
  /** When this system noticed. */
  fired_at: string
  /** When the number behind it was actually published — usually earlier than
   *  `fired_at`, and never conflated with it. */
  observed_on: string | null
  message: string
  observed_value: number | null
  is_read: boolean
}

export interface AlertsResponse {
  watches: Watch[]
  firings: Firing[]
  unread: number
  /** Always false today. Nothing in this stack emails, texts or calls anyone,
   *  and the UI says so rather than implying delivery it does not perform. */
  delivers_notifications: boolean
  /** null when the background loop is disabled and evaluation is driven
   *  externally. */
  evaluation_interval_seconds: number | null
  kinds: { value: WatchKind; description: string }[]
}
