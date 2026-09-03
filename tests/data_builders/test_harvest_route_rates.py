"""Route-level charter rates, and the discipline about which lanes count.

This harvester exists to give `opt.basis` the one thing it has always lacked:
route-level evidence denominated in dollars per day. The risk it carries is the
mirror image of that value -- a lane attached to the wrong route family would
move a real recommendation using a rate that was never quoted for it.

So most of what is tested here is refusal. The sharpest case is West Coast
India: every RouteFamily in this project is anchored on the EAST coast, the
source publishes South Africa to WCI lanes, and counting them would look
entirely reasonable and be quietly wrong.
"""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

import polars as pl
import pytest

from data_builders import harvest_route_rates as hr
from opt.network import RouteFamily

FIXTURE = Path(__file__).parent / "fixtures" / "handybulk_route_rates_sample.html"


@pytest.fixture
def page() -> str:
    return FIXTURE.read_text(encoding="utf-8")


@pytest.fixture
def parsed(page: str) -> tuple[date | None, list[hr.RouteQuote]]:
    return hr.parse_page(page)


class TestParsing:
    def test_the_page_date_is_the_publication_date_not_the_clock(
        self, parsed: tuple[date | None, list]
    ) -> None:
        """A quote harvested late still belongs to the day it was published."""
        quoted_on, quotes = parsed
        assert quoted_on == date(2026, 9, 3)
        assert all(q.quoted_on == date(2026, 9, 3) for q in quotes)

    def test_it_reads_every_lane_in_the_slice(self, parsed: tuple[date | None, list]) -> None:
        _d, quotes = parsed
        assert len(quotes) == 77

    def test_rates_are_dollars_per_day(self, parsed: tuple[date | None, list]) -> None:
        """The whole point: `opt.basis` cannot use a $/tonne observation, which
        is why the three Signal route points already on disk sit unused."""
        _d, quotes = parsed
        assert all(1_000 < q.usd_per_day < 200_000 for q in quotes)

    def test_ultramax_is_recorded_as_supramax(self, parsed: tuple[date | None, list]) -> None:
        """Ultramax is a real size this project does not model separately, and
        the market quotes the two together. Following the Baltic's own BSI
        convention beats inventing a fifth class."""
        _d, quotes = parsed
        assert {q.vessel_class for q in quotes} <= {
            "HANDYSIZE",
            "SUPRAMAX",
            "PANAMAX",
            "CAPESIZE",
        }
        assert any(q.vessel_class == "SUPRAMAX" for q in quotes)


class TestOnlyTheRightLanesCount:
    def test_the_indonesia_lane_maps(self, parsed: tuple[date | None, list]) -> None:
        _d, quotes = parsed
        indonesia = [q for q in quotes if q.route_family is RouteFamily.INDONESIA_EC_INDIA]
        assert indonesia
        assert all("India" in q.dest_text for q in indonesia)
        assert all(q.series_id == "HB_SUPRAMAX_INDONESIA_ECI_USD_DAY" for q in indonesia)

    def test_west_coast_india_never_maps(self, parsed: tuple[date | None, list]) -> None:
        """The sharpest refusal in this module. Every RouteFamily is anchored
        on EAST Coast India; a west-coast lane is a different coast and a
        different market. The source publishes them, they mention India, and
        counting them would be entirely reasonable-looking and wrong."""
        _d, quotes = parsed
        wci = [q for q in quotes if "West Coast India" in q.dest_text]
        assert wci, "the fixture must contain a WCI lane for this test to mean anything"
        assert all(q.route_family is None for q in wci)
        assert all(q.series_id is None for q in wci)

    def test_a_lane_to_somewhere_else_entirely_never_maps(
        self, parsed: tuple[date | None, list]
    ) -> None:
        _d, quotes = parsed
        others = [
            q
            for q in quotes
            if "India" not in q.dest_text and q.route_family is not None
        ]
        assert others == []

    def test_unmapped_lanes_are_still_kept(self, parsed: tuple[date | None, list]) -> None:
        """A lane not mapped today may be mapped later. Discarding real
        observations to save bytes is never the right trade."""
        _d, quotes = parsed
        assert sum(1 for q in quotes if q.route_family is None) > 50

    @pytest.mark.parametrize(
        ("origin", "dest", "expected"),
        [
            ("Indonesia", "East Coast India (ECI)", RouteFamily.INDONESIA_EC_INDIA),
            ("South Africa (SAF)", "East Coast India (ECI)", RouteFamily.SOUTH_AFRICA_EC_INDIA),
            ("Australia", "East Coast India (ECI)", RouteFamily.AUSTRALIA_EC_INDIA),
            ("US Gulf (USG)", "East Coast India (ECI)", RouteFamily.US_EC_INDIA),
            # Right origin, wrong coast.
            ("South Africa (SAF)", "West Coast India (WCI)", None),
            # Right destination, an origin this project does not trade from.
            ("Black Sea", "East Coast India (ECI)", None),
            # Neither.
            ("Continent", "Brazil", None),
        ],
    )
    def test_the_mapping_rule_directly(
        self, origin: str, dest: str, expected: RouteFamily | None
    ) -> None:
        assert hr._route_family_for(origin, dest) is expected


class TestSeriesNaming:
    def test_series_end_with_the_suffix_basis_tests_for(self) -> None:
        """`opt.basis` decides an observation is usable by checking for
        `_USD_DAY`. A series named anything else is silently ignored."""
        q = hr.RouteQuote(
            quoted_on=date(2026, 9, 3),
            vessel_class="SUPRAMAX",
            origin_text="Indonesia",
            dest_text="East Coast India (ECI)",
            usd_per_day=22_500,
            route_family=RouteFamily.INDONESIA_EC_INDIA,
        )
        assert q.series_id is not None
        assert q.series_id.endswith("_USD_DAY")

    def test_the_prefix_is_not_signals(self) -> None:
        """`SG_` means Signal. Filing one publisher's assessments under
        another's name, in a table whose whole purpose is knowing where a
        number came from, would be the worst possible shortcut."""
        assert hr.SERIES_PREFIX == "HB"
        q = hr.RouteQuote(
            quoted_on=date(2026, 9, 3),
            vessel_class="PANAMAX",
            origin_text="Australia",
            dest_text="ECI",
            usd_per_day=20_000,
            route_family=RouteFamily.AUSTRALIA_EC_INDIA,
        )
        assert q.series_id == "HB_PANAMAX_AUSTRALIA_ECI_USD_DAY"

    def test_every_generated_series_is_registered_with_opt_basis(
        self, parsed: tuple[date | None, list]
    ) -> None:
        """A series this harvester writes but `opt.basis` does not look up is
        data collected and then ignored -- which reads from outside exactly
        like "no evidence available"."""
        from opt.basis import ROUTE_FAMILY_TO_SIGNAL_SERIES

        registered = {s for ids in ROUTE_FAMILY_TO_SIGNAL_SERIES.values() for s in ids}
        _d, quotes = parsed
        for q in quotes:
            if q.series_id is not None:
                assert q.series_id in registered, f"{q.series_id} is written but never read"


class TestAppend:
    def test_new_lanes_are_written(self, parsed: tuple[date | None, list], tmp_path: Path) -> None:
        _d, quotes = parsed
        path = tmp_path / "routes.csv"
        assert hr.append_quotes(quotes, path) == len(quotes)
        with path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        assert len(rows) == len(quotes)

    def test_running_twice_in_a_day_adds_nothing(
        self, parsed: tuple[date | None, list], tmp_path: Path
    ) -> None:
        _d, quotes = parsed
        path = tmp_path / "routes.csv"
        hr.append_quotes(quotes, path)
        assert hr.append_quotes(quotes, path) == 0

    def test_the_same_lane_on_a_new_day_is_a_new_row(
        self, parsed: tuple[date | None, list], tmp_path: Path
    ) -> None:
        """The page republishes the same lanes daily at new levels. That is a
        time series, not a duplicate."""
        _d, quotes = parsed
        path = tmp_path / "routes.csv"
        hr.append_quotes(quotes, path)
        tomorrow = [
            hr.RouteQuote(
                quoted_on=date(2026, 9, 4),
                vessel_class=q.vessel_class,
                origin_text=q.origin_text,
                dest_text=q.dest_text,
                usd_per_day=q.usd_per_day + 100,
                route_family=q.route_family,
            )
            for q in quotes
        ]
        assert hr.append_quotes(tomorrow, path) == len(quotes)


class TestMasterUpdateIsBounded:
    @pytest.fixture
    def master(self, tmp_path: Path) -> Path:
        path = tmp_path / "master_long.parquet"
        pl.DataFrame(
            {
                "series_id": ["SUPRAMAX_TCAVG", "PW_PARADIP_CALLS"],
                "date": [date(2026, 9, 3), date(2026, 9, 3)],
                "value": [20858.0, 7.0],
                "unit": ["usd/day", "calls"],
                "source": ["handybulk", "portwatch"],
            }
        ).write_parquet(path)
        return path

    def test_only_mapped_lanes_reach_the_master(
        self, parsed: tuple[date | None, list], master: Path
    ) -> None:
        """An unmapped lane has no consumer, and 75 of them a day would be
        noise in a file every model reads."""
        _d, quotes = parsed
        hr.update_master(quotes, master_path=master)
        df = pl.read_parquet(master)
        hb = df.filter(pl.col("series_id").str.starts_with("HB_"))
        assert hb.height == 1
        assert hb["series_id"][0] == "HB_SUPRAMAX_INDONESIA_ECI_USD_DAY"

    def test_one_row_per_series_per_day(
        self, parsed: tuple[date | None, list], master: Path
    ) -> None:
        """The page quotes the same family twice in a day (Supramax and
        Ultramax both run Indonesia to ECI). Averaging two indicative levels
        would invent a third number neither publisher quoted."""
        _d, quotes = parsed
        indonesia = [q for q in quotes if q.route_family is RouteFamily.INDONESIA_EC_INDIA]
        assert len(indonesia) >= 2, "the fixture must have the duplicate for this to mean anything"
        hr.update_master(quotes, master_path=master)
        df = pl.read_parquet(master).filter(pl.col("series_id").str.starts_with("HB_"))
        assert df.height == 1

    def test_unrelated_series_are_untouched(
        self, parsed: tuple[date | None, list], master: Path
    ) -> None:
        """Content, not row order. `update_master` re-sorts by (series_id,
        date) exactly as `build_master.main` does, so the rows come back in a
        different order having not changed at all -- comparing frames directly
        fails on that and would be testing the sort, not the guarantee."""
        key = ["series_id", "date"]
        before = (
            pl.read_parquet(master)
            .filter(~pl.col("series_id").str.starts_with("HB_"))
            .sort(key)
        )
        _d, quotes = parsed
        hr.update_master(quotes, master_path=master)
        after = (
            pl.read_parquet(master)
            .filter(~pl.col("series_id").str.starts_with("HB_"))
            .sort(key)
        )
        assert before.equals(after)

    def test_running_twice_adds_nothing(
        self, parsed: tuple[date | None, list], master: Path
    ) -> None:
        _d, quotes = parsed
        hr.update_master(quotes, master_path=master)
        assert hr.update_master(quotes, master_path=master) == 0

    def test_a_missing_master_is_reported_not_created(
        self, parsed: tuple[date | None, list], tmp_path: Path
    ) -> None:
        _d, quotes = parsed
        assert hr.update_master(quotes, master_path=tmp_path / "absent.parquet") == 0
        assert not (tmp_path / "absent.parquet").exists()


class TestOfflineSafety:
    def test_a_failed_fetch_returns_a_result(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(hr, "fetch_page", lambda url=hr.SOURCE_URL: None)
        result = hr.harvest(path=tmp_path / "routes.csv")
        assert result.fetched is False
        assert result.added == 0
        assert result.reason is not None

    def test_an_unparseable_page_says_so(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(hr, "fetch_page", lambda url=hr.SOURCE_URL: "<html></html>")
        result = hr.harvest(path=tmp_path / "routes.csv")
        assert result.fetched is True
        assert result.parsed == 0
        assert "wording" in (result.reason or "")
