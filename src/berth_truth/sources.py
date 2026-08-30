"""Source + adapter registry -- P1 §3, §5, §8.

One ``PortSource`` per (port, document) pair this package knows how to fetch.
Provenance only: source name, URL, retrieval timestamp, publication date.
No licence-audit workstream here by design -- this is an educational project;
the URL and its retrieval date are the record kept.

Verification status for every port was established by direct fetch-and-parse
(``VERIFIED``) or by search research that did not go as far as parsing a real
document (``REPORTED_UNVERIFIED_STRUCTURE``, ``RESEARCH_LEAD``) -- callers
(P2, downstream models) must be able to tell these apart, which is exactly
what ``source_quality`` and ``coverage_level`` are for.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict

from opt.network import PortEnum

__all__ = [
    "PORTWATCH_MAPPING_STATUS",
    "PORT_SOURCES",
    "AdapterKind",
    "CoverageLevel",
    "PortSource",
    "PortWatchMappingStatus",
    "SourceQuality",
    "sources_for_port",
]


class CoverageLevel(str, Enum):
    A = "A"  # machine-readable / API
    B = "B"  # official HTML
    C = "C"  # official PDF / periodic report
    D = "D"  # static berth/nav documentation only, no live feed
    E = "E"  # no operational feed found after a real search


class SourceQuality(str, Enum):
    """How authoritative the source is. Downstream models (P2's empirical
    waits, P3's class inference) must be able to see this and weight or
    caveat accordingly -- a PUBLIC_AGGREGATOR feed is not equivalent
    evidence to an OFFICIAL_PORT_AUTHORITY one, even at the same
    CoverageLevel."""

    OFFICIAL_PORT_AUTHORITY = "OFFICIAL_PORT_AUTHORITY"
    OFFICIAL_TERMINAL_OPERATOR = "OFFICIAL_TERMINAL_OPERATOR"
    GOVERNMENT_OTHER = "GOVERNMENT_OTHER"
    PUBLIC_AGGREGATOR = "PUBLIC_AGGREGATOR"


class AdapterKind(str, Enum):
    HTML_TABLE = "HTML_TABLE"
    PDF_REPORT = "PDF_REPORT"
    JSON_API = "JSON_API"
    CSV_XLSX = "CSV_XLSX"
    STATIC_DOC = "STATIC_DOC"
    MANUAL_DECLARATION = "MANUAL_DECLARATION"


class PortSource(BaseModel):
    model_config = ConfigDict(frozen=True)

    port: PortEnum
    source_name: str
    source_url: str
    doc_format: str
    """Free text describing the concrete document shape (e.g. "daily traffic
    PDF", "vessel schedule HTML page") -- not a closed vocabulary, since real
    sources don't share one."""
    cadence: str
    adapter: AdapterKind
    coverage_level: CoverageLevel
    source_quality: SourceQuality
    publication_date_field: str | None = None
    """Where in the document the real publication date is stated, if known
    (e.g. "title line: DAILY TRAFFIC UPDATE FOR DD-MM-YYYY")."""
    verified: bool
    """True only if this session directly fetched and parsed a real document
    from this source (not merely found evidence it exists)."""
    notes: str = ""


PORT_SOURCES: dict[PortEnum, tuple[PortSource, ...]] = {
    # --- Verified by direct fetch and parse this session ---------------------
    PortEnum.PARADIP: (
        PortSource(
            port=PortEnum.PARADIP,
            source_name="Paradip Port Authority Traffic Department",
            source_url="https://www.paradipport.gov.in/uploads/{yyyy}/{mm}/dtr{ddmm}.pdf",
            doc_format="Daily Traffic Update PDF (7pp: A. Working Vessels, B. Vessels Waiting "
            "at Anchorage, C. Expected Vessel, D. Berthing Movements)",
            cadence="daily",
            adapter=AdapterKind.PDF_REPORT,
            coverage_level=CoverageLevel.C,
            source_quality=SourceQuality.OFFICIAL_PORT_AUTHORITY,
            publication_date_field="title line: 'DAILY TRAFFIC UPDATE FOR DD-MM-YYYY'",
            verified=True,
            notes=(
                "Directly fetched and parsed (pypdf) 2026-08-27/28. Per-vessel: draft, LOA, "
                "beam, berth, cargo, shipper/receiver/stevedore, D/L flag, three distinct "
                "timestamps (ARVL/READY/BERTH), ETD/SLD, TOTAL/NORM/DAY'S-actual, MQ/BQ-to-date, "
                "BALANCE, event-code remarks, and high-tide restriction notes. Column order in "
                "extract_text() is interleaved -- positional extraction required, see "
                "providers.pdf_report."
            ),
        ),
        PortSource(
            port=PortEnum.PARADIP,
            source_name="Paradip Port Authority Traffic Department (archive)",
            source_url="https://paradipport.gov.in/Writereaddata/Daily_Traffic/dtr{ddmm}.pdf",
            doc_format="Daily Traffic Update PDF (same shape as the current-uploads source)",
            cadence="daily, historical",
            adapter=AdapterKind.PDF_REPORT,
            coverage_level=CoverageLevel.C,
            source_quality=SourceQuality.OFFICIAL_PORT_AUTHORITY,
            verified=True,
            notes="Older documents live under this path pattern rather than /uploads/. Used for backfill.",
        ),
    ),
    PortEnum.VIZAG: (
        PortSource(
            port=PortEnum.VIZAG,
            source_name="Visakhapatnam Port Authority Traffic Department",
            source_url="https://vpt.shipping.gov.in/admin_assets/uploads/{ts}_BERTHING%20PROGRAME%20-%20ETA.pdf",
            doc_format="Vessels Waiting & Expected PDF, grouped by commodity class",
            cadence="recurring, exact cadence not confirmed",
            adapter=AdapterKind.PDF_REPORT,
            coverage_level=CoverageLevel.C,
            source_quality=SourceQuality.OFFICIAL_PORT_AUTHORITY,
            publication_date_field="header: 'VESSELS WAITING & EXPECTED At HH:MM hrs Dt:- DD Month YYYY'",
            verified=True,
            notes=(
                "Directly fetched and parsed 2026-08-27. Vessel name, nationality, position "
                "('ROADS' = at anchorage), draft, arrival date, agent, tonnage, commodity, "
                "shipper. No berth-time column -- waits must be derived by differencing "
                "consecutive captures (evidence_class=SNAPSHOT_TRANSITION). The index page "
                "listing successive uploads was not located this session; the URL's timestamp "
                "component was read from a single located instance, not enumerated."
            ),
        ),
    ),
    PortEnum.DHAMRA: (
        PortSource(
            port=PortEnum.DHAMRA,
            source_name="Adani Ports vessel schedule",
            source_url="https://www.adaniports.com/ports-and-terminals/dhamra-port/vesselschedule",
            doc_format="Live HTML vessel schedule (4 tables: at berth, at anchorage, expected, sailed 24h)",
            cadence="live",
            adapter=AdapterKind.HTML_TABLE,
            coverage_level=CoverageLevel.B,
            source_quality=SourceQuality.OFFICIAL_TERMINAL_OPERATOR,
            verified=True,
            notes="BT-0, already integrated. Robots.txt and terms reviewed (see archiver.py docstring).",
        ),
    ),
    PortEnum.GANGAVARAM: (
        PortSource(
            port=PortEnum.GANGAVARAM,
            source_name="Adani Ports vessel schedule",
            source_url="https://www.adaniports.com/ports-and-terminals/gangavaram-port/vesselschedule",
            doc_format="Live HTML vessel schedule (same template as Dhamra)",
            cadence="live",
            adapter=AdapterKind.HTML_TABLE,
            coverage_level=CoverageLevel.B,
            source_quality=SourceQuality.OFFICIAL_TERMINAL_OPERATOR,
            verified=True,
            notes="BT-0, already integrated.",
        ),
    ),
    PortEnum.NEWCASTLE_AU: (
        PortSource(
            port=PortEnum.NEWCASTLE_AU,
            source_name="Port Authority of NSW -- Newcastle Harbour daily vessel movements",
            source_url="https://www.portauthoritynsw.com.au/port-operations/newcastle-harbour/newcastle-harbour-daily-vessel-movements",
            doc_format="Official daily vessel movements page",
            cadence="daily",
            adapter=AdapterKind.HTML_TABLE,
            coverage_level=CoverageLevel.B,
            source_quality=SourceQuality.OFFICIAL_PORT_AUTHORITY,
            verified=False,
            notes="Page located and its purpose confirmed by search; not yet fetched/parsed this session.",
        ),
        PortSource(
            port=PortEnum.NEWCASTLE_AU,
            source_name="Port of Newcastle shipping schedule",
            source_url="https://pon.com.au/shipping/shipping-schedule/",
            doc_format="Shipping schedule page (states it is managed by Port Authority of NSW)",
            cadence="daily",
            adapter=AdapterKind.HTML_TABLE,
            coverage_level=CoverageLevel.B,
            source_quality=SourceQuality.OFFICIAL_TERMINAL_OPERATOR,
            verified=False,
        ),
    ),
    # --- Reported as available; located by search, not yet fetched/parsed -----
    PortEnum.GOPALPUR: (
        PortSource(
            port=PortEnum.GOPALPUR,
            source_name="Gopalpur Ports Ltd (Adani) -- research lead",
            source_url="",
            doc_format="unconfirmed -- live schedule reportedly exists per search",
            cadence="unknown",
            adapter=AdapterKind.HTML_TABLE,
            coverage_level=CoverageLevel.E,
            source_quality=SourceQuality.PUBLIC_AGGREGATOR,
            verified=False,
            notes=(
                "LIVE_OPERATIONAL_FEED_NOT_FOUND: search returned only aggregator pages "
                "(V-OCEAN, Ruzave, ShipNext) and general port/infrastructure descriptions; "
                "no official Gopalpur Ports Ltd live-schedule URL was located and confirmed "
                "this session. Historical trade-notice references exist but a specific, "
                "fetchable URL was not identified."
            ),
        ),
    ),
    PortEnum.HALDIA: (
        PortSource(
            port=PortEnum.HALDIA,
            source_name="Syama Prasad Mookerjee Port Authority -- Haldia Dock Complex",
            source_url="https://smportkolkata.shipping.gov.in/smpk/hld/en/",
            doc_format="unconfirmed -- official port site, specific vessel-line-up document not located",
            cadence="unknown",
            adapter=AdapterKind.HTML_TABLE,
            coverage_level=CoverageLevel.D,
            source_quality=SourceQuality.OFFICIAL_PORT_AUTHORITY,
            verified=False,
            notes=(
                "Official Haldia Dock Complex site confirmed (a .gov.in Ministry of Ports "
                "statutory body). A specific daily vessel-position/draft-forecast page or "
                "document was not located and fetched this session -- searched, not verified. "
                "Recorded at Level D (authoritative site, live feed unconfirmed) rather than "
                "claiming Level B/C without having parsed a real document."
            ),
        ),
    ),
    PortEnum.SAGAR_SANDHEADS: (
        PortSource(
            port=PortEnum.SAGAR_SANDHEADS,
            source_name="Sandheads vessel-at-anchorage information -- research lead",
            source_url="",
            doc_format="unconfirmed -- reportedly includes anchoring time, draft, LOA, cargo",
            cadence="unknown",
            adapter=AdapterKind.HTML_TABLE,
            coverage_level=CoverageLevel.E,
            source_quality=SourceQuality.PUBLIC_AGGREGATOR,
            verified=False,
            notes=(
                "LIVE_OPERATIONAL_FEED_NOT_FOUND: a specific official URL publishing "
                "Sandheads anchorage vessel data was not located and confirmed this session. "
                "Kolkata Port / SMPK's own site is the most likely home for this and was not "
                "exhaustively searched to that specific page."
            ),
        ),
    ),
    PortEnum.GLADSTONE_AU: (
        PortSource(
            port=PortEnum.GLADSTONE_AU,
            source_name="North Queensland Bulk Ports / Gladstone Ports Corporation -- shipping movements (QSHIPS)",
            source_url="",
            doc_format="unconfirmed -- official shipping movements / QSHIPS system reported",
            cadence="unknown",
            adapter=AdapterKind.HTML_TABLE,
            coverage_level=CoverageLevel.E,
            source_quality=SourceQuality.PUBLIC_AGGREGATOR,
            verified=False,
            notes="LIVE_OPERATIONAL_FEED_NOT_FOUND: a specific fetchable QSHIPS URL was not located and confirmed this session.",
        ),
    ),
    PortEnum.SINGAPORE: (
        PortSource(
            port=PortEnum.SINGAPORE,
            source_name="Maritime and Port Authority of Singapore -- vessel arrival/departure",
            source_url="",
            doc_format="unconfirmed -- MPA / OCEANS-X system reported",
            cadence="unknown",
            adapter=AdapterKind.JSON_API,
            coverage_level=CoverageLevel.E,
            source_quality=SourceQuality.PUBLIC_AGGREGATOR,
            verified=False,
            notes="LIVE_OPERATIONAL_FEED_NOT_FOUND: a specific fetchable MPA/OCEANS-X endpoint was not located and confirmed this session.",
        ),
    ),
    # --- Weaker / fragmented -- degrade honestly ------------------------------
    PortEnum.RICHARDS_BAY: (
        PortSource(
            port=PortEnum.RICHARDS_BAY,
            source_name="Transnet / Richards Bay Coal Terminal -- constraints only",
            source_url="",
            doc_format="terminal constraint/handling documentation reported strong; live line-up unconfirmed",
            cadence="static",
            adapter=AdapterKind.STATIC_DOC,
            coverage_level=CoverageLevel.D,
            source_quality=SourceQuality.OFFICIAL_TERMINAL_OPERATOR,
            verified=False,
            notes="LIVE_OPERATIONAL_FEED_NOT_FOUND for a live schedule; terminal constraints appear strong but a specific fetchable document was not located this session.",
        ),
    ),
    PortEnum.BEIRA: (
        PortSource(
            port=PortEnum.BEIRA,
            source_name="CFM / Cornelder de Mocambique -- constraints only",
            source_url="",
            doc_format="operational/constraint documentation reported; official live schedule unconfirmed",
            cadence="static",
            adapter=AdapterKind.STATIC_DOC,
            coverage_level=CoverageLevel.D,
            source_quality=SourceQuality.OFFICIAL_TERMINAL_OPERATOR,
            verified=False,
            notes="LIVE_OPERATIONAL_FEED_NOT_FOUND for a live schedule this session.",
        ),
    ),
    PortEnum.MUARA_PANTAI: (
        PortSource(
            port=PortEnum.MUARA_PANTAI,
            source_name="Mahakam delta coal transshipment -- research lead",
            source_url="",
            doc_format="unconfirmed -- anchorage/transshipment operation, public movements exist",
            cadence="unknown",
            adapter=AdapterKind.MANUAL_DECLARATION,
            coverage_level=CoverageLevel.E,
            source_quality=SourceQuality.PUBLIC_AGGREGATOR,
            verified=False,
            notes="LIVE_OPERATIONAL_FEED_NOT_FOUND: official live feed for the anchorage itself not located; see PORTWATCH_MAPPING_STATUS for its Samarinda proxy.",
        ),
    ),
    PortEnum.HAMPTON_ROADS: (
        PortSource(
            port=PortEnum.HAMPTON_ROADS,
            source_name="Norfolk Southern / Lamberts Point -- constraints only",
            source_url="",
            doc_format="terminal constraint documentation reported strong; public live coal-terminal schedule weaker",
            cadence="static",
            adapter=AdapterKind.STATIC_DOC,
            coverage_level=CoverageLevel.D,
            source_quality=SourceQuality.OFFICIAL_TERMINAL_OPERATOR,
            verified=False,
            notes="LIVE_OPERATIONAL_FEED_NOT_FOUND for a live schedule this session.",
        ),
    ),
    PortEnum.BALIKPAPAN: (
        PortSource(
            port=PortEnum.BALIKPAPAN,
            source_name="Kariangau Terminal schedule -- unresolved relevance",
            source_url="",
            doc_format="an official Kariangau terminal schedule reportedly exists",
            cadence="unknown",
            adapter=AdapterKind.HTML_TABLE,
            coverage_level=CoverageLevel.E,
            source_quality=SourceQuality.PUBLIC_AGGREGATOR,
            verified=False,
            notes=(
                "NOT SUBSTITUTED: whether Kariangau is the actual bulk-coal operation this "
                "route intends was not confirmed this session. Recorded as unresolved rather "
                "than assumed equivalent -- see port_master.py's note on this port."
            ),
        ),
    ),
    PortEnum.VOSTOCHNY_RU: (
        PortSource(
            port=PortEnum.VOSTOCHNY_RU,
            source_name="NHK Maritime Services -- Vostochny port page",
            source_url="https://www.nhk-maritime.com/ports/vostochny-port",
            doc_format="operator/agency port description (PPK-3 berth specs)",
            cadence="static",
            adapter=AdapterKind.STATIC_DOC,
            coverage_level=CoverageLevel.D,
            source_quality=SourceQuality.PUBLIC_AGGREGATOR,
            verified=False,
            notes="Secondary (agency) source, not a primary port-authority document. Used for opt.network.PortEnum.VOSTOCHNY_RU's constraint figures, cross-checked against a second independent source (Credo-Trans).",
        ),
        PortSource(
            port=PortEnum.VOSTOCHNY_RU,
            source_name="Credo-Trans -- Port of Vostochny overview",
            source_url="https://credo-trans.com/russias-port-port-of-vostochny/",
            doc_format="operator/agency port description",
            cadence="static",
            adapter=AdapterKind.STATIC_DOC,
            coverage_level=CoverageLevel.D,
            source_quality=SourceQuality.PUBLIC_AGGREGATOR,
            verified=False,
            notes="Second independent source, converges with NHK Maritime on LOA 300m / draft up to 16.0m.",
        ),
    ),
}


def sources_for_port(port: PortEnum) -> tuple[PortSource, ...]:
    return PORT_SOURCES.get(port, ())


class PortWatchMappingStatus(str, Enum):
    IDENTITY_CONFIRMED = "IDENTITY_CONFIRMED"
    """The PortWatch CSV's own row content (portname/country/ISO3) confirms
    this is the same real-world port -- not merely a nearby one."""

    PROXY = "PROXY"
    """A different, nearby port's PortWatch data is used to stand in for
    this location, disclosed as a proxy, never presented as this port's own
    measurement."""

    UNAVAILABLE = "UNAVAILABLE"
    """No PortWatch file was found for this port at all."""


PORTWATCH_MAPPING_STATUS: dict[PortEnum, tuple[PortWatchMappingStatus, str]] = {
    PortEnum.PARADIP: (PortWatchMappingStatus.IDENTITY_CONFIRMED, "Paradip"),
    PortEnum.VIZAG: (PortWatchMappingStatus.IDENTITY_CONFIRMED, "Visakhapatnam"),
    PortEnum.GANGAVARAM: (PortWatchMappingStatus.UNAVAILABLE, ""),
    PortEnum.GOPALPUR: (PortWatchMappingStatus.IDENTITY_CONFIRMED, "Gopalpur"),
    PortEnum.DHAMRA: (PortWatchMappingStatus.IDENTITY_CONFIRMED, "Dhamra"),
    PortEnum.SAGAR_SANDHEADS: (PortWatchMappingStatus.UNAVAILABLE, ""),
    PortEnum.HALDIA: (PortWatchMappingStatus.IDENTITY_CONFIRMED, "Haldia"),
    PortEnum.NEWCASTLE_AU: (PortWatchMappingStatus.IDENTITY_CONFIRMED, "Newcastle_AU"),
    PortEnum.GLADSTONE_AU: (PortWatchMappingStatus.UNAVAILABLE, ""),
    PortEnum.RICHARDS_BAY: (PortWatchMappingStatus.IDENTITY_CONFIRMED, "Richards_Bay_ZA"),
    PortEnum.BEIRA: (PortWatchMappingStatus.IDENTITY_CONFIRMED, "Beira_MZ"),
    PortEnum.MUARA_PANTAI: (
        PortWatchMappingStatus.PROXY,
        (
            "Samarinda_ID -- an anchorage in the same Mahakam delta coal complex, not Muara "
            "Pantai itself; already disclosed as a proxy in build_geography.PORT_COORDS's "
            "own pre-existing comment. Reduced-confidence: traffic/congestion figures for "
            "MUARA_PANTAI describe Samarinda's port calls, not measured Muara Pantai activity."
        ),
    ),
    PortEnum.BALIKPAPAN: (PortWatchMappingStatus.IDENTITY_CONFIRMED, "Balikpapan_ID"),
    PortEnum.HAMPTON_ROADS: (PortWatchMappingStatus.UNAVAILABLE, ""),
    PortEnum.SINGAPORE: (PortWatchMappingStatus.UNAVAILABLE, ""),
    PortEnum.VOSTOCHNY_RU: (
        PortWatchMappingStatus.IDENTITY_CONFIRMED,
        (
            "Vostochny_RU -- the CSV's own rows self-declare portname='Vostochnyy', "
            "country='Russian Federation', ISO3='RUS' (portid=port1374); confirmed by "
            "reading the file directly, not inferred from proximity."
        ),
    ),
}
