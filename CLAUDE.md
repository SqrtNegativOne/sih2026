# CLAUDE.md — house rules for the SIH2026 dry-bulk chartering repo

## What this project is
A real-forecast-driven dry-bulk chartering optimizer for SAIL: rate forecast,
LOCK/WAIT timing, vessel-class optimization, port constraints, risk flags.
Backend is Python + FastAPI (`backend/`), frontend is React + TypeScript
(`frontend/`), domain logic lives in `src/`.

## Non-negotiables — read before writing any code

1. NEVER fabricate a number. If the data to compute something honestly is not
   on disk, the correct behaviour is to return None, raise a named exception,
   or state the gap in a docstring — NOT to invent a plausible-looking value.
   `tests/test_no_synthetic_frontend_data.py` is a repo-wide regex tripwire
   that FAILS THE BUILD on `Math.random()`, `charCodeAt`, `Math.sin(` and
   similar hash-as-data tricks anywhere under `frontend/src/`. Do not add to
   its allowlist.

2. Provenance is tracked explicitly. `src/data_builders/provenance.py` defines
   OBSERVED / ESTIMATED / INFERRED / MODEL_DERIVED / DECLARED. Anything you
   compute from a real input is at best MODEL_DERIVED — never inherit the
   input's provenance. Label every new data field.

3. Docstrings carry the honest caveat. This codebase's convention is that a
   module explains what it does NOT do and why. Match that tone. Cite the real
   source (resolution number, dataset name, URL) for any external constant.

## Style
- Polars over Pandas. PyTorch over TensorFlow. `uv` as package manager/runner.
- Strict typing everywhere: full type hints, `Final` for module constants,
  `Literal` for closed string sets, Pydantic `BaseModel` with
  `model_config = ConfigDict(frozen=True)` for domain types.
- Prefer a named exception over a silent fallback. Where a fallback IS correct
  (e.g. searoute failing offline), flag it in the returned value the way
  `opt/route_trace.py` sets `is_great_circle_fallback`.

## Layout
- `raw_data/`   raw external pulls, never edited by hand
- `src/data/`   processed parquet/csv built from raw_data
- `src/data_builders/`  the scripts that do that transformation
- `src/ml/`     features, models, live forecast
- `src/opt/`    optimizer, quote, risk, stopping, landed cost
- `src/tonnage/`, `src/berth_truth/`, `src/fragility/`, `src/impact/`  moats
- `backend/main.py`  FastAPI routes (~1200 lines, all routes live here)
- `frontend/src/components/desk/`  the trading-desk panels

## Commands
- Run tests:      uv run python -m pytest -q
- Run one area:   uv run python -m pytest tests/opt -q
- Lint:           uv run ruff check src backend
- Backend:        uv run uvicorn backend.main:app --reload
- Frontend:       cd frontend && npm run dev   (proxies /api to :8000)

## Network policy for new data sources
Every external fetch must be (a) a build-time harvester under
`src/data_builders/` writing into `raw_data/`, never a per-request call from
`backend/` or `opt/`, (b) cached to disk, and (c) offline-safe — if the cache
is missing, the consumer degrades to "no signal" rather than raising into a
quote. `src/data_builders/harvest_portwatch.py` is the reference pattern.

## Change tracking
Every change made to this project — code, data, or docs — gets logged in
[`docs/12_fix_changelog.md`](docs/12_fix_changelog.md): what changed, why, and
how it was verified. That file also carries the fault-register status tracker
and dated log; read it before starting work to see what's already been fixed,
and append to its dated log (never silently edit past entries) when you finish
something.
