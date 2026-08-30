"""P1 -- the fact_port_call append-only store."""
from __future__ import annotations

from datetime import UTC, datetime

from berth_truth.fact_port_call import FactPortCall, FactPortCallStore
from berth_truth.sources import SourceQuality
from opt.network import PortEnum


def _row(*, sha: str = "abc123", idx: int = 0, **overrides) -> FactPortCall:
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


class TestNullOverInvention:
    def test_absent_field_stays_none_not_a_default(self) -> None:
        row = _row()
        assert row.loa_m is None
        assert row.arrival_draft_m is None
        assert row.total_qty_t is None

    def test_vessel_class_is_named_inferred_not_asserted(self) -> None:
        row = _row()
        assert row.vessel_class_inferred is None
        assert hasattr(row, "vessel_class_inferred")
        assert not hasattr(row, "vessel_class")


class TestAppendOnlyStore:
    def test_append_writes_new_rows(self, tmp_path) -> None:
        store = FactPortCallStore(path=tmp_path / "fact_port_call.jsonl")
        written = store.append_many([_row(idx=0), _row(idx=1)])
        assert written == 2
        assert len(store.read_all()) == 2

    def test_reingesting_the_same_capture_writes_zero_new_rows(self, tmp_path) -> None:
        """Idempotent ingestion: the same (content_sha256, row_index) key
        must never be duplicated on disk."""
        store = FactPortCallStore(path=tmp_path / "fact_port_call.jsonl")
        rows = [_row(idx=0), _row(idx=1)]
        store.append_many(rows)
        second_write = store.append_many(rows)  # re-ingest identical capture
        assert second_write == 0
        assert len(store.read_all()) == 2

    def test_a_revised_document_creates_new_rows_not_an_overwrite(self, tmp_path) -> None:
        """Supersession is a new record with a new content_sha256, never a
        destructive edit of the old one."""
        store = FactPortCallStore(path=tmp_path / "fact_port_call.jsonl")
        store.append_many([_row(sha="original", idx=0, total_qty_t=100.0)])
        store.append_many([_row(sha="revised", idx=0, total_qty_t=200.0)])
        all_rows = store.read_all()
        assert len(all_rows) == 2
        totals = sorted(r.total_qty_t for r in all_rows)
        assert totals == [100.0, 200.0]

    def test_never_truncates_existing_lines(self, tmp_path) -> None:
        store = FactPortCallStore(path=tmp_path / "fact_port_call.jsonl")
        store.append_many([_row(sha="first", idx=0)])
        original_content = store.path.read_text(encoding="utf-8")
        store.append_many([_row(sha="second", idx=0)])
        new_content = store.path.read_text(encoding="utf-8")
        assert new_content.startswith(original_content)


class TestQuery:
    def test_filter_by_port(self, tmp_path) -> None:
        store = FactPortCallStore(path=tmp_path / "fact_port_call.jsonl")
        store.append_many([
            _row(sha="a", idx=0, port=PortEnum.PARADIP),
            _row(sha="b", idx=0, port=PortEnum.VIZAG),
        ])
        result = store.query(port=PortEnum.PARADIP)
        assert len(result) == 1
        assert result[0].port is PortEnum.PARADIP

    def test_quarantined_rows_excluded_by_default(self, tmp_path) -> None:
        store = FactPortCallStore(path=tmp_path / "fact_port_call.jsonl")
        store.append_many([
            _row(sha="clean", idx=0, quarantine_reason=None),
            _row(sha="dirty", idx=0, quarantine_reason="checksum failed"),
        ])
        default_result = store.query()
        assert len(default_result) == 1
        assert default_result[0].quarantine_reason is None

        with_quarantined = store.query(include_quarantined=True)
        assert len(with_quarantined) == 2

    def test_filter_by_date_range_uses_arrival_ts(self, tmp_path) -> None:
        from datetime import date

        store = FactPortCallStore(path=tmp_path / "fact_port_call.jsonl")
        store.append_many([
            _row(sha="jan", idx=0, arrival_ts=datetime(2026, 1, 1, 0, 0, tzinfo=UTC)),
            _row(sha="aug", idx=0, arrival_ts=datetime(2026, 8, 1, 0, 0, tzinfo=UTC)),
        ])
        result = store.query(date_from=date(2026, 6, 1))
        assert len(result) == 1
        assert result[0].arrival_ts.month == 8
