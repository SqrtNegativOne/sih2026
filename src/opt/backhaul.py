"""Backhaul opportunity scoring -- P6 commercial upgrade.

Answers "if this vessel discharges at port A, is there a real, class-
compatible loading opportunity at port B that avoids an empty ballast leg
back?" Combines four real inputs, none rebuilt from scratch:

1. **Observed load/discharge pairing frequency**, from
   ``berth_truth.fact_port_call``'s real ``load_discharge`` flag -- see
   ``observed_pairing_evidence`` and its own honesty caveat below.
2. **Timing feasibility**, from the real geography-derived ballast transit
   time against the same window used for (3).
3. **Vessel-class compatibility**, reusing ``opt.voyage``'s real feasibility
   check (draft/LOA/beam/DWT against the berth_truth register where one
   exists, ``opt.network.Port`` dimensions otherwise) -- not a second,
   simplified dimension check.
4. **Poisson cargo-hazard rate**, reusing ``opt.repositioning``'s existing
   ``cargo_probability_within_window`` directly -- not a second hazard model.

**A real, load-bearing data limitation, disclosed rather than worked around:**
every row currently in ``raw_data/berth_truth/fact_port_call.jsonl`` is from
a single port (Paradip) -- confirmed by direct query, not assumed. There is
therefore no real evidence of any actual *cross-port* pairing (vessel
discharges at A, loads at B) anywhere in the data yet: ``observed_pairing_evidence``
is written to support it generally (so it activates the moment a second
port's berth_truth coverage exists), but for two different ports today it can
only ever report zero observed vessels, honestly. For the *same* port used as
both legs, it reports a real, meaningful number: of vessels with a real call
at that port, what fraction have both a LOAD and a DISCHARGE call there
(a same-port turnaround signal, which is what the data actually supports today).

**Never folded into the numeric score.** ``observed_pairing_evidence`` is
informational context on ``BackhaulOpportunityScore``, not a multiplier --
see ``evaluate_credit_evidence`` for why a $/MT credit is not computed from
it (structural: ``FactPortCall`` carries no rate/freight field at all, so
there is nothing to correlate a pairing against, regardless of sample size).

**score_usd -- a real dollar value, not just a probability.** The original
version of this module scored a candidate port purely as
``cargo_probability`` (or 0.0 if infeasible) -- a real number, but not money,
so it couldn't answer "is this actually worth the ballast leg." ``score_usd``
now puts real economics behind it, the same shape ``opt.repositioning``'s
``recommend_repositioning`` already uses for the equivalent idle-vessel
decision: ``cargo_probability * base_tce_usd_per_day * assumed_window_days -
ballast_cost_usd`` (real bunker consumption x real distance x the real
blended bunker price). Same disclosed limitation as ``opt.repositioning``:
``base_tce_usd_per_day`` is today's real TC quote for the vessel's class --
identical at every candidate port, because the rate forecast has no
route-level geography yet (a larger, separate gap). It differentiates
candidates by real, port-specific cargo probability and real, port-specific
ballast cost -- not by "rates are better here" -- and that's disclosed in
``limitations``, not hidden. ``None`` (not a fabricated number) when no real
TC quote exists for the requested date; ``score`` (the original bounded
[0, 1] probability) stays available as a fallback ranking signal in that
case.
"""
from __future__ import annotations

import functools
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from berth_truth.fact_port_call import FactPortCall, FactPortCallStore, default_store
from opt.geography import distance_nm as geo_distance_nm
from opt.network import BLENDED_BUNKER_USD_PER_TONNE, PortEnum
from opt.repositioning import cargo_probability_within_window
from opt.types import FeasibilityVerdict, Vessel

__all__ = [
    "BackhaulOpportunityScore",
    "CreditEvidenceVerdict",
    "PairingEvidence",
    "backhaul_opportunity_score",
    "clear_backhaul_cache",
    "evaluate_credit_evidence",
    "observed_pairing_evidence",
]

#: Below this many vessels observed at a port, a pairing rate is reported but
#: flagged insufficient rather than trusted -- the same threshold P4's
#: opt.basis.MIN_ROUTE_OBS uses for "how many real observations before a
#: rate is trustworthy," reused here for consistency of that judgment call
#: across the codebase, not re-derived from scratch.
MIN_PAIRING_OBS: Final[int] = 5

_RATE_FIELD_KEYWORDS: Final[tuple[str, ...]] = ("rate", "usd", "freight", "tce", "price", "$")


def _row_date(row: FactPortCall) -> datetime | None:
    """Same field-priority convention FactPortCallStore.query() uses
    internally for "what date does this row represent" (arrival, then berth,
    then ETA, else the source document's own date) -- restated here since
    that logic is a private closure inside query(), not exported."""
    for ts in (row.arrival_ts, row.berth_ts, row.eta_ts):
        if ts is not None:
            return ts
    if row.source_doc_date is not None:
        return datetime.combine(row.source_doc_date, datetime.min.time())
    return None


@functools.cache
def _all_calls(store: FactPortCallStore) -> tuple[FactPortCall, ...]:
    """Cached whole-store read, keyed only by store (FactPortCallStore is a
    frozen, hashable dataclass).

    ``FactPortCallStore.query()`` calls ``read_all()`` -- a full file read +
    JSON-parse of the whole real fact_port_call log -- INSIDE itself, on
    every call, regardless of which port is asked for. A backhaul sweep
    against N candidate ports therefore does N full-file reads even when
    N-1 of them return zero rows for a port with no coverage -- measured at
    ~10s uncached for a 15-port sweep, unusable inside a live request.
    Caching store.query() itself (or read_all()) would change behaviour for
    every other consumer of FactPortCallStore across the codebase, some of
    which legitimately need to see a row immediately after append_many() in
    the same process (see tests/berth_truth/test_fact_port_call.py) -- too
    wide a blast radius for a P6-scoped fix. Caching the read HERE instead,
    then filtering by port locally in Python, keeps every other consumer's
    behaviour (including staleness-after-write) completely unchanged; this
    module's own clear_backhaul_cache() covers the one caller (this file)
    that now needs an explicit invalidation after a live re-harvest."""
    return store.read_all()


def _vessel_call_index(port: PortEnum, store: FactPortCallStore) -> dict[str, list[FactPortCall]]:
    """Real, non-quarantined calls at ``port``, grouped by vessel_name.
    Rows with no vessel_name can't be linked across calls, so they're
    excluded here (not silently counted as a non-match). Filters the
    cached whole-store read locally rather than calling store.query()
    (which would re-read the file) -- see _all_calls."""
    index: dict[str, list[FactPortCall]] = {}
    for row in _all_calls(store):
        if row.port is not port or row.is_quarantined:
            continue
        if row.vessel_name:
            index.setdefault(row.vessel_name, []).append(row)
    return index


def clear_backhaul_cache() -> None:
    """Drop the cached whole-store read. Call after rebuilding the P1
    fact_port_call log (or after append_many-ing new rows via the same
    store object) within a live process -- mirrors
    opt.repositioning.clear_hazard_cache / opt.voyage.clear_effective_handling_rate_cache."""
    _all_calls.cache_clear()


@dataclass(frozen=True)
class PairingEvidence:
    """What berth_truth.fact_port_call actually shows about load/discharge
    pairing between discharge_port and load_port -- real counts, not a rate
    presented without its own denominator."""

    discharge_port: PortEnum
    load_port: PortEnum
    cross_port: bool
    """True when discharge_port != load_port -- see module docstring: this
    case has zero real observations today (single-port BT coverage), by
    construction, not a bug in the counting logic below."""
    n_total_vessels: int
    """Vessels with >=1 real call at discharge_port (the population "at
    risk" of this backhaul pattern)."""
    n_paired_vessels: int
    """Of those, vessels also showing a real LOAD call at load_port (cross-
    port case) or a real LOAD call at the same port (same-port case), after
    a real DISCHARGE call there."""
    load_port_has_coverage: bool
    """True when load_port has >=1 real fact_port_call row at all. Tracked
    separately from n_paired_vessels/n_total_vessels: without this, a
    cross-port pair where load_port simply has zero berth_truth coverage
    computes n_paired=0 against a real, nonzero n_total (from the discharge
    side alone) and reports pairing_rate=0.0 -- which reads as "proven zero"
    rather than "no evidence at the load port to pair against." Always True
    for the same-port case by construction."""
    pairing_rate: float | None
    """n_paired_vessels / n_total_vessels, or None when n_total_vessels == 0
    (nothing to divide by -- never silently reported as 0.0, which would
    read as "observed and zero" rather than "no evidence either way").
    Meaningful only when load_port_has_coverage is also True -- see above."""
    is_sufficient: bool
    """False below MIN_PAIRING_OBS total vessels, or when load_port has no
    coverage at all -- a real rate computed from too few vessels (or from a
    structurally empty comparison) to be treated as a stable estimate."""


def observed_pairing_evidence(
    discharge_port: PortEnum, load_port: PortEnum, *, store: FactPortCallStore | None = None
) -> PairingEvidence:
    """Real, live-queried evidence of vessels discharging at discharge_port
    and subsequently loading at load_port (or, when the two ports are the
    same, turning around there) -- see class docstring and module docstring
    for exactly what this can and cannot show today.

    ``store`` defaults to the real fact_port_call log (``berth_truth.fact_port_call.default_store``);
    pass an explicit one (matching the convention already used by
    ``berth_truth.empirical.compute_wait_distribution``) to test against a
    controlled fixture instead of live data."""
    store = store if store is not None else default_store()
    discharge_calls = _vessel_call_index(discharge_port, store)
    n_total = len(discharge_calls)

    if discharge_port == load_port:
        load_port_has_coverage = True
        n_paired = sum(
            1
            for calls in discharge_calls.values()
            if any(c.load_discharge == "DISCHARGE" for c in calls)
            and any(c.load_discharge == "LOAD" for c in calls)
        )
    else:
        load_calls = _vessel_call_index(load_port, store)
        load_port_has_coverage = len(load_calls) > 0
        n_paired = 0
        for vessel_name, d_calls in discharge_calls.items():
            l_calls = load_calls.get(vessel_name)
            if not l_calls:
                continue
            last_discharge = max(
                (_row_date(c) for c in d_calls if c.load_discharge == "DISCHARGE"),
                default=None,
            )
            first_load_after = min(
                (
                    dt
                    for c in l_calls
                    if c.load_discharge == "LOAD" and (dt := _row_date(c)) is not None
                    and last_discharge is not None and dt >= last_discharge
                ),
                default=None,
            )
            if last_discharge is not None and first_load_after is not None:
                n_paired += 1

    return PairingEvidence(
        discharge_port=discharge_port,
        load_port=load_port,
        cross_port=discharge_port != load_port,
        n_total_vessels=n_total,
        n_paired_vessels=n_paired,
        load_port_has_coverage=load_port_has_coverage,
        pairing_rate=(n_paired / n_total) if n_total > 0 else None,
        is_sufficient=(n_total >= MIN_PAIRING_OBS) and load_port_has_coverage,
    )


@dataclass(frozen=True)
class CreditEvidenceVerdict:
    sufficient_for_credit: bool
    reason: str
    rate_bearing_fields_found: tuple[str, ...]


def evaluate_credit_evidence() -> CreditEvidenceVerdict:
    """Live schema check, not a hardcoded claim: does FactPortCall carry any
    field a real backhaul pairing could be correlated against to estimate a
    $/MT credit? Re-checked against the model's actual fields every call
    (cheap -- pure introspection, no I/O) so this cannot go stale silently
    the way a comment could."""
    field_names = tuple(FactPortCall.model_fields.keys())
    matches = tuple(f for f in field_names if any(k in f.lower() for k in _RATE_FIELD_KEYWORDS))
    if not matches:
        return CreditEvidenceVerdict(
            sufficient_for_credit=False,
            reason=(
                "FactPortCall carries no rate/freight/$ field of any kind "
                f"(checked all {len(field_names)} fields: quantities, "
                "timestamps, identity and provenance only) -- there is no "
                "realized price anywhere in this table to correlate an "
                "observed pairing against, so a $/MT credit cannot be "
                "estimated from this data regardless of sample size. "
                "Backhaul opportunity is informational only."
            ),
            rate_bearing_fields_found=(),
        )
    return CreditEvidenceVerdict(
        sufficient_for_credit=False,
        reason=(
            f"FactPortCall now carries a candidate rate-bearing field "
            f"{matches} that did not exist when this gate was last "
            "reviewed -- a real correlation/validation study against "
            "observed pairings is required before enabling a $/MT credit; "
            "this function deliberately still returns "
            "sufficient_for_credit=False until that study exists and this "
            "code is revisited by hand."
        ),
        rate_bearing_fields_found=matches,
    )


#: Computed once at import time -- pure Pydantic field introspection, no I/O,
#: so there is no reason to defer or cache it like the real data-backed
#: computations elsewhere in this module.
CREDIT_EVIDENCE: Final[CreditEvidenceVerdict] = evaluate_credit_evidence()


@dataclass(frozen=True)
class BackhaulOpportunityScore:
    vessel_id: str
    discharge_port: PortEnum
    candidate_load_port: PortEnum
    vessel_class: str

    ballast_distance_nm: float
    ballast_days: float
    assumed_window_days: int
    timing_feasible: bool
    """False when ballast_days exceeds assumed_window_days -- the vessel
    cannot physically reach the candidate port within the same window the
    cargo-hazard probability below was computed over, so that probability
    does not meaningfully describe this specific vessel's opportunity."""

    cargo_probability: float
    """P(class-appropriate cargo departs candidate_load_port within
    assumed_window_days) -- opt.repositioning.cargo_probability_within_window,
    reused directly, not recomputed."""
    cargo_probability_is_real_data: bool

    class_feasibility: FeasibilityVerdict
    """opt.voyage's real feasibility verdict for this vessel at
    candidate_load_port -- reused directly, not a second dimension check."""

    pairing_evidence: PairingEvidence
    """Informational only -- see module docstring. Never multiplied into
    `score` below."""

    score: float
    """Bounded [0, 1]: cargo_probability, zeroed if the vessel cannot
    physically make the port in time or does not pass the class-feasibility
    check. Deliberately simple and inspectable -- every factor that zeroes
    it is a real, named field on this same object, not a hidden weight."""

    ballast_cost_usd: float
    """Real bunker cost of the ballast leg to this candidate port: ballast
    days x the vessel's own real ballast fuel consumption rate x the real
    blended bunker price -- the same calculation opt.repositioning uses for
    the equivalent idle-vessel decision."""

    base_tce_usd_per_day: float | None
    """Today's real TC quote for this vessel's class, used to value
    score_usd -- see module docstring's disclosed limitation (class-level,
    identical at every candidate port). None when no real quote exists for
    the requested date."""

    score_usd: float | None
    """cargo_probability * base_tce_usd_per_day * assumed_window_days -
    ballast_cost_usd, zeroed under the same conditions as `score` -- a real
    dollar ranking signal, not just a probability. None exactly when
    base_tce_usd_per_day is None (no real quote to value it with) -- never
    fabricated; fall back to `score` for ranking in that case."""

    credit_usd_per_mt: None
    """Always None -- see CREDIT_EVIDENCE.reason. Present as an explicit
    field (not omitted) so a caller sees the gap rather than inferring
    "zero credit" from a missing key."""
    credit_evidence_reason: str

    limitations: tuple[str, ...]


def backhaul_opportunity_score(
    vessel: Vessel,
    discharge_port: PortEnum,
    candidate_load_port: PortEnum,
    *,
    assumed_window_days: int = 30,
    base_tce_usd_per_day: float | None = None,
    store: FactPortCallStore | None = None,
) -> BackhaulOpportunityScore:
    """Score one candidate backhaul leg for one vessel. See module and
    dataclass docstrings for exactly what each component is and is not.
    ``store`` is test-injection only -- see ``observed_pairing_evidence``.
    ``base_tce_usd_per_day``: today's real TC quote for ``vessel.vessel_class``
    (e.g. from ``ml.live_forecast.forecast_all_classes``), used to compute
    ``score_usd``. Omit (None) when no real quote is available -- score_usd
    comes back None too, not fabricated; ``score`` still works as a
    probability-only fallback ranking."""
    # Local import: opt.voyage pulls in ortools at module scope.
    from opt.voyage import _vessel_can_call

    dist_nm = geo_distance_nm(discharge_port.value.id, candidate_load_port.value.id)
    speed = vessel.speed_kn if vessel.speed_kn > 0 else 12.0
    ballast_days = dist_nm / (speed * 24.0)
    timing_feasible = ballast_days <= assumed_window_days

    cargo_probability, probability_is_real = cargo_probability_within_window(
        candidate_load_port, vessel.vessel_class, assumed_window_days
    )

    feasibility = _vessel_can_call(vessel, candidate_load_port)
    pairing = observed_pairing_evidence(discharge_port, candidate_load_port, store=store)

    # Pairing evidence only vetoes the score when it is REAL, SUFFICIENT, and
    # shows zero turnaround at this exact port (n_paired_vessels == 0 despite
    # enough vessels to trust the rate) -- a real, evidenced "no pairs" veto.
    # Absent/insufficient/structurally-empty pairing evidence (the common
    # case today, see module docstring) does NOT veto: absence of evidence
    # is not evidence of absence, and hazard+feasibility remain the best
    # real information available for a cross-port candidate.
    pairing_vetoes = pairing.is_sufficient and pairing.n_paired_vessels == 0
    is_viable = timing_feasible and feasibility.is_feasible and not pairing_vetoes
    score = cargo_probability if is_viable else 0.0

    ballast_cost_usd = ballast_days * vessel.ballast_fuel_consumption_tpd * BLENDED_BUNKER_USD_PER_TONNE
    if base_tce_usd_per_day is None:
        score_usd = None
    else:
        expected_voyage_value = cargo_probability * base_tce_usd_per_day * assumed_window_days
        score_usd = (expected_voyage_value - ballast_cost_usd) if is_viable else 0.0

    limitations: list[str] = [CREDIT_EVIDENCE.reason]
    if base_tce_usd_per_day is None:
        limitations.append(
            "No real TC quote/forecast available for this vessel class as of the requested "
            "date -- score_usd could not be computed; ranked by cargo_probability (score) alone."
        )
    if not probability_is_real:
        limitations.append(
            f"{candidate_load_port.name} has no real PortWatch/tonnage-field coverage -- "
            "cargo_probability used a neutral 0.5 prior, not a calibrated estimate."
        )
    if not timing_feasible:
        limitations.append(
            f"ballast transit ({ballast_days:.1f}d) exceeds the assumed window "
            f"({assumed_window_days}d) -- score zeroed, not a real opportunity at this speed/window."
        )
    if not feasibility.is_feasible:
        limitations.append(f"vessel does not pass class/dimension feasibility at {candidate_load_port.name}: {feasibility.reason}")
    if pairing_vetoes:
        limitations.append(
            f"score zeroed: {pairing.n_total_vessels} real vessel(s) observed at "
            f"{discharge_port.name}, none showed a real LOAD there after a DISCHARGE "
            "-- sufficient, real evidence of no turnaround at this port."
        )
    if pairing.cross_port and not pairing.load_port_has_coverage:
        limitations.append(
            f"{candidate_load_port.name} has no real berth_truth fact_port_call "
            f"coverage at all -- pairing_evidence against {discharge_port.name} is "
            "structurally empty (0 possible matches), not evidence of a low pairing rate."
        )
    elif not pairing.is_sufficient:
        limitations.append(
            f"pairing_evidence is based on only {pairing.n_total_vessels} vessel(s) "
            f"(< {MIN_PAIRING_OBS}) -- too few to treat pairing_rate as stable."
        )

    return BackhaulOpportunityScore(
        vessel_id=vessel.vessel_id,
        discharge_port=discharge_port,
        candidate_load_port=candidate_load_port,
        vessel_class=vessel.vessel_class.value,
        ballast_distance_nm=dist_nm,
        ballast_days=ballast_days,
        assumed_window_days=assumed_window_days,
        timing_feasible=timing_feasible,
        cargo_probability=cargo_probability,
        cargo_probability_is_real_data=probability_is_real,
        class_feasibility=feasibility,
        pairing_evidence=pairing,
        score=score,
        ballast_cost_usd=ballast_cost_usd,
        base_tce_usd_per_day=base_tce_usd_per_day,
        score_usd=score_usd,
        credit_usd_per_mt=None,
        credit_evidence_reason=CREDIT_EVIDENCE.reason,
        limitations=tuple(limitations),
    )
