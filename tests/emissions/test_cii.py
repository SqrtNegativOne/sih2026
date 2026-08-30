"""Real-arithmetic tests for emissions.cii against the IMO resolutions its
constants come from -- see that module's own docstring for citations."""
from __future__ import annotations

import pytest

from emissions import cii


def test_co2_tonnes_uses_the_real_hfo_conversion_factor():
    # MEPC.308(73) Annex para 2.2.1: HFO CF = 3.114 t-CO2/t-fuel.
    assert cii.co2_tonnes(100.0, "HFO") == pytest.approx(311.4)


def test_co2_tonnes_is_case_insensitive_and_supports_every_listed_fuel():
    for fuel, cf in cii.FUEL_CF_T_CO2_PER_T_FUEL.items():
        assert cii.co2_tonnes(10.0, fuel.lower()) == pytest.approx(10.0 * cf)


def test_co2_tonnes_rejects_an_unlisted_fuel_rather_than_guessing():
    with pytest.raises(ValueError):
        cii.co2_tonnes(100.0, "BIODIESEL")


def test_bulk_carrier_reference_line_caps_capacity_at_279000_dwt():
    # MEPC.353(78) Table 1: ships >= 279,000 DWT use 279,000 as Capacity.
    at_cap = cii.reference_cii_bulk_carrier(279_000.0)
    above_cap = cii.reference_cii_bulk_carrier(400_000.0)
    assert above_cap == pytest.approx(at_cap)


def test_required_cii_bulk_carrier_raises_for_an_unpublished_year():
    # MEPC.338(76) Table 1 only publishes Z for 2023-2026.
    with pytest.raises(cii.CIIYearNotPublishedError):
        cii.required_cii_bulk_carrier(82_000.0, 2027)
    with pytest.raises(cii.CIIYearNotPublishedError):
        cii.required_cii_bulk_carrier(82_000.0, 2022)


def test_rating_boundaries_match_the_guidelines_own_worked_example():
    # MEPC.354(78) Annex, Table 1 discussion: required=10 -> boundaries
    # 8.6/9.4/10.6/11.8; attained=9 -> "B".
    required = 10.0
    assert cii.rating_bulk_carrier(8.6, required) == "A"
    assert cii.rating_bulk_carrier(9.0, required) == "B"
    # Boundary values themselves are avoided (9.4/10.0 != 0.94 in binary
    # floating point, an artifact of the division, not the rating logic) --
    # test just inside/outside each boundary instead.
    assert cii.rating_bulk_carrier(9.39, required) == "B"
    assert cii.rating_bulk_carrier(9.41, required) == "C"
    assert cii.rating_bulk_carrier(10.59, required) == "C"
    assert cii.rating_bulk_carrier(10.61, required) == "D"
    assert cii.rating_bulk_carrier(11.79, required) == "D"
    assert cii.rating_bulk_carrier(11.81, required) == "E"
