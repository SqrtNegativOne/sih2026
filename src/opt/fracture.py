"""The Fracture Index: one 0-100 score per chokepoint, fusing a lagging
traffic signal, a leading news signal, and two static declared facts.

Why fuse at all
---------------
``opt.risk.chokepoint_disruption_alert`` already fires when a chokepoint's
real daily transit count *drops* anomalously -- but by then the disruption
has happened. ``data_builders.harvest_gdelt`` gives a leading signal: news
coverage of conflict events geolocated at the chokepoint, which typically
moves before traffic does. Neither is sufficient alone. Traffic can fall for
reasons that are not risk (a demand slump, a holiday), and coverage can spike
for reasons that never touch shipping. Combining them, with two independent
declared facts alongside, gives a score that is harder to fool than any of
its parts.

The four inputs
---------------
1. ``transit_z`` -- z-score of the chokepoint's latest real daily dry-bulk
   transit count against its own trailing baseline, computed with the exact
   same helper and window ``opt.risk.chokepoint_disruption_alert`` uses
   (``opt.risk._zscore_of_last``, ``CHOKEPOINT_WINDOW_DAYS``). **Negative is
   risk** -- a drop signals blockage; a spike does not, and is scored zero,
   matching that function's own convention rather than inventing a second one.
   Provenance of the underlying counts: OBSERVED (IMF PortWatch).
2. ``conflict_z`` -- z-score of the chokepoint's latest real weekly
   conflict-coded GDELT event count against **its own** trailing history.
   **Positive is risk.** Provenance of the underlying counts: OBSERVED, but
   of *media coverage*, not of events -- see ``data_builders.harvest_gdelt``.
3. ``jwc_listed`` -- whether the chokepoint sits inside a Joint War Committee
   Listed Area (``opt.war_risk.LISTED_AREAS``). Provenance: DECLARED.
4. ``draft_restricted`` -- whether a transcribed canal-authority draft
   advisory is in force at this chokepoint on this date
   (``raw_data/panama/draft_advisories.csv``; Panama only today).
   Provenance: DECLARED.

**Never compared across chokepoints.** Both z-scores measure a chokepoint
against its own past, never against another chokepoint. This is not a
stylistic preference -- it is required. Verified directly on the real
12-month GDELT harvest: raw event counts rank the Malacca Strait (6,169) and
the Cape of Good Hope (4,014) far above Bab el-Mandeb (186), because large
cities sit inside those circles. Raw counts measure media-market size at
least as much as maritime risk. Within one chokepoint against its own
history the same series is genuinely informative.

Composition rules
-----------------
Each available input produces a 0-100 sub-score. The index is the
**weight-normalised mean of the sub-scores that were actually available**:

    index = sum(w_i * sub_i for i in available) / sum(w_i for i in available)

Missing inputs are **dropped, never zero-filled.** Zero-filling a missing
signal would quietly assert "this chokepoint is calm on that dimension",
which is a different and much stronger claim than "we could not measure it".
Dropping and renormalising says only what the available evidence supports.
``inputs_available`` names exactly which inputs fed the number, so a reader
can never mistake a one-signal score for a four-signal one.

**The band cap.** A fracture computed without *both* measured signals
(``transit_z`` and ``conflict_z``) is capped at ``"watch"``, however high its
index. You cannot responsibly call a chokepoint "critical" on one measured
signal plus a static listing -- an area's presence on a war-risk list is a
standing fact about the region, not evidence that something is happening
there this week. ``BAND_CAP_WHEN_INCOMPLETE`` and
``_REQUIRED_FOR_UNCAPPED_BAND`` implement this explicitly rather than leaving
it to a reader to notice.

``draft_restricted`` is not in that required set on purpose: it is
structurally inapplicable to 27 of the 28 chokepoints (no canal authority, no
advisory table), so requiring it would cap every non-Panama chokepoint
forever. It is dropped as an input wherever no advisory table covers the
chokepoint, and also wherever the table is stale for the requested date --
see ``_draft_restriction``.

Provenance
----------
The raw inputs are OBSERVED (transit and event counts) or DECLARED (war-risk
listing, draft advisory). The index and band are **MODEL_DERIVED** -- a
computation over real inputs, inheriting none of their labels. See
``data_builders.provenance``.
"""
from __future__ import annotations

import csv
import logging
from datetime import date
from pathlib import Path
from typing import Final, Literal

import numpy as np
import polars as pl
from pydantic import BaseModel, ConfigDict

from opt.chokepoints import CHOKEPOINT_GEOMETRY, CHOKEPOINT_NAMES, chokepoints_for_route
from opt.network import PortEnum
from opt.risk import CHOKEPOINT_DIR, CHOKEPOINT_WINDOW_DAYS, _zscore_of_last
from opt.war_risk import LISTED_AREAS

__all__ = [
    "BAND_CAP_WHEN_INCOMPLETE",
    "BAND_THRESHOLDS",
    "FRACTURE_WEIGHTS",
    "Band",
    "ChokepointFracture",
    "chokepoint_fracture",
    "jwc_listed_chokepoints",
    "route_fracture",
]

LOGGER: Final[logging.Logger] = logging.getLogger(__name__)

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
GDELT_WEEKLY_PATH: Final[Path] = REPO_ROOT / "raw_data" / "gdelt" / "chokepoint_conflict_weekly.csv"
PANAMA_ADVISORIES_PATH: Final[Path] = REPO_ROOT / "raw_data" / "panama" / "draft_advisories.csv"

Band = Literal["calm", "watch", "elevated", "critical"]

#: Weights over the four inputs, summing to exactly 1.0. Renormalised over
#: whichever subset is actually available (see the module docstring).
#:
#: The ordering reflects how directly each input evidences a *shipping*
#: disruption. A real transit drop is the most direct evidence there is --
#: it is the thing being predicted. Conflict coverage leads it but is noisier
#: and measures media attention as much as ground truth, so it is weighted
#: below it. A war-risk listing is authoritative but standing and slow-moving
#: (the committee amends on no fixed schedule), so it informs the level
#: rather than the moment. A draft advisory is a real, hard constraint but
#: narrow -- it restricts how deep a vessel may load, it does not close a
#: strait -- so it carries the least weight of the four.
FRACTURE_WEIGHTS: Final[dict[str, float]] = {
    "transit_z": 0.40,
    "conflict_z": 0.30,
    "jwc_listed": 0.20,
    "draft_restricted": 0.10,
}

#: The z-score at which a continuous input saturates its 0-100 sub-score.
#: 3.0 sigma against a signal's own baseline is a genuinely extreme move;
#: beyond it, "more extreme" adds no useful discrimination for this purpose.
#: Chosen to sit above opt.risk's own alert threshold (1.75), so a chokepoint
#: that has merely tripped the existing alert scores around 58/100 on that
#: input rather than immediately maxing it out.
Z_SATURATION: Final[float] = 3.0

#: Trailing window (ISO weeks) the conflict-intensity z-score is computed
#: over. The committed GDELT harvest is ~53 weeks deep, so 12 weeks is a real
#: baseline that does not consume most of the available history at once.
GDELT_Z_WINDOW_WEEKS: Final[int] = 12

#: Index -> band. A documented constant, not inline magic numbers. Checked
#: against the weights above: with all four inputs at their maximum the index
#: is exactly 100, so every band below is genuinely reachable.
BAND_THRESHOLDS: Final[tuple[tuple[float, Band], ...]] = (
    (75.0, "critical"),
    (50.0, "elevated"),
    (25.0, "watch"),
)

#: Both measured signals must be present for a band above this cap. See the
#: module docstring's "The band cap".
_REQUIRED_FOR_UNCAPPED_BAND: Final[frozenset[str]] = frozenset({"transit_z", "conflict_z"})
BAND_CAP_WHEN_INCOMPLETE: Final[Band] = "watch"

_BAND_ORDER: Final[dict[Band, int]] = {"calm": 0, "watch": 1, "elevated": 2, "critical": 3}


class ChokepointFracture(BaseModel):
    """One chokepoint's fused disruption score. ``index`` is 0-100, higher is
    more fractured; ``inputs_available`` says which of the four inputs
    actually fed it."""

    model_config = ConfigDict(frozen=True)

    chokepoint_id: str
    chokepoint_name: str
    transit_z: float | None
    conflict_z: float | None
    draft_restricted: bool
    jwc_listed: bool
    index: float
    band: Band
    inputs_available: tuple[str, ...]
    explanation: str


def jwc_listed_chokepoints() -> frozenset[str]:
    """Chokepoint ids whose real centroid falls inside a Joint War Committee
    Listed Area envelope (``opt.war_risk.LISTED_AREAS``).

    Derived from the two real tables rather than maintained as a third,
    hand-kept list that could silently drift out of step with either -- edit
    a chokepoint's coordinates or a listed area's bounds and this follows
    automatically.

    A centroid test, and therefore conservative by construction: a chokepoint
    whose circle overlaps a listed area but whose centre sits outside it is
    reported unlisted. That is the safe direction for a *chokepoint* flag,
    because the route-level question ("does my voyage enter a listed area?")
    is answered separately and properly by
    ``opt.war_risk.listed_areas_on_route``, which tests the whole polyline.
    """
    return frozenset(
        cp_id
        for cp_id, geom in CHOKEPOINT_GEOMETRY.items()
        if any(area.contains(geom.lon, geom.lat) for area in LISTED_AREAS)
    )


def _transit_z(chokepoint_id: str, as_of: date, chokepoint_dir: Path) -> float | None:
    """The same real PortWatch transit-count z-score
    ``opt.risk.chokepoint_disruption_alert`` computes -- the raw value here,
    not just whether it crossed that function's alert threshold. ``None``
    when there is no file for this chokepoint or not enough real history to
    judge."""
    path = chokepoint_dir / f"{chokepoint_id}_daily_transits.csv"
    if not path.exists():
        return None

    dates: list[str] = []
    counts: list[float] = []
    try:
        with path.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                dates.append(row["date"])
                counts.append(float(row["n_dry_bulk"]))
    except (KeyError, ValueError, OSError, TypeError):
        LOGGER.warning("Unreadable transit file for %s; treating as no signal.", chokepoint_id, exc_info=True)
        return None

    order = sorted(range(len(dates)), key=lambda i: dates[i])
    as_of_str = as_of.isoformat()
    trimmed = [counts[i] for i in order if dates[i] <= as_of_str]

    result = _zscore_of_last(np.array(trimmed), CHOKEPOINT_WINDOW_DAYS)
    return result[1] if result is not None else None


def _conflict_z(chokepoint_id: str, as_of: date, gdelt_path: Path) -> float | None:
    """z-score of this chokepoint's most recent real weekly GDELT
    conflict-event count against ITS OWN trailing history (never against
    another chokepoint's -- see the module docstring). ``None`` when the
    harvest file is missing or this chokepoint has too little history."""
    if not gdelt_path.exists():
        return None
    try:
        frame = pl.read_csv(gdelt_path)
    # A malformed harvest must degrade to "no signal", never take a quote down.
    except Exception:
        LOGGER.warning("Unreadable GDELT harvest at %s; treating as no signal.", gdelt_path, exc_info=True)
        return None

    iso = as_of.isocalendar()
    subset = (
        frame.filter(pl.col("chokepoint_id") == chokepoint_id)
        .filter(
            (pl.col("iso_year") < iso.year)
            | ((pl.col("iso_year") == iso.year) & (pl.col("iso_week") <= iso.week))
        )
        .sort(["iso_year", "iso_week"])
    )
    if subset.is_empty():
        return None
    counts = subset["event_count"].to_numpy().astype(float)
    result = _zscore_of_last(counts, GDELT_Z_WINDOW_WEEKS)
    return result[1] if result is not None else None


def _draft_restriction(chokepoint_id: str, as_of: date, advisories_path: Path) -> bool | None:
    """Whether a transcribed canal-authority draft advisory is in force here.

    ``None`` means **unknown, so drop this input** -- returned when no
    advisory row covers this chokepoint at all (27 of 28 today), or when
    ``as_of`` falls after the table's own ``last_reviewed`` date. That second
    case matters: a hand-transcribed table going stale must degrade to "we
    don't know", never to a confident ``False`` that would read as "the canal
    authority has confirmed no restriction".

    ``False`` is returned only where a row genuinely covers the date and no
    restriction was in force. Provenance: DECLARED (see the CSV's own note
    column and this module's docstring).
    """
    if not advisories_path.exists():
        return None
    try:
        with advisories_path.open(newline="", encoding="utf-8") as f:
            rows = [r for r in csv.DictReader(f) if r["chokepoint_id"] == chokepoint_id]
    except (KeyError, OSError, TypeError):
        LOGGER.warning("Unreadable draft advisory table at %s.", advisories_path, exc_info=True)
        return None
    if not rows:
        return None

    covered = False
    for row in rows:
        last_reviewed = date.fromisoformat(row["last_reviewed"])
        if as_of > last_reviewed:
            continue  # table is stale for this date -- see docstring
        covered = True
        effective_from = date.fromisoformat(row["effective_from"])
        effective_to = date.fromisoformat(row["effective_to"]) if row["effective_to"] else last_reviewed
        if effective_from <= as_of <= effective_to and float(row["max_draft_ft"]) < float(row["normal_max_draft_ft"]):
            return True
    return False if covered else None


def _sub_score_from_z(z: float, *, risk_direction: float) -> float:
    """0-100 sub-score from a z-score. ``risk_direction`` is -1.0 when a
    *drop* is the risk (transits) and +1.0 when a *rise* is (conflict); a
    move in the benign direction scores 0, never a negative contribution."""
    return 100.0 * max(0.0, min(1.0, (z * risk_direction) / Z_SATURATION))


def _band_for_index(index: float) -> Band:
    for threshold, band in BAND_THRESHOLDS:
        if index >= threshold:
            return band
    return "calm"


def chokepoint_fracture(
    chokepoint_id: str,
    as_of: date,
    *,
    gdelt_path: Path | None = None,
    advisories_path: Path | None = None,
    chokepoint_dir: Path | None = None,
) -> ChokepointFracture:
    """Fuse every available signal for one chokepoint into a 0-100 index.

    Never raises for missing data: a chokepoint with nothing on disk returns
    a real object with a low index, ``band="calm"``, and an explanation
    saying plainly that little was measurable -- which is not the same claim
    as "this chokepoint is calm", and the explanation says so.

    The three path parameters are test-injection only, defaulting to the real
    artifacts on disk -- the same convention ``opt.landed_cost`` and
    ``berth_truth.empirical`` already use.
    """
    if chokepoint_id not in CHOKEPOINT_NAMES:
        raise KeyError(f"Unknown chokepoint id {chokepoint_id!r}. Valid ids: {sorted(CHOKEPOINT_NAMES)}")

    transit = _transit_z(chokepoint_id, as_of, chokepoint_dir or CHOKEPOINT_DIR)
    conflict = _conflict_z(chokepoint_id, as_of, gdelt_path or GDELT_WEEKLY_PATH)
    draft = _draft_restriction(chokepoint_id, as_of, advisories_path or PANAMA_ADVISORIES_PATH)
    jwc = chokepoint_id in jwc_listed_chokepoints()

    # Build only the inputs that were genuinely available. Nothing is
    # zero-filled -- see the module docstring. jwc_listed is always available:
    # the listed-area table is committed and complete, so "not listed" is a
    # real observation rather than a missing measurement.
    sub_scores: dict[str, float] = {"jwc_listed": 100.0 if jwc else 0.0}
    if transit is not None:
        sub_scores["transit_z"] = _sub_score_from_z(transit, risk_direction=-1.0)
    if conflict is not None:
        sub_scores["conflict_z"] = _sub_score_from_z(conflict, risk_direction=+1.0)
    if draft is not None:
        sub_scores["draft_restricted"] = 100.0 if draft else 0.0

    weight_total = sum(FRACTURE_WEIGHTS[k] for k in sub_scores)
    index = sum(FRACTURE_WEIGHTS[k] * v for k, v in sub_scores.items()) / weight_total
    inputs_available = tuple(k for k in FRACTURE_WEIGHTS if k in sub_scores)

    band = _band_for_index(index)
    capped = not _REQUIRED_FOR_UNCAPPED_BAND.issubset(sub_scores)
    if capped and _BAND_ORDER[band] > _BAND_ORDER[BAND_CAP_WHEN_INCOMPLETE]:
        band = BAND_CAP_WHEN_INCOMPLETE

    name = CHOKEPOINT_NAMES[chokepoint_id]
    parts = [f"{name}: index {index:.1f}/100 ({band})"]
    parts.append(
        f"transit z={transit:+.2f} vs its own {CHOKEPOINT_WINDOW_DAYS}-day baseline"
        if transit is not None
        else "no transit signal on disk"
    )
    parts.append(
        f"conflict coverage z={conflict:+.2f} vs its own {GDELT_Z_WINDOW_WEEKS}-week history"
        if conflict is not None
        else "no conflict signal on disk"
    )
    if jwc:
        parts.append("inside a Joint War Committee Listed Area")
    if draft is True:
        parts.append("a canal-authority draft advisory is in force")
    explanation = "; ".join(parts) + "."
    if capped:
        explanation += (
            f" Band capped at '{BAND_CAP_WHEN_INCOMPLETE}': computed from "
            f"{len(inputs_available)} of {len(FRACTURE_WEIGHTS)} inputs "
            f"({', '.join(inputs_available)}), which is not enough to justify a higher one."
        )
    return ChokepointFracture(
        chokepoint_id=chokepoint_id,
        chokepoint_name=name,
        transit_z=transit,
        conflict_z=conflict,
        draft_restricted=bool(draft),
        jwc_listed=jwc,
        index=index,
        band=band,
        inputs_available=inputs_available,
        explanation=explanation,
    )


def route_fracture(
    origin: PortEnum,
    dest: PortEnum,
    as_of: date,
    *,
    gdelt_path: Path | None = None,
    advisories_path: Path | None = None,
    chokepoint_dir: Path | None = None,
) -> tuple[ChokepointFracture, ...]:
    """A ``ChokepointFracture`` for every chokepoint the real route between
    these two ports actually crosses, in the order the route meets them
    (``opt.chokepoints.chokepoints_for_route``). Returns ``()`` for a route
    that transits no monitored chokepoint -- a real answer, not a gap."""
    return tuple(
        chokepoint_fracture(
            cp_id,
            as_of,
            gdelt_path=gdelt_path,
            advisories_path=advisories_path,
            chokepoint_dir=chokepoint_dir,
        )
        for cp_id in chokepoints_for_route(origin, dest)
    )
