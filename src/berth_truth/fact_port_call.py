"""The normalised operational fact table -- P1 §6.

One row per vessel call, from any source, any port. All fields nullable
except identity/provenance: an absent field stays ``None``, never a default
or a guess. Append-only, content-addressed, never overwritten -- a corrected
later document creates a new record; supersession is a fact to record, not
an edit to apply destructively (same principle BT-1's constraint register
already uses for effective-dated editions).

Consumed by P2 (empirical wait/handling distributions) and P3 (vessel-class
inference from observed dimensions) -- the read API here is deliberately
shaped for both: filter by port, date range, vessel class, commodity.
"""
from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, field_serializer, field_validator

from berth_truth.sources import SourceQuality
from opt.network import PortEnum

__all__ = [
    "CargoKind",
    "EvidenceClass",
    "FactPortCall",
    "FactPortCallStore",
    "LoadDischarge",
    "classify_cargo",
    "distinct_calls",
]

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
FACT_PORT_CALL_LOG: Final[Path] = REPO_ROOT / "raw_data" / "berth_truth" / "fact_port_call.jsonl"

EvidenceClass = Literal["DIRECTLY_REPORTED", "SNAPSHOT_TRANSITION"]
"""DIRECTLY_REPORTED: the source document itself states this fact (Paradip's
own norm/actual/timestamp columns). SNAPSHOT_TRANSITION: derived by
differencing two point-in-time captures (e.g. a vessel present at anchorage
in one Visakhapatnam capture and gone in the next -> inferred departure).
Never conflated: a derived fact is weaker evidence than a directly reported
one, and a caller must be able to tell which it's looking at."""

LoadDischarge = Literal["LOAD", "DISCHARGE"]


class FactPortCall(BaseModel):
    """One vessel call. Identity is ``(content_sha256, row_index)`` -- the
    same content re-ingested twice produces the same key and is therefore
    not duplicated (idempotent ingestion); a genuinely revised document gets
    a new content_sha256 and so a new, additional record, never an
    overwrite."""

    model_config = ConfigDict(frozen=True)

    port: PortEnum
    """opt.network.PortEnum's members carry a ``Port`` object as their
    value (not a plain string), so it does not round-trip through JSON via
    Pydantic's default enum handling -- confirmed directly: dumping and
    re-validating a real row raised a ValidationError. No existing model in
    the codebase stores a raw PortEnum field through JSON (checked), so
    this is a new problem, not a missed precedent. Serialised by name only
    (see the validator/serializer pair below), never by its Port value."""
    terminal: str | None = None
    berth_or_point: str | None = None
    """The berth id, OR the transfer/anchorage point name for a LIGHTERAGE
    / ANCHORAGE_TRANSFER port (see port_master.OperationalModel) -- never a
    fabricated berth number for a location that has none."""

    vessel_name: str | None = None
    imo: str | None = None
    loa_m: float | None = None
    beam_m: float | None = None
    arrival_draft_m: float | None = None
    vessel_class_inferred: str | None = None
    """Named _inferred_ deliberately, not vessel_class: dimensions alone are
    not exact vessel-particulars ground truth without DWT/IMO/an external
    vessel record. See P3 -- OBSERVED_DIMENSIONS -> INFERRED_VESSEL_CLASS is
    a modelling step that belongs to P3, not a fact this table asserts."""

    cargo_raw: str | None = None
    commodity_class: str | None = None
    load_discharge: LoadDischarge | None = None
    shipper: str | None = None
    receiver: str | None = None
    stevedore: str | None = None

    arrival_ts: datetime | None = None
    ready_ts: datetime | None = None
    berth_ts: datetime | None = None
    sail_ts: datetime | None = None
    eta_ts: datetime | None = None
    etd_ts: datetime | None = None

    total_qty_t: float | None = None
    handled_qty_t: float | None = None
    balance_qty_t: float | None = None
    norm_tpd: float | None = None
    actual_tpd: float | None = None

    source_url: str
    source_doc_date: date | None = None
    source_quality: SourceQuality
    retrieved_at: datetime
    content_sha256: str
    row_index: int
    parser_version: str
    extraction_confidence: float | None = None
    evidence_class: EvidenceClass = "DIRECTLY_REPORTED"
    quarantine_reason: str | None = None
    """Non-null means this row failed a validation rule (e.g. total !=
    handled + balance) and must not be trusted for downstream statistics --
    but it is still retained, never dropped, so the raw evidence is never lost."""

    @property
    def is_quarantined(self) -> bool:
        return self.quarantine_reason is not None

    @field_validator("port", mode="before")
    @classmethod
    def _port_from_name(cls, value: object) -> object:
        """Accepts a PortEnum instance (the normal in-memory construction
        path) or its stored name string (the JSON round-trip path) -- never
        the Port value dict Pydantic's default enum handling would attempt
        to match, which does not round-trip reliably."""
        if isinstance(value, str):
            return PortEnum[value]
        return value

    @field_serializer("port")
    def _serialize_port(self, port: PortEnum) -> str:
        return port.name


@dataclass(frozen=True)
class FactPortCallStore:
    """Append-only JSONL store, one line per row. Mirrors
    berth_truth.store's append-only ledger pattern rather than introducing a
    new persistence idiom."""

    path: Path = FACT_PORT_CALL_LOG

    def append_many(self, rows: Iterable[FactPortCall]) -> int:
        """Append every row not already present by ``(content_sha256,
        row_index)``. Returns the count actually written -- idempotent
        re-ingestion of the same capture writes zero new rows."""
        existing = self._existing_keys()
        to_write = [r for r in rows if (r.content_sha256, r.row_index) not in existing]
        if not to_write:
            return 0
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            for row in to_write:
                handle.write(row.model_dump_json() + "\n")
        return len(to_write)

    def _existing_keys(self) -> set[tuple[str, int]]:
        if not self.path.exists():
            return set()
        keys: set[tuple[str, int]] = set()
        with self.path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                keys.add((record["content_sha256"], record["row_index"]))
        return keys

    def read_all(self) -> tuple[FactPortCall, ...]:
        if not self.path.exists():
            return ()
        rows: list[FactPortCall] = []
        with self.path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    rows.append(FactPortCall.model_validate_json(line))
        return tuple(rows)

    def query(
        self,
        *,
        port: PortEnum | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        vessel_class: str | None = None,
        commodity_class: str | None = None,
        include_quarantined: bool = False,
    ) -> tuple[FactPortCall, ...]:
        """Read-path shaped for both P2 (wait/handling distributions, needs
        port + date range, sometimes + vessel_class/commodity) and P3
        (class inference, needs port + vessel_class across all dates)."""
        rows = self.read_all()

        def _row_date(row: FactPortCall) -> date | None:
            for ts in (row.arrival_ts, row.berth_ts, row.eta_ts):
                if ts is not None:
                    return ts.date()
            return row.source_doc_date

        def matches(row: FactPortCall) -> bool:
            if not include_quarantined and row.is_quarantined:
                return False
            if port is not None and row.port is not port:
                return False
            if vessel_class is not None and row.vessel_class_inferred != vessel_class:
                return False
            if commodity_class is not None and row.commodity_class != commodity_class:
                return False
            if date_from is not None or date_to is not None:
                rd = _row_date(row)
                if rd is None:
                    return False
                if date_from is not None and rd < date_from:
                    return False
                if date_to is not None and rd > date_to:
                    return False
            return True

        return tuple(r for r in rows if matches(r))


def default_store() -> FactPortCallStore:
    return FactPortCallStore()


# ---------------------------------------------------------------------------
# F-10 / F-11 / F-12 fix: one real vessel call, correctly counted once, and
# with its trade correctly told apart from an unrelated one.
# ---------------------------------------------------------------------------
#
# These daily-traffic-report sources re-list a vessel in EVERY report issued
# while it is still in port -- a ship that lingers 5 days appears in 5
# separate documents, hence 5 separate FactPortCall rows, each one a real,
# distinctly-sourced record (correctly NOT deduplicated by
# FactPortCallStore.append_many, which is keyed on document content, not on
# vessel identity -- collapsing that at ingestion would destroy the audit
# trail of which document said what, on which day). Verified directly
# against the real Paradip log: 1,224 stored rows are only 304 distinct
# real port calls by (vessel, arrival) -- one vessel appears 56 times.
#
# A statistic computed over the raw rows is therefore length-biased: a ship
# that waited longer is *reported on more often* and so counts more times
# in a naive percentile/median. distinct_calls() below is the query-time
# fix -- collapse to one row per real call, keeping the most complete
# version (the latest source document, since later reports tend to have
# actual_tpd/berth_ts filled in that earlier ones, filed while the vessel
# was still working, do not). Deliberately NOT changed in
# FactPortCallStore.query()'s own default output: that function also backs
# the raw "recent vessel calls" audit table on the Port Twin screen, where
# seeing every real document snapshot is the point.


def distinct_calls(rows: Iterable[FactPortCall]) -> tuple[FactPortCall, ...]:
    """Collapse repeated report sightings of the same real vessel call down
    to one row each -- see the module note above for why this exists and
    why it is not the default behaviour of ``FactPortCallStore.query()``.

    Identity is ``(vessel_name, arrival_ts)``: the same ship arriving at
    the same real moment. A row with either field missing can't be matched
    to any other row, so it passes through unchanged and uncounted against
    -- never silently dropped. Among genuine duplicates, the one with the
    latest ``source_doc_date`` wins (falling back to whichever was seen
    last when dates tie or are also missing), since later reports on an
    ongoing or completed call tend to carry more complete figures.
    """
    best: dict[tuple[str, datetime], FactPortCall] = {}
    passthrough: list[FactPortCall] = []
    for row in rows:
        if row.vessel_name is None or row.arrival_ts is None:
            passthrough.append(row)
            continue
        key = (row.vessel_name, row.arrival_ts)
        current = best.get(key)
        if current is None:
            best[key] = row
            continue
        current_date = current.source_doc_date
        row_date = row.source_doc_date
        if row_date is not None and (current_date is None or row_date >= current_date):
            best[key] = row
    return tuple(best.values()) + tuple(passthrough)


CargoKind = Literal["dry_bulk", "liquid_or_gas", "container", "unknown"]

#: Keyword sniff on the free-text cargo_raw field -- the only real signal
#: available: commodity_class is a declared model field but is null on
#: every real row ingested so far (checked directly, not assumed). This is
#: a heuristic, not a controlled vocabulary match, and says so via the
#: "unknown" fallback rather than guessing. Order matters: checked
#: liquid/container before dry-bulk since a real cargo_raw string is
#: sometimes a compound description (e.g. a berth remark appended) where a
#: dry-bulk-looking substring could coincidentally appear in an otherwise
#: liquid entry -- none observed in practice, but liquid/container are
#: checked first as the more specific, less ambiguous signal regardless.
_LIQUID_OR_GAS_KEYWORDS: Final[tuple[str, ...]] = (
    "OIL", "H.S.D", "HSD", "LPG", "NAPHTHA", "CRUDE", "PETROL", "DIESEL",
    "ATF", "SKO", "PROPANE", "BUTANE", "SPIRIT", "MS ", "LNG",
)
_CONTAINER_KEYWORDS: Final[tuple[str, ...]] = ("CONTAINER",)
_DRY_BULK_KEYWORDS: Final[tuple[str, ...]] = (
    "COAL", "ORE", "LIMESTONE", "COKE", "CLINKER", "BAUXITE", "FERTIL",
    "GRAIN", "ALUMINA", "GYPSUM", "DOLOMITE",
)


def classify_cargo(cargo_raw: str | None) -> CargoKind:
    """Best-effort real classification from the cargo's own free-text
    description -- "unknown" (never a guessed default) when nothing
    matches or the field itself is missing. See the module note above for
    why this exists: mixing an oil tanker's mooring-buoy draft or a liquid
    berth's throughput into a dry-bulk statistic silently corrupts it."""
    if not cargo_raw:
        return "unknown"
    upper = cargo_raw.upper()
    if any(k in upper for k in _LIQUID_OR_GAS_KEYWORDS):
        return "liquid_or_gas"
    if any(k in upper for k in _CONTAINER_KEYWORDS):
        return "container"
    if any(k in upper for k in _DRY_BULK_KEYWORDS):
        return "dry_bulk"
    return "unknown"
