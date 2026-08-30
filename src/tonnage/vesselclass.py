"""Vessel class INFERENCE from observed dimensions -- P3 requirement 2.

``berth_truth.fact_port_call`` gives real, observed ``loa_m``/``beam_m``/
``arrival_draft_m`` per vessel call. Those three numbers correlate with vessel
class but are not exact vessel-particulars ground truth: two different classes'
real-world size distributions overlap (a large Handysize and a small Supramax
can share a similar LOA), and this repo's own reference dimensions
(``opt.fleetmix._CLASS_SPECS``) are representative single points, not the real
distribution. This module is named and typed to keep that distinction visible:

    OBSERVED_DIMENSIONS (loa_m, beam_m, arrival_draft_m)
            |  inference, with confidence
            v
    INFERRED_VESSEL_CLASS

Never ``vessel_class`` (ground truth); always ``inferred_class`` /
``vessel_class_inferred`` (a modelled read of the evidence). Where a source
supplies an IMO that can be joined to an external vessel record with actual
declared DWT/class, that record is ``DECLARED``, not ``INFERRED`` -- see
``count_declared_upgrades`` below, which reports the real count (0, in every
source currently ingested -- no parser in this codebase populates ``imo``).

**No labelled ground truth exists for the real ingested calls.** The Paradip
Section A report -- the only real source with volume today (1,224 calls,
``berth_truth/providers/pdf_report.py``) -- carries no IMO, no DWT column, no
external vessel-registry join. So "validate accuracy against true labels" is
not achievable with real data currently available, and this module does not
pretend otherwise: ``cross_consistency_check`` below validates the classifier
by comparing its output against an *independently derived* real signal
(``tonnage.classmix.port_class_weights``, built from cargo tonnage-per-call,
not vessel dimensions) for the same port and window. Agreement between two
independently derived real signals is evidence the classifier is not
producing noise; it is explicitly NOT the same claim as labelled accuracy, and
every report of this module's output says so.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import polars as pl

from berth_truth.fact_port_call import FactPortCall
from opt.fleetmix import _CLASS_SPECS
from opt.types import VesselClass
from tonnage.classmix import port_class_weights

__all__ = [
    "DIMENSION_KEYS",
    "CrossConsistencyResult",
    "PortClassMixAggregate",
    "VesselClassInference",
    "aggregate_inferred_class_mix",
    "classify_fact_port_calls",
    "classify_vessel",
    "count_declared_upgrades",
    "cross_consistency_check",
]

#: The observed dimensions used for inference, in the order compared. DWT is
#: deliberately excluded: `fact_port_call` never carries it (no parser extracts
#: a DWT column -- the Paradip source doesn't have one), so it is never an
#: observed input here, only ever a class-reference constant on the other side
#: of the comparison.
DIMENSION_KEYS: Final[tuple[str, ...]] = ("loa_m", "beam_m", "draft_m")

#: Tolerance applied when checking an observed dimension against a port's
#: declared max_loa_m/max_beam_m/max_draft_m (`plausible_at_port`). Not zero:
#: a declared port limit is itself an approximate operational figure, not a
#: laser-surveyed constant, and this codebase already has a documented real
#: example of a near-miss at this exact scale (`opt.fleetmix._CLASS_SPECS`'s
#: own comment: real Panamax beam 32.26m vs Paradip's declared max_beam_m
#: 32.2m, a 0.06m/0.2% "artificial near-miss from borrowing the wrong class's
#: reference beam rather than a genuine port limitation"). Measured live
#: against the real 1,224-row Paradip ingestion: of 718 rows exceeding at
#: least one declared limit under a strict >0 check, 581 (81%) are within 5%
#: of every limit they exceed -- essentially all of them clustered at a beam
#: excess with a 0.06m median, the same near-miss pattern. 5% is chosen as the
#: threshold that absorbs that documented, understood noise source without
#: absorbing the genuinely extreme cases (LOA up to 333m against a 225m
#: declared limit) a strict check also finds. Still a real, disclosed
#: judgement call, not a fitted parameter -- there is no labelled "genuinely
#: implausible" set to fit it against.
_PLAUSIBILITY_TOLERANCE: Final[float] = 0.05


@dataclass(frozen=True)
class VesselClassInference:
    """One inference. ``confidence`` is a real, computed separation measure
    (how much closer the observed dimensions sit to the winning class than to
    the runner-up), not a calibrated probability -- there is no labelled data
    to calibrate a probability against, and presenting one would overstate
    what this method can honestly claim."""

    inferred_class: VesselClass
    confidence: float
    distances: dict[VesselClass, float]  # mean relative deviation per class, >= 0

    @property
    def runner_up(self) -> VesselClass | None:
        others = sorted((c for c in self.distances if c != self.inferred_class), key=lambda c: self.distances[c])
        return others[0] if others else None


def _relative_distance(loa_m: float, beam_m: float, draft_m: float, cls: VesselClass) -> float:
    """Mean absolute relative deviation from `cls`'s reference dimensions --
    scale-free (LOA's ~200m range and draft's ~15m range would otherwise let LOA
    dominate a raw-unit distance), and directly interpretable ("on average, X%
    off this class's reference vessel")."""
    spec = _CLASS_SPECS[cls]
    observed = (loa_m, beam_m, draft_m)
    reference = (spec["loa_m"], spec["beam_m"], spec["draft_m"])
    deviations = [abs(o - r) / r for o, r in zip(observed, reference)]
    return sum(deviations) / len(deviations)


def classify_vessel(loa_m: float, beam_m: float, draft_m: float) -> VesselClassInference:
    """Infer a vessel class from observed dimensions alone.

    Nearest-neighbour against `opt.fleetmix._CLASS_SPECS`'s real, cited
    reference dimensions (the same reference vectors `opt.fleetmix` already
    uses for its representative-vessel construction -- reused, not
    reinvented) in scale-free relative-deviation space.
    """
    if loa_m <= 0 or beam_m <= 0 or draft_m <= 0:
        raise ValueError(f"Dimensions must be positive: loa_m={loa_m}, beam_m={beam_m}, draft_m={draft_m}")
    distances = {cls: _relative_distance(loa_m, beam_m, draft_m, cls) for cls in VesselClass}
    ranked = sorted(distances, key=lambda c: distances[c])
    best, second = ranked[0], ranked[1]
    best_d, second_d = distances[best], distances[second]
    # best <= second_d always (best is the argmin) -- confidence in [0.5, 1.0]:
    # 1.0 when the observed dims sit exactly on one class's reference and far
    # from every other; 0.5 when the two nearest classes are equidistant (the
    # least this method can honestly claim, having still picked one of them).
    confidence = 0.5 if (best_d + second_d) == 0 else second_d / (best_d + second_d)
    return VesselClassInference(inferred_class=best, confidence=confidence, distances=distances)


@dataclass(frozen=True)
class ClassifiedCall:
    """One `fact_port_call` row plus its inference -- identity fields carried
    through so a caller can join back to the source row.

    ``plausible_at_port``: the row's OWN observed dimensions checked against
    the CALLING PORT's own declared operational limits (``opt.network.PortEnum``
    -- ``max_loa_m``/``max_beam_m``/``max_draft_m``, the same real figures the
    optimizer's own feasibility checks use), with a real, disclosed tolerance
    -- see ``_PLAUSIBILITY_TOLERANCE``. Found live, not hypothetically: a
    strict (zero-tolerance) version of this check flags 718 of 962 real
    classified Paradip rows (74.6%), which sounds like a serious data problem
    until broken down -- 581 of those 718 (81%) exceed every limit they
    breach by under 5%, dominated by a ~0.06m median beam excess, the exact
    same near-miss already documented in ``opt.fleetmix._CLASS_SPECS`` (real
    Panamax beam 32.26m vs Paradip's declared 32.2m). The 5%-tolerant check
    this module actually applies reduces the implausible count to the
    genuinely extreme tail: real rows with LOA up to 333m and draft up to
    20.1m against Paradip's declared 225.0m/14.3m -- far beyond even the
    Capesize reference (292m/16.5m) and beyond Paradip's own berths in any
    reading of the tolerance. Paradip is a multi-cargo port; that tail is more
    plausibly a non-dry-bulk vessel sharing the same anchorage/traffic report,
    a manual daily-report transcription error, or a genuine outlier -- this
    module cannot distinguish which, so it flags rather than silently trusts.
    A ``False`` here means the observed input itself already contradicts what
    the calling port declares possible beyond a documented noise tolerance,
    independent of which class the dimensions are nearest to."""

    content_sha256: str
    row_index: int
    port_label: str
    inference: VesselClassInference
    plausible_at_port: bool


@dataclass(frozen=True)
class ClassificationBatchResult:
    """`classified` plus an honest accounting of what could not be classified
    and why -- mirrors the `(mix, skipped)` pattern `classmix.build_fleet_class_mix`
    already uses for the same kind of real-world-data gap."""

    classified: list[ClassifiedCall]
    n_input_rows: int
    n_missing_dimension: int  # loa_m/beam_m/arrival_draft_m is None
    n_nonpositive_dimension: int  # present but <= 0 -- not a usable physical value


def classify_fact_port_calls(rows: list[FactPortCall]) -> ClassificationBatchResult:
    """Classify every row that has all three real dimensions AND every
    dimension is physically usable (> 0).

    Two distinct real-data gaps, counted separately rather than folded into one
    silent skip: (1) the field is genuinely absent (``None``) -- P1 measured
    1,211 of 1,224 real Paradip rows (98.9%) have all three present; (2) the
    field is present but non-positive -- measured live, 249 of those 1,211
    (20.6%) have ``arrival_draft_m == 0.0`` (LOA/beam were real in every case
    checked; only draft was zero). ``pdf_report._to_float`` already returns
    ``None`` for a genuinely blank source cell, so a literal ``0.0`` reflects
    what the source PDF's draft column actually printed for that row -- most
    plausibly a not-yet-recorded-draft convention for a vessel still working
    cargo or awaiting berth allocation, not a parser defect. Either way, no
    real vessel berths at 0.0m draft, so this is not a usable observed
    dimension and is skipped rather than fed into the classifier, where it
    would silently pull every such row toward whichever class has the
    shallowest reference draft."""
    from opt.network import PORT_TO_TONNAGE_LABEL

    out: list[ClassifiedCall] = []
    n_missing = 0
    n_nonpositive = 0
    for row in rows:
        if row.loa_m is None or row.beam_m is None or row.arrival_draft_m is None:
            n_missing += 1
            continue
        if row.loa_m <= 0 or row.beam_m <= 0 or row.arrival_draft_m <= 0:
            n_nonpositive += 1
            continue
        inference = classify_vessel(row.loa_m, row.beam_m, row.arrival_draft_m)
        port_spec = row.port.value
        tol = 1.0 + _PLAUSIBILITY_TOLERANCE
        plausible = (
            row.loa_m <= port_spec.max_loa_m * tol
            and row.beam_m <= port_spec.max_beam_m * tol
            and row.arrival_draft_m <= port_spec.max_draft_m * tol
        )
        out.append(
            ClassifiedCall(
                content_sha256=row.content_sha256,
                row_index=row.row_index,
                port_label=PORT_TO_TONNAGE_LABEL.get(row.port, row.port.name),
                inference=inference,
                plausible_at_port=plausible,
            )
        )
    return ClassificationBatchResult(
        classified=out, n_input_rows=len(rows), n_missing_dimension=n_missing, n_nonpositive_dimension=n_nonpositive,
    )


def count_declared_upgrades(rows: list[FactPortCall]) -> int:
    """How many real rows carry an IMO that could upgrade INFERRED to DECLARED.

    Real, computed count -- not asserted. Currently 0 for every row any parser
    in this codebase has ever produced: no ingested source (Paradip Section A,
    the only real-volume source today) carries an IMO column, so there is
    nothing to join to an external vessel record. Kept as a real function
    (not a hardcoded 0) so this number updates itself the moment a source that
    does carry IMO is ingested."""
    return sum(1 for r in rows if r.imo is not None and r.imo.strip() != "")


# ---------------------------------------------------------------------------
# Cross-consistency validation against tonnage.classmix's independent signal
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PortClassMixAggregate:
    port_label: str
    n_calls: int
    n_excluded_implausible: int
    share_by_class: dict[VesselClass, float]  # sums to 1.0
    mean_confidence: float


def aggregate_inferred_class_mix(classified: list[ClassifiedCall], port_label: str) -> PortClassMixAggregate:
    """Per-vessel inferences -> a port-level class-share distribution, the same
    shape `tonnage.classmix.port_class_weights` returns, so the two are
    directly comparable.

    Rows flagged `plausible_at_port=False` (observed dimensions exceed the
    calling port's own declared limits -- see `ClassifiedCall`) are excluded
    from the aggregate, not silently averaged in: they would otherwise pull a
    port's class-mix toward classes those vessels almost certainly are not,
    given they could not physically have berthed there as that class."""
    calls = [c for c in classified if c.port_label == port_label]
    if not calls:
        raise ValueError(f"No classified calls for port_label={port_label!r}.")
    n_excluded = sum(1 for c in calls if not c.plausible_at_port)
    calls = [c for c in calls if c.plausible_at_port]
    if not calls:
        raise ValueError(f"No PLAUSIBLE classified calls for port_label={port_label!r}.")
    counts: dict[VesselClass, int] = {cls: 0 for cls in VesselClass}
    for c in calls:
        counts[c.inference.inferred_class] += 1
    n = len(calls)
    share = {cls: count / n for cls, count in counts.items()}
    mean_conf = sum(c.inference.confidence for c in calls) / n
    return PortClassMixAggregate(
        port_label=port_label, n_calls=n, n_excluded_implausible=n_excluded,
        share_by_class=share, mean_confidence=mean_conf,
    )


@dataclass(frozen=True)
class CrossConsistencyResult:
    """**Not a labelled-accuracy measurement** -- no external vessel-registry
    ground truth exists for the real ingested calls (see module docstring).
    This compares two INDEPENDENTLY derived real estimates of the same port's
    class mix: this module's per-vessel dimension-based inference, aggregated,
    against `tonnage.classmix.port_class_weights`'s cargo-tonnage-based
    estimate. Close agreement is evidence the two real, unrelated signals are
    not contradicting each other; it is not proof either is individually
    correct against a true label, because no true label exists to check
    against."""

    port_label: str
    n_calls: int
    dimension_based_share: dict[VesselClass, float]
    tonnage_based_share: dict[VesselClass, float]
    per_class_abs_delta: dict[VesselClass, float]
    total_variation_distance: float  # 0 = identical distributions, 1 = disjoint


def cross_consistency_check(classified: list[ClassifiedCall], port_label: str) -> CrossConsistencyResult:
    dim_agg = aggregate_inferred_class_mix(classified, port_label)
    tonnage_weights = port_class_weights(port_label)
    per_class_delta = {
        cls: abs(dim_agg.share_by_class[cls] - tonnage_weights[cls]) for cls in VesselClass
    }
    tvd = sum(per_class_delta.values()) / 2.0
    return CrossConsistencyResult(
        port_label=port_label,
        n_calls=dim_agg.n_calls,
        dimension_based_share=dim_agg.share_by_class,
        tonnage_based_share=tonnage_weights,
        per_class_abs_delta=per_class_delta,
        total_variation_distance=tvd,
    )


def classified_to_frame(classified: list[ClassifiedCall]) -> pl.DataFrame:
    """Flatten to a DataFrame for API/frontend serving and for extrapolation
    labelling: `port_label` here is always a port `classify_fact_port_calls`
    actually saw real rows for -- any OTHER port has no per-vessel inference
    coverage at all, which callers must treat as extrapolation, not silently
    assume is covered."""
    if not classified:
        return pl.DataFrame(
            schema={
                "content_sha256": pl.Utf8, "row_index": pl.Int64, "port_label": pl.Utf8,
                "inferred_class": pl.Utf8, "confidence": pl.Float64, "plausible_at_port": pl.Boolean,
            }
        )
    return pl.DataFrame(
        {
            "content_sha256": [c.content_sha256 for c in classified],
            "row_index": [c.row_index for c in classified],
            "port_label": [c.port_label for c in classified],
            "inferred_class": [c.inference.inferred_class.value for c in classified],
            "confidence": [c.inference.confidence for c in classified],
            "plausible_at_port": [c.plausible_at_port for c in classified],
        }
    )
