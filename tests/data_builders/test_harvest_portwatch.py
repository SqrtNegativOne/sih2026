"""Tests for data_builders.harvest_portwatch and the curated candidate list.

Network calls are mocked throughout -- these test the pagination, resolution,
and CSV-writing logic, not PortWatch's live availability. See
data_builders.harvest_portwatch.main() for the live run.
"""
from __future__ import annotations

import csv
import datetime
import urllib.error
from pathlib import Path

import pytest

from data_builders import harvest_portwatch as hp
from data_builders._port_candidates import CANDIDATES, PortCandidate
from data_builders.harvest_portwatch import (
    CHOKEPOINT_IDS,
    ResolvedPort,
    _existing_file_matches,
    _pull_paginated,
    pull_ports,
    resolve_candidates,
    write_port_index,
)

# ---------------------------------------------------------------------------
# Candidate list integrity -- catches curation mistakes, not network issues
# ---------------------------------------------------------------------------


def test_no_duplicate_labels() -> None:
    labels = [c.label for c in CANDIDATES]
    assert len(labels) == len(set(labels)), "duplicate PortCandidate labels found"


def test_every_candidate_has_at_least_one_search_term() -> None:
    for c in CANDIDATES:
        assert len(c.search_terms) >= 1, f"{c.label} has no search terms"


def test_search_terms_are_uppercase() -> None:
    """The resolver builds a raw SQL LIKE clause against UPPER(portname);
    a lowercase term would never match anything and fail silently."""
    for c in CANDIDATES:
        for term in c.search_terms:
            assert term == term.upper(), f"{c.label}: search term {term!r} is not uppercase"


def test_labels_are_valid_filename_stems() -> None:
    """Each label becomes `{label}_daily_portcalls.csv`; keep it filesystem-safe."""
    bad_chars = set(' /\\:*?"<>|')
    for c in CANDIDATES:
        assert not (bad_chars & set(c.label)), f"{c.label} contains a character unsafe for filenames"


def test_reasonable_total_candidate_count() -> None:
    """Sanity bound matching the ~150-port target (129 new + 14 already pulled)."""
    assert 100 <= len(CANDIDATES) <= 200


def test_chokepoint_ids_match_known_portwatch_count() -> None:
    """Verified against the live service: exactly 28 chokepoints, chokepoint1..28."""
    assert CHOKEPOINT_IDS == tuple(f"chokepoint{i}" for i in range(1, 29))


# ---------------------------------------------------------------------------
# resolve_candidates -- network mocked
# ---------------------------------------------------------------------------


def _fake_hit(portid: str, portname: str, lat: float = 1.0, lon: float = 2.0) -> dict:
    return {
        "attributes": {
            "portid": portid,
            "portname": portname,
            "country": "Testland",
            "ISO3": "TST",
            "lat": lat,
            "lon": lon,
        }
    }


def test_resolve_candidates_marks_a_clean_hit_resolved(monkeypatch: pytest.MonkeyPatch) -> None:
    cand = PortCandidate("Test_Port", ("TESTPORT",), "coal", "pacific", "TST")
    monkeypatch.setattr(
        "data_builders.harvest_portwatch._search_port",
        lambda term: [_fake_hit("portX", "Test Port")],
    )
    monkeypatch.setattr("data_builders.harvest_portwatch.time.sleep", lambda _: None)

    resolved, unresolved = resolve_candidates((cand,))
    assert unresolved == []
    assert len(resolved) == 1
    assert resolved[0] == ResolvedPort(
        label="Test_Port", portid="portX", portname="Test Port",
        country="Testland", iso3="TST", lat=1.0, lon=2.0,
        role="coal", basin="pacific",
    )


def test_resolve_candidates_marks_a_true_miss_unresolved(monkeypatch: pytest.MonkeyPatch) -> None:
    cand = PortCandidate("Nowhere_Port", ("NOWHERE",), "coal", "pacific", "TST")
    monkeypatch.setattr("data_builders.harvest_portwatch._search_port", lambda term: [])
    monkeypatch.setattr("data_builders.harvest_portwatch.time.sleep", lambda _: None)

    resolved, unresolved = resolve_candidates((cand,))
    assert resolved == []
    assert unresolved == [cand]


def test_resolve_candidates_tries_second_search_term_after_first_miss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cand = PortCandidate("Two_Term_Port", ("FIRSTTRY", "SECONDTRY"), "grain", "atlantic", "TST")
    calls: list[str] = []

    def fake_search(term: str) -> list[dict]:
        calls.append(term)
        return [] if term == "FIRSTTRY" else [_fake_hit("portY", "Second Try Port")]

    monkeypatch.setattr("data_builders.harvest_portwatch._search_port", fake_search)
    monkeypatch.setattr("data_builders.harvest_portwatch.time.sleep", lambda _: None)

    resolved, unresolved = resolve_candidates((cand,))
    assert calls == ["FIRSTTRY", "SECONDTRY"]
    assert unresolved == []
    assert resolved[0].portid == "portY"


def test_resolve_candidates_takes_first_hit_on_ambiguous_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cand = PortCandidate("Ambiguous_Port", ("AMBIG",), "coal", "pacific", "TST")
    monkeypatch.setattr(
        "data_builders.harvest_portwatch._search_port",
        lambda term: [_fake_hit("portFirst", "First Match"), _fake_hit("portSecond", "Second Match")],
    )
    monkeypatch.setattr("data_builders.harvest_portwatch.time.sleep", lambda _: None)

    resolved, _ = resolve_candidates((cand,))
    assert resolved[0].portid == "portFirst"


# ---------------------------------------------------------------------------
# write_port_index
# ---------------------------------------------------------------------------


def test_write_port_index_records_both_hits_and_misses(tmp_path: Path) -> None:
    resolved = [
        ResolvedPort(
            label="A", portid="p1", portname="Port A", country="X", iso3="XXX",
            lat=1.0, lon=2.0, role="coal", basin="pacific",
        )
    ]
    unresolved = [PortCandidate("B", ("B_TERM",), "grain", "atlantic", "YYY")]

    out = tmp_path / "index.csv"
    write_port_index(resolved, unresolved, out)

    rows = list(csv.DictReader(out.open(encoding="utf-8")))
    assert len(rows) == 2
    hit = next(r for r in rows if r["label"] == "A")
    miss = next(r for r in rows if r["label"] == "B")
    assert hit["resolved"] == "yes"
    assert hit["portid"] == "p1"
    assert miss["resolved"] == "no"
    assert miss["portid"] == ""


# ---------------------------------------------------------------------------
# _pull_paginated -- the ArcGIS pagination quirk (missing key on single page)
# ---------------------------------------------------------------------------


def test_pull_paginated_stops_when_exceeded_transfer_limit_is_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A single-page response omits exceededTransferLimit entirely rather than
    setting it False -- documented in PULL_NOTES.md as a real quirk that broke
    a naive `data["exceededTransferLimit"]` access during the original pull."""
    calls: list[int] = []

    def fake_get_json(url: str, params: dict) -> dict:
        calls.append(int(params["resultOffset"]))
        return {"features": [{"attributes": {"date": "2024-01-01", "value": 1}}]}

    monkeypatch.setattr("data_builders.harvest_portwatch._get_json", fake_get_json)
    monkeypatch.setattr("data_builders.harvest_portwatch.time.sleep", lambda _: None)

    rows = _pull_paginated("http://fake", "portid='x'")
    assert len(calls) == 1, "must not page again when the limit flag is absent"
    assert rows == [{"date": "2024-01-01", "value": 1}]


def test_skip_existing_checkpoint_verifies_content_not_just_presence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Regression test for a real incident during this harvest.

    A prior (supposedly-killed) run's process outlived the stop signal on
    Windows and kept writing into the shared output directory with stale
    resolution logic, leaving a file that existed under the right filename but
    held the wrong port's data entirely (Santos_BR's file contained General
    Santos, Philippines). A checkpoint that only checks `path.exists()` trusts
    that silently; pull_ports must notice the mismatch and re-pull instead.
    """
    port = ResolvedPort(
        label="Santos_BR", portid="portBR123", portname="Santos", country="Brazil",
        iso3="BRA", lat=-23.9, lon=-46.3, role="grain", basin="atlantic",
    )

    # Simulate the foreign file: same filename, wrong port's content.
    foreign_path = tmp_path / "Santos_BR_daily_portcalls.csv"
    with foreign_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["date", "portid", "portname"])
        w.writeheader()
        w.writerow({"date": "2019-01-01", "portid": "portPH999", "portname": "General Santos"})

    pull_calls: list[str] = []

    def fake_pull_paginated(url: str, where: str) -> list[dict]:
        pull_calls.append(where)
        return [{"date": "2019-01-01", "portid": "portBR123", "portname": "Santos"}]

    monkeypatch.setattr("data_builders.harvest_portwatch._pull_paginated", fake_pull_paginated)
    monkeypatch.setattr("data_builders.harvest_portwatch.time.sleep", lambda _: None)

    pull_ports([port], out_dir=tmp_path, skip_existing=True)

    assert pull_calls, "the mismatched file must trigger a real re-pull, not a skip"
    rewritten = list(csv.DictReader(foreign_path.open(encoding="utf-8")))
    assert rewritten[0]["portid"] == "portBR123"


def test_skip_existing_checkpoint_trusts_a_genuinely_matching_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    port = ResolvedPort(
        label="Santos_BR", portid="portBR123", portname="Santos", country="Brazil",
        iso3="BRA", lat=-23.9, lon=-46.3, role="grain", basin="atlantic",
    )
    correct_path = tmp_path / "Santos_BR_daily_portcalls.csv"
    with correct_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["date", "portid", "portname"])
        w.writeheader()
        w.writerow({"date": "2019-01-01", "portid": "portBR123", "portname": "Santos"})

    def fail_if_called(url: str, where: str) -> list[dict]:
        raise AssertionError("a genuinely matching file must not trigger a re-pull")

    monkeypatch.setattr("data_builders.harvest_portwatch._pull_paginated", fail_if_called)
    pull_ports([port], out_dir=tmp_path, skip_existing=True)


def test_existing_file_matches_false_when_file_absent(tmp_path: Path) -> None:
    assert _existing_file_matches(tmp_path / "nope.csv", "anything") is False


def test_pull_paginated_continues_across_multiple_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    pages = [
        {"features": [{"attributes": {"i": i}} for i in range(1000)], "exceededTransferLimit": True},
        {"features": [{"attributes": {"i": i}} for i in range(1000, 1500)]},
    ]
    call_count = {"n": 0}

    def fake_get_json(url: str, params: dict) -> dict:
        page = pages[call_count["n"]]
        call_count["n"] += 1
        return page

    monkeypatch.setattr("data_builders.harvest_portwatch._get_json", fake_get_json)
    monkeypatch.setattr("data_builders.harvest_portwatch.time.sleep", lambda _: None)

    rows = _pull_paginated("http://fake", "portid='x'")
    assert len(rows) == 1500
    assert rows[0]["i"] == 0
    assert rows[-1]["i"] == 1499


class TestIncrementalRefresh:
    """Bringing a completed harvest up to date, without re-downloading it.

    `pull_ports` is a one-shot: it skips any port whose file exists, so
    re-running it changes nothing, and `skip_existing=False` re-downloads every
    port's whole history and rewrites 53 MB. Neither keeps the data current,
    and in practice nothing did -- all 128 files sat at one stale date while
    the desk served congestion and tightness figures derived from them.

    `refresh_ports` asks each port only for rows newer than its own file
    already carries. What is tested here is that it appends and never rewrites.
    """

    @staticmethod
    def _write(path: Path, portid: str, dates: list[str]) -> None:
        with path.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=["date", "portid", "portcalls_dry_bulk"])
            w.writeheader()
            for d in dates:
                w.writerow({"date": d, "portid": portid, "portcalls_dry_bulk": "1"})

    def test_it_reads_the_portid_from_the_file_not_an_index(self, tmp_path: Path) -> None:
        """So a refresh cannot ask one port for another's data even if the port
        index and the directory have drifted apart."""
        p = tmp_path / "Foo_daily_portcalls.csv"
        self._write(p, "port883", ["2026-08-01", "2026-08-02"])
        portid, latest, fields = hp._existing_bounds(p)
        assert portid == "port883"
        assert latest == "2026-08-02"
        assert fields == ["date", "portid", "portcalls_dry_bulk"]

    def test_a_file_holding_two_ports_is_refused(self, tmp_path: Path) -> None:
        """Refusing beats guessing: appending one port's rows to a file that
        already mixes two would deepen the corruption."""
        p = tmp_path / "Mixed_daily_portcalls.csv"
        with p.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=["date", "portid", "portcalls_dry_bulk"])
            w.writeheader()
            w.writerow({"date": "2026-08-01", "portid": "portA", "portcalls_dry_bulk": "1"})
            w.writerow({"date": "2026-08-02", "portid": "portB", "portcalls_dry_bulk": "1"})
        portid, _latest, _fields = hp._existing_bounds(p)
        assert portid is None

    def test_new_rows_are_appended_and_history_is_untouched(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        p = tmp_path / "Foo_daily_portcalls.csv"
        self._write(p, "port883", ["2026-08-01", "2026-08-02"])
        before = p.read_text(encoding="utf-8")

        monkeypatch.setattr(
            hp,
            "_pull_paginated",
            lambda _url, _where: [
                {"date": "2026-08-03", "portid": "port883", "portcalls_dry_bulk": "4"},
                {"date": "2026-08-04", "portid": "port883", "portcalls_dry_bulk": "5"},
            ],
        )
        result = hp.refresh_ports(out_dir=tmp_path)

        assert result.rows_added == 2
        assert result.files_updated == 1
        assert result.newest_date == "2026-08-04"
        after = p.read_text(encoding="utf-8")
        assert after.startswith(before), "existing rows must survive byte-identical"

    def test_rows_not_actually_newer_are_dropped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Belt and braces on the server-side date predicate. If the filter
        were ever ignored, the whole history would come back and be appended
        on top of itself."""
        p = tmp_path / "Foo_daily_portcalls.csv"
        self._write(p, "port883", ["2026-08-01", "2026-08-02"])
        monkeypatch.setattr(
            hp,
            "_pull_paginated",
            lambda _url, _where: [
                {"date": "2026-08-01", "portid": "port883", "portcalls_dry_bulk": "9"},
                {"date": "2026-08-02", "portid": "port883", "portcalls_dry_bulk": "9"},
            ],
        )
        result = hp.refresh_ports(out_dir=tmp_path)
        assert result.rows_added == 0
        assert p.read_text(encoding="utf-8").count("2026-08-01") == 1

    def test_the_query_asks_only_for_newer_rows(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: list[str] = []

        def capture(_url: str, where: str) -> list[dict]:
            seen.append(where)
            return []

        p = tmp_path / "Foo_daily_portcalls.csv"
        self._write(p, "port883", ["2026-08-01", "2026-08-14"])
        monkeypatch.setattr(hp, "_pull_paginated", capture)
        hp.refresh_ports(out_dir=tmp_path)
        assert seen == ["portid='port883' AND date > DATE '2026-08-14'"]

    def test_one_unreachable_port_does_not_stop_the_rest(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """And it is named, not just counted -- a partial refresh leaves some
        ports current and others not, and "12 failed" does not tell anyone
        which figures to distrust."""
        good = tmp_path / "Good_daily_portcalls.csv"
        bad = tmp_path / "Bad_daily_portcalls.csv"
        self._write(good, "portGOOD", ["2026-08-01"])
        self._write(bad, "portBAD", ["2026-08-01"])

        def flaky(_url: str, where: str) -> list[dict]:
            if "portBAD" in where:
                raise urllib.error.URLError("down")
            return [{"date": "2026-08-02", "portid": "portGOOD", "portcalls_dry_bulk": "2"}]

        monkeypatch.setattr(hp, "_pull_paginated", flaky)
        result = hp.refresh_ports(out_dir=tmp_path)
        assert result.rows_added == 1
        assert result.failures == ("Bad_daily_portcalls.csv",)

    def test_it_refreshes_only_files_that_already_exist(self, tmp_path: Path) -> None:
        """This brings a harvest up to date; it does not start one. A port
        never pulled needs `pull_ports`, a deliberate and much heavier job."""
        result = hp.refresh_ports(out_dir=tmp_path)
        assert result.files_checked == 0
        assert result.rows_added == 0

    def test_new_rows_reach_the_parquet_the_forecast_reads(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`tonnage.stockflow` reads the CSVs directly and goes current on its
        own; `ml.features.congestion` reads PW_ series out of master_long, and
        those are model features in the rate forecast. Without this the tonnage
        screen would advance while the forecast quietly kept using old
        congestion."""
        import polars as pl

        ports = tmp_path / "ports"
        ports.mkdir()
        f = ports / "Paradip_daily_portcalls.csv"
        with f.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(
                fh,
                fieldnames=["date", "portid", "portcalls_dry_bulk", "import_dry_bulk", "export_dry_bulk"],
            )
            w.writeheader()
            for d, c in (("2026-08-01", 3), ("2026-08-02", 4)):
                w.writerow({"date": d, "portid": "port883", "portcalls_dry_bulk": c,
                            "import_dry_bulk": 1.0, "export_dry_bulk": 2.0})

        master = tmp_path / "master_long.parquet"
        pl.DataFrame(
            {
                "series_id": ["PW_PARADIP_CALLS", "SUPRAMAX_TCAVG"],
                "date": [datetime.date(2026, 8, 1), datetime.date(2026, 8, 1)],
                "value": [3.0, 20000.0],
                "unit": ["calls", "usd/day"],
                "source": ["portwatch", "handybulk"],
            }
        ).write_parquet(master)

        added = hp.update_master_ports(out_dir=ports, master_path=master)
        df = pl.read_parquet(master)
        calls = df.filter(pl.col("series_id") == "PW_PARADIP_CALLS").sort("date")
        assert added > 0
        assert calls["date"].to_list()[-1] == datetime.date(2026, 8, 2)
        # The already-present date keeps its original value.
        assert calls["value"][0] == 3.0

    def test_it_never_introduces_a_series_the_master_lacks(
        self, tmp_path: Path
    ) -> None:
        """The 342 series from the extended harvest that the parquet has never
        carried are a separate, deliberate decision -- not something a daily
        top-up makes on anyone's behalf."""
        import polars as pl

        ports = tmp_path / "ports"
        ports.mkdir()
        f = ports / "Newport_daily_portcalls.csv"
        with f.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(
                fh,
                fieldnames=["date", "portid", "portcalls_dry_bulk", "import_dry_bulk", "export_dry_bulk"],
            )
            w.writeheader()
            w.writerow({"date": "2026-08-02", "portid": "portNEW", "portcalls_dry_bulk": 9,
                        "import_dry_bulk": 1.0, "export_dry_bulk": 2.0})

        master = tmp_path / "master_long.parquet"
        pl.DataFrame(
            {
                "series_id": ["PW_PARADIP_CALLS"],
                "date": [datetime.date(2026, 8, 1)],
                "value": [3.0],
                "unit": ["calls"],
                "source": ["portwatch"],
            }
        ).write_parquet(master)

        before = set(pl.read_parquet(master)["series_id"].unique().to_list())
        hp.update_master_ports(out_dir=ports, master_path=master)
        after = set(pl.read_parquet(master)["series_id"].unique().to_list())
        assert after == before

    def test_the_series_naming_matches_build_master(self) -> None:
        """This module duplicates build_master's slug rule rather than
        importing it. If the two ever disagree, a daily top-up would write to
        a series a full rebuild does not produce."""
        from data_builders import build_master

        source = Path(build_master.__file__).read_text(encoding="utf-8")
        assert 're.sub(r"[^A-Z0-9]+", "_", file.stem.split("_daily")[0].upper()).strip("_")' in source
        assert hp._series_slug(Path("Richards_Bay_ZA_daily_portcalls.csv")) == "RICHARDS_BAY_ZA"
        assert hp._series_slug(Path("Paradip_daily_portcalls.csv")) == "PARADIP"
