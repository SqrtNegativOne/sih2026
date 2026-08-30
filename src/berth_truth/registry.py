"""The constraint register: every BerthConstraint this project can cite.

Every row below is either sourced to a specific document, page and effective
date, or explicitly marked NOT_PUBLISHED / SUPERSEDED with a stated reason --
never carried over from ``opt.network.PortEnum``, whose per-port figures were
verified against these same primary documents and found wrong at every East
Coast destination checked (Paradip, Vizag, Gangavaram, Dhamra all disagreed
with their own official documents).

Three ports, three different evidentiary situations:

* **Dhamra** -- current BPTS (DPC/07, w.e.f. 2026-04-01) fetched and parsed;
  superseded prior edition (DPC/06) retained, not deleted; the port's live
  vessel schedule (BT-0) confirmed seven more berth identifiers operating
  with no published limits at all.
* **Gangavaram** -- current BPTS (AGPL/06, w.e.f. 2024-10-01) fetched and
  parsed; its own berth numbering (B1-B9) matches the live schedule (BT-0)
  exactly, so every row is both published and observed.
* **Visakhapatnam** -- only a September 2021 table was located despite a
  documented search for a current successor (see the VISAKHAPATNAM section
  below for exactly what was tried and ruled out). Seeded as SUPERSEDED per
  instruction, not presented as current.

REGISTRY is a flat tuple, not a dict keyed by (port, berth) -- more than one
row can exist for the same berth (current + superseded editions), and
resolver.resolve_constraint is what selects the one in force at a given date.
"""
from __future__ import annotations

from datetime import date
from typing import Final

from berth_truth.models import (
    BerthConstraint,
    CommodityClass,
    DraftSource,
    LimitStatus,
    PortId,
    PromulgationCycle,
)

_TODAY: Final[date] = date(2026, 8, 27)  # retrieved_at for every fetch performed this session

# ===========================================================================
# DHAMRA -- BPTS/DPC/07, w.e.f. 2026-04-01
# https://www.adaniports.com/-/media/project/ports/portsandterminals/dhamra-port/tariff/dhamra-bpts_dpc_01-wef-1-apr-2026.pdf
# Sections 19 (Berth Parameters) and 20 (Berth allotment criteria).
# ===========================================================================

_DHAMRA_DPC07_DOC: Final[str] = "Dhamra-BPTS-DPC-07"
_DHAMRA_DPC07_URL: Final[str] = (
    "https://www.adaniports.com/-/media/project/ports/portsandterminals/"
    "dhamra-port/tariff/dhamra-bpts_dpc_01-wef-1-apr-2026.pdf"
)
_DHAMRA_DPC07_EFFECTIVE_FROM: Final[date] = date(2026, 4, 1)

_DHAMRA_DPC06_DOC: Final[str] = "Dhamra-BPTS-DPC-06"
_DHAMRA_DPC06_URL: Final[str] = (
    "https://www.adaniports.com/-/media/Project/Ports/PortsAndTerminals/"
    "Quick-Links/Dhamra-BPTS_DPC_01-WEF-1st-Oct-2025.pdf"
)
_DHAMRA_DPC06_EFFECTIVE_FROM: Final[date] = date(2025, 10, 1)
_DHAMRA_DPC06_EFFECTIVE_TO: Final[date] = date(2026, 3, 31)  # day before DPC/07 took effect

# Real §20 functional classification, verbatim, identical across both DPC/06
# and DPC/07 (confirmed by fetching and comparing both editions): berth
# identity to what the document itself calls its handling type. BRGB (Barge
# Berth) is absent from this list in the source itself -- not an omission
# here, the BPTS's own §20 simply doesn't classify it. LNGT's is quoted
# exactly ("As per the declared Policy"), not paraphrased.
_DHAMRA_BERTH_FUNCTION: Final[dict[str, str]] = {
    "BB1": "Import Mechanised",
    "BB2": "Import Mechanised",
    "BB3": "Export Mechanised",
    "BB3A": "Semi Mechanised / Manual",
    "BB4": "Semi Mechanised / Manual",
    "LNGT": "As per the declared Policy",
}


def _dhamra_published(
    *,
    berth_id: str,
    max_loa_m: float,
    max_displacement_t: float,
    doc: str,
    url: str,
    effective_from: date,
    effective_to: date | None,
    limit_status: LimitStatus,
    internal_conflict: str | None = None,
) -> BerthConstraint:
    """One BB-series/barge/LNG berth row from a Dhamra BPTS edition.

    Draft is never populated here, in either edition: the BPTS itself omits
    it (its own §19 table has no draft column), which is exactly why
    declarations.py exists as a separate module. draft_source is
    DAILY_DECLARATION only for BB1/BB2, since Dhamra's Monthly Draft
    Declaration's own title line scopes it to exactly those two berths --
    stamping every Dhamra berth DAILY_DECLARATION would misrepresent
    berths no such document has ever covered as having one.
    """
    is_declared_berth = berth_id in ("BB1", "BB2")
    return BerthConstraint(
        port_id=PortId.DHAMRA,
        berth_id=berth_id,
        is_published_constraint_berth=True,
        is_observed_operational_berth=True,  # confirmed live via BT-0 for every one of these
        limit_status=limit_status,
        permissible_draft_m=None,
        max_loa_m=max_loa_m,
        max_displacement_t=max_displacement_t,
        berth_function=_DHAMRA_BERTH_FUNCTION.get(berth_id),
        promulgation_cycle=PromulgationCycle.DAILY if is_declared_berth else PromulgationCycle.STATIC,
        draft_source=DraftSource.DAILY_DECLARATION if is_declared_berth else DraftSource.NONE,
        source_url=url,
        source_doc_id=doc,
        source_page="21",  # the document's own printed page number for §19's table
        doc_internal_date=effective_from,  # no separate "generated on" stamp exists; the
        # issue's own w.e.f. date is the most precise internal date available
        effective_from=effective_from,
        effective_to=effective_to,
        retrieved_at=_TODAY,
        internal_conflict=internal_conflict,
    )


_DHAMRA_DPC07_ROWS: Final[tuple[BerthConstraint, ...]] = (
    _dhamra_published(
        berth_id="BB1", max_loa_m=350.0, max_displacement_t=250_000.0,
        doc=_DHAMRA_DPC07_DOC, url=_DHAMRA_DPC07_URL,
        effective_from=_DHAMRA_DPC07_EFFECTIVE_FROM, effective_to=None,
        limit_status=LimitStatus.PUBLISHED,
    ),
    _dhamra_published(
        berth_id="BB2", max_loa_m=350.0, max_displacement_t=250_000.0,
        doc=_DHAMRA_DPC07_DOC, url=_DHAMRA_DPC07_URL,
        effective_from=_DHAMRA_DPC07_EFFECTIVE_FROM, effective_to=None,
        limit_status=LimitStatus.PUBLISHED,
    ),
    _dhamra_published(
        berth_id="BB3", max_loa_m=350.0, max_displacement_t=250_000.0,
        doc=_DHAMRA_DPC07_DOC, url=_DHAMRA_DPC07_URL,
        effective_from=_DHAMRA_DPC07_EFFECTIVE_FROM, effective_to=None,
        limit_status=LimitStatus.PUBLISHED,
    ),
    _dhamra_published(
        berth_id="BB3A", max_loa_m=350.0, max_displacement_t=250_000.0,
        doc=_DHAMRA_DPC07_DOC, url=_DHAMRA_DPC07_URL,
        effective_from=_DHAMRA_DPC07_EFFECTIVE_FROM, effective_to=None,
        limit_status=LimitStatus.PUBLISHED,
    ),
    _dhamra_published(
        berth_id="BB4", max_loa_m=347.0, max_displacement_t=250_000.0,
        doc=_DHAMRA_DPC07_DOC, url=_DHAMRA_DPC07_URL,
        effective_from=_DHAMRA_DPC07_EFFECTIVE_FROM, effective_to=None,
        limit_status=LimitStatus.PUBLISHED,
        internal_conflict=(
            "DPC/07 section 19's Berth Parameters table lists BB4 (new in this edition -- "
            "absent from DPC/06), and section 20's berth-allotment-criteria list also names "
            "it (Semi Mechanised / Manual). But section 20.1's operational capacity note -- "
            "'the port has capacity ... 1 bulk/break bulk vessel on semi mechanised/manual "
            "berth BB3A' -- still names only BB3A, not BB4, for that simultaneous-vessel "
            "count. Recorded as found; not resolved."
        ),
    ),
    _dhamra_published(
        berth_id="BRGB", max_loa_m=130.0, max_displacement_t=8_000.0,
        doc=_DHAMRA_DPC07_DOC, url=_DHAMRA_DPC07_URL,
        effective_from=_DHAMRA_DPC07_EFFECTIVE_FROM, effective_to=None,
        limit_status=LimitStatus.PUBLISHED,
    ),
    _dhamra_published(
        berth_id="LNGT", max_loa_m=350.0, max_displacement_t=180_000.0,
        doc=_DHAMRA_DPC07_DOC, url=_DHAMRA_DPC07_URL,
        effective_from=_DHAMRA_DPC07_EFFECTIVE_FROM, effective_to=None,
        limit_status=LimitStatus.PUBLISHED,
    ),
)

_DHAMRA_DPC06_ROWS: Final[tuple[BerthConstraint, ...]] = (
    _dhamra_published(
        berth_id="BB1", max_loa_m=350.0, max_displacement_t=250_000.0,
        doc=_DHAMRA_DPC06_DOC, url=_DHAMRA_DPC06_URL,
        effective_from=_DHAMRA_DPC06_EFFECTIVE_FROM, effective_to=_DHAMRA_DPC06_EFFECTIVE_TO,
        limit_status=LimitStatus.SUPERSEDED,
    ),
    _dhamra_published(
        berth_id="BB2", max_loa_m=350.0, max_displacement_t=250_000.0,
        doc=_DHAMRA_DPC06_DOC, url=_DHAMRA_DPC06_URL,
        effective_from=_DHAMRA_DPC06_EFFECTIVE_FROM, effective_to=_DHAMRA_DPC06_EFFECTIVE_TO,
        limit_status=LimitStatus.SUPERSEDED,
    ),
    _dhamra_published(
        berth_id="BB3", max_loa_m=350.0, max_displacement_t=250_000.0,
        doc=_DHAMRA_DPC06_DOC, url=_DHAMRA_DPC06_URL,
        effective_from=_DHAMRA_DPC06_EFFECTIVE_FROM, effective_to=_DHAMRA_DPC06_EFFECTIVE_TO,
        limit_status=LimitStatus.SUPERSEDED,
    ),
    _dhamra_published(
        berth_id="BB3A", max_loa_m=350.0, max_displacement_t=250_000.0,
        doc=_DHAMRA_DPC06_DOC, url=_DHAMRA_DPC06_URL,
        effective_from=_DHAMRA_DPC06_EFFECTIVE_FROM, effective_to=_DHAMRA_DPC06_EFFECTIVE_TO,
        limit_status=LimitStatus.SUPERSEDED,
    ),
    # No BB4 row: DPC/06's own Berth Parameters table does not list one --
    # confirmed by fetching and reading both editions, not an omission here.
    _dhamra_published(
        berth_id="BRGB", max_loa_m=130.0, max_displacement_t=8_000.0,
        doc=_DHAMRA_DPC06_DOC, url=_DHAMRA_DPC06_URL,
        effective_from=_DHAMRA_DPC06_EFFECTIVE_FROM, effective_to=_DHAMRA_DPC06_EFFECTIVE_TO,
        limit_status=LimitStatus.SUPERSEDED,
    ),
    _dhamra_published(
        berth_id="LNGT", max_loa_m=350.0, max_displacement_t=180_000.0,
        doc=_DHAMRA_DPC06_DOC, url=_DHAMRA_DPC06_URL,
        effective_from=_DHAMRA_DPC06_EFFECTIVE_FROM, effective_to=_DHAMRA_DPC06_EFFECTIVE_TO,
        limit_status=LimitStatus.SUPERSEDED,
    ),
)

# supersedes_doc_id is set only on the DPC/07 rows -- DPC/06 is known to have
# itself superseded an issue-05, but no structured berth-table data for
# issue-05 was obtained, so DPC/06's own supersedes_doc_id is left None
# rather than naming a document this register has no other data for.
_DHAMRA_DPC07_ROWS = tuple(
    row.model_copy(update={"supersedes_doc_id": _DHAMRA_DPC06_DOC}) for row in _DHAMRA_DPC07_ROWS
)

# Observed-only: confirmed operating via BT-0's real archived Dhamra
# snapshot (2026-08-27) -- present in the "Vessels at Berth" table (several
# occupied, several VACANT-but-identified) with no entry in either BPTS
# edition. BB5 specifically: Adani's FY26 operational reporting states a new
# bulk berth was commissioned; it is absent from DPC/07's own Berth
# Parameters table regardless. The other six are sub-berth / segment / STS
# designations the BPTS simply doesn't name. No dimension is inherited from
# any similarly-named published berth -- BB3N is not BB3, B3AS is not BB3A.
_DHAMRA_LIVE_SCHEDULE_URL: Final[str] = "https://www.adaniports.com/ports-and-terminals/dhamra-port/vesselschedule"
_DHAMRA_OBSERVED_ONLY_IDS: Final[tuple[str, ...]] = ("BB5", "BB2E", "BB3B", "BB3N", "BB4N", "B3AS", "DHS1")

_DHAMRA_OBSERVED_ONLY_ROWS: Final[tuple[BerthConstraint, ...]] = tuple(
    BerthConstraint(
        port_id=PortId.DHAMRA,
        berth_id=berth_id,
        is_published_constraint_berth=False,
        is_observed_operational_berth=True,
        limit_status=LimitStatus.NOT_PUBLISHED,
        promulgation_cycle=None,
        draft_source=DraftSource.NONE,
        source_url=_DHAMRA_LIVE_SCHEDULE_URL,
        source_doc_id="adani-dhamra-vessel-schedule-live",
        doc_internal_date=None,  # a live page has no document revision date to cite
        effective_from=_TODAY,  # the date this berth's existence was actually confirmed
        effective_to=None,
        retrieved_at=_TODAY,
    )
    for berth_id in _DHAMRA_OBSERVED_ONLY_IDS
)

DHAMRA_ROWS: Final[tuple[BerthConstraint, ...]] = (
    *_DHAMRA_DPC07_ROWS,
    *_DHAMRA_DPC06_ROWS,
    *_DHAMRA_OBSERVED_ONLY_ROWS,
)

# ===========================================================================
# GANGAVARAM -- BPTS/AGPL/06, w.e.f. 2024-10-01
# https://cdn.gac.com/prod/docs/INDIA-Gangavaram_New-BPTS-Oct-2024.pdf
# Sections 19 (Berth Parameters), 20 (Berth allotment criteria / commodity
# priority) and 21 (Berthing / Un-berthing guidelines -- tidal restriction).
# ===========================================================================

_GANGAVARAM_DOC: Final[str] = "Gangavaram-BPTS-AGPL-06"
_GANGAVARAM_URL: Final[str] = "https://cdn.gac.com/prod/docs/INDIA-Gangavaram_New-BPTS-Oct-2024.pdf"
_GANGAVARAM_EFFECTIVE_FROM: Final[date] = date(2024, 10, 1)

# (berth, designed_depth_m, permissible_draft_m, berth_length_m -- not
# modelled, no field for it -- permissible_LOA_m, displacement_t,
# commodity_class, berth_function text from section 20, tide_rule text from
# section 21). All real, from the BPTS's own section 19/20/21 tables.
_GANGAVARAM_SPECS: Final[tuple[tuple[str, float, float, float, float, CommodityClass | None, str, str | None], ...]] = (
    ("1", 14.0, 13.0, 230.0, 65_000.0, None, "First Come First Serve (FCFS)", "POB: at any time (BPTS §21)"),
    ("2", 15.5, 14.5, 230.0, 98_000.0, None, "First Come First Serve (FCFS)", "POB: at any time (BPTS §21)"),
    ("3", 15.5, 15.0, 230.0, 98_000.0, None, "First Come First Serve (FCFS)", "POB: at any time (BPTS §21)"),
    (
        "4", 19.5, 17.7, 300.0, 236_000.0, CommodityClass.IRON_ORE_FINES_PELLETS,
        "Priority for Iron Ore Fines / Pellets",
        (
            "Un-berthing POB: any time except for loaded Cape size vessels, which are "
            "subject to tidal restriction (BPTS §21)"
        ),
    ),
    (
        "5", 19.5, 18.0, 292.0, 236_000.0, CommodityClass.COAL_COKE,
        "Priority for Coal/Coke",
        (
            "Berthing POB: any time except for loaded Cape size vessels, which are "
            "subject to tidal restriction (BPTS §21)"
        ),
    ),
    (
        "6", 19.5, 18.0, 300.0, 236_000.0, CommodityClass.COAL_COKE,
        "Priority for Coal/Coke",
        (
            "Berthing POB: any time except for loaded Cape size vessels, which are "
            "subject to tidal restriction (BPTS §21)"
        ),
    ),
    ("7", 15.5, 14.5, 200.0, 98_000.0, None, "First Come First Serve (FCFS)", "POB: at any time (BPTS §21)"),
    ("8", 15.5, 14.5, 230.0, 98_000.0, None, "First Come First Serve (FCFS)", "POB: at any time (BPTS §21)"),
    (
        "9", 15.5, 14.5, 290.0, 98_000.0, CommodityClass.CONTAINER,
        "Priority for Container Vessels",
        "POB: at any time (BPTS §21)",
    ),
)

GANGAVARAM_ROWS: Final[tuple[BerthConstraint, ...]] = tuple(
    BerthConstraint(
        port_id=PortId.GANGAVARAM,
        berth_id=f"B{berth_no}",
        is_published_constraint_berth=True,
        is_observed_operational_berth=True,  # B1-B9 confirmed live via BT-0, matching this numbering exactly
        limit_status=LimitStatus.PUBLISHED,
        designed_depth_m=designed_depth_m,
        permissible_draft_m=permissible_draft_m,
        max_loa_m=permissible_loa_m,
        max_displacement_t=displacement_t,
        commodity_class=commodity,
        berth_function=berth_function,
        tide_rule=tide_rule,
        promulgation_cycle=PromulgationCycle.ON_REVISION,
        draft_source=DraftSource.BPTS_STATIC,  # draft is in this same document, unlike Dhamra
        source_url=_GANGAVARAM_URL,
        source_doc_id=_GANGAVARAM_DOC,
        source_page="18",
        doc_internal_date=_GANGAVARAM_EFFECTIVE_FROM,
        effective_from=_GANGAVARAM_EFFECTIVE_FROM,
        effective_to=None,
        retrieved_at=_TODAY,
    )
    for (
        berth_no, designed_depth_m, permissible_draft_m, permissible_loa_m,
        displacement_t, commodity, berth_function, tide_rule,
    ) in _GANGAVARAM_SPECS
)

# ===========================================================================
# VISAKHAPATNAM -- no current document located. Seeded from the September
# 2021 table as SUPERSEDED, per instruction, not presented as current.
#
# What was tried for a current successor, this session:
#   1. Fetched vizagport.com's "Harbour & Berth Facilities" page directly --
#      its only linked document is titled "Minimum Draft requirements"
#      (vpt.shipping.gov.in/admin_assets/uploads/1686140103_...pdf). Fetched
#      and parsed it: it is a BALLAST draft safety table (minimum draft to
#      keep the propeller submerged while running light), a different
#      subject entirely from maximum permissible draft per berth. Not usable.
#   2. Searched for and fetched a "Berth Details.pdf" from the same site
#      (vpt.shipping.gov.in/admin_assets/uploads/1640237145_...pdf), which
#      does list berth-wise LOA/beam/draft. But it shows OR-1 as an active
#      berth with a real draft figure (10.06m) -- and the 2021 table already
#      in hand states OR-1 was "Decommissioned w.e.f. 21.01.2021". A document
#      showing OR-1 active cannot postdate one recording its decommissioning;
#      this "Berth Details.pdf" is therefore older than the September 2021
#      table already held, not a successor to it, despite an upload
#      timestamp (Dec 2021) that would suggest otherwise -- the same "URL/
#      upload date is not evidence of content currency" lesson as Dhamra's
#      Monthly Draft Declaration.
#   3. Found external corroboration (GAC "Hot Port News", a shipping agency's
#      summary, not a primary port document) that OR-I's permissible draft
#      was raised 11.0m -> 11.5m w.e.f. 2026-05-12, following the jetty's
#      reported recommissioning. This is real evidence the 2021 table is
#      stale, but it is a secondary source's report of a primary action, not
#      itself a citable VPA document -- so it is recorded as the reason OR-1
#      is marked SUPERSEDED, not used to seed a "current" draft value.
#   4. Two further searches for a Visakhapatnam Port Authority trade-notice
#      or circulars listing turned up berthing-programme (vessel-traffic)
#      PDFs only, no berth-constraint circular.
#
# Conclusion: the current document was not found. This is reported, not
# papered over -- see the module docstring and the BT-1 completion report.
# ===========================================================================

_VIZAG_DOC: Final[str] = "VPT-Marine-AllowableLOABeamDraft-2021-09"
_VIZAG_URL: Final[str] = (
    "https://vizagport.com/wp-content/uploads/2021/09/"
    "2.-Allowable-LOABeamDraft-of-the-vessels-as-on-September-2021.pdf"
)
_VIZAG_DOC_DATE: Final[date] = date(2021, 9, 1)  # month precision only -- the source gives "as on September 2021"

# (berth_id, max_loa_m, max_beam_m | None, permissible_draft_m | None,
#  tide_allowance_m | None, is_soft_limit, internal_conflict | None)
# Real Table-1 (Inner Harbour) and outer-harbour "For Arrivals" table, both
# "as on September 2021". Combined-berth entries (EQ-3 to EQ-4, EQ-5+EQ-6)
# are kept combined, exactly as the source presents them, rather than split
# into individual berths with invented individual figures.
_VIZAG_INNER: Final[tuple[tuple[str, float, float | None, float, float | None, bool, str | None], ...]] = (
    ("EQ-1_ADANI", 240.0, None, 14.5, 1.0, False, None),
    ("EQ-3_TO_EQ-4", 240.0, None, 14.5, 0.5, False, None),
    ("EQ-5_PLUS_EQ-6", 240.0, None, 11.0, None, False, None),
    ("EQ-7", 240.0, None, 14.5, 0.5, False, None),
    ("EQ-8_VSPL", 235.0, None, 14.5, 0.5, False, None),
    ("EQ-9_VSPL", 235.0, None, 14.5, 1.0, False, None),
    ("EQ-10_IMC", 160.0, None, 11.0, 1.0, False, None),
    ("WQ-1", 240.0, None, 13.5, 0.5, False, None),
    ("WQ-2", 240.0, None, 13.5, 0.5, False, None),
    ("WQ-3", 240.0, None, 13.5, 0.5, False, None),
    ("WQ-4", 240.0, None, 11.5, None, False, None),
    ("WQ-5", 240.0, None, 11.5, None, False, None),
    ("WQ-6_WQ-MPL", 230.0, None, 13.0, 1.0, False, None),
    ("WQ-7", 240.0, None, 14.5, 0.5, False, None),
    ("WQ-8", 240.0, None, 14.5, 0.5, False, None),
    ("RE_WQ-1", 145.0, None, 11.0, None, False, None),
    (
        "OR-1", 0.0, None, None, None, False,
        (
            "The 2021 table itself states this berth is 'Decommissioned w.e.f. 21.01.2021' "
            "-- no dimensions given, so none are seeded (the 0.0 LOA placeholder is never "
            "used: see below, this row's max_loa_m is overridden to None). A shipping "
            "agency's news summary (GAC Hot Port News, published 2026-05-15, not a primary "
            "VPA document) separately reports the jetty was recommissioned with permissible "
            "draft raised 11.0m -> 11.5m w.e.f. 2026-05-12 -- not independently confirmed "
            "against a primary VPA circular, so no current value is seeded for this berth "
            "either. Both facts together are why this entire document is treated as "
            "SUPERSEDED rather than current."
        ),
    ),
    ("OR-2", 170.0, None, 9.75, None, False, None),
    ("OR-3", 160.0, None, 11.0, None, False, None),
    ("F_BERTH", 200.0, None, 10.06, None, False, None),
    ("GREEN_CHANNEL_BERTH", 130.0, None, 8.2, None, False, None),
)

_VIZAG_OUTER: Final[tuple[tuple[str, float, float, float, float | None, bool, CommodityClass | None], ...]] = (
    ("OSTT", 280.0, 50.0, 17.0, None, False, None),
    ("OB-I", 300.0, 50.0, 16.5, None, False, None),
    ("OB-II", 300.0, 50.0, 16.5, None, False, None),
    ("VCTPL_MPB", 390.0, 42.0, 16.0, 1.0, False, None),
    ("VGCB", 300.0, 50.0, 18.1, 1.0, False, CommodityClass.COAL_COKE),
    ("LPG", 230.0, 42.0, 14.0, None, False, None),
    ("CHANNEL_BERTH_10000DWT", 150.0, 18.75, 8.5, None, False, None),
    ("FISHING_HARBOUR", 70.0, 14.0, 5.5, 0.7, False, None),
)

_vizag_inner_rows = [
    BerthConstraint(
        port_id=PortId.VISAKHAPATNAM,
        berth_id=berth_id,
        is_published_constraint_berth=True,
        # No live schedule source for Vizag was archived this task -- False
        # here means "not confirmed observed", not "confirmed not operating".
        is_observed_operational_berth=False,
        limit_status=LimitStatus.SUPERSEDED,
        permissible_draft_m=draft,
        max_loa_m=None if berth_id == "OR-1" else loa,
        tide_allowance_m=tide,
        is_soft_limit=soft,
        promulgation_cycle=PromulgationCycle.ON_REVISION,
        draft_source=DraftSource.BPTS_STATIC,
        source_url=_VIZAG_URL,
        source_doc_id=_VIZAG_DOC,
        source_page="1",
        doc_internal_date=_VIZAG_DOC_DATE,
        effective_from=_VIZAG_DOC_DATE,
        effective_to=None,  # true supersession date unknown -- not fabricated
        retrieved_at=_TODAY,
        internal_conflict=conflict,
    )
    for berth_id, loa, _beam, draft, tide, soft, conflict in _VIZAG_INNER
]

_vizag_outer_rows = [
    BerthConstraint(
        port_id=PortId.VISAKHAPATNAM,
        berth_id=berth_id,
        is_published_constraint_berth=True,
        is_observed_operational_berth=False,
        limit_status=LimitStatus.SUPERSEDED,
        permissible_draft_m=draft,
        max_loa_m=loa,
        max_beam_m=beam,
        tide_allowance_m=tide,
        is_soft_limit=soft,
        commodity_class=commodity,
        promulgation_cycle=PromulgationCycle.ON_REVISION,
        draft_source=DraftSource.BPTS_STATIC,
        source_url=_VIZAG_URL,
        source_doc_id=_VIZAG_DOC,
        source_page="2",
        doc_internal_date=_VIZAG_DOC_DATE,
        effective_from=_VIZAG_DOC_DATE,
        effective_to=None,
        retrieved_at=_TODAY,
    )
    for berth_id, loa, beam, draft, tide, soft, commodity in _VIZAG_OUTER
]

VISAKHAPATNAM_ROWS: Final[tuple[BerthConstraint, ...]] = (*_vizag_inner_rows, *_vizag_outer_rows)

# ===========================================================================
REGISTRY: Final[tuple[BerthConstraint, ...]] = (
    *DHAMRA_ROWS,
    *GANGAVARAM_ROWS,
    *VISAKHAPATNAM_ROWS,
)


def rows_for_port(port_id: PortId) -> tuple[BerthConstraint, ...]:
    """Every seeded row for one port, regardless of edition or status --
    resolver.resolve_constraint is what filters this down to what was in
    force at a given date. An unknown port returns an empty tuple, never a
    fabricated default."""
    return tuple(row for row in REGISTRY if row.port_id is port_id)
