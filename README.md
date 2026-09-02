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

## Accounts and sign-in

The desk **runs with no accounts and no sign-in by default**, and that is deliberate: a fresh
clone has to work end to end with nothing configured. Everything above stays true as written.

The account system is fully built and usable in either mode. To require a sign-in, set one
environment variable on the backend:

```
set DESK_REQUIRE_AUTH=1
```

Then open the desk. With no accounts yet it offers to create the first one, an administrator.
There is **no default password and no seeded account anywhere in this repository** — a well-known
first-run credential is the single most reliably exploited thing in self-hosted software, so the
first admin is created by whoever sets the deployment up, with a password they choose.

Three roles, and `src/auth/models.py` explains why there are three rather than two:

| Role | Can |
|---|---|
| `viewer` | Run and read everything — quotes, season plans, fragility, portfolio, the ledger |
| `chartering_manager` | The above, plus recording what a fixture actually achieved (their name goes on the ledger line) |
| `admin` | The above, plus managing accounts and resetting the ledger |

Account management is admin-only in **both** modes: "this deployment is open" is a statement about
the desk, never about the account system.

Other environment variables, all optional:

| Variable | Effect |
|---|---|
| `DESK_REQUIRE_AUTH` | `1` to require a sign-in for every route. Default: open. |
| `DESK_AUTH_DB` | Path to the account database. Default: `raw_data/auth/desk.sqlite3` (gitignored). |
| `DESK_COOKIE_SECURE` | `1` when serving over https. Off by default because a Secure cookie is never stored on plain http, so defaulting it on would silently break local runs. |
| `DESK_CORS_ORIGINS` | Comma-separated origins allowed to send credentials. Needed only when the frontend is served from a different origin than the API. |

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
