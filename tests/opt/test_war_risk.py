"""Joint War Committee Listed Areas and the additional war-risk premium.

Nothing here asserts that the placeholder premium rate is *correct* -- it is
explicitly an assumption (see ``opt.war_risk``'s module docstring), so these
tests pin its arithmetic, its disclosure, and its overridability instead.
"""
from __future__ import annotations

from datetime import date

import pytest

from data_builders.provenance import Provenance
from opt.network import PortEnum
from opt.war_risk import (
    DEFAULT_RATE_PCT_PER_7_DAYS,
    LISTED_AREAS,
    listed_areas_on_polyline,
    listed_areas_on_route,
    war_risk_premium_usd,
)


class TestListedAreaTable:
    def test_every_area_carries_a_source_and_a_review_date(self):
        assert LISTED_AREAS, "the listed-area table must not be empty"
        for area in LISTED_AREAS:
            assert area.source_url.startswith("https://"), area.area_id
            assert isinstance(area.last_reviewed, date), area.area_id
            assert area.note, f"{area.area_id} must say what its envelope approximates"

    def test_area_ids_are_unique_and_boxes_are_well_formed(self):
        ids = [a.area_id for a in LISTED_AREAS]
        assert len(ids) == len(set(ids))
        for area in LISTED_AREAS:
            assert area.lat_min < area.lat_max, area.area_id
            assert area.lon_min < area.lon_max, area.area_id

    def test_bosporus_is_deliberately_outside_the_black_sea_envelope(self):
        """The circular's Black Sea/Azov polygon runs from the Ukraine-Romania
        border to the Russia-Georgia border and does not reach Turkish waters.
        The Bosporus (41.17N, 29.09E) must therefore NOT be listed -- this is
        the single easiest way to get this table wrong."""
        black_sea = next(a for a in LISTED_AREAS if a.area_id == "black_sea_azov")
        assert not black_sea.contains(29.09, 41.17)

    def test_hormuz_and_bab_el_mandeb_are_inside_their_envelopes(self):
        gulf_of_oman = next(a for a in LISTED_AREAS if a.area_id == "gulf_of_oman")
        gulf_of_aden = next(a for a in LISTED_AREAS if a.area_id == "gulf_of_aden")
        assert gulf_of_oman.contains(56.86, 26.30)  # Strait of Hormuz
        assert gulf_of_aden.contains(43.35, 12.79)  # Bab el-Mandeb


class TestListedAreasOnRoute:
    def test_a_route_through_a_listed_area_returns_it(self):
        """Hampton Roads -> Vizag is a real Suez/Bab el-Mandeb routing, so it
        must enter the Southern Red Sea and Gulf of Aden areas."""
        areas = listed_areas_on_route(PortEnum.HAMPTON_ROADS, PortEnum.VIZAG)
        assert "southern_red_sea" in areas
        assert "gulf_of_aden" in areas

    def test_a_route_that_avoids_every_listed_area_returns_empty(self):
        """Newcastle -> Paradip runs through the Torres/Ombai/Malacca chain,
        nowhere near a listed area."""
        assert listed_areas_on_route(PortEnum.NEWCASTLE_AU, PortEnum.PARADIP) == ()

    def test_a_degenerate_polyline_has_no_segments_and_returns_empty(self):
        assert listed_areas_on_polyline([(56.86, 26.30)]) == ()
        assert listed_areas_on_polyline([]) == ()

    def test_a_segment_crossing_a_box_is_caught_even_with_both_ends_outside(self):
        """The densification test's whole point: a coarse two-point segment
        whose endpoints both sit outside an envelope but whose path crosses
        it must still be detected. Straddles the Gulf of Oman box
        (lat 22.5-27.5, lon 55.0-61.5) west-to-east."""
        polyline = [(50.0, 25.0), (65.0, 25.0)]
        assert "gulf_of_oman" in listed_areas_on_polyline(polyline)

    def test_areas_are_returned_in_the_order_the_route_meets_them(self):
        areas = listed_areas_on_route(PortEnum.HAMPTON_ROADS, PortEnum.VIZAG)
        # Sailing west to east, the Red Sea is entered before the Gulf of Aden.
        assert areas.index("southern_red_sea") < areas.index("gulf_of_aden")


class TestWarRiskPremium:
    def test_no_hull_value_returns_none_never_an_assumed_hull(self):
        assert war_risk_premium_usd(None, ("gulf_of_aden",), 31.0) is None

    def test_a_non_positive_hull_value_also_returns_none(self):
        assert war_risk_premium_usd(0.0, ("gulf_of_aden",), 31.0) is None

    def test_no_listed_areas_means_no_premium_is_owed(self):
        assert war_risk_premium_usd(45_000_000.0, (), 31.0) is None

    def test_premium_is_charged_in_whole_seven_day_periods(self):
        """31 transit days is 5 periods (ceil(31/7)), not 4.43."""
        result = war_risk_premium_usd(45_000_000.0, ("gulf_of_aden",), 31.0)
        assert result is not None
        assert result.periods_charged == 5
        expected = 45_000_000.0 * (DEFAULT_RATE_PCT_PER_7_DAYS / 100.0) * 5
        assert result.premium_usd == pytest.approx(expected)

    def test_a_transit_shorter_than_one_period_still_charges_one(self):
        result = war_risk_premium_usd(45_000_000.0, ("gulf_of_aden",), 2.0)
        assert result is not None
        assert result.periods_charged == 1

    def test_multiple_areas_are_charged_once_not_additively(self):
        one = war_risk_premium_usd(45_000_000.0, ("gulf_of_aden",), 14.0)
        three = war_risk_premium_usd(
            45_000_000.0, ("southern_red_sea", "gulf_of_aden", "gulf_of_oman"), 14.0
        )
        assert one is not None and three is not None
        assert three.premium_usd == pytest.approx(one.premium_usd)
        assert three.areas == ("southern_red_sea", "gulf_of_aden", "gulf_of_oman")

    def test_a_caller_supplied_rate_replaces_the_placeholder_and_says_so(self):
        result = war_risk_premium_usd(
            45_000_000.0, ("gulf_of_aden",), 7.0, rate_pct_per_7_days=1.25
        )
        assert result is not None
        assert result.rate_pct_per_7_days == 1.25
        assert result.rate_is_caller_supplied is True
        assert result.premium_usd == pytest.approx(45_000_000.0 * 0.0125)
        assert "NOT A MARKET QUOTE" not in result.basis

    def test_the_default_rate_is_disclosed_as_an_estimate_not_a_quote(self):
        result = war_risk_premium_usd(45_000_000.0, ("gulf_of_aden",), 7.0)
        assert result is not None
        assert result.rate_is_caller_supplied is False
        assert result.provenance is Provenance.ESTIMATED
        assert "NOT A MARKET QUOTE" in result.basis
        assert "negotiated per fixture" in result.basis

    def test_a_non_positive_transit_is_a_caller_error_not_a_silent_zero(self):
        with pytest.raises(ValueError, match="transit_days must be positive"):
            war_risk_premium_usd(45_000_000.0, ("gulf_of_aden",), 0.0)
