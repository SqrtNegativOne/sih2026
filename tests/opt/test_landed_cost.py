"""P6 landed-cost breakdown: component-wise; None not 0 for unavailable;
user assumptions labelled DECLARED; arithmetic checked against a fixture;
no hardcoded commercial term."""
from __future__ import annotations

from datetime import UTC, date, datetime

import polars as pl
import pytest

from berth_truth.fact_port_call import FactPortCall, FactPortCallStore
from berth_truth.sources import SourceQuality
from data_builders.provenance import Provenance
from opt.landed_cost import COMMODITY_SERIES, LandedCostRequest, compute_landed_cost
from opt.network import PortEnum


def _row(*, sha: str, idx: int = 0, **overrides) -> FactPortCall:
    defaults = {
        "port": PortEnum.PARADIP,
        "vessel_name": "TEST VESSEL",
        "source_url": "http://example.test",
        "source_quality": SourceQuality.OFFICIAL_PORT_AUTHORITY,
        "retrieved_at": datetime(2026, 8, 28, 12, 0, 0, tzinfo=UTC),
        "content_sha256": sha,
        "row_index": idx,
        "parser_version": "test/1",
    }
    defaults.update(overrides)
    return FactPortCall(**defaults)


def _macro_fixture() -> pl.DataFrame:
    """A tiny, controlled macro_long-shaped frame -- isolates commodity/FX
    tests from whatever the real, moving macro_long.parquet says today."""
    return pl.DataFrame(
        {
            "series_id": ["MACRO_IRON_ORE", "MACRO_COAL_AUSTRALIAN", "MACRO_USD_INR"],
            "date": [date(2026, 7, 1), date(2026, 7, 1), date(2026, 7, 15)],
            "value": [100.0, 150.0, 90.0],
            "unit": ["usd_per_mt", "usd_per_mt", "inr_per_usd"],
            "source": ["worldbank_pink_sheet", "worldbank_pink_sheet", "fred"],
        }
    )


class TestNoHardcodedCommercialTerm:
    """Structural check: the request's commercial-term fields have no
    default other than None/False -- nothing this module could silently
    fall back to."""

    def test_handling_demurrage_laytime_default_to_none(self) -> None:
        req = LandedCostRequest(
            dest_port=PortEnum.PARADIP, cargo_volume_mt=75_000,
            freight_usd_per_day=15_000, voyage_days=25,
        )
        assert req.handling_rate_usd_per_mt is None
        assert req.demurrage_usd_per_day is None
        assert req.laytime_allowance_days is None
        assert req.commodity is None
        assert req.convert_to_inr is False


class TestComponentWiseAvailabilityIsNoneNotZero:
    def test_handling_and_demurrage_are_none_when_not_supplied(self, tmp_path) -> None:
        store = FactPortCallStore(path=tmp_path / "fact_port_call.jsonl")
        req = LandedCostRequest(
            dest_port=PortEnum.PARADIP, cargo_volume_mt=75_000,
            freight_usd_per_day=15_000, voyage_days=25,
        )
        result = compute_landed_cost(req, store=store, macro_long=_macro_fixture())
        assert result.handling_cost_usd_per_mt is None
        assert result.demurrage_cost_usd_per_mt is None
        assert "handling_cost" in result.components_missing
        assert "demurrage_cost" in result.components_missing
        assert "handling_cost" not in result.components_included

    def test_wait_cost_is_none_below_the_real_sufficiency_threshold(self, tmp_path) -> None:
        store = FactPortCallStore(path=tmp_path / "fact_port_call.jsonl")
        store.append_many([
            _row(sha="a", arrival_ts=datetime(2026, 1, 1, 0), berth_ts=datetime(2026, 1, 1, 6)),
        ])
        req = LandedCostRequest(
            dest_port=PortEnum.PARADIP, cargo_volume_mt=75_000,
            freight_usd_per_day=15_000, voyage_days=25,
        )
        result = compute_landed_cost(req, store=store, macro_long=_macro_fixture())
        assert result.wait_cost_usd_per_mt is None
        assert "insufficient empirical sample" in result.wait_cost_reason
        assert "wait_cost" in result.components_missing

    def test_commodity_price_is_none_for_an_unknown_commodity(self, tmp_path) -> None:
        store = FactPortCallStore(path=tmp_path / "fact_port_call.jsonl")
        req = LandedCostRequest(
            dest_port=PortEnum.PARADIP, cargo_volume_mt=75_000,
            freight_usd_per_day=15_000, voyage_days=25, commodity="limestone",
        )
        result = compute_landed_cost(req, store=store, macro_long=_macro_fixture())
        assert result.commodity_price_usd_per_mt is None
        assert "unknown commodity" in result.commodity_price_reason


class TestUserSuppliedTermsAreLabelledDeclared:
    def test_handling_rate_is_tagged_declared(self, tmp_path) -> None:
        store = FactPortCallStore(path=tmp_path / "fact_port_call.jsonl")
        req = LandedCostRequest(
            dest_port=PortEnum.PARADIP, cargo_volume_mt=75_000,
            freight_usd_per_day=15_000, voyage_days=25, handling_rate_usd_per_mt=3.5,
        )
        result = compute_landed_cost(req, store=store, macro_long=_macro_fixture())
        assert result.handling_cost_usd_per_mt == 3.5
        assert result.handling_cost_provenance == Provenance.DECLARED

    def test_demurrage_requires_both_rate_and_allowance(self, tmp_path) -> None:
        store = FactPortCallStore(path=tmp_path / "fact_port_call.jsonl")
        req = LandedCostRequest(
            dest_port=PortEnum.PARADIP, cargo_volume_mt=75_000,
            freight_usd_per_day=15_000, voyage_days=25,
            demurrage_usd_per_day=8_000,  # laytime_allowance_days omitted
        )
        result = compute_landed_cost(req, store=store, macro_long=_macro_fixture())
        assert result.demurrage_cost_usd_per_mt is None
        assert "only one was supplied" in result.demurrage_cost_reason

    def test_commodity_price_from_real_data_is_tagged_observed_not_declared(self) -> None:
        """The published market price is real, external, observed data --
        distinct from the DECLARED commercial terms the user types in."""
        req = LandedCostRequest(
            dest_port=PortEnum.PARADIP, cargo_volume_mt=75_000,
            freight_usd_per_day=15_000, voyage_days=25, commodity="coal", as_of=date(2026, 8, 1),
        )
        result = compute_landed_cost(req)
        assert result.commodity_price_provenance == Provenance.OBSERVED


class TestArithmeticOnAFixture:
    """Hand-computable numbers, no real-data dependency -- pins the exact
    formula, not just "a number came back"."""

    def test_freight_wait_handling_demurrage_arithmetic(self, tmp_path) -> None:
        store = FactPortCallStore(path=tmp_path / "fact_port_call.jsonl")
        # 30 real vessels, each arrival->berth = exactly 48h -- a clean,
        # sufficient (>= MINIMUM_SAMPLE_SIZE=20), zero-variance sample so
        # p50_hours is exactly 48.0, not an estimate to re-derive.
        rows = [
            _row(
                sha=f"v{i}", vessel_name=f"VESSEL {i}",
                arrival_ts=datetime(2026, 1, 1, 0),
                berth_ts=datetime(2026, 1, 3, 0),  # exactly 48h later
            )
            for i in range(30)
        ]
        store.append_many(rows)

        req = LandedCostRequest(
            dest_port=PortEnum.PARADIP, cargo_volume_mt=100_000,
            freight_usd_per_day=20_000, voyage_days=10,
            opex_usd_per_day=6_000,
            handling_rate_usd_per_mt=2.0,
            demurrage_usd_per_day=10_000, laytime_allowance_days=1.0,  # 2.0d actual - 1.0d allowance = 1.0d exposure
        )
        result = compute_landed_cost(req, store=store, macro_long=_macro_fixture())

        expected_freight = (20_000 * 10) / 100_000  # 2.0
        assert result.freight_usd_per_mt == pytest.approx(expected_freight)

        assert result.wait_p50_hours == pytest.approx(48.0)
        expected_wait_cost = (2.0 * 6_000) / 100_000  # 2 days x opex / cargo
        assert result.wait_cost_usd_per_mt == pytest.approx(expected_wait_cost)

        assert result.handling_cost_usd_per_mt == 2.0

        expected_demurrage = (1.0 * 10_000) / 100_000  # 1 exposure day x rate / cargo
        assert result.demurrage_cost_usd_per_mt == pytest.approx(expected_demurrage)

        expected_total = expected_freight + expected_wait_cost + 2.0 + expected_demurrage
        assert result.partial_total_usd_per_mt == pytest.approx(expected_total)
        assert set(result.components_included) == {"freight", "wait_cost", "handling_cost", "demurrage_cost"}
        # war_risk joins commodity_price as legitimately missing here: this
        # fixture supplies neither an origin port nor a hull value, so there
        # is no route to resolve Listed Areas against and no hull to price.
        assert result.components_missing == ("war_risk", "commodity_price")

    def test_partial_total_sums_exactly_the_included_components(self, tmp_path) -> None:
        store = FactPortCallStore(path=tmp_path / "fact_port_call.jsonl")
        req = LandedCostRequest(
            dest_port=PortEnum.PARADIP, cargo_volume_mt=50_000,
            freight_usd_per_day=12_000, voyage_days=8, commodity="iron_ore", as_of=date(2026, 8, 1),
        )
        result = compute_landed_cost(req, store=store, macro_long=_macro_fixture())
        manual_sum = sum(
            v for v in (
                result.freight_usd_per_mt, result.wait_cost_usd_per_mt, result.handling_cost_usd_per_mt,
                result.demurrage_cost_usd_per_mt, result.commodity_price_usd_per_mt,
            ) if v is not None
        )
        assert result.partial_total_usd_per_mt == pytest.approx(manual_sum)

    def test_fx_conversion_multiplies_the_total_not_summed_as_a_cost(self, tmp_path) -> None:
        store = FactPortCallStore(path=tmp_path / "fact_port_call.jsonl")
        req = LandedCostRequest(
            dest_port=PortEnum.PARADIP, cargo_volume_mt=50_000,
            freight_usd_per_day=12_000, voyage_days=8, convert_to_inr=True,
        )
        result = compute_landed_cost(req, store=store, macro_long=_macro_fixture())
        assert result.fx_inr_per_usd == 90.0
        assert result.fx_provenance == Provenance.OBSERVED
        assert result.partial_total_inr_per_mt == pytest.approx(result.partial_total_usd_per_mt * 90.0)

    def test_fx_is_none_when_not_requested(self, tmp_path) -> None:
        store = FactPortCallStore(path=tmp_path / "fact_port_call.jsonl")
        req = LandedCostRequest(
            dest_port=PortEnum.PARADIP, cargo_volume_mt=50_000,
            freight_usd_per_day=12_000, voyage_days=8, convert_to_inr=False,
        )
        result = compute_landed_cost(req, store=store, macro_long=_macro_fixture())
        assert result.fx_inr_per_usd is None
        assert result.partial_total_inr_per_mt is None


class TestCommodityUnitCorrectness:
    """Regression pin for the real P6 unit-mislabel fix: both commodities
    resolve directly as usd_per_mt, no DMTU-style multiplication."""

    def test_both_commodities_map_to_a_series_id(self) -> None:
        assert COMMODITY_SERIES["iron_ore"] == "MACRO_IRON_ORE"
        assert COMMODITY_SERIES["coal"] == "MACRO_COAL_AUSTRALIAN"

    def test_iron_ore_price_is_the_raw_value_not_multiplied_by_anything(self, tmp_path) -> None:
        store = FactPortCallStore(path=tmp_path / "fact_port_call.jsonl")
        req = LandedCostRequest(
            dest_port=PortEnum.PARADIP, cargo_volume_mt=50_000,
            freight_usd_per_day=12_000, voyage_days=8, commodity="iron_ore",
        )
        result = compute_landed_cost(req, store=store, macro_long=_macro_fixture())
        assert result.commodity_price_usd_per_mt == 100.0  # exactly the fixture's raw value
        assert result.commodity_price_raw_unit == "usd_per_mt"

    def test_real_macro_long_parquet_has_no_dmtu_unit_today(self) -> None:
        """Confirms the on-disk artifact was actually rebuilt after the P6
        fix, not just the code changed with a stale parquet left behind."""
        from opt.landed_cost import MACRO_LONG_PATH

        real = pl.read_parquet(MACRO_LONG_PATH)
        iron_ore_units = real.filter(pl.col("series_id") == "MACRO_IRON_ORE")["unit"].unique().to_list()
        assert iron_ore_units == ["usd_per_mt"]


class TestWarRiskPremiumLineItem:
    """The war-risk premium is its own visible line, never folded into
    freight, and never a fabricated zero when hull value is unknown."""

    def _req(self, tmp_path, **overrides) -> LandedCostRequest:
        base = {
            "dest_port": PortEnum.VIZAG, "cargo_volume_mt": 75_000,
            "freight_usd_per_day": 20_000, "voyage_days": 31,
        }
        base.update(overrides)
        return LandedCostRequest(**base)

    def test_defaults_to_none_so_nothing_is_ever_silently_assumed(self, tmp_path) -> None:
        req = self._req(tmp_path)
        assert req.origin_port is None
        assert req.hull_value_usd is None
        assert req.war_risk_rate_pct_per_7_days is None

    def test_no_hull_value_shows_no_line_rather_than_a_zero_line(self, tmp_path) -> None:
        """The named acceptance case: with no hull value the component is
        None and listed as MISSING -- never 0.0 folded into the total."""
        store = FactPortCallStore(path=tmp_path / "fact_port_call.jsonl")
        req = self._req(tmp_path, origin_port=PortEnum.HAMPTON_ROADS)
        result = compute_landed_cost(req, store=store, macro_long=_macro_fixture())

        assert result.war_risk_usd_per_mt is None
        assert result.war_risk_premium_usd is None
        assert "war_risk" in result.components_missing
        assert "war_risk" not in result.components_included
        assert "hull_value_usd" in result.war_risk_reason
        # It still reports the real areas the route enters -- the gap is the
        # hull value, not the geography.
        assert "gulf_of_aden" in result.war_risk_areas

    def test_no_origin_port_means_the_route_cannot_be_resolved(self, tmp_path) -> None:
        store = FactPortCallStore(path=tmp_path / "fact_port_call.jsonl")
        result = compute_landed_cost(
            self._req(tmp_path, hull_value_usd=45_000_000.0), store=store, macro_long=_macro_fixture()
        )
        assert result.war_risk_usd_per_mt is None
        assert result.war_risk_areas == ()
        assert "origin_port" in result.war_risk_reason

    def test_a_route_through_no_listed_area_owes_nothing_and_says_why(self, tmp_path) -> None:
        store = FactPortCallStore(path=tmp_path / "fact_port_call.jsonl")
        req = self._req(
            tmp_path, dest_port=PortEnum.PARADIP,
            origin_port=PortEnum.NEWCASTLE_AU, hull_value_usd=45_000_000.0,
        )
        result = compute_landed_cost(req, store=store, macro_long=_macro_fixture())

        assert result.war_risk_usd_per_mt is None
        assert result.war_risk_areas == ()
        assert "no Joint War Committee Listed Area" in result.war_risk_reason

    def test_a_real_exposure_produces_its_own_line_and_enters_the_total(self, tmp_path) -> None:
        store = FactPortCallStore(path=tmp_path / "fact_port_call.jsonl")
        req = self._req(
            tmp_path, origin_port=PortEnum.HAMPTON_ROADS, hull_value_usd=45_000_000.0,
        )
        result = compute_landed_cost(req, store=store, macro_long=_macro_fixture())

        assert result.war_risk_usd_per_mt is not None
        assert "war_risk" in result.components_included
        # ceil(31/7) = 5 periods at the module's own placeholder rate.
        expected_total = 45_000_000.0 * (0.4 / 100.0) * 5
        assert result.war_risk_premium_usd == pytest.approx(expected_total)
        assert result.war_risk_usd_per_mt == pytest.approx(expected_total / 75_000)
        assert result.war_risk_provenance is Provenance.ESTIMATED
        assert result.war_risk_rate_is_caller_supplied is False
        # It is a SEPARATE line -- freight is untouched by it.
        assert result.freight_usd_per_mt == pytest.approx(20_000 * 31 / 75_000)
        assert result.partial_total_usd_per_mt == pytest.approx(
            result.freight_usd_per_mt + result.war_risk_usd_per_mt
        )

    def test_a_caller_supplied_rate_replaces_the_placeholder(self, tmp_path) -> None:
        store = FactPortCallStore(path=tmp_path / "fact_port_call.jsonl")
        req = self._req(
            tmp_path, origin_port=PortEnum.HAMPTON_ROADS, hull_value_usd=45_000_000.0,
            war_risk_rate_pct_per_7_days=1.0,
        )
        result = compute_landed_cost(req, store=store, macro_long=_macro_fixture())

        assert result.war_risk_rate_pct_per_7_days == 1.0
        assert result.war_risk_rate_is_caller_supplied is True
        assert result.war_risk_premium_usd == pytest.approx(45_000_000.0 * 0.01 * 5)
