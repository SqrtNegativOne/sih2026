"""Data model for the vessel-schedule snapshot archiver (BT-0).

A ``ScheduleSnapshot`` is what one fetch of one port's live vessel-schedule
page produced: the four tables the page always carries (at berth, at
anchorage, expected, sailed in last 24 hours), each of which may be present
with rows, present with zero rows, or absent from the page entirely -- three
distinct states, not two. Confirmed live on 2026-08-27: Gangavaram's "Vessels
at Anchorage" table was present with zero rows (an empty ``<tbody>``), which
is a different fact from the table not existing on the page at all.

Every row is tagged ``evidence_class="SNAPSHOT_TRANSITION"``. These rows carry
no vessel dimensions (no LOA, no beam, no draft, no DWT -- confirmed absent
from the real markup) and must never be scored at the same confidence as a
parsed daily traffic report (Paradip-style). They are a state transition with
a timestamp, nothing more.

Berth identity is intentionally *not* normalised here. Dhamra's live schedule
uses berth identifiers a static constraint document does not: BB5, BB2E,
BB3B, BB3N, BB4N, BRGB, LNGT, DHS1, alongside the six identifiers that do
have a published constraint (BB1, BB2, BB3, BB3A, BB4, plus the barge/LNG
berths). This module has no opinion on which are which -- it records what the
page said. Reconciling observed identifiers against published constraints is
BT-1's job, not this one's.
"""
from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, computed_field

PARSER_VERSION = "adani_schedule/1"


class PortId(str, Enum):
    """Every port berth_truth has data for, from any source -- the shared join
    key across the constraint register (registry.py), draft declarations
    (declarations.py) and archived live schedules (this module). Not
    opt.network.PortEnum -- that enum names ports the optimizer routes to;
    this names ports berth_truth itself has evidence for, which is a
    different and currently smaller set.

    Not every member has a live schedule: archiver.SOURCE_URLS covers only
    DHAMRA and GANGAVARAM (Adani publishes a vessel-schedule page for each);
    VISAKHAPATNAM has a constraint document but no such page was found in
    that shape, so it exists here for the register only. archiver.py reads
    its port list from SOURCE_URLS.keys(), not from this enum, specifically
    so adding a register-only port here never gives the CLI a port it can't
    actually fetch.
    """

    DHAMRA = "DHAMRA"
    GANGAVARAM = "GANGAVARAM"
    VISAKHAPATNAM = "VISAKHAPATNAM"


class TableKind(str, Enum):
    """The four tables every Adani vessel-schedule page publishes, keyed by
    their real on-page heading text (case-insensitive, whitespace-trimmed) --
    see parsers.adani_schedule.HEADING_TO_TABLE_KIND for the exact mapping."""

    AT_BERTH = "AT_BERTH"
    AT_ANCHORAGE = "AT_ANCHORAGE"
    EXPECTED = "EXPECTED"
    SAILED_24H = "SAILED_24H"


class QuarantinedField(BaseModel):
    """One value that could not be parsed and was not guessed at.

    Scoped to a single field on a single row, not the whole row: a timestamp
    that fails to parse should not discard a perfectly good vessel name and
    berth number sitting in the same row.
    """

    model_config = ConfigDict(frozen=True)

    table_kind: TableKind
    row_index: int
    field_name: str
    raw_value: str
    reason: str


class ScheduleRow(BaseModel):
    """One row from one table on one snapshot.

    Every field is nullable because the four tables do not share a schema --
    the berth table has no ATA, the sailed table has no berth number -- and
    because a vacant-berth row (real, observed at every Dhamra and
    Gangavaram snapshot so far) has almost nothing in it but a berth number.
    """

    model_config = ConfigDict(frozen=True)

    table_kind: TableKind
    berth_no: str | None = None
    sbu_name: str | None = None
    vessel_name: str | None = None
    is_vacant: bool = False
    imp_exp: str | None = None
    cargo_raw: str | None = None

    etc_ts: datetime | None = None  # AT_BERTH: Expected Time of Completion
    ata_ts: datetime | None = None  # AT_ANCHORAGE: Actual Time of Arrival
    eta_ts: datetime | None = None  # EXPECTED: Estimated Time of Arrival
    pob_ts: datetime | None = None  # SAILED_24H: Pilot on Board
    pd_ts: datetime | None = None  # SAILED_24H: Pilot Disembark
    atub_ts: datetime | None = None  # SAILED_24H: Actual Time of Unberthing

    evidence_class: Literal["SNAPSHOT_TRANSITION"] = "SNAPSHOT_TRANSITION"


class ScheduleSnapshot(BaseModel):
    """Everything one fetch of one port's schedule page produced.

    ``content_sha256`` is the identity of the *content*, not of this fetch --
    see store.write_snapshot for how a byte-identical re-fetch is recorded as
    a re-observation of this same snapshot rather than a new one.
    """

    model_config = ConfigDict(frozen=True)

    port_id: PortId
    fetched_at: datetime
    source_url: str
    content_sha256: str
    tables_present: frozenset[TableKind]
    tables_empty: frozenset[TableKind]
    rows: tuple[ScheduleRow, ...]
    quarantined_fields: tuple[QuarantinedField, ...] = ()
    parser_version: str = PARSER_VERSION

    @computed_field  # type: ignore[prop-decorator]
    @property
    def observed_berth_ids(self) -> frozenset[str]:
        """Every distinct berth_no seen in this snapshot's AT_BERTH rows,
        including vacant ones -- vacant rows are exactly how BB2E, BB3B,
        BB3N, BB4N, BRGB and LNGT were confirmed to exist operationally
        despite carrying no published constraint."""
        return frozenset(
            row.berth_no
            for row in self.rows
            if row.table_kind is TableKind.AT_BERTH and row.berth_no is not None
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def parse_confidence(self) -> float:
        """1.0 minus a penalty for missing tables and quarantined fields.

        base = (tables found) / 4 -- a table absent from the page (not
          merely empty) is the one thing that should move this number, since
          an empty table is a legitimate, fully-parsed observation.
        penalty = 0.05 per quarantined field, capped at base so the score
          never goes negative.
        Deterministic and re-derivable from the stored rows; not a subjective
        rating.
        """
        base = len(self.tables_present) / len(TableKind)
        penalty = min(base, 0.05 * len(self.quarantined_fields))
        return round(base - penalty, 4)


# ---------------------------------------------------------------------------
# BT-1 -- constraint register, resolver, draft declarations.
#
# A BerthConstraint is one berth's published limits as of one document. It is
# deliberately NOT the same shape as opt.network.Port: that flat, one-row-
# per-port record is what BT-1 exists to replace for the ports it covers.
# Limits here are per berth, routed by commodity, gated by tide, and carry
# their own citation and effective-date window -- verified necessary against
# real documents: Dhamra's own BPTS states its permissible draft is
# "promulgated on monthly basis", and Gangavaram's BPTS publishes designed
# depth and permissible draft as two different numbers for the same berth
# (berth 4: 19.5m designed, 17.7m permissible) that a single max_draft_m
# field cannot distinguish.
# ---------------------------------------------------------------------------


class LimitStatus(str, Enum):
    """How trustworthy a BerthConstraint's populated fields are, as of its
    own effective_from/effective_to window -- not a statement about whether
    the berth exists (see is_published_constraint_berth /
    is_observed_operational_berth for that)."""

    PUBLISHED = "PUBLISHED"
    """Cited to a current, in-force document."""

    NOT_PUBLISHED = "NOT_PUBLISHED"
    """No document states this. Fields are null, not estimated."""

    ASSUMED = "ASSUMED"
    """A value inferred rather than directly stated by a source -- e.g. a
    beam figure standing in only because no port-specific one exists.
    Reserved for a future case; nothing seeded in this task uses it, because
    every value seeded here is either cited or left null."""

    SUPERSEDED = "SUPERSEDED"
    """Was PUBLISHED once; a newer document has taken over, or (Dhamra
    BB4's neighbours in DPC/06, Vizag's 2021 table) known or suspected to be
    stale with no replacement document located yet. Retained, never deleted
    -- resolving at a past as_of date should still return what was actually
    in force then."""


class PromulgationCycle(str, Enum):
    """How often the *document* backing a constraint is reissued. Nullable
    on BerthConstraint: a berth with no published limit at all has no
    promulgation cycle to describe."""

    STATIC = "STATIC"
    """Fixed until the next full document revision (Gangavaram, Vizag's berth
    table itself; Dhamra's LOA/displacement/function)."""

    DAILY = "DAILY"
    """Reissued every day -- Dhamra's Monthly Draft Declaration, which is a
    date-indexed daily series bundled into one monthly file."""

    MONTHLY = "MONTHLY"
    """Reissued on a monthly cadence. Distinct from DAILY: a MONTHLY document
    states one value covering the whole month, not one row per day."""

    ON_REVISION = "ON_REVISION"
    """Changes only when the port issues a new edition, with no fixed
    schedule -- Dhamra and Gangavaram's BPTS documents themselves."""


class DraftSource(str, Enum):
    """Where a berth's draft, specifically, comes from -- kept separate from
    the berth's other limits (LOA, displacement, ...) because Dhamra's own
    BPTS carries LOA/displacement but explicitly omits draft, which lives in
    a completely separate document scoped to only two of its seven published
    berths."""

    BPTS_STATIC = "BPTS_STATIC"
    """Draft is stated directly in the same static berthing-policy document
    as the berth's other limits (Gangavaram, Vizag)."""

    DAILY_DECLARATION = "DAILY_DECLARATION"
    """Draft comes from a separate, date-indexed declaration series (Dhamra
    BB1/BB2 only -- see declarations.py). resolve_constraint calls
    declarations.resolve_draft for a berth in this state; the static
    permissible_draft_m field on the BerthConstraint itself stays null."""

    MARINE_CIRCULAR = "MARINE_CIRCULAR"
    """Draft comes from a one-off marine circular rather than a recurring
    series. Reserved for a future source; nothing seeded in this task uses
    it, since no such circular was obtained as a primary document this
    session (see registry.py's Vizag notes for what was tried)."""

    NONE = "NONE"
    """No draft source exists for this berth at all -- neither a static
    figure nor a declaration series. Dhamra's BB3, BB3A, BB4, the barge berth
    and the LNG berth are all in this state: their LOA and displacement are
    published, their draft is not, by anyone, anywhere located."""


class DraftStatus(str, Enum):
    """The result of resolving a berth's draft for one specific date --
    always present on a DraftResolution, never left implicit."""

    DECLARED = "DECLARED"
    """A declaration exists whose date matches as_of exactly, from a document
    whose own internal date falls in the same calendar month as as_of."""

    STALE_OR_UNAVAILABLE = "STALE_OR_UNAVAILABLE"
    """A declaration series covers this berth, but no row satisfies the
    strict validity rule for as_of -- most commonly because the only
    document available is for a different month or year entirely."""

    NOT_APPLICABLE = "NOT_APPLICABLE"
    """This berth's draft_source is not DAILY_DECLARATION, so no declaration
    lookup was attempted. Its permissible_draft_m (if any) comes straight
    from the static register entry instead."""


class CommodityClass(str, Enum):
    """Commodity-based berth priority, only where a source states one.
    A berth with no commodity_class is not "any commodity" by assumption --
    it means the source didn't assign one; resolver.py treats that as
    generally usable, but that's a resolution-time judgement, not a claim
    this enum makes."""

    COAL_COKE = "COAL_COKE"
    IRON_ORE_FINES_PELLETS = "IRON_ORE_FINES_PELLETS"
    CONTAINER = "CONTAINER"


class BerthConstraint(BaseModel):
    """One berth's published limits, as of one document.

    ``is_published_constraint_berth`` and ``is_observed_operational_berth``
    are independent booleans -- Gangavaram's berths are both; Dhamra's BB5
    (a real bulk berth, confirmed operating, absent from its BPTS) is
    observed-only; a berth named in a BPTS but never yet seen in a live
    schedule (none seeded here, but the shape allows it) would be
    published-only.
    """

    model_config = ConfigDict(frozen=True)

    port_id: PortId
    berth_id: str

    is_published_constraint_berth: bool
    is_observed_operational_berth: bool
    limit_status: LimitStatus

    channel_depth_m: float | None = None
    designed_depth_m: float | None = None
    permissible_draft_m: float | None = None
    tide_allowance_m: float | None = None
    tide_rule: str | None = None
    max_loa_m: float | None = None
    max_beam_m: float | None = None
    max_displacement_t: float | None = None
    max_dwt: float | None = None
    berth_function: str | None = None
    """Free text, taken from the source's own wording (e.g. "Import
    Mechanised"), not normalised into a closed vocabulary -- the real
    documents don't share one across ports."""
    commodity_class: CommodityClass | None = None
    is_soft_limit: bool = False
    """True where the source itself says the limit can flex case-by-case
    (Vizag: "Vessels with higher draft will be permitted considering
    suitable rising tide... on case to case basis"). Nothing seeded from
    Dhamra or Gangavaram's BPTS is marked soft -- their berth-parameter
    tables state fixed figures."""

    promulgation_cycle: PromulgationCycle | None = None
    """Null exactly when limit_status is NOT_PUBLISHED: there is no
    promulgation cycle for a limit that was never promulgated."""
    draft_source: DraftSource = DraftSource.NONE

    source_url: str | None = None
    source_doc_id: str
    source_page: str | None = None
    doc_internal_date: date | None = None
    supersedes_doc_id: str | None = None
    effective_from: date
    effective_to: date | None = None
    retrieved_at: date

    internal_conflict: str | None = None
    """A contradiction found within or across the cited sources, recorded
    rather than silently resolved -- e.g. Dhamra DPC/07 section 19 lists BB4
    while section 20.1's capacity note still only names BB3A."""


class DraftDeclaration(BaseModel):
    """One row of a date-indexed draft declaration series (Dhamra's Monthly
    Draft Declaration is the only source of this shape seeded in this task).

    ``berth_scope`` is a tuple because one declaration series can name more
    than one berth at once -- the real document's own title line reads "...
    BB1 & BB2 IMPORT BERTH", not one berth at a time.
    """

    model_config = ConfigDict(frozen=True)

    port_id: PortId
    berth_scope: tuple[str, ...]
    date: date
    max_sw_arrival_draft_m: float
    doc_internal_date: date
    """The most recent date this document's own content actually covers --
    not a "generated on" stamp (none was found in the real document), and
    never the retrieval date. See declarations.parse_declaration_text."""
    source_url: str
    retrieved_at: date


class DraftResolution(BaseModel):
    """The result of resolving one berth's draft for one date. Always
    produced -- never a bare None -- so a caller can see *why* a draft is
    unavailable, not just that it is."""

    model_config = ConfigDict(frozen=True)

    draft_status: DraftStatus
    permissible_draft_m: float | None
    draft_as_of: date | None
    """The calendar date this value is actually declared for -- distinct
    from the as_of the caller asked about, though they're equal whenever
    draft_status is DECLARED."""
    source_doc_id: str | None = None
    warning: str | None = None
    """Populated whenever draft_status is not DECLARED, naming the document
    consulted (if any), its internal date, and the gap in days -- never a
    bare status code with no explanation of what was actually checked."""


class ResolvedBerth(BaseModel):
    """One candidate berth from resolve_constraint, with its draft resolved
    (or explicitly marked not applicable) alongside its static limits --
    callers should never need to separately call resolve_draft themselves."""

    model_config = ConfigDict(frozen=True)

    constraint: BerthConstraint
    draft: DraftResolution


class ConstraintResolution(BaseModel):
    """Everything resolve_constraint found for one (port, commodity, as_of)
    query, split into berths a clearance decision may actually use
    (``candidates``) and berths known to operate with no citable limits
    (``observed_only``) -- the latter must never be used to clear a vessel,
    only to disclose that the port operates more berths than are published.
    """

    model_config = ConfigDict(frozen=True)

    port_id: PortId
    commodity_class: CommodityClass | None
    as_of: date
    candidates: tuple[ResolvedBerth, ...]
    observed_only: tuple[BerthConstraint, ...]
