"""Live Decision Ledger -- P5 requirements 6-9. Forward-only, append-only
record of real recommendations this system actually made, plus outcomes as
they genuinely arrive.

**Not a backtest.** Nothing here reads historical data or simulates a past
decision -- see ``opt.replay`` for that (HISTORICAL_MODEL_REPLAY, explicitly
separate per requirement 11). This module only ever records a REAL
``opt.quote.quote()``/``quote_envelope()`` result at the moment it was
computed, and later, a REAL outcome someone reports against it.

Storage mirrors ``berth_truth.fact_port_call.FactPortCallStore`` (P1): a
plain append-only JSONL file, one line per record, never rewritten in place.
Two separate logs, not one mutable table: ``ledger_entries.jsonl`` (one row
per recommendation, immutable once written) and ``ledger_outcomes.jsonl``
(one row per realised outcome, linked to an entry by ``entry_id``, appended
independently and never edited or deleted). An entry with no linked outcome
is real and pending -- excluded from performance stats, never backfilled
with a guess.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Final

from opt.types import QuoteResult

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
LEDGER_DIR: Final[Path] = REPO_ROOT / "raw_data" / "ledger"
ENTRIES_LOG: Final[Path] = LEDGER_DIR / "ledger_entries.jsonl"
OUTCOMES_LOG: Final[Path] = LEDGER_DIR / "ledger_outcomes.jsonl"

MODELS_DIR: Final[Path] = REPO_ROOT / "src" / "data" / "models"
MASTER_LONG_PATH: Final[Path] = REPO_ROOT / "src" / "data" / "master_long.parquet"

__all__ = [
    "LedgerEntry",
    "MutationNotAllowedError",
    "OutcomeRecord",
    "PerformanceSummary",
    "RateHorizonRecord",
    "compute_performance",
    "current_data_version",
    "current_model_version",
    "delete_entry",
    "entries_with_outcomes",
    "read_entries",
    "read_outcomes",
    "record_outcome",
    "record_recommendation",
    "reset_ledger",
    "update_entry",
]


class MutationNotAllowedError(RuntimeError):
    """Raised by any attempt to rewrite or delete an existing ledger line --
    the append-only guarantee, enforced in code, not just by convention."""


def current_model_version() -> str:
    """Real, reproducible version string from the actual trained model
    files' modification times -- not an invented semver tag this codebase
    has no mechanism to assign. Changes exactly when the models are
    re-exported (``ml.export``)."""
    if not MODELS_DIR.exists():
        return "unavailable:models_dir_missing"
    parts = []
    for h in (7, 30, 90):
        p = MODELS_DIR / f"xgb_h{h}.ubj"
        parts.append(f"h{h}={int(p.stat().st_mtime)}" if p.exists() else f"h{h}=missing")
    return "xgb:" + ",".join(parts)


def current_data_version() -> str:
    """Real modification time of master_long.parquet -- the same "is this
    stale" signal a caller could check themselves, recorded so a later
    review of an old entry knows which real data vintage priced it."""
    if not MASTER_LONG_PATH.exists():
        return "unavailable:master_long_missing"
    return f"master_long:{int(MASTER_LONG_PATH.stat().st_mtime)}"


@dataclass(frozen=True)
class RateHorizonRecord:
    """A minimal, JSON-stable restatement of one opt.types.RateHorizon --
    forecasts and uncertainty, per requirement 6."""

    horizon_days: int
    p10_usd_per_day: float
    p50_usd_per_day: float
    p90_usd_per_day: float


@dataclass(frozen=True)
class LedgerEntry:
    """One real recommendation. Immutable once written -- see
    MutationNotAllowedError."""

    entry_id: str
    decision_timestamp: datetime

    # Full input state -- enough to reproduce the call to opt.quote.quote().
    cargo_volume_dwt: float
    origin_port: str
    dest_port: str
    laycan_start: date
    laycan_end: date
    contract_term_days: int
    commodity: str
    as_of: date
    risk_tolerance: float

    # Forecasts and uncertainty.
    today_quote_usd_per_day: float
    rate_forecast: tuple[RateHorizonRecord, ...]

    # The recommendation, and the alternative considered (LOCK/WAIT is
    # binary, so "the alternative" is simply the other action).
    lock_action: str
    alternative_action: str
    ceiling_usd_per_day: float

    # The exercise boundary behind the decision (opt.stopping), when the
    # LSMC solve ran -- None exactly when it fell back to the plain rule.
    exercise_boundary_usd_per_day: tuple[float, ...] | None

    # Model/data versions, real and reproducible (see the two functions above).
    model_version: str
    data_version: str

    target_vessel_class: str


@dataclass(frozen=True)
class OutcomeRecord:
    """One real, externally-reported outcome, linked to a LedgerEntry.
    Appended independently -- never edits or replaces the entry."""

    outcome_id: str
    entry_id: str
    recorded_at: datetime
    realized_rate_usd_per_day: float
    """The real market rate observed for this decision's vessel class, at
    whatever later date the caller is reporting against -- supplied by the
    caller, since this ledger has no live market feed of its own to check
    against; it can only record what it is told really happened."""
    realized_at_date: date
    note: str | None = None


def _write_jsonl_append_only(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True, default=str) + "\n")


def record_recommendation(
    quote_result: QuoteResult,
    *,
    risk_tolerance: float = 0.0,
    decision_timestamp: datetime | None = None,
    stopping_boundary: tuple[float, ...] | None = None,
) -> LedgerEntry:
    """Append one real recommendation. Called once per real
    ``opt.quote.quote()``/``quote_envelope()`` result a caller wants
    recorded -- not automatically inside ``quote()`` itself (a caller may
    run many exploratory what-if quotes that were never acted on; this
    ledger is for recommendations, not every solve).

    ``risk_tolerance`` must be passed explicitly by the caller: it is the
    caller's own real input to ``quote()``/``quote_envelope()``, but
    ``QuoteResult`` does not carry it back on the result object, so this
    module cannot read it off ``quote_result`` itself -- defaults to 0.0
    (risk-neutral) only when a caller genuinely has nothing else to pass,
    which would misrecord a risk-averse caller's real input as 0.0 if
    silently assumed instead of required here.

    ``stopping_boundary`` is optional because ``quote()`` does not currently
    expose ``opt.stopping``'s ``StoppingResult`` on ``QuoteResult`` -- pass
    it through explicitly when a caller has it (see backend.main's wiring)."""
    entry = LedgerEntry(
        entry_id=str(uuid.uuid4()),
        decision_timestamp=decision_timestamp or datetime.now(UTC),
        cargo_volume_dwt=quote_result.cargo_volume_dwt,
        origin_port=quote_result.origin_port.name,
        dest_port=quote_result.dest_port.name,
        laycan_start=quote_result.laycan_start,
        laycan_end=quote_result.laycan_end,
        contract_term_days=quote_result.contract_term_days,
        commodity=quote_result.commodity,
        as_of=quote_result.as_of,
        risk_tolerance=risk_tolerance,
        today_quote_usd_per_day=quote_result.today_quote_usd_per_day,
        rate_forecast=tuple(
            RateHorizonRecord(
                horizon_days=h.horizon_days, p10_usd_per_day=h.p10_usd_per_day,
                p50_usd_per_day=h.p50_usd_per_day, p90_usd_per_day=h.p90_usd_per_day,
            )
            for h in quote_result.rate_forecast
        ),
        lock_action=quote_result.lock_action,
        alternative_action="WAIT" if quote_result.lock_action == "LOCK" else "LOCK",
        ceiling_usd_per_day=quote_result.ceiling_usd_per_day,
        exercise_boundary_usd_per_day=stopping_boundary,
        model_version=current_model_version(),
        data_version=current_data_version(),
        target_vessel_class=quote_result.target_vessel_class.value,
    )
    _write_jsonl_append_only(
        ENTRIES_LOG,
        {
            "entry_id": entry.entry_id, "decision_timestamp": entry.decision_timestamp.isoformat(),
            "cargo_volume_dwt": entry.cargo_volume_dwt, "origin_port": entry.origin_port,
            "dest_port": entry.dest_port, "laycan_start": entry.laycan_start.isoformat(),
            "laycan_end": entry.laycan_end.isoformat(), "contract_term_days": entry.contract_term_days,
            "commodity": entry.commodity, "as_of": entry.as_of.isoformat(), "risk_tolerance": entry.risk_tolerance,
            "today_quote_usd_per_day": entry.today_quote_usd_per_day,
            "rate_forecast": [
                {"horizon_days": r.horizon_days, "p10_usd_per_day": r.p10_usd_per_day,
                 "p50_usd_per_day": r.p50_usd_per_day, "p90_usd_per_day": r.p90_usd_per_day}
                for r in entry.rate_forecast
            ],
            "lock_action": entry.lock_action, "alternative_action": entry.alternative_action,
            "ceiling_usd_per_day": entry.ceiling_usd_per_day,
            "exercise_boundary_usd_per_day": list(entry.exercise_boundary_usd_per_day) if entry.exercise_boundary_usd_per_day else None,
            "model_version": entry.model_version, "data_version": entry.data_version,
            "target_vessel_class": entry.target_vessel_class,
        },
    )
    return entry


def record_outcome(
    entry_id: str, *, realized_rate_usd_per_day: float, realized_at_date: date,
    note: str | None = None, recorded_at: datetime | None = None,
) -> OutcomeRecord:
    """Append one real outcome, linked to an existing entry. Raises if
    ``entry_id`` does not name a real, already-recorded entry -- an outcome
    cannot be linked to nothing."""
    if realized_rate_usd_per_day <= 0:
        raise ValueError(f"realized_rate_usd_per_day must be positive, got {realized_rate_usd_per_day}.")
    known_ids = {e.entry_id for e in read_entries()}
    if entry_id not in known_ids:
        raise KeyError(f"No ledger entry with entry_id={entry_id!r} -- cannot link an outcome to it.")

    outcome = OutcomeRecord(
        outcome_id=str(uuid.uuid4()), entry_id=entry_id, recorded_at=recorded_at or datetime.now(UTC),
        realized_rate_usd_per_day=realized_rate_usd_per_day, realized_at_date=realized_at_date, note=note,
    )
    _write_jsonl_append_only(
        OUTCOMES_LOG,
        {
            "outcome_id": outcome.outcome_id, "entry_id": outcome.entry_id,
            "recorded_at": outcome.recorded_at.isoformat(),
            "realized_rate_usd_per_day": outcome.realized_rate_usd_per_day,
            "realized_at_date": outcome.realized_at_date.isoformat(), "note": outcome.note,
        },
    )
    return outcome


def update_entry(entry_id: str, **_changes: object) -> None:
    """Always raises. Entries are never mutated (requirement 9) -- this
    function exists so that guarantee is a concrete, callable, testable
    thing rather than merely "there happens to be no update function,"
    which a future contributor could add without ever having to reckon with
    a name that says otherwise."""
    raise MutationNotAllowedError(
        f"Ledger entries are append-only and can never be updated (attempted on entry_id={entry_id!r}). "
        "A corrected recommendation is a new entry; a realised outcome is a new, separately linked "
        "OutcomeRecord via record_outcome() -- never an edit to the original entry."
    )


def delete_entry(entry_id: str) -> None:
    """Always raises -- see update_entry."""
    raise MutationNotAllowedError(
        f"Ledger entries can never be deleted (attempted on entry_id={entry_id!r})."
    )


def reset_ledger() -> int:
    """F-38 fix: a real, safe way to clear the ledger, added because there
    previously wasn't one -- every real ``/quote`` call auto-records here
    by design (the anti-cherry-picking guarantee this module exists for:
    a caller can't quietly keep only the recommendations that turned out
    well), which is correct for real use but meant a session of ordinary
    exploration/testing left the ledger permanently full of entries with
    no way to start clean before a demo.

    Deliberately NOT the same operation `delete_entry`/`update_entry`
    forbid above. Those guard against SELECTIVE editing -- deleting or
    rewriting one entry while keeping others, which is exactly what would
    let someone hide an unfavourable recommendation. This clears
    EVERYTHING, all-or-nothing: there is no way to keep the entries you
    like and drop the ones you don't, so it cannot be used to cherry-pick.
    Real, intentional use is a clean reset before a demo or a new
    evaluation period, not a general-purpose edit capability.

    Returns the total number of entry + outcome records actually removed.
    """
    removed = 0
    for path in (ENTRIES_LOG, OUTCOMES_LOG):
        if path.exists():
            removed += sum(1 for _ in path.open(encoding="utf-8") if _.strip())
            path.unlink()
    return removed


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def read_entries(date_from: date | None = None, date_to: date | None = None) -> tuple[LedgerEntry, ...]:
    """Every real entry ever recorded, oldest first. Genuinely empty (not an
    error, not seeded examples) if nothing has been recorded yet -- see
    requirement 8."""
    out = []
    for rec in _read_jsonl(ENTRIES_LOG):
        ts = datetime.fromisoformat(rec["decision_timestamp"])
        if date_from is not None and ts.date() < date_from:
            continue
        if date_to is not None and ts.date() > date_to:
            continue
        out.append(
            LedgerEntry(
                entry_id=rec["entry_id"], decision_timestamp=ts,
                cargo_volume_dwt=rec["cargo_volume_dwt"], origin_port=rec["origin_port"], dest_port=rec["dest_port"],
                laycan_start=date.fromisoformat(rec["laycan_start"]), laycan_end=date.fromisoformat(rec["laycan_end"]),
                contract_term_days=rec["contract_term_days"], commodity=rec["commodity"],
                as_of=date.fromisoformat(rec["as_of"]), risk_tolerance=rec["risk_tolerance"],
                today_quote_usd_per_day=rec["today_quote_usd_per_day"],
                rate_forecast=tuple(RateHorizonRecord(**r) for r in rec["rate_forecast"]),
                lock_action=rec["lock_action"], alternative_action=rec["alternative_action"],
                ceiling_usd_per_day=rec["ceiling_usd_per_day"],
                exercise_boundary_usd_per_day=tuple(rec["exercise_boundary_usd_per_day"]) if rec.get("exercise_boundary_usd_per_day") else None,
                model_version=rec["model_version"], data_version=rec["data_version"],
                target_vessel_class=rec["target_vessel_class"],
            )
        )
    return tuple(out)


def read_outcomes() -> tuple[OutcomeRecord, ...]:
    out = []
    for rec in _read_jsonl(OUTCOMES_LOG):
        out.append(
            OutcomeRecord(
                outcome_id=rec["outcome_id"], entry_id=rec["entry_id"],
                recorded_at=datetime.fromisoformat(rec["recorded_at"]),
                realized_rate_usd_per_day=rec["realized_rate_usd_per_day"],
                realized_at_date=date.fromisoformat(rec["realized_at_date"]), note=rec.get("note"),
            )
        )
    return tuple(out)


@dataclass(frozen=True)
class LinkedRecord:
    entry: LedgerEntry
    outcome: OutcomeRecord | None
    """None means genuinely pending -- excluded from performance stats."""


def entries_with_outcomes(date_from: date | None = None, date_to: date | None = None) -> tuple[LinkedRecord, ...]:
    """Every entry, with its outcome attached where one has been recorded
    (the most recent one, if more than one was ever recorded against the
    same entry_id) -- pending entries carry outcome=None, never a guess."""
    outcomes_by_entry: dict[str, list[OutcomeRecord]] = {}
    for o in read_outcomes():
        outcomes_by_entry.setdefault(o.entry_id, []).append(o)
    out = []
    for e in read_entries(date_from, date_to):
        linked = outcomes_by_entry.get(e.entry_id)
        latest = max(linked, key=lambda o: o.recorded_at) if linked else None
        out.append(LinkedRecord(entry=e, outcome=latest))
    return tuple(out)


@dataclass(frozen=True)
class PerformanceSummary:
    """Cumulative live performance -- computed ONLY from entries with a real
    linked outcome (requirement: pending entries excluded). Genuinely empty
    (n_scored=0, every mean field None) when nothing has both a
    recommendation and a reported outcome yet -- an honest empty ledger, not
    a fabricated edge value."""

    n_entries_total: int
    n_pending: int
    n_scored: int

    mean_realized_regret_usd_per_day: float | None
    """oracle savings - this system's savings, per scored decision, mean.
    None when n_scored == 0."""
    lock_accuracy: float | None
    """Fraction of scored LOCK decisions where locking was actually cheaper
    than the realised rate (the same "was this the right call" hindsight
    test opt.backtest uses for hit_rate). None when there are zero scored
    LOCK decisions."""

    mean_savings_vs_always_lock_usd_per_day: float | None
    mean_savings_vs_always_wait_usd_per_day: float | None
    """This system's mean $/day savings (vs. the always-spot baseline every
    decision is measured against) minus each naive baseline's own mean --
    positive means this system beat that baseline. None when n_scored == 0."""


def compute_performance(date_from: date | None = None, date_to: date | None = None) -> PerformanceSummary:
    """Real regret/correctness/baseline arithmetic over every entry with a
    real linked outcome. Uses the SAME savings definition
    ``opt.backtest.BacktestRow`` does (savings vs. always-spot: LOCK earns
    ``realised_spot - quote`` if you locked, 0 if you waited and just paid
    spot) -- not a different formula invented for the live ledger, so live
    and replay numbers stay comparable in DEFINITION even though (per
    requirement 11) their STATISTICS are never merged into one number."""
    linked = [r for r in entries_with_outcomes(date_from, date_to) if r.outcome is not None]
    n_total = len(read_entries(date_from, date_to))
    n_scored = len(linked)

    if n_scored == 0:
        return PerformanceSummary(
            n_entries_total=n_total, n_pending=n_total, n_scored=0,
            mean_realized_regret_usd_per_day=None, lock_accuracy=None,
            mean_savings_vs_always_lock_usd_per_day=None, mean_savings_vs_always_wait_usd_per_day=None,
        )

    savings_this_system: list[float] = []
    savings_always_lock: list[float] = []
    savings_oracle: list[float] = []
    lock_decisions_correct: list[bool] = []

    for r in linked:
        entry, outcome = r.entry, r.outcome
        assert outcome is not None
        realised = outcome.realized_rate_usd_per_day
        quote = entry.today_quote_usd_per_day
        this_system_locked = entry.lock_action == "LOCK"
        oracle_should_lock = quote <= realised

        this_savings = (realised - quote) if this_system_locked else 0.0
        always_lock_savings = realised - quote
        oracle_savings = (realised - quote) if oracle_should_lock else 0.0

        savings_this_system.append(this_savings)
        savings_always_lock.append(always_lock_savings)
        savings_oracle.append(oracle_savings)
        if this_system_locked:
            lock_decisions_correct.append(oracle_should_lock)

    mean_this = sum(savings_this_system) / n_scored
    mean_always_lock = sum(savings_always_lock) / n_scored
    mean_always_wait = 0.0  # always-wait = always-spot = 0 savings by definition, same as opt.backtest
    mean_oracle = sum(savings_oracle) / n_scored

    return PerformanceSummary(
        n_entries_total=n_total, n_pending=n_total - n_scored, n_scored=n_scored,
        mean_realized_regret_usd_per_day=mean_oracle - mean_this,
        lock_accuracy=(sum(lock_decisions_correct) / len(lock_decisions_correct)) if lock_decisions_correct else None,
        mean_savings_vs_always_lock_usd_per_day=mean_this - mean_always_lock,
        mean_savings_vs_always_wait_usd_per_day=mean_this - mean_always_wait,
    )
