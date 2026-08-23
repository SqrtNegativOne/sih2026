"""Static Reference Data for the SIH Freight Optimizer.

All seven East Coast Indian discharge ports named in the problem statement,
plus the key loading port regions from Australia, Indonesia, Mozambique, and the US.

Sources for distances: approximate great-circle + Suez/Cape routing.
Port specs: approximate from public port authority data and the problem statement.
Bunker prices: approximate IFO 380 / VLSFO 0.5% blended, mid-2025.
"""
from __future__ import annotations

from opt.types import PortSpec

# ---------------------------------------------------------------------------
# Port Specifications
# ---------------------------------------------------------------------------
# East Coast India — Discharge Ports
# Draft/DWT limits sourced from port authority records (approximate).
PORT_SPECS: dict[str, PortSpec] = {
    # EC India discharge ports
    "Paradip": PortSpec(
        port_id="Paradip",
        max_loa_m=225.0,
        max_beam_m=32.2,
        max_draft_m=14.3,    # Channel draught
        max_dwt=75_000,      # Capesize cannot berth
        handling_rate_tph=1_200.0,
        expected_wait_days=3.5,
    ),
    "Vizag": PortSpec(
        port_id="Vizag",
        max_loa_m=300.0,
        max_beam_m=45.0,
        max_draft_m=17.0,   # Outer anchorage; inner berths shallower
        max_dwt=180_000,
        handling_rate_tph=2_500.0,
        expected_wait_days=2.0,
    ),
    "Gangavaram": PortSpec(
        port_id="Gangavaram",
        max_loa_m=300.0,
        max_beam_m=50.0,
        max_draft_m=16.5,
        max_dwt=200_000,
        handling_rate_tph=3_000.0,
        expected_wait_days=1.5,
    ),
    "Gopalpur": PortSpec(
        port_id="Gopalpur",
        max_loa_m=185.0,
        max_beam_m=28.0,
        max_draft_m=10.7,   # Shallow; Handysize / small Supra only
        max_dwt=35_000,
        handling_rate_tph=600.0,
        expected_wait_days=4.0,
    ),
    "Dhamra": PortSpec(
        port_id="Dhamra",
        max_loa_m=300.0,
        max_beam_m=46.0,
        max_draft_m=17.0,
        max_dwt=200_000,
        handling_rate_tph=2_800.0,
        expected_wait_days=2.5,
    ),
    "Sagar_Sandheads": PortSpec(
        port_id="Sagar_Sandheads",
        max_loa_m=225.0,
        max_beam_m=32.0,
        max_draft_m=9.5,    # Very shallow; Handysize only; lightering required for larger
        max_dwt=40_000,
        handling_rate_tph=700.0,
        expected_wait_days=5.0,
    ),
    "Haldia": PortSpec(
        port_id="Haldia",
        max_loa_m=215.0,
        max_beam_m=32.0,
        max_draft_m=8.5,    # Very constrained; Handysize / small Supra
        max_dwt=40_000,
        handling_rate_tph=800.0,
        expected_wait_days=4.5,
    ),
    # Origin / transshipment hubs
    "Newcastle_AU": PortSpec(
        port_id="Newcastle_AU",
        max_draft_m=14.5,
        max_dwt=85_000,
        handling_rate_tph=3_500.0,
        expected_wait_days=5.0,  # Notorious queue
    ),
    "Gladstone_AU": PortSpec(
        port_id="Gladstone_AU",
        max_draft_m=15.0,
        max_dwt=150_000,
        handling_rate_tph=4_000.0,
        expected_wait_days=3.0,
    ),
    "Richards_Bay": PortSpec(  # Mozambique / South Africa coal corridor
        port_id="Richards_Bay",
        max_draft_m=17.5,
        max_dwt=220_000,
        handling_rate_tph=5_000.0,
        expected_wait_days=7.0,
    ),
    "Beira": PortSpec(  # Mozambique
        port_id="Beira",
        max_draft_m=8.0,
        max_dwt=30_000,
        handling_rate_tph=500.0,
        expected_wait_days=3.0,
    ),
    "Muara_Pantai": PortSpec(  # Indonesia (Kalimantan)
        port_id="Muara_Pantai",
        max_draft_m=13.0,
        max_dwt=80_000,
        handling_rate_tph=2_000.0,
        expected_wait_days=2.0,
    ),
    "Balikpapan": PortSpec(
        port_id="Balikpapan",
        max_draft_m=12.5,
        max_dwt=75_000,
        handling_rate_tph=1_800.0,
        expected_wait_days=1.5,
    ),
    "Hampton_Roads": PortSpec(  # US East Coast
        port_id="Hampton_Roads",
        max_draft_m=14.0,
        max_dwt=100_000,
        handling_rate_tph=2_000.0,
        expected_wait_days=2.0,
    ),
    "Singapore": PortSpec(  # Bunkering / transshipment hub
        port_id="Singapore",
        max_draft_m=20.0,
        max_dwt=300_000,
        handling_rate_tph=None,
        expected_wait_days=0.5,
    ),
}

# ---------------------------------------------------------------------------
# Port Distance Matrix (nautical miles, approximate laden routing)
# ---------------------------------------------------------------------------
# EC India discharge ports are all within ~500nm of each other;
# use Paradip as the reference node for inter-port distances.
PORT_DISTANCES: dict[tuple[str, str], float] = {
    # Within EC India
    ("Haldia", "Sagar_Sandheads"):      60.0,
    ("Haldia", "Paradip"):             180.0,
    ("Haldia", "Dhamra"):              220.0,
    ("Haldia", "Gopalpur"):            350.0,
    ("Haldia", "Vizag"):               510.0,
    ("Haldia", "Gangavaram"):          550.0,
    ("Sagar_Sandheads", "Paradip"):    130.0,
    ("Sagar_Sandheads", "Dhamra"):     175.0,
    ("Sagar_Sandheads", "Gopalpur"):   310.0,
    ("Sagar_Sandheads", "Vizag"):      470.0,
    ("Sagar_Sandheads", "Gangavaram"): 510.0,
    ("Paradip", "Dhamra"):             100.0,
    ("Paradip", "Gopalpur"):           200.0,
    ("Paradip", "Vizag"):              360.0,
    ("Paradip", "Gangavaram"):         400.0,
    ("Dhamra", "Gopalpur"):            150.0,
    ("Dhamra", "Vizag"):               310.0,
    ("Dhamra", "Gangavaram"):          350.0,
    ("Gopalpur", "Vizag"):             175.0,
    ("Gopalpur", "Gangavaram"):        215.0,
    ("Vizag", "Gangavaram"):            40.0,

    # Australia to EC India (via Malacca Strait)
    ("Newcastle_AU", "Paradip"):      5_600.0,
    ("Newcastle_AU", "Vizag"):        5_700.0,
    ("Newcastle_AU", "Haldia"):       5_750.0,
    ("Gladstone_AU", "Paradip"):      5_200.0,
    ("Gladstone_AU", "Vizag"):        5_300.0,
    ("Gladstone_AU", "Haldia"):       5_350.0,

    # Indonesia to EC India
    ("Muara_Pantai", "Paradip"):      2_800.0,
    ("Muara_Pantai", "Vizag"):        2_900.0,
    ("Muara_Pantai", "Haldia"):       2_950.0,
    ("Balikpapan", "Paradip"):        2_700.0,
    ("Balikpapan", "Vizag"):          2_800.0,
    ("Balikpapan", "Haldia"):         2_850.0,

    # Southern Africa to EC India (via Cape or Suez)
    # Richards Bay -> EC India: shorter via Suez (~8,200nm) vs Cape (~10,500nm)
    # Using Suez routing (post-Red Sea normalcy assumption)
    ("Richards_Bay", "Paradip"):      8_200.0,
    ("Richards_Bay", "Vizag"):        8_250.0,
    ("Richards_Bay", "Haldia"):       8_350.0,
    ("Beira", "Paradip"):             7_800.0,
    ("Beira", "Vizag"):               7_850.0,
    ("Beira", "Haldia"):              7_950.0,

    # US East Coast to EC India (via Suez or Cape Horn)
    # Via Suez: ~12,500nm
    ("Hampton_Roads", "Paradip"):    12_500.0,
    ("Hampton_Roads", "Vizag"):      12_550.0,
    ("Hampton_Roads", "Haldia"):     12_600.0,

    # Singapore (hub) to EC India
    ("Singapore", "Paradip"):         1_600.0,
    ("Singapore", "Vizag"):           1_700.0,
    ("Singapore", "Haldia"):          1_750.0,
    ("Singapore", "Gopalpur"):        1_600.0,
    ("Singapore", "Gangavaram"):      1_680.0,
}

# ---------------------------------------------------------------------------
# Bunker Prices (USD / metric tonne, approximate mid-2025)
# ---------------------------------------------------------------------------
BUNKER_PRICES: dict[str, float] = {
    "Paradip":         610.0,
    "Vizag":           620.0,
    "Gangavaram":      620.0,
    "Gopalpur":        640.0,   # Minor port premium
    "Dhamra":          630.0,
    "Sagar_Sandheads": 650.0,
    "Haldia":          615.0,
    "Newcastle_AU":    590.0,
    "Gladstone_AU":    585.0,
    "Richards_Bay":    570.0,
    "Beira":           640.0,
    "Muara_Pantai":    600.0,
    "Balikpapan":      595.0,
    "Hampton_Roads":   650.0,
    "Singapore":       540.0,   # Cheapest bunkering hub in region
}

# Convenience: fleet-wide weighted average if you don't want per-port
BLENDED_BUNKER_USD_PER_TONNE: float = 610.0
