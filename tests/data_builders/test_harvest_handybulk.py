"""The daily Baltic rate harvester.

Two things matter more than the parsing here, and both are about not damaging
what already exists:

- a date already stored is never rewritten, because the stored figure is what
  the models trained on and what every past recommendation was priced against;
- the master parquet update touches only the nine series this source publishes,
  because a job that runs daily must not be able to reshape the rest of the
  dataset as a side effect. It did exactly that once, in development, turning a
  seven-day rate top-up into a 951,848-row change.

The fixture is a real slice of the published page, so the parser is exercised
against the site's own markup rather than something written to suit it.
"""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

import polars as pl
import pytest
import requests

from data_builders import harvest_handybulk as hh

FIXTURE = Path(__file__).parent / "fixtures" / "handybulk_page_sample.html"


@pytest.fixture
def page() -> str:
    return FIXTURE.read_text(encoding="utf-8")


@pytest.fixture
def parsed(page: str) -> dict[date, dict[str, float]]:
    return hh.parse_page(page)


class TestParsing:
    def test_it_reads_every_dated_entry(self, parsed: dict) -> None:
        assert set(parsed) == {
            date(2026, 8, 26),
            date(2026, 8, 27),
            date(2026, 8, 28),
            date(2026, 9, 1),
        }

    def test_it_reads_both_numbers_per_class(self, parsed: dict) -> None:
        """Each entry carries an index level in points and a $/day average, and
        the harvester exists for the second one."""
        day = parsed[date(2026, 9, 1)]
        assert day["bsi"] == 1650.0
        assert day["supramax_tc_avg_usd_day"] == 20858.0
        assert day["bci"] == 5221.0
        assert day["capesize_tc_avg_usd_day"] == 47350.0

    def test_it_handles_the_pages_varied_wording(self, parsed: dict) -> None:
        """The source says "with average daily earnings", "while average daily
        income" and "as average daily earnings" for different classes in the
        same sentence block. All four classes must come through regardless."""
        day = parsed[date(2026, 9, 1)]
        for column in (
            "capesize_tc_avg_usd_day",
            "panamax_tc_avg_usd_day",
            "supramax_tc_avg_usd_day",
            "handysize_tc_avg_usd_day",
        ):
            assert day[column] > 0

    def test_a_missing_figure_is_absent_rather_than_guessed(self, parsed: dict) -> None:
        """28 August genuinely has no BHSI figure on the page. The record comes
        back without that key -- it is never filled from the previous day, which
        would put a number the source did not publish under the source's name."""
        assert "bhsi" not in parsed[date(2026, 8, 28)]
        assert parsed[date(2026, 8, 28)]["supramax_tc_avg_usd_day"] == 20819.0

    def test_a_page_with_no_entries_parses_to_nothing(self) -> None:
        assert hh.parse_page("<html><body><p>Nothing here</p></body></html>") == {}

    def test_thousands_separators_are_handled(self, parsed: dict) -> None:
        """Every figure on the page is comma-grouped; a naive float() would
        raise on all of them."""
        assert parsed[date(2026, 9, 1)]["capesize_tc_avg_usd_day"] == 47350.0


class TestOfflineSafety:
    def test_a_failed_fetch_returns_a_result_rather_than_raising(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A harvester that raises turns a transient network problem into a
        broken caller. No data is a normal state -- it is also what a weekend
        looks like."""

        def boom(*_a: object, **_k: object) -> None:
            raise requests.ConnectionError("no network")

        monkeypatch.setattr(hh.requests, "get", boom)
        result = hh.harvest(path=tmp_path / "levels.csv")
        assert result.fetched is False
        assert result.added_days == 0
        assert result.reason is not None

    def test_a_reachable_but_unparseable_page_says_so(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Silently harvesting zero rows every day looks exactly like a quiet
        market. If the page's wording changes, that has to be visible."""
        monkeypatch.setattr(hh, "fetch_page", lambda url=hh.SOURCE_URL: "<html></html>")
        result = hh.harvest(path=tmp_path / "levels.csv")
        assert result.fetched is True
        assert result.parsed_days == 0
        assert "wording" in (result.reason or "")


class TestMergeNeverRewritesHistory:
    def test_new_dates_are_added(self, parsed: dict) -> None:
        rows, conflicts = hh.merge_rows(parsed, existing={})
        assert len(rows) == 4
        assert conflicts == []

    def test_an_existing_date_is_left_exactly_as_it_was(self, parsed: dict) -> None:
        """The stored figure is what the models trained on and what past
        recommendations were priced against. Rewriting it would change the past
        out from under the decision ledger."""
        existing = {
            "2026-09-01": {
                "date": "2026-09-01",
                "supramax_tc_avg_usd_day": "19999",
                "source_url": "original",
            }
        }
        rows, conflicts = hh.merge_rows(parsed, existing)
        kept = next(r for r in rows if r["date"] == "2026-09-01")
        assert kept["supramax_tc_avg_usd_day"] == "19999"
        assert kept["source_url"] == "original"

    def test_a_disagreement_is_reported_not_applied(self, parsed: dict) -> None:
        existing = {
            "2026-09-01": {"date": "2026-09-01", "supramax_tc_avg_usd_day": "19999"}
        }
        _rows, conflicts = hh.merge_rows(parsed, existing)
        assert any("2026-09-01" in c and "supramax" in c for c in conflicts)

    def test_a_matching_value_is_not_a_conflict(self, parsed: dict) -> None:
        existing = {
            "2026-09-01": {"date": "2026-09-01", "supramax_tc_avg_usd_day": "20858"}
        }
        _rows, conflicts = hh.merge_rows(parsed, existing)
        assert conflicts == []

    def test_rows_come_back_sorted_by_date(self, parsed: dict) -> None:
        rows, _ = hh.merge_rows(parsed, existing={})
        assert [r["date"] for r in rows] == sorted(r["date"] for r in rows)


class TestCsvRoundTrip:
    def test_written_then_read_is_unchanged(self, parsed: dict, tmp_path: Path) -> None:
        path = tmp_path / "levels.csv"
        rows, _ = hh.merge_rows(parsed, existing={})
        hh.write_csv(rows, path)
        back = hh.read_existing(path)
        assert set(back) == {r["date"] for r in rows}
        assert back["2026-09-01"]["supramax_tc_avg_usd_day"] == "20858"

    def test_the_column_order_matches_the_existing_file(
        self, parsed: dict, tmp_path: Path
    ) -> None:
        """`build_master.read_handybulk` reads by name, but a stable order keeps
        a diff of this file readable."""
        path = tmp_path / "levels.csv"
        rows, _ = hh.merge_rows(parsed, existing={})
        hh.write_csv(rows, path)
        with path.open(newline="", encoding="utf-8") as handle:
            assert next(csv.reader(handle)) == list(hh.COLUMNS)

    def test_harvest_is_idempotent(
        self, parsed: dict, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, page: str
    ) -> None:
        """Running it twice in a day must not duplicate a thing."""
        monkeypatch.setattr(hh, "fetch_page", lambda url=hh.SOURCE_URL: page)
        path = tmp_path / "levels.csv"
        first = hh.harvest(path=path)
        second = hh.harvest(path=path)
        assert first.added_days == 4
        assert second.added_days == 0
        assert len(hh.read_existing(path)) == 4


class TestMasterUpdateIsBounded:
    """The blast-radius guarantee.

    A daily job that can rewrite anything it likes in the shared dataset is a
    daily opportunity to break something nobody was watching.
    """

    @pytest.fixture
    def master(self, tmp_path: Path) -> Path:
        """A miniature master carrying one harvested series and one that is
        nothing to do with this source."""
        path = tmp_path / "master_long.parquet"
        pl.DataFrame(
            {
                "series_id": ["SUPRAMAX_TCAVG", "SUPRAMAX_TCAVG", "PW_PARADIP_CALLS"],
                "date": [date(2026, 8, 26), date(2026, 8, 27), date(2026, 8, 26)],
                "value": [20784.0, 20789.0, 7.0],
                "unit": ["usd/day", "usd/day", "calls"],
                "source": ["handybulk", "handybulk", "portwatch"],
            }
        ).write_parquet(path)
        return path

    def test_only_genuinely_new_dates_are_appended(
        self, parsed: dict, master: Path
    ) -> None:
        added = hh.update_master(parsed, master_path=master)
        df = pl.read_parquet(master)
        sup = df.filter(pl.col("series_id") == "SUPRAMAX_TCAVG").sort("date")
        # 26 and 27 were already there; 28 and 1 Sep are new.
        assert sup.height == 4
        assert sup["date"].to_list()[-1] == date(2026, 9, 1)
        assert added > 0

    def test_an_existing_value_is_never_overwritten(
        self, parsed: dict, master: Path
    ) -> None:
        hh.update_master(parsed, master_path=master)
        df = pl.read_parquet(master)
        kept = df.filter(
            (pl.col("series_id") == "SUPRAMAX_TCAVG") & (pl.col("date") == date(2026, 8, 26))
        )
        assert kept["value"][0] == 20784.0

    def test_series_this_source_does_not_publish_are_untouched(
        self, parsed: dict, master: Path
    ) -> None:
        before = pl.read_parquet(master).filter(pl.col("series_id") == "PW_PARADIP_CALLS")
        hh.update_master(parsed, master_path=master)
        after = pl.read_parquet(master).filter(pl.col("series_id") == "PW_PARADIP_CALLS")
        assert before.equals(after)

    def test_no_new_series_appear(self, parsed: dict, master: Path) -> None:
        """Only the nine this source publishes may ever be written, and only
        those already present can gain rows."""
        before = set(pl.read_parquet(master)["series_id"].unique().to_list())
        hh.update_master(parsed, master_path=master)
        after = set(pl.read_parquet(master)["series_id"].unique().to_list())
        assert after - before <= set(hh.HARVESTED_SERIES)

    def test_running_twice_adds_nothing_the_second_time(
        self, parsed: dict, master: Path
    ) -> None:
        hh.update_master(parsed, master_path=master)
        rows_after_first = pl.read_parquet(master).height
        assert hh.update_master(parsed, master_path=master) == 0
        assert pl.read_parquet(master).height == rows_after_first

    def test_a_missing_master_is_reported_not_created(
        self, parsed: dict, tmp_path: Path
    ) -> None:
        """Writing a brand-new master from one source's nine series would be a
        far more destructive answer than doing nothing."""
        assert hh.update_master(parsed, master_path=tmp_path / "absent.parquet") == 0
        assert not (tmp_path / "absent.parquet").exists()

    def test_the_harvested_series_list_matches_what_the_parser_produces(self) -> None:
        """Guards the two lists drifting apart -- a column the parser reads but
        the series list omits would be harvested into the CSV and then never
        reach the parquet."""
        from_columns = {sid for sid, _unit in hh._SERIES_FROM_COLUMN.values()}
        assert from_columns == set(hh.HARVESTED_SERIES)

    def test_the_column_map_matches_build_master(self) -> None:
        """`build_master.read_handybulk` maps the same CSV columns to the same
        series. If the two disagree, a full rebuild and a daily top-up would
        produce different rows from one file."""
        from data_builders import build_master

        source = Path(build_master.__file__).read_text(encoding="utf-8")
        for column, (series_id, unit) in hh._SERIES_FROM_COLUMN.items():
            assert f'"{column}": ("{series_id}", "{unit}")' in source


class TestBackfill:
    """Recovering a gap, and why the front page is not enough.

    An early fetch of the front page returned 56 KB carrying three weeks of
    entries. The same URL the next day returned 16 KB carrying exactly one, the
    rest having moved behind month-archive links. A harvester reading only the
    front page therefore works perfectly for as long as it runs every day, and
    silently loses every day of an outage the moment it does not -- the worst
    shape of bug, because the failure is invisible until you need the data.
    """

    def test_no_gap_means_no_extra_requests(self) -> None:
        """The ordinary daily case must cost one request."""
        today = date(2026, 9, 1)
        assert hh._months_to_backfill(newest_stored=today, newest_seen=today) == []
        assert hh._months_to_backfill(newest_stored=date(2026, 9, 2), newest_seen=today) == []

    def test_an_empty_store_does_not_trigger_a_crawl(self) -> None:
        """With nothing stored there is no gap to size, and walking the whole
        archive is not this function's job."""
        assert hh._months_to_backfill(newest_stored=None, newest_seen=date(2026, 9, 1)) == []

    def test_a_gap_within_one_month_asks_for_that_month(self) -> None:
        months = hh._months_to_backfill(
            newest_stored=date(2026, 8, 20), newest_seen=date(2026, 8, 28)
        )
        assert months == [(2026, 8)]

    def test_a_gap_across_a_month_boundary_asks_for_both(self) -> None:
        months = hh._months_to_backfill(
            newest_stored=date(2026, 8, 20), newest_seen=date(2026, 9, 1)
        )
        assert months == [(2026, 9), (2026, 8)]

    def test_a_long_gap_is_capped(self) -> None:
        """A cap on politeness, not on correctness -- a longer outage is closed
        by running the harvester again."""
        months = hh._months_to_backfill(
            newest_stored=date(2025, 1, 1), newest_seen=date(2026, 9, 1)
        )
        assert len(months) == hh.MAX_BACKFILL_MONTHS

    def test_a_year_boundary_steps_back_correctly(self) -> None:
        months = hh._months_to_backfill(
            newest_stored=date(2025, 12, 20), newest_seen=date(2026, 1, 5)
        )
        assert months == [(2026, 1), (2025, 12)]

    def test_month_urls_use_the_sites_own_spelling(self) -> None:
        assert hh.month_url(2026, 8) == "https://www.handybulk.com/baltic-dry-index/2026/august/"
        assert hh.month_url(2026, 1) == "https://www.handybulk.com/baltic-dry-index/2026/january/"
        assert hh.month_url(2025, 12) == "https://www.handybulk.com/baltic-dry-index/2025/december/"

    def test_an_archive_that_is_not_there_yet_is_skipped(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, page: str
    ) -> None:
        """The current month's archive does not exist until the month has run a
        while -- a real 404 seen in practice. It must not abort the harvest,
        because the other archive is the one carrying the data.
        """
        path = tmp_path / "levels.csv"
        hh.write_csv(
            [{"date": "2026-08-20", "supramax_tc_avg_usd_day": "20698", "source_url": "seed"}],
            path,
        )

        def fake_fetch(url: str = hh.SOURCE_URL) -> str | None:
            if url == hh.SOURCE_URL:
                return page
            if "september" in url:
                return None  # the 404
            return page

        monkeypatch.setattr(hh, "fetch_page", fake_fetch)
        result = hh.harvest(path=path)
        assert result.fetched is True
        assert result.added_days > 0

    def test_backfill_can_be_switched_off(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, page: str
    ) -> None:
        calls: list[str] = []

        def counting_fetch(url: str = hh.SOURCE_URL) -> str:
            calls.append(url)
            return page

        monkeypatch.setattr(hh, "fetch_page", counting_fetch)
        path = tmp_path / "levels.csv"
        hh.write_csv([{"date": "2026-01-01", "source_url": "seed"}], path)
        hh.harvest(path=path, backfill=False)
        assert calls == [hh.SOURCE_URL]

    def test_the_front_page_wins_on_a_date_both_carry(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, page: str
    ) -> None:
        """It is the more recently published rendering of the same figure."""
        archive = page.replace("$20,858", "$11,111")

        def fake_fetch(url: str = hh.SOURCE_URL) -> str:
            return page if url == hh.SOURCE_URL else archive

        monkeypatch.setattr(hh, "fetch_page", fake_fetch)
        path = tmp_path / "levels.csv"
        hh.write_csv([{"date": "2026-08-01", "source_url": "seed"}], path)
        hh.harvest(path=path)
        stored = hh.read_existing(path)
        assert stored["2026-09-01"]["supramax_tc_avg_usd_day"] == "20858"
