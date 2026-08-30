# SIH26006 — Freight Forecasting & Chartering Optimizer

Dry-bulk vessel chartering and freight-rate decision support for SAIL's East Coast India imports,
built for Smart India Hackathon problem statement SIH26006. Real market-data forecasting,
LOCK/WAIT timing, vessel-type recommendation, port-constraint checks, risk flags, decision
fragility analysis, a live/replay decision ledger, and commercial (backhaul + landed-cost)
add-ons — see `docs/plan.md` for the full design writeup.

**New to this branch?** Read [`docs/TEAM_AUDIT_AND_FEATURE_GUIDE.md`](docs/TEAM_AUDIT_AND_FEATURE_GUIDE.md)
first — what's real vs. honestly disclosed as unavailable, where to see every feature in the UI,
and the full closure report against the original audit.

## Setup (verified from a clean shell, P7)

Requires Python 3.12+ and Node 18+. **Does not require `uv`** — everything below uses the repo's
own `.venv` and `npm` directly.

```
# Backend
python -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\pip install -e .
.venv\Scripts\pip install --group dev

# Frontend
cd frontend
npm install
cd ..
```

Verified end-to-end from a genuinely fresh `python -m venv` on 2026-08-28 -- the `pip install
--upgrade pip` step is not optional: `--group` (installing `[dependency-groups]` from
`pyproject.toml`, PEP 735) needs pip 25.1+, and a freshly created venv's bundled pip (24.2 here)
does not have it and fails outright with "no such option: --group" until upgraded.

## Run

```
run.bat
```

or, equivalently, `powershell -File run.ps1`. Both start the backend (`http://127.0.0.1:8000`) and
the frontend dev server (`http://127.0.0.1:5173`, or the next free port — check the Frontend
window) in separate terminal windows, using `.venv\Scripts\uvicorn.exe` and `npm run dev` directly.

To run either half by hand instead:

```
.venv\Scripts\uvicorn.exe backend.main:app --reload
cd frontend && npm run dev
```

### A known npm optional-dependency issue (Windows)

`npm install` can, on some machines/npm versions, fail to fetch the platform-specific
`@rolldown/binding-win32-x64-msvc` optional dependency Vite's rolldown bundler needs — a documented
npm optional-dependency resolution bug, not specific to this repo. `npm run dev`/`npm run build`
then fail with a missing-binding error. If that happens:

```
npm cache clean --force
rmdir /s /q node_modules
npm install
```

If it still doesn't resolve, fetch the binding directly and place it manually:

```
npm pack @rolldown/binding-win32-x64-msvc
# extract the resulting .tgz into node_modules/@rolldown/binding-win32-x64-msvc/
```

Verified in this environment on 2026-08-28: `node_modules/@rolldown/binding-win32-x64-msvc` is
present, `npm run dev` and `npm run build` both run cleanly — the workaround above was not needed
here, and is documented as insurance for a fresh clone where it might recur, not because it
reproduced in this checkout.

## Tests

```
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m ruff check src/ tests/
cd frontend && npm run build && npx tsc --noEmit
```
