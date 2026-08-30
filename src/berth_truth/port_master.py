"""Canonical port network for Berth Truth -- P1 §1-2.

Two separate questions this module answers:

1. **PS coverage audit** -- does the network cover every country the problem
   statement names as an origin? ``docs/00_og_problem_statement.md`` opens
   with "key origins like Australia, the US, Mozambique, Russia and
   Indonesia"; its own detailed description two paragraphs later drops
   Russia, and ``docs/01_problem_statement.md:8`` already flags this as a
   likely accidental omission. Before this module, ``PortEnum`` had no
   Russian member at all -- confirmed by iterating it. ``VOSTOCHNY_RU`` was
   added to ``opt.network`` to close that gap (see its own docstring there
   for the sourcing).

   ``CORE_PS_NETWORK`` is the ports needed to cover every PS-named country
   plus all seven Indian discharge ports. ``EXTENDED_NETWORK`` is everything
   else already in ``PortEnum`` -- kept, useful, but never allowed to stand
   in for a PS-required country (Richards Bay is South Africa; it does not
   cover Mozambique).

2. **Operational structure** -- not every port is a conventional berth.
   Sagar/Sandheads is a lightering anchorage for Haldia (this was already
   documented, independently, in ``build_geography.PORT_COORDS``'s own
   comment before this module existed -- "the pilot station and lightering
   anchorage seaward of the Hooghly, not a berth"). Muara Pantai is an
   anchorage/transshipment operation. Hampton Roads is a harbour complex
   whose real coal capability lives at a specific terminal (Lamberts Point),
   not the harbour as a whole. Forcing all of these into "one port, N
   berths" would misrepresent what a query against them actually means.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict

from opt.network import PortEnum

__all__ = [
    "CORE_PS_NETWORK",
    "EXTENDED_NETWORK",
    "PORT_PROFILES",
    "PS_REQUIRED_COUNTRIES",
    "NetworkTier",
    "OperationalModel",
    "PortProfile",
    "PortRole",
    "TerminalProfile",
    "ps_country_coverage",
]


class NetworkTier(str, Enum):
    """Whether a port is needed to satisfy the PS's own stated country
    coverage, or is a useful addition on top of that."""

    CORE_PS = "CORE_PS"
    EXTENDED = "EXTENDED"


class PortRole(str, Enum):
    LOAD = "LOAD"
    DISCHARGE = "DISCHARGE"
    HUB = "HUB"
    LIGHTERAGE = "LIGHTERAGE"


class OperationalModel(str, Enum):
    """How a location is actually worked -- governs what shape of berth-
    truth data is even meaningful for it. See the module docstring."""

    BERTHED = "BERTHED"
    """Conventional alongside berths: Paradip, Vizag, Dhamra, Gangavaram,
    Newcastle, Gladstone, Richards Bay, Balikpapan, Vostochny, Gopalpur."""

    MULTI_TERMINAL = "MULTI_TERMINAL"
    """Distinct terminals with genuinely different limits under one harbour
    name. Hampton Roads is this: the harbour covers Norfolk, Lamberts Point,
    Newport News and others, and one flat port-wide constant (the pre-P1
    state) cannot represent Lamberts Point's actual coal-berth limits."""

    ANCHORAGE_TRANSFER = "ANCHORAGE_TRANSFER"
    """Cargo is worked at anchorage, not alongside a berth. Muara Pantai
    (Mahakam delta coal transshipment) is this -- confirmed by
    build_geography's own pre-existing comment describing it as a
    transshipment anchorage with no PortWatch entry of its own."""

    LIGHTERAGE = "LIGHTERAGE"
    """Ship-to-ship or barge transfer at a roadstead, feeding a parent port.
    Sagar/Sandheads is this: a pilot station and lightering anchorage
    seaward of the Hooghly that exists in the network specifically because
    Capesize parcels bound for Haldia are lightered there -- it has no
    berths of its own to publish limits for."""

    HUB_TRANSSHIP = "HUB_TRANSSHIP"
    """Used only as a transshipment/repositioning node, not as a cargo
    origin or discharge point in its own right. Singapore is this in the
    current network."""


class TerminalProfile(BaseModel):
    """One distinct operating point within a ``MULTI_TERMINAL`` port. Not
    populated for the other operational models -- a ``BERTHED`` port's
    berths live in the berth_truth constraint register (BT-1/P2), not here;
    this is only for the case where one ``PortEnum`` member's own name hides
    more than one really-different facility."""

    model_config = ConfigDict(frozen=True)

    name: str
    notes: str


class PortProfile(BaseModel):
    """Everything P1 establishes about one port before any live-source work
    begins: which country it's in, whether the PS needs it, and how it's
    actually operated."""

    model_config = ConfigDict(frozen=True)

    port: PortEnum
    canonical_id: str
    country: str
    role: PortRole
    network_tier: NetworkTier
    operational_model: OperationalModel
    parent_or_hub: PortEnum | None = None
    """Mirrors ``opt.fleetmix.TRANSSHIPMENT_HUB`` for LIGHTERAGE/
    ANCHORAGE_TRANSFER/HUB_TRANSSHIP locations that route through another
    port. ``None`` for a port that stands on its own."""
    terminals: tuple[TerminalProfile, ...] = ()
    notes: str = ""


#: Every country the PS names as an origin, verbatim from the opening
#: sentence of docs/00_og_problem_statement.md:3 -- "varying supply and
#: demand dynamics from key origins like Australia, the US, Mozambique,
#: Russia and Indonesia". Kept as the PS's own words, not a paraphrase, so
#: the coverage check below is auditable against the source line.
PS_REQUIRED_COUNTRIES: tuple[str, ...] = (
    "Australia",
    "United States",
    "Mozambique",
    "Russia",
    "Indonesia",
)

PORT_PROFILES: dict[PortEnum, PortProfile] = {
    # --- Seven PS-named East Coast India discharge ports -- CORE_PS, all BERTHED
    # except Sandheads (LIGHTERAGE, feeding Haldia).
    PortEnum.PARADIP: PortProfile(
        port=PortEnum.PARADIP, canonical_id="Paradip", country="India",
        role=PortRole.DISCHARGE, network_tier=NetworkTier.CORE_PS,
        operational_model=OperationalModel.BERTHED,
    ),
    PortEnum.VIZAG: PortProfile(
        port=PortEnum.VIZAG, canonical_id="Visakhapatnam", country="India",
        role=PortRole.DISCHARGE, network_tier=NetworkTier.CORE_PS,
        operational_model=OperationalModel.BERTHED,
    ),
    PortEnum.GANGAVARAM: PortProfile(
        port=PortEnum.GANGAVARAM, canonical_id="Gangavaram", country="India",
        role=PortRole.DISCHARGE, network_tier=NetworkTier.CORE_PS,
        operational_model=OperationalModel.BERTHED,
    ),
    PortEnum.GOPALPUR: PortProfile(
        port=PortEnum.GOPALPUR, canonical_id="Gopalpur", country="India",
        role=PortRole.DISCHARGE, network_tier=NetworkTier.CORE_PS,
        operational_model=OperationalModel.BERTHED,
    ),
    PortEnum.DHAMRA: PortProfile(
        port=PortEnum.DHAMRA, canonical_id="Dhamra", country="India",
        role=PortRole.DISCHARGE, network_tier=NetworkTier.CORE_PS,
        operational_model=OperationalModel.BERTHED,
    ),
    PortEnum.SAGAR_SANDHEADS: PortProfile(
        port=PortEnum.SAGAR_SANDHEADS, canonical_id="Sagar_Sandheads", country="India",
        role=PortRole.LIGHTERAGE, network_tier=NetworkTier.CORE_PS,
        operational_model=OperationalModel.LIGHTERAGE,
        parent_or_hub=PortEnum.HALDIA,
        notes=(
            "Pilot station / lightering anchorage seaward of the Hooghly, not a berth "
            "(already documented this way in build_geography.PORT_COORDS's own comment "
            "before this module existed). Capesize parcels bound for Haldia are "
            "lightered here; it has no berths of its own to publish limits for."
        ),
    ),
    PortEnum.HALDIA: PortProfile(
        port=PortEnum.HALDIA, canonical_id="Haldia", country="India",
        role=PortRole.DISCHARGE, network_tier=NetworkTier.CORE_PS,
        operational_model=OperationalModel.BERTHED,
    ),
    # --- Australia (PS-required) -- CORE_PS
    PortEnum.NEWCASTLE_AU: PortProfile(
        port=PortEnum.NEWCASTLE_AU, canonical_id="Newcastle_AU", country="Australia",
        role=PortRole.LOAD, network_tier=NetworkTier.CORE_PS,
        operational_model=OperationalModel.BERTHED,
        notes="Predominantly thermal coal.",
    ),
    PortEnum.GLADSTONE_AU: PortProfile(
        port=PortEnum.GLADSTONE_AU, canonical_id="Gladstone_AU", country="Australia",
        role=PortRole.LOAD, network_tier=NetworkTier.EXTENDED,
        operational_model=OperationalModel.BERTHED,
        notes=(
            "Australia is already covered as CORE_PS via Newcastle; Gladstone is a "
            "useful additional Australian market, not required for country coverage."
        ),
    ),
    # --- South Africa -- NOT a PS-required country (the PS names Mozambique, not
    # South Africa). EXTENDED: a real, useful market, but cannot substitute for the
    # Mozambique requirement below.
    PortEnum.RICHARDS_BAY: PortProfile(
        port=PortEnum.RICHARDS_BAY, canonical_id="Richards_Bay", country="South Africa",
        role=PortRole.LOAD, network_tier=NetworkTier.EXTENDED,
        operational_model=OperationalModel.BERTHED,
        notes="South Africa is not a PS-named country. Does not cover the Mozambique requirement.",
    ),
    # --- Mozambique (PS-required) -- CORE_PS
    PortEnum.BEIRA: PortProfile(
        port=PortEnum.BEIRA, canonical_id="Beira", country="Mozambique",
        role=PortRole.LOAD, network_tier=NetworkTier.CORE_PS,
        operational_model=OperationalModel.BERTHED,
    ),
    # --- Indonesia (PS-required) -- CORE_PS
    PortEnum.MUARA_PANTAI: PortProfile(
        port=PortEnum.MUARA_PANTAI, canonical_id="Muara_Pantai", country="Indonesia",
        role=PortRole.LOAD, network_tier=NetworkTier.CORE_PS,
        operational_model=OperationalModel.ANCHORAGE_TRANSFER,
        notes=(
            "Coal transshipment anchorage in the Mahakam delta, East Kalimantan -- "
            "already documented as such (not a conventional berth) in "
            "build_geography.PORT_COORDS's pre-existing comment. Position and "
            "PortWatch traffic are proxied via Samarinda (see the PORTWATCH_UNAVAILABLE "
            "/ PROXY note in sources.py); the proxy is disclosed, not presented as an "
            "independent measurement of Muara Pantai itself."
        ),
    ),
    PortEnum.BALIKPAPAN: PortProfile(
        port=PortEnum.BALIKPAPAN, canonical_id="Balikpapan", country="Indonesia",
        role=PortRole.LOAD, network_tier=NetworkTier.EXTENDED,
        operational_model=OperationalModel.BERTHED,
        notes=(
            "Indonesia is already covered as CORE_PS via Muara Pantai. An official "
            "Kariangau terminal schedule was located during source verification, but "
            "whether Kariangau is the actual bulk-coal operation this route intends "
            "was not confirmed -- see sources.py for the caveat. Kept EXTENDED, not "
            "promoted to a country-covering role, until that is verified."
        ),
    ),
    # --- United States (PS-required) -- CORE_PS. MULTI_TERMINAL: the harbour name
    # hides genuinely different terminals; the coal-relevant one is Lamberts Point.
    PortEnum.HAMPTON_ROADS: PortProfile(
        port=PortEnum.HAMPTON_ROADS, canonical_id="Hampton_Roads", country="United States",
        role=PortRole.LOAD, network_tier=NetworkTier.CORE_PS,
        operational_model=OperationalModel.MULTI_TERMINAL,
        terminals=(
            TerminalProfile(
                name="Lamberts Point (Norfolk Southern)",
                notes="The standard US East Coast coal-export reference terminal within the Hampton Roads harbour complex.",
            ),
            TerminalProfile(
                name="Norfolk International Terminals",
                notes="Container/general cargo -- not the coal-relevant terminal, listed for completeness of the harbour's structure.",
            ),
            TerminalProfile(
                name="Newport News Marine Terminal",
                notes="Distinct facility within the same harbour; limits not established to be identical to Lamberts Point.",
            ),
        ),
        notes=(
            "Pre-P1, the whole harbour was one flat PortEnum constant. The port-wide "
            "figure is retained on PortEnum for backward compatibility with existing "
            "callers; a MULTI_TERMINAL query should resolve to Lamberts Point "
            "specifically once P2's register work reaches this port."
        ),
    ),
    # --- Singapore -- not a PS-required country (transshipment hub only)
    PortEnum.SINGAPORE: PortProfile(
        port=PortEnum.SINGAPORE, canonical_id="Singapore", country="Singapore",
        role=PortRole.HUB, network_tier=NetworkTier.EXTENDED,
        operational_model=OperationalModel.HUB_TRANSSHIP,
        notes="Used only as a transshipment/repositioning node in the current network, never as a cargo origin or discharge point in its own right.",
    ),
    # --- Russia (PS-required, previously entirely missing) -- CORE_PS
    PortEnum.VOSTOCHNY_RU: PortProfile(
        port=PortEnum.VOSTOCHNY_RU, canonical_id="Vostochny_RU", country="Russia",
        role=PortRole.LOAD, network_tier=NetworkTier.CORE_PS,
        operational_model=OperationalModel.BERTHED,
        notes=(
            "Added by P1 to close the network's Russia gap -- see opt.network."
            "PortEnum.VOSTOCHNY_RU's own docstring for full sourcing. Constraints are "
            "sourced from two independent operator/agency pages (NHK Maritime "
            "Services, Credo-Trans), not a primary port-authority document -- weaker "
            "sourcing than the register-backed Indian ports, disclosed as such."
        ),
    ),
}


CORE_PS_NETWORK: tuple[PortEnum, ...] = tuple(
    sorted(
        (p for p, profile in PORT_PROFILES.items() if profile.network_tier is NetworkTier.CORE_PS),
        key=lambda p: p.name,
    )
)

EXTENDED_NETWORK: tuple[PortEnum, ...] = tuple(
    sorted(
        (p for p, profile in PORT_PROFILES.items() if profile.network_tier is NetworkTier.EXTENDED),
        key=lambda p: p.name,
    )
)


def ps_country_coverage() -> dict[str, PortEnum | None]:
    """One row per PS-required country: the CORE_PS port covering it, or
    ``None`` if the audit found no covering port. An EXTENDED-tier port can
    never appear here as a covering port -- by construction, since only
    CORE_PS profiles are searched -- so Richards Bay (South Africa) can
    never be reported as covering Mozambique."""
    coverage: dict[str, PortEnum | None] = dict.fromkeys(PS_REQUIRED_COUNTRIES)
    for port in CORE_PS_NETWORK:
        country = PORT_PROFILES[port].country
        if country in coverage and coverage[country] is None:
            coverage[country] = port
    return coverage
