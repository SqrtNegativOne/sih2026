"""M1 Identification Gate -- P3 requirement 1: the determination that governs
everything else the Tonnage Field claims.

The question: can *absolute* free/available tonnage (``stock_available_dwt``,
distinct from ``stock_total_dwt``) be identified from evidence actually on disk?

This module does not re-derive the underlying finding -- ``tonnage.validate``
already did that honestly (``SignalValidationSummary.absolute_scale_validated``,
permanently ``False``, with the 0.35x-26x spread as evidence). What this module
adds, per the P3 prompt:

1. An explicit **evidence survey** -- what would separating total fleet from
   available/ballasting fleet actually require, and does anything in
   ``raw_data/`` supply it. This is the "attempt" the prompt asks for: not a
   rhetorical claim that it's impossible, but a named check of every real data
   source in this repository against what the split needs.
2. A machine-readable, testable ``IndexType`` verdict (``ABSOLUTE`` |
   ``RELATIVE``) derived from that survey plus the existing Signal validation --
   never a hardcoded string scattered across docstrings.
3. Formal ``IVVerdict`` / ``KalmanVerdict`` records -- P3 requirement 4 --
   restating conclusions ``tonnage.supplycurve`` and ``tonnage.stockflow``
   already reached in prose, as structured, reportable, testable objects.
4. A ``SignDiagnosis`` per vessel class -- P3 requirement 3 -- assembled from
   the real diagnostic functions in ``tonnage.supplycurve`` (first-difference,
   regime-split, lead/lag, basin-aggregation, target-transform), run live
   against real data, never against a hoped-for sign.

No new calibration factor is introduced anywhere in this module. Every constant
consumed here (``UNCTAD_FLEET_DWT_BY_CLASS``, ``CLASS_MIDPOINT_DWT``,
``RESIDENCE_WINDOW_DAYS``) already exists in ``tonnage.stockflow`` /
``tonnage.classmix`` with its own citation; this module reuses, it does not add.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Final

import polars as pl

from opt.types import VesselClass
from tonnage.stockflow import StockflowResult
from tonnage.supplycurve import (
    BasinAggregationCheck,
    FirstDifferenceCheck,
    LeadLagCorrelation,
    NoOverlapError,
    RegimeSplitCheck,
    SupplyCurveFit,
    TargetTransformCheck,
    basin_aggregation_diagnostic,
    first_difference_diagnostic,
    lead_lag_correlation,
    regime_split_diagnostic,
    target_transform_diagnostic,
)
from tonnage.validate import (
    SignalValidationSummary,
    compare_to_signal,
    summarize_signal_validation,
)

__all__ = [
    "AvailableTonnageEvidenceSurvey",
    "EvidenceSourceAssessment",
    "IVVerdict",
    "IdentificationGateResult",
    "IndexType",
    "KalmanVerdict",
    "MethodStatus",
    "SignDiagnosis",
    "diagnose_all_signs",
    "diagnose_sign",
    "evaluate_identification_gate",
    "evaluate_iv_verdict",
    "evaluate_kalman_verdict",
    "survey_available_tonnage_evidence",
]


class IndexType(str, Enum):
    """What kind of number ``tonnage.stockflow.stock_dwt`` (and everything built
    on it) is allowed to be presented as."""

    ABSOLUTE = "ABSOLUTE"
    RELATIVE = "RELATIVE"


# ---------------------------------------------------------------------------
# 1. Evidence survey -- the "attempt" the prompt requires
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EvidenceSourceAssessment:
    """One real data source in this repository, assessed against one specific
    question: does it distinguish a vessel that is *available* (free, ballasting,
    open for the next fixture) from one that is *committed* (laden, under period
    charter, already fixed)? Port-call arrival/departure records alone cannot --
    a vessel that just discharged and instantly re-fixed looks identical, in
    PortWatch or in ``fact_port_call``, to one that discharged and now sits idle
    for three weeks. Only a feed that reports vessel *state between* port calls
    (AIS speed/draft trajectory, a charter-fixture status feed, a live
    ballaster list) can make that distinction."""

    source: str
    path: str
    what_it_provides: str
    supports_total_available_split: bool
    reason: str


def survey_available_tonnage_evidence() -> AvailableTonnageEvidenceSurvey:
    """Real, named check of every raw data source on disk (``raw_data/``, listed
    directly -- not from memory) against the total-vs-available question.

    Nothing here is hypothetical: every path named is a real directory/file this
    repository actually has (or explicitly does not have, in the closing entry).
    """
    sources = (
        EvidenceSourceAssessment(
            source="PortWatch daily port calls",
            path="raw_data/portwatch/*_daily_portcalls.csv",
            what_it_provides=(
                "Per-port, per-day COUNT (portcalls_dry_bulk -- real AIS-derived, "
                "provenance OBSERVED) and TONNAGE (import_dry_bulk/export_dry_bulk "
                "-- PortWatch's own model estimate from AIS draft changes, "
                "provenance ESTIMATED, not a customs/weighbridge measurement -- "
                "see data_builders.provenance) of dry-bulk calls. A flow at the "
                "port boundary either way, not a vessel-level state."
            ),
            supports_total_available_split=False,
            reason=(
                "A completed call tells you a vessel WAS at a port on a given day. "
                "It says nothing about what that vessel does next -- refix "
                "immediately (unavailable) or ballast and wait (available). "
                "tonnage.stockflow already treats this correctly: it reconstructs "
                "a flow-conservation trajectory, never a per-vessel state."
            ),
        ),
        EvidenceSourceAssessment(
            source="fact_port_call (P1 berth_truth ingestion)",
            path="raw_data/berth_truth/fact_port_call.jsonl",
            what_it_provides=(
                "One row per vessel call at Paradip, with observed LOA/beam/draft "
                "and (usually) a vessel name -- but no IMO in any currently "
                "ingested source, no position history, no idle/ballast flag."
            ),
            supports_total_available_split=False,
            reason=(
                "Same limitation as PortWatch, at higher per-call fidelity: an "
                "arrival record, not a between-calls trajectory. Without an IMO "
                "join to an external vessel-position/fixture feed (none exists in "
                "this repo -- see the closing entry below), there is no way to "
                "know whether the vessel now sits open or has already re-fixed."
            ),
        ),
        EvidenceSourceAssessment(
            source="Signal Ocean weekly ballaster snapshots",
            path="raw_data/signal_weekly/",
            what_it_provides=(
                "12 real, hand-transcribed (basin, class, date) ballaster COUNTS "
                "from 3 of 15 real Signal weekly issues -- see "
                "tonnage.validate.KNOWN_SIGNAL_BALLASTER_SNAPSHOTS."
            ),
            supports_total_available_split=False,
            reason=(
                "This is exactly the quantity a total-available split would need "
                "-- but as 12 sparse point observations, not a continuous series. "
                "It can validate (or fail to validate) a reconstruction against "
                "real ground truth at those 12 points (tonnage.validate does "
                "this); it cannot itself BE the daily reconstruction input, and "
                "fitting a correction factor to make the reconstruction hit these "
                "same 12 points would be tuning a parameter on its own validation "
                "set -- the exact mistake tonnage.validate's own docstring "
                "already refuses to make."
            ),
        ),
        EvidenceSourceAssessment(
            source="AIS position/speed feed, charter-fixture status feed, or any "
            "other per-vessel state source",
            path="(does not exist anywhere in raw_data/)",
            what_it_provides="N/A -- not present in this repository.",
            supports_total_available_split=False,
            reason=(
                "This is the one kind of evidence that WOULD support the split "
                "(vessel-level speed/draft trajectory distinguishing laden transit "
                "from ballasting from anchored/idle, or a fixture-status feed "
                "distinguishing open from committed). Checked directly: "
                "raw_data/ contains baltic_routes.csv, berth_truth/, "
                "handybulk_*.csv, investing_com/, pilot_index_levels.csv, "
                "portwatch/, signal_weekly/, sources.md -- none of these is a "
                "per-vessel feed. Commercial products (Signal Ocean, Clarksons) "
                "sell exactly this and it is out of scope to acquire for this "
                "project (see the licensing/data-source discipline elsewhere in "
                "this codebase)."
            ),
        ),
    )
    return AvailableTonnageEvidenceSurvey(sources=sources)


@dataclass(frozen=True)
class AvailableTonnageEvidenceSurvey:
    sources: tuple[EvidenceSourceAssessment, ...]

    @property
    def any_source_supports_split(self) -> bool:
        return any(s.supports_total_available_split for s in self.sources)


# ---------------------------------------------------------------------------
# 2. The gate itself
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IdentificationGateResult:
    index_type: IndexType
    evidence_survey: AvailableTonnageEvidenceSurvey
    signal_validation: SignalValidationSummary
    calibration_factor_applied: bool
    reasoning: str


def evaluate_identification_gate(result: StockflowResult) -> IdentificationGateResult:
    """The one call that decides ``ABSOLUTE`` vs ``RELATIVE`` for the whole
    Tonnage Field. Requires BOTH a supporting evidence source AND a validated
    absolute scale to claim ``ABSOLUTE`` -- either one failing is disqualifying,
    since an absolute number that merely "isn't contradicted" by evidence but was
    never actually validated is not the same as an identified one."""
    survey = survey_available_tonnage_evidence()
    points = compare_to_signal(result)
    signal_summary = summarize_signal_validation(points)

    index_type = (
        IndexType.ABSOLUTE
        if (survey.any_source_supports_split and signal_summary.absolute_scale_validated)
        else IndexType.RELATIVE
    )

    if index_type is IndexType.RELATIVE:
        reasoning = (
            "RELATIVE. Two independent checks both fail the bar for ABSOLUTE: "
            "(1) no evidence source on disk distinguishes an available/ballasting "
            "vessel from a committed one (see evidence_survey -- port-call "
            "records are a flow at the port boundary, not a vessel-level state); "
            f"(2) the reconstruction's implied ballaster count runs "
            f"{signal_summary.min_ratio:.2f}x to {signal_summary.max_ratio:.2f}x "
            f"real Signal Ocean ballaster counts across {signal_summary.n_points} "
            "real comparison points, with no single correction factor that fixes "
            "all of them (tonnage.validate.SignalValidationSummary). Per the P3 "
            "instruction, no calibration factor is fitted to force a fit against "
            "those 12 points -- that would be tuning on the validation set. "
            "stock_dwt and everything downstream (tightness, forward projection) "
            "ships as a Physical Supply Pressure Index / Tonnage Tightness Index: "
            "a relative, within-(basin, class)-over-time signal, with no "
            "absolute-DWT claim attached to it anywhere in the API or UI."
        )
    else:
        reasoning = (
            "ABSOLUTE. An evidence source supporting the total/available split "
            f"was found AND the Signal comparison validated the absolute scale "
            f"({signal_summary.n_points} points, ratio range "
            f"{signal_summary.min_ratio:.2f}x-{signal_summary.max_ratio:.2f}x)."
        )

    return IdentificationGateResult(
        index_type=index_type,
        evidence_survey=survey,
        signal_validation=signal_summary,
        calibration_factor_applied=False,
        reasoning=reasoning,
    )


# ---------------------------------------------------------------------------
# 3. IV and Kalman verdicts -- P3 requirement 4
# ---------------------------------------------------------------------------


class MethodStatus(str, Enum):
    NOT_ATTEMPTED = "NOT_ATTEMPTED"
    REJECTED_ALTERNATIVE_SHIPPED = "REJECTED_ALTERNATIVE_SHIPPED"
    IMPLEMENTED_VALIDATED = "IMPLEMENTED_VALIDATED"


@dataclass(frozen=True)
class IVVerdict:
    status: MethodStatus
    candidate_instruments_considered: tuple[str, ...]
    reason: str


def evaluate_iv_verdict() -> IVVerdict:
    """A valid instrument must move tightness (relevance) while affecting the
    freight rate ONLY through tightness (exclusion). No candidate available in
    this repo's real data clears exclusion."""
    return IVVerdict(
        status=MethodStatus.NOT_ATTEMPTED,
        candidate_instruments_considered=(
            (
                "weather/chokepoint-driven supply shocks -- docs/plan.md's original "
                "proposed instrument"
            ),
        ),
        reason=(
            "No defensible instrument was identified, so none was fitted. "
            "Weather/chokepoint disruption in the data actually on disk "
            "(PortWatch daily call counts, the only flow series available) "
            "plausibly affects port throughput, vessel positioning, and route "
            "choice simultaneously with rates -- the same channels the rate "
            "itself moves through -- so exclusion cannot be argued from evidence "
            "in this repository, only asserted. tonnage.supplycurve's own module "
            "docstring already reaches this conclusion "
            "('What this module does not attempt'). The IV claim is removed from "
            "docs/plan.md (see the file-tree correction in that document's "
            "'Target architecture' section)."
        ),
    )


@dataclass(frozen=True)
class KalmanVerdict:
    status: MethodStatus
    implemented_alternative: str
    reason: str


def evaluate_kalman_verdict() -> KalmanVerdict:
    return KalmanVerdict(
        status=MethodStatus.REJECTED_ALTERNATIVE_SHIPPED,
        implemented_alternative="anchored flow-conservation integrator (tonnage.stockflow.reconstruct)",
        reason=(
            "A textbook Kalman filter needs a real per-step observation of the "
            "latent state (free tonnage) to correct its estimate against; none "
            "exists in free data on disk (see survey_available_tonnage_evidence "
            "-- no AIS, no per-vessel idle/ballast feed). There is therefore "
            "nothing to run a Kalman correction step against, and none is "
            "implemented. tonnage.stockflow.reconstruct ships the honest "
            "alternative instead: a flow-conservation accumulator, periodically "
            "anchored to the real UNCTAD world-fleet total and distributed "
            "across basins by real observed activity share -- stockflow.py's own "
            "module docstring already names this choice explicitly ('the honest "
            "version of that idea... rather than dressing up a plain accumulator "
            "with Kalman-filter vocabulary it doesn't earn'). No Kalman-vs-"
            "no-Kalman comparison is reported because no Kalman variant exists "
            "to compare against -- there is no per-day observation on disk to "
            "build one from."
        ),
    )


# ---------------------------------------------------------------------------
# 4. Sign diagnosis -- P3 requirement 3, assembled from tonnage.supplycurve's
#    real diagnostic functions, run live.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SignDiagnosis:
    vessel_class: VesselClass
    fit: SupplyCurveFit
    first_difference: FirstDifferenceCheck
    regime_split: RegimeSplitCheck
    lead_lag: LeadLagCorrelation
    basin_aggregation: BasinAggregationCheck
    target_transform: TargetTransformCheck
    explanation: str


_SIGN_MATCH_THRESHOLD: Final[float] = 0.05


def _explain(cls: VesselClass, fit: SupplyCurveFit, fd: FirstDifferenceCheck, rs: RegimeSplitCheck,
             ba: BasinAggregationCheck, tt: TargetTransformCheck) -> str:
    """Plain-language synthesis of the real numbers above -- never a template
    filled from a desired conclusion. Every clause below is conditioned on an
    actual comparison of the computed values."""
    wrong_signed = fit.pearson_r < 0
    clauses: list[str] = []

    trend_confound = wrong_signed and fd.pearson_r_diff > fd.pearson_r_level + _SIGN_MATCH_THRESHOLD
    if trend_confound:
        clauses.append(
            f"first-differencing flips the sign (level r={fd.pearson_r_level:.3f} -> "
            f"diff r={fd.pearson_r_diff:.3f}), consistent with a shared time trend "
            "confounding the level correlation rather than a genuine inverse "
            "physical relationship"
        )
    elif abs(fd.pearson_r_diff - fd.pearson_r_level) > _SIGN_MATCH_THRESHOLD:
        clauses.append(
            f"first-differencing changes the correlation materially (level "
            f"r={fd.pearson_r_level:.3f} -> diff r={fd.pearson_r_diff:.3f}), "
            "indicating part of the level relationship is trend-driven rather "
            "than short-run structural"
        )

    regime_signs_differ = (rs.first_half_r == rs.first_half_r and rs.second_half_r == rs.second_half_r
                            and (rs.first_half_r > 0) != (rs.second_half_r > 0))
    if regime_signs_differ:
        clauses.append(
            f"the relationship's SIGN itself differs between the first half "
            f"(r={rs.first_half_r:.3f}, n={rs.first_half_n}) and second half "
            f"(r={rs.second_half_r:.3f}, n={rs.second_half_n}, split at "
            f"{rs.split_date}) of the sample -- pooling two regimes with "
            "opposite signs can produce a pooled correlation with either sign, "
            "unrelated to the true within-regime relationship"
        )
    elif abs(rs.first_half_r - rs.second_half_r) > 0.2:
        clauses.append(
            f"correlation strength is unstable across the sample (first half "
            f"r={rs.first_half_r:.3f}, second half r={rs.second_half_r:.3f}) -- "
            "regime instability, even without a sign flip"
        )

    if ba.best_basin_beats_pooled:
        strongest_basin = max(
            ((b, r) for b, r in ba.by_basin_r.items() if r == r), key=lambda kv: abs(kv[1])
        )
        clauses.append(
            f"pooling all basins washes out a stronger per-basin signal: "
            f"{strongest_basin[0]} alone correlates at r={strongest_basin[1]:.3f} "
            f"vs the pooled r={ba.pooled_r:.3f}"
        )

    if abs(tt.pearson_r_log - tt.pearson_r_raw) > _SIGN_MATCH_THRESHOLD:
        clauses.append(
            f"a log-rate transform changes the correlation (raw r="
            f"{tt.pearson_r_raw:.3f}, log r={tt.pearson_r_log:.3f})"
        )

    if not clauses:
        return (
            f"{cls.value}: level r={fit.pearson_r:.3f} (n={fit.n_obs}). None of the "
            "tested candidate explanations (trend confounding, regime "
            "instability, basin aggregation, target transform) materially "
            "changes the picture -- the level correlation appears to be a "
            "reasonably direct read of the real (tightness, rate) relationship "
            "in this sample, weak or wrong-signed as measured. No IV exists to "
            "test whether it is causal (see evaluate_iv_verdict); this remains "
            "associational."
        )

    prefix = (
        f"{cls.value}: level r={fit.pearson_r:.3f} (n={fit.n_obs}) is "
        f"{'wrong-signed' if wrong_signed else 'correctly signed but ' + ('weak' if fit.weak_signal else 'not weak')}. "
    )
    return prefix + "Real evidence found for: " + "; ".join(clauses) + ". Not causal in any case -- see evaluate_iv_verdict (no IV attempted, no defensible instrument)."


def diagnose_sign(vessel_class: VesselClass, fit: SupplyCurveFit, tightness_index: pl.DataFrame,
                   basin_tightness_index: pl.DataFrame) -> SignDiagnosis:
    fd = first_difference_diagnostic(vessel_class, tightness_index)
    rs = regime_split_diagnostic(vessel_class, tightness_index)
    ll = lead_lag_correlation(vessel_class, tightness_index)
    ba = basin_aggregation_diagnostic(vessel_class, tightness_index, basin_tightness_index)
    tt = target_transform_diagnostic(vessel_class, tightness_index)
    explanation = _explain(vessel_class, fit, fd, rs, ba, tt)
    return SignDiagnosis(
        vessel_class=vessel_class, fit=fit, first_difference=fd, regime_split=rs,
        lead_lag=ll, basin_aggregation=ba, target_transform=tt, explanation=explanation,
    )


def diagnose_all_signs(
    fits: dict[VesselClass, SupplyCurveFit], tightness_index: pl.DataFrame, basin_tightness_index: pl.DataFrame
) -> dict[VesselClass, SignDiagnosis]:
    out: dict[VesselClass, SignDiagnosis] = {}
    for cls, fit in fits.items():
        try:
            out[cls] = diagnose_sign(cls, fit, tightness_index, basin_tightness_index)
        except NoOverlapError:
            continue
    return out
