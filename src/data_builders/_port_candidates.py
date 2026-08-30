"""Candidate global dry-bulk ports to resolve against the PortWatch database.

This is a curation input, not output: each entry is a (label, search terms,
commodity role, basin) tuple describing a port worth having in the Tonnage
Field reconstruction, based on real dry-bulk trade knowledge -- major coal,
iron ore, grain and bauxite/alumina load ports, plus the import hubs that
consume them. Coverage goes well beyond the SIH26006 problem statement's
named lanes (Australia/US/Mozambique/Indonesia -> 7 EC India ports) on
purpose: reconstructing basin-level free tonnage requires seeing the whole
network a ballaster could be sitting in, not just the direct lanes.

The 14 ports already resolved and pulled by the original harvest (see
raw_data/portwatch/ports_index.csv) are NOT repeated here.

Each candidate is resolved against the live PortWatch ports database before
being trusted -- see harvest_portwatch.py resolve_candidates(). A label
appearing here is a hypothesis, not a guarantee the port exists in PortWatch.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PortCandidate:
    label: str
    search_terms: tuple[str, ...]  # tried in order against UPPER(portname)/UPPER(fullname)
    role: str  # "coal", "iron_ore", "grain", "bauxite", "import_hub", "mixed"
    basin: str  # rough basin tag for later Tonnage Field basin partition
    country_hint: str | None = None


CANDIDATES: tuple[PortCandidate, ...] = (
    # ---------------------------------------------------------------
    # Australia -- coal (Queensland/NSW) and iron ore (WA)
    # ---------------------------------------------------------------
    PortCandidate("Abbot_Point_AU", ("ABBOT POINT",), "coal", "pacific", "AUS"),
    PortCandidate("Dalrymple_Bay_AU", ("DALRYMPLE BAY",), "coal", "pacific", "AUS"),
    PortCandidate("Port_Kembla_AU", ("PORT KEMBLA",), "coal", "pacific", "AUS"),
    PortCandidate("Port_Hedland_AU", ("PORT HEDLAND",), "iron_ore", "pacific", "AUS"),
    PortCandidate("Dampier_AU", ("DAMPIER",), "iron_ore", "pacific", "AUS"),
    PortCandidate("Cape_Lambert_AU", ("CAPE LAMBERT", "POINT SAMSON"), "iron_ore", "pacific", "AUS"),
    PortCandidate("Geraldton_AU", ("GERALDTON",), "iron_ore", "pacific", "AUS"),
    PortCandidate("Esperance_AU", ("ESPERANCE",), "grain", "pacific", "AUS"),
    PortCandidate("Kwinana_AU", ("KWINANA", "FREMANTLE"), "grain", "pacific", "AUS"),
    PortCandidate("Gove_AU", ("GOVE",), "bauxite", "pacific", "AUS"),
    PortCandidate("Weipa_AU", ("WEIPA",), "bauxite", "pacific", "AUS"),

    # ---------------------------------------------------------------
    # Indonesia -- thermal coal (Kalimantan)
    # ---------------------------------------------------------------
    PortCandidate("Taboneo_ID", ("TABONEO",), "coal", "pacific", "IDN"),
    PortCandidate("Tanjung_Bara_ID", ("TANJUNG BARA", "SANGATTA"), "coal", "pacific", "IDN"),
    PortCandidate("Muara_Berau_ID", ("MUARA BERAU",), "coal", "pacific", "IDN"),
    PortCandidate("Kotabaru_ID", ("KOTABARU", "SATUI"), "coal", "pacific", "IDN"),
    PortCandidate("Tarahan_ID", ("TARAHAN", "PANJANG"), "coal", "pacific", "IDN"),
    PortCandidate("Adang_Bay_ID", ("ADANG BAY", "PULAU LAUT"), "coal", "pacific", "IDN"),

    # ---------------------------------------------------------------
    # Russia -- coal (Far East + NW), grain (Black Sea/Azov)
    # ---------------------------------------------------------------
    PortCandidate("Vostochny_RU", ("VOSTOCHNY", "NAKHODKA"), "coal", "pacific", "RUS"),
    PortCandidate("Murmansk_RU", ("MURMANSK",), "coal", "atlantic", "RUS"),
    PortCandidate("Ust_Luga_RU", ("UST-LUGA", "UST LUGA"), "coal", "atlantic", "RUS"),
    PortCandidate("Vanino_RU", ("VANINO",), "coal", "pacific", "RUS"),
    PortCandidate("Novorossiysk_RU", ("NOVOROSSIYSK",), "grain", "atlantic", "RUS"),

    # ---------------------------------------------------------------
    # South Africa -- iron ore (Saldanha) on top of the existing Richards Bay
    # ---------------------------------------------------------------
    PortCandidate("Saldanha_Bay_ZA", ("SALDANHA",), "iron_ore", "indian_ocean", "ZAF"),

    # ---------------------------------------------------------------
    # Brazil -- iron ore + grain (Vale/soy complex)
    # ---------------------------------------------------------------
    PortCandidate("Tubarao_BR", ("TUBARAO", "VITORIA"), "iron_ore", "atlantic", "BRA"),
    PortCandidate("Ponta_da_Madeira_BR", ("PONTA DA MADEIRA", "ITAQUI", "SAO LUIS"), "iron_ore", "atlantic", "BRA"),
    PortCandidate("Itaguai_BR", ("ITAGUAI", "SEPETIBA"), "iron_ore", "atlantic", "BRA"),
    PortCandidate("Santos_BR", ("SANTOS",), "grain", "atlantic", "BRA"),
    PortCandidate("Paranagua_BR", ("PARANAGUA",), "grain", "atlantic", "BRA"),
    PortCandidate("Rio_Grande_BR", ("RIO GRANDE",), "grain", "atlantic", "BRA"),

    # ---------------------------------------------------------------
    # Argentina -- grain (up-river Parana complex + Bahia Blanca)
    # ---------------------------------------------------------------
    PortCandidate("Rosario_AR", ("ROSARIO",), "grain", "atlantic", "ARG"),
    PortCandidate("Bahia_Blanca_AR", ("BAHIA BLANCA",), "grain", "atlantic", "ARG"),
    PortCandidate("Necochea_AR", ("NECOCHEA", "QUEQUEN"), "grain", "atlantic", "ARG"),
    PortCandidate("San_Lorenzo_AR", ("SAN LORENZO",), "grain", "atlantic", "ARG"),

    # ---------------------------------------------------------------
    # Colombia -- coal (Caribbean)
    # ---------------------------------------------------------------
    PortCandidate("Puerto_Bolivar_CO", ("PUERTO BOLIVAR",), "coal", "atlantic", "COL"),
    PortCandidate("Santa_Marta_CO", ("SANTA MARTA",), "coal", "atlantic", "COL"),
    PortCandidate("Cienaga_CO", ("CIENAGA", "DRUMMOND"), "coal", "atlantic", "COL"),

    # ---------------------------------------------------------------
    # USA -- coal (Gulf + East Coast), grain (Gulf + PNW), iron ore (Great Lakes)
    # ---------------------------------------------------------------
    PortCandidate("Baltimore_US", ("BALTIMORE",), "coal", "atlantic", "USA"),
    PortCandidate("New_Orleans_US", ("NEW ORLEANS",), "grain", "atlantic", "USA"),
    PortCandidate("Mobile_US", ("MOBILE",), "coal", "atlantic", "USA"),
    PortCandidate("Houston_US", ("HOUSTON",), "grain", "atlantic", "USA"),
    PortCandidate("Duluth_US", ("DULUTH", "SUPERIOR"), "iron_ore", "atlantic", "USA"),
    PortCandidate("Portland_US", ("PORTLAND",), "grain", "pacific", "USA"),
    PortCandidate("Longview_US", ("LONGVIEW", "KALAMA"), "grain", "pacific", "USA"),

    # ---------------------------------------------------------------
    # Canada -- grain, coal, iron ore
    # ---------------------------------------------------------------
    PortCandidate("Vancouver_CA", ("VANCOUVER",), "grain", "pacific", "CAN"),
    PortCandidate("Prince_Rupert_CA", ("PRINCE RUPERT",), "coal", "pacific", "CAN"),
    PortCandidate("Thunder_Bay_CA", ("THUNDER BAY",), "grain", "atlantic", "CAN"),
    PortCandidate("Sept_Iles_CA", ("SEPT-ILES", "SEPT ILES", "SEVEN ISLANDS"), "iron_ore", "atlantic", "CAN"),

    # ---------------------------------------------------------------
    # West Africa -- bauxite (Guinea) and iron ore emerging supply
    # ---------------------------------------------------------------
    PortCandidate("Kamsar_GN", ("KAMSAR",), "bauxite", "atlantic", "GIN"),
    PortCandidate("Conakry_GN", ("CONAKRY",), "bauxite", "atlantic", "GIN"),

    # ---------------------------------------------------------------
    # Ukraine / Black Sea -- grain (subject to wartime disruption; data may be sparse)
    # ---------------------------------------------------------------
    PortCandidate("Odesa_UA", ("ODESA", "ODESSA"), "grain", "atlantic", "UKR"),
    PortCandidate("Chornomorsk_UA", ("CHORNOMORSK", "ILLICHIVSK"), "grain", "atlantic", "UKR"),
    PortCandidate("Yuzhne_UA", ("YUZHNE", "PIVDENNYI"), "grain", "atlantic", "UKR"),
    PortCandidate("Constanta_RO", ("CONSTANTA",), "grain", "atlantic", "ROU"),

    # ---------------------------------------------------------------
    # China -- the dominant dry-bulk import demand centre (coal + iron ore)
    # ---------------------------------------------------------------
    PortCandidate("Qingdao_CN", ("QINGDAO",), "import_hub", "pacific", "CHN"),
    PortCandidate("Rizhao_CN", ("RIZHAO",), "import_hub", "pacific", "CHN"),
    PortCandidate("Tianjin_CN", ("TIANJIN",), "import_hub", "pacific", "CHN"),
    PortCandidate("Caofeidian_CN", ("CAOFEIDIAN",), "import_hub", "pacific", "CHN"),
    PortCandidate("Dalian_CN", ("DALIAN",), "import_hub", "pacific", "CHN"),
    PortCandidate("Lianyungang_CN", ("LIANYUNGANG",), "import_hub", "pacific", "CHN"),
    PortCandidate("Ningbo_Zhoushan_CN", ("NINGBO",), "import_hub", "pacific", "CHN"),
    PortCandidate("Yingkou_CN", ("YINGKOU",), "import_hub", "pacific", "CHN"),
    PortCandidate("Guangzhou_CN", ("GUANGZHOU",), "import_hub", "pacific", "CHN"),
    PortCandidate("Zhanjiang_CN", ("ZHANJIANG",), "import_hub", "pacific", "CHN"),
    PortCandidate("Fangcheng_CN", ("FANGCHENG",), "import_hub", "pacific", "CHN"),
    PortCandidate("Jingtang_CN", ("JINGTANG",), "import_hub", "pacific", "CHN"),
    PortCandidate("Yantai_CN", ("YANTAI",), "import_hub", "pacific", "CHN"),
    PortCandidate("Shanghai_CN", ("SHANGHAI",), "import_hub", "pacific", "CHN"),

    # ---------------------------------------------------------------
    # Japan / Korea / Taiwan -- steel-mill import hubs
    # ---------------------------------------------------------------
    PortCandidate("Chiba_JP", ("CHIBA",), "import_hub", "pacific", "JPN"),
    PortCandidate("Kashima_JP", ("KASHIMA",), "import_hub", "pacific", "JPN"),
    PortCandidate("Fukuyama_JP", ("FUKUYAMA",), "import_hub", "pacific", "JPN"),
    PortCandidate("Kimitsu_JP", ("KIMITSU",), "import_hub", "pacific", "JPN"),
    PortCandidate("Mizushima_JP", ("MIZUSHIMA",), "import_hub", "pacific", "JPN"),
    PortCandidate("Oita_JP", ("OITA",), "import_hub", "pacific", "JPN"),
    PortCandidate("Gwangyang_KR", ("GWANGYANG", "KWANGYANG"), "import_hub", "pacific", "KOR"),
    PortCandidate("Pohang_KR", ("POHANG",), "import_hub", "pacific", "KOR"),
    PortCandidate("Incheon_KR", ("INCHEON",), "import_hub", "pacific", "KOR"),
    PortCandidate("Kaohsiung_TW", ("KAOHSIUNG",), "import_hub", "pacific", "TWN"),
    PortCandidate("Taichung_TW", ("TAICHUNG",), "import_hub", "pacific", "TWN"),

    # ---------------------------------------------------------------
    # SE Asia -- secondary importers on the Indonesia->Asia coal route
    # ---------------------------------------------------------------
    PortCandidate("Manila_PH", ("MANILA",), "import_hub", "pacific", "PHL"),
    PortCandidate("Cebu_PH", ("CEBU",), "import_hub", "pacific", "PHL"),
    PortCandidate("Ho_Chi_Minh_VN", ("HO CHI MINH", "SAIGON", "CAT LAI"), "import_hub", "pacific", "VNM"),
    PortCandidate("Hai_Phong_VN", ("HAI PHONG", "HAIPHONG"), "import_hub", "pacific", "VNM"),
    PortCandidate("Port_Klang_MY", ("PORT KLANG", "KELANG"), "import_hub", "pacific", "MYS"),
    PortCandidate("Kuantan_MY", ("KUANTAN",), "import_hub", "pacific", "MYS"),

    # ---------------------------------------------------------------
    # Europe -- transshipment/import hubs (Rotterdam = ARA benchmark port)
    # ---------------------------------------------------------------
    PortCandidate("Rotterdam_NL", ("ROTTERDAM",), "import_hub", "atlantic", "NLD"),
    PortCandidate("Amsterdam_NL", ("AMSTERDAM",), "import_hub", "atlantic", "NLD"),
    PortCandidate("Immingham_GB", ("IMMINGHAM",), "import_hub", "atlantic", "GBR"),
    PortCandidate("Hamburg_DE", ("HAMBURG",), "import_hub", "atlantic", "DEU"),

    # ---------------------------------------------------------------
    # Turkey / East Med -- grain + coal importer at the Bosporus gateway
    # ---------------------------------------------------------------
    PortCandidate("Iskenderun_TR", ("ISKENDERUN",), "import_hub", "atlantic", "TUR"),
    PortCandidate("Izmir_TR", ("IZMIR",), "import_hub", "atlantic", "TUR"),

    # ---------------------------------------------------------------
    # Middle East / South Asia fringe -- relevant to the Suez/Hormuz corridor
    # ---------------------------------------------------------------
    PortCandidate("Karachi_PK", ("KARACHI",), "import_hub", "indian_ocean", "PAK"),
    PortCandidate("Chittagong_BD", ("CHATTOGRAM", "CHITTAGONG"), "import_hub", "indian_ocean", "BGD"),
    PortCandidate("Colombo_LK", ("COLOMBO",), "import_hub", "indian_ocean", "LKA"),
    PortCandidate("Jebel_Ali_AE", ("JEBEL ALI",), "import_hub", "indian_ocean", "ARE"),

    # ---------------------------------------------------------------
    # India -- west coast, for a fuller domestic supply/demand picture.
    # The SIH problem statement scopes discharge to 7 EC ports; these are
    # additional observation points for the basin-level tonnage model, not
    # part of the chartering decision itself.
    # ---------------------------------------------------------------
    PortCandidate("Mundra_IN", ("MUNDRA",), "import_hub", "indian_ocean", "IND"),
    PortCandidate("Kandla_IN", ("KANDLA", "DEENDAYAL"), "import_hub", "indian_ocean", "IND"),
    PortCandidate("JNPT_IN", ("JAWAHARLAL NEHRU", "NHAVA SHEVA"), "import_hub", "indian_ocean", "IND"),
    PortCandidate("Krishnapatnam_IN", ("KRISHNAPATNAM",), "import_hub", "indian_ocean", "IND"),
    PortCandidate("Chennai_IN", ("CHENNAI", "MADRAS"), "import_hub", "indian_ocean", "IND"),
    PortCandidate("Ennore_IN", ("ENNORE", "KAMARAJAR"), "import_hub", "indian_ocean", "IND"),
    PortCandidate("Tuticorin_IN", ("TUTICORIN", "THOOTHUKUDI"), "import_hub", "indian_ocean", "IND"),
    PortCandidate("Goa_IN", ("MORMUGAO", "GOA"), "iron_ore", "indian_ocean", "IND"),
    PortCandidate("New_Mangalore_IN", ("NEW MANGALORE", "MANGALORE"), "import_hub", "indian_ocean", "IND"),

    # ---------------------------------------------------------------
    # West Africa -- iron ore (Mauritania/Liberia/Sierra Leone majors, distinct
    # from the Guinea bauxite ports above)
    # ---------------------------------------------------------------
    PortCandidate("Nouadhibou_MR", ("NOUADHIBOU",), "iron_ore", "atlantic", "MRT"),
    PortCandidate("Buchanan_LR", ("BUCHANAN",), "iron_ore", "atlantic", "LBR"),
    PortCandidate("Pepel_SL", ("PEPEL", "MARAMPA"), "iron_ore", "atlantic", "SLE"),

    # ---------------------------------------------------------------
    # East Africa / Indian Ocean rim -- import hubs and secondary coal/grain
    # ---------------------------------------------------------------
    PortCandidate("Mombasa_KE", ("MOMBASA",), "import_hub", "indian_ocean", "KEN"),
    PortCandidate("Dar_es_Salaam_TZ", ("DAR ES SALAAM",), "import_hub", "indian_ocean", "TZA"),
    PortCandidate("Djibouti_DJ", ("DJIBOUTI",), "import_hub", "indian_ocean", "DJI"),
    PortCandidate("Bandar_Abbas_IR", ("BANDAR ABBAS",), "import_hub", "indian_ocean", "IRN"),
    PortCandidate("Salalah_OM", ("SALALAH",), "import_hub", "indian_ocean", "OMN"),

    # ---------------------------------------------------------------
    # More Indonesia -- additional dry-bulk load points beyond Kalimantan coal
    # ---------------------------------------------------------------
    PortCandidate("Palembang_ID", ("PALEMBANG",), "coal", "indian_ocean", "IDN"),
    PortCandidate("Belawan_ID", ("BELAWAN",), "import_hub", "indian_ocean", "IDN"),

    # ---------------------------------------------------------------
    # Thailand -- import hub on the SE Asia coal/grain route
    # ---------------------------------------------------------------
    PortCandidate("Map_Ta_Phut_TH", ("MAP TA PHUT",), "import_hub", "pacific", "THA"),
    PortCandidate("Laem_Chabang_TH", ("LAEM CHABANG",), "import_hub", "pacific", "THA"),

    # ---------------------------------------------------------------
    # More China -- additional import hubs to strengthen the dominant demand basin
    # ---------------------------------------------------------------
    PortCandidate("Nantong_CN", ("NANTONG",), "import_hub", "pacific", "CHN"),
    PortCandidate("Jinzhou_CN", ("JINZHOU",), "import_hub", "pacific", "CHN"),
    PortCandidate("Beihai_CN", ("BEIHAI",), "import_hub", "pacific", "CHN"),
    PortCandidate("Weifang_CN", ("WEIFANG",), "import_hub", "pacific", "CHN"),

    # ---------------------------------------------------------------
    # More Australia -- additional grain export points (South/West coast)
    # ---------------------------------------------------------------
    PortCandidate("Port_Lincoln_AU", ("PORT LINCOLN",), "grain", "pacific", "AUS"),
    PortCandidate("Port_Adelaide_AU", ("PORT ADELAIDE", "OUTER HARBOR"), "grain", "pacific", "AUS"),
    PortCandidate("Portland_AU", ("PORTLAND",), "grain", "pacific", "AUS"),
    PortCandidate("Albany_AU", ("ALBANY",), "grain", "pacific", "AUS"),

    # ---------------------------------------------------------------
    # More USA Gulf -- additional grain/coal export points
    # ---------------------------------------------------------------
    PortCandidate("Beaumont_US", ("BEAUMONT",), "grain", "atlantic", "USA"),
    PortCandidate("Lake_Charles_US", ("LAKE CHARLES",), "grain", "atlantic", "USA"),
    PortCandidate("Corpus_Christi_US", ("CORPUS CHRISTI",), "grain", "atlantic", "USA"),

    # ---------------------------------------------------------------
    # More NW Europe -- additional import hubs
    # ---------------------------------------------------------------
    PortCandidate("Antwerp_BE", ("ANTWERP",), "import_hub", "atlantic", "BEL"),
    PortCandidate("Bremen_DE", ("BREMEN", "BREMERHAVEN"), "import_hub", "atlantic", "DEU"),
    PortCandidate("Gdansk_PL", ("GDANSK",), "import_hub", "atlantic", "POL"),
)
