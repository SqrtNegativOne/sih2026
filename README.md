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

The desk **requires a sign-in**. On a deployment with no accounts yet it offers to create the
first one, an administrator — about twenty seconds, and it explains itself — so a fresh clone
still works end to end with nothing configured.

To reopen it for a demo where signing in is friction with no audience:

```
set DESK_REQUIRE_AUTH=0
```
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
| `DESK_REQUIRE_AUTH` | `0` to open the desk to anyone. Default: sign-in required. |
| `DESK_AUTH_DB` | Path to the account database. Default: `raw_data/auth/desk.sqlite3` (gitignored). |
| `DESK_COOKIE_SECURE` | `1` when serving over https. Off by default because a Secure cookie is never stored on plain http, so defaulting it on would silently break local runs. |
| `DESK_CORS_ORIGINS` | Comma-separated origins allowed to send credentials. Needed only when the frontend is served from a different origin than the API. |
| `DESK_ALERT_INTERVAL_SECONDS` | How often standing alerts are evaluated. Default 900 (15 minutes). |
| `DESK_DISABLE_ALERT_LOOP` | `1` to stop the background alert evaluation. |
| `DESK_RATE_REFRESH_SECONDS` | How often the day's Baltic rates are fetched. Default 86400 (daily). |
| `DESK_DISABLE_RATE_REFRESH` | `1` to stop the desk fetching rates at all — for an air-gapped run, or to drive `data_builders.harvest_handybulk` from cron instead. |

## Keeping the market data current

The rate series everything depends on — the forecast, the LOCK/WAIT ceiling, the Period Cover
benchmark, the standing alerts — advances on its own now. The backend fetches the day's Baltic
index levels and time-charter averages once every 24 hours, folds anything new into
`src/data/master_long.parquet`, and re-evaluates standing alerts immediately, so a new figure can
fire a watch the moment it lands.

To run it by hand:

```
uv run python -m data_builders.harvest_handybulk
```

Three properties worth knowing:

- **A date already stored is never rewritten.** The stored figure is what the models trained on and
  what past recommendations were priced against. If the source ever disagrees with stored history,
  the difference is logged for a human and not applied.
- **It only touches the nine series that source publishes.** A daily job must not be able to reshape
  the rest of the dataset as a side effect.
- **It is offline-safe.** A failed fetch leaves everything exactly as it was and reports "no new
  data" — which is also what a weekend looks like.
| `DESK_ALERT_INTERVAL_SECONDS` | How often standing alerts are evaluated. Default 900 (15 minutes). |
| `DESK_DISABLE_ALERT_LOOP` | `1` to stop the background evaluation loop — for tests, or to drive evaluation from cron against `POST /alerts/evaluate` instead. |

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
