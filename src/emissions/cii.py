"""IMO Carbon Intensity Indicator (CII) arithmetic for bulk carriers, 2023-2026.

Every constant below is taken directly from the primary IMO resolutions (not a
third-party summary), fetched and read section-by-section while writing this
module:

- Fuel-to-CO2 conversion factors (``FUEL_CF_T_CO2_PER_T_FUEL``): Resolution
  MEPC.308(73), *2018 Guidelines on the method of calculation of the attained
  Energy Efficiency Design Index (EEDI) for new ships*, Annex, para 2.2.1,
  the "CF (t-CO2/t-Fuel)" table. Resolution MEPC.352(78) para 4.1 (below)
  reuses this same table for CII, "in line with" the EEDI guidelines.
- Attained CII (AER) formula: Resolution MEPC.352(78), *2022 Guidelines on
  operational carbon intensity indicators and the calculation methods (CII
  Guidelines, G1)*, Annex, paras 4.1-4.2: attained CII = M / W, where M is
  total CO2 mass in GRAMS and W is DWT x nautical miles sailed (the
  "supply-based" transport work proxy; DWT is the correct capacity basis for
  bulk carriers per para 4.2).
- Bulk carrier reference line (``_BULK_CARRIER_REF_A/_C``, DWT cap):
  Resolution MEPC.353(78), *2022 Guidelines on the reference lines for use
  with operational carbon intensity indicators (CII Reference Lines
  Guidelines, G2)*, Annex, Table 1: CIIref = a * Capacity^-c, a=4745, c=0.622,
  capacity capped at 279,000 DWT for ships at or above that deadweight.
- Annual reduction factor Z and the required-CII formula: Resolution
  MEPC.338(76), *2021 Guidelines on the operational carbon intensity
  reduction factors relative to reference lines (CII Reduction Factors
  Guidelines, G3)*, Annex, para 4.1 and Table 1. Only 2023-2026 are
  published there (5%, 7%, 9%, 11%); 2020-2022 used separate flat
  business-as-usual factors not implemented here, and 2027-2030 were left
  "to be further strengthened" as of that guideline -- see
  ``CIIYearNotPublishedError``.
- Bulk carrier rating boundaries (``_BULK_CARRIER_DD``): Resolution
  MEPC.354(78), *2022 Guidelines on the operational carbon intensity rating
  of ships (CII Rating Guidelines, G4)*, Annex, Table 1 (dd-vector after
  exponential transformation) and its worked example, which this module's
  own tests reproduce exactly (required=10 -> boundaries 8.6/9.4/10.6/11.8,
  attained=9 -> "B").

This module implements only the bulk-carrier row of each table above -- the
only ship type this fleet optimizer prices. It does NOT implement EEDI/EEXI
(design-stage indices, not this operational one), any other ship type's
reference line or dd-vector, or years outside 2023-2026.
"""
from __future__ import annotations

from typing import Final, Literal

CarbonRating = Literal["A", "B", "C", "D", "E"]

__all__ = [
    "FUEL_CF_T_CO2_PER_T_FUEL",
    "CIIYearNotPublishedError",
    "attained_cii",
    "co2_tonnes",
    "rating_bulk_carrier",
    "reference_cii_bulk_carrier",
    "required_cii_bulk_carrier",
]


class CIIYearNotPublishedError(Exception):
    """Raised by ``required_cii_bulk_carrier`` for a year outside 2023-2026.

    The IMO has published a reduction factor Z (MEPC.338(76) Table 1) only
    for 2023 through 2026. Years 2020-2022 used separate flat
    business-as-usual factors this module does not implement, and 2027-2030
    were left "to be further strengthened" as of that guideline. Guessing an
    extrapolated value for either range would be exactly the fabricated
    number CLAUDE.md's non-negotiable #1 forbids -- callers must catch this
    and degrade to "no rating," not invent one.
    """


#: t-CO2 emitted per tonne of fuel burnt, MEPC.308(73) Annex para 2.2.1.
FUEL_CF_T_CO2_PER_T_FUEL: Final[dict[str, float]] = {
    "MDO": 3.206,  # Diesel/Gas Oil, ISO 8217 Grades DMX through DMB
    "LFO": 3.151,  # Light Fuel Oil, ISO 8217 Grades RMA through RMD
    "HFO": 3.114,  # Heavy Fuel Oil, ISO 8217 Grades RME through RMK
    "LPG_PROPANE": 3.000,
    "LPG_BUTANE": 3.030,
    "LNG": 2.750,
    "METHANOL": 1.375,
    "ETHANOL": 1.913,
}

#: Bulk carrier reference-line parameters, MEPC.353(78) Table 1: CIIref (in
#: gCO2/dwt-nm) = a * Capacity^-c, Capacity = DWT capped at 279,000.
_BULK_CARRIER_REF_A: Final[float] = 4745.0
_BULK_CARRIER_REF_C: Final[float] = 0.622
_BULK_CARRIER_CAPACITY_CAP_DWT: Final[float] = 279_000.0

#: Annual reduction factor Z (%), MEPC.338(76) Table 1 -- only years actually
#: published there.
_Z_REDUCTION_FACTOR_PCT: Final[dict[int, float]] = {
    2023: 5.0,
    2024: 7.0,
    2025: 9.0,
    2026: 11.0,
}

#: Bulk carrier dd-vector after exponential transformation, MEPC.354(78)
#: Table 1: the multiple of required CII each rating boundary sits at.
#: attained/required <= this value's own key's threshold assigns that
#: rating -- see rating_bulk_carrier.
_BULK_CARRIER_DD: Final[dict[CarbonRating, float]] = {
    "A": 0.86,
    "B": 0.94,
    "C": 1.06,
    "D": 1.18,
}


def co2_tonnes(fuel_tonnes: float, fuel_type: str = "HFO") -> float:
    """CO2 emitted (tonnes) for a given fuel burn, MEPC.308(73) para 2.2.1 /
    MEPC.352(78) para 4.1: mass of CO2 = fuel consumed x CF.

    Raises
    ------
    ValueError
        ``fuel_type`` is not one of ``FUEL_CF_T_CO2_PER_T_FUEL`` -- there is
        no honest conversion factor to fall back to for an unlisted fuel; the
        guidelines themselves say to obtain one from the fuel supplier in
        that case, which this module cannot do.
    """
    try:
        cf = FUEL_CF_T_CO2_PER_T_FUEL[fuel_type.upper()]
    except KeyError:
        raise ValueError(
            f"Unknown fuel_type {fuel_type!r}; known: {sorted(FUEL_CF_T_CO2_PER_T_FUEL)}"
        ) from None
    return fuel_tonnes * cf


def _bulk_carrier_capacity_dwt(dwt: float) -> float:
    return min(dwt, _BULK_CARRIER_CAPACITY_CAP_DWT)


def reference_cii_bulk_carrier(dwt: float) -> float:
    """2019 reference-line CII (gCO2/dwt-nm) for a bulk carrier of this DWT,
    MEPC.353(78) Table 1: CIIref = a * Capacity^-c."""
    capacity = _bulk_carrier_capacity_dwt(dwt)
    return _BULK_CARRIER_REF_A * capacity ** (-_BULK_CARRIER_REF_C)


def required_cii_bulk_carrier(dwt: float, year: int) -> float:
    """Required annual operational CII (gCO2/dwt-nm), MEPC.338(76) para 4.1:
    (1 - Z/100) * CIIref.

    Raises
    ------
    CIIYearNotPublishedError
        ``year`` is outside 2023-2026 -- see that class's docstring.
    """
    try:
        z_pct = _Z_REDUCTION_FACTOR_PCT[year]
    except KeyError:
        raise CIIYearNotPublishedError(
            f"IMO has not published a CII reduction factor for {year}; only "
            f"{sorted(_Z_REDUCTION_FACTOR_PCT)} exist (MEPC.338(76) Table 1)."
        ) from None
    return (1.0 - z_pct / 100.0) * reference_cii_bulk_carrier(dwt)


def attained_cii(co2_tonnes_value: float, dwt: float, distance_nm: float) -> float:
    """Attained operational CII on the AER (DWT) basis, in gCO2/dwt-nm,
    MEPC.352(78) para 4: M / W.

    ``M`` (mass of CO2) is defined in grams and ``W`` (transport work) in
    dwt-nautical-miles, per the guideline -- both this function's inputs are
    in the more natural tonnes/DWT/nm units the rest of this codebase uses,
    so the CO2 mass is converted tonnes -> grams internally (x1,000,000) to
    land on the same gCO2/dwt-nm scale the reference line and rating
    boundaries above are calibrated to. Skipping that conversion silently
    would produce a number a million times too small and every rating would
    read as "A."
    """
    co2_grams = co2_tonnes_value * 1_000_000.0
    transport_work_dwt_nm = dwt * distance_nm
    return co2_grams / transport_work_dwt_nm


def rating_bulk_carrier(attained_cii_value: float, required_cii_value: float) -> CarbonRating:
    """A-E rating for a bulk carrier, MEPC.354(78) Table 1 dd-vector: rate by
    where attained/required lands relative to the four boundary multiples.

    Verified against the guideline's own worked example: required=10,
    attained=9 -> "B" (boundaries 8.6/9.4/10.6/11.8).
    """
    ratio = attained_cii_value / required_cii_value
    if ratio <= _BULK_CARRIER_DD["A"]:
        return "A"
    if ratio <= _BULK_CARRIER_DD["B"]:
        return "B"
    if ratio <= _BULK_CARRIER_DD["C"]:
        return "C"
    if ratio <= _BULK_CARRIER_DD["D"]:
        return "D"
    return "E"
