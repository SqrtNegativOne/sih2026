# Deployment: Render (backend) + Vercel (frontend)

This is a two-service deploy, not a single platform, because the two halves
have genuinely different resource shapes: the backend loads `xgboost` and
`polars` plus the parquet/model files under `src/data/` into a long-lived
process at startup (wrong fit for a serverless function with a
cold-start-per-request model), while the frontend is a static Vite build that
any static host can serve. `torch` is a project dependency but is not part of
this: it's only ever imported by `src/ml/model_lstm.py`, an offline training
script nothing in `backend/main.py`'s import chain reaches, so it costs
install time but not runtime memory.

## Why the frontend proxies through Vercel instead of calling Render directly

The backend's session cookie is set `samesite="lax"` (`backend/main.py`,
`response.set_cookie`) — deliberately, so a cross-site form post or embedded
image can't ride an authenticated session. That is correct for same-origin
use but means the cookie is silently dropped by the browser on any
**cross-site** `fetch`, which is exactly what a Vercel frontend calling a
`*.onrender.com` backend directly would be. Rather than loosening that cookie
policy for every deployment, `frontend/vercel.json` makes Vercel itself proxy
`/api/*` to the Render backend server-side — the browser only ever talks to
the Vercel origin, so the request is same-origin from its point of view, the
existing `samesite="lax"` cookie keeps working unmodified, and the backend's
`DESK_CORS_ORIGINS` (see `backend/main.py`) never needs to be set for this
path at all. This is the same shape `frontend/src/lib/api.ts`'s own F-39
comment already anticipated ("something in front of the static files...
proxies `/api` itself") — no frontend or backend code changed to support it.

## One-time setup

1. **Backend on Render.** Dashboard → New → Blueprint → connect this repo →
   Render reads `render.yaml` at the repo root. Confirm the service name is
   `sih2026-backend` (or note whatever name Render actually assigns if that
   one is taken — service subdomains are global across all Render accounts).
   First build installs `uv`, runs `uv sync --frozen`, and the health check
   hits `/docs` (the app has no route at `/`, so that would look like a
   permanent failure to Render's default health check).

   The free plan (`render.yaml`'s default) has 512MB RAM and spins down after
   15 minutes idle. **Confirmed in production on 2026-09-07**: Render's own
   "exceeded its memory limit" alert fired and auto-restarted the instance —
   a request landing right after reads as the app hanging or failing, when
   what actually happened is the process got killed and is restarting. The
   cause is `xgboost`'s three loaded models plus `polars` holding the on-disk
   market/port-call data in memory across everything `backend/main.py`
   imports at startup — not `torch` (see above). Fix: Render dashboard →
   the service → Settings → Instance Type → `starter` or above. This is a
   billing change only the account owner can make, so it isn't set in
   `render.yaml` by default; slimming the dependency set is not the right
   fix here, the data this backend holds in memory to answer a quote
   honestly (real market history, real port-call records) is the point of
   the product.

2. **Get the real backend URL** from the Render dashboard once it deploys
   (`https://<service-name>.onrender.com`). If it differs from
   `sih2026-backend`, update the `destination` in `frontend/vercel.json` to
   match before the next step.

3. **Frontend on Vercel.** Dashboard → Add New → Project → import this repo →
   set **Root Directory** to `frontend` → framework preset Vite (build
   command `npm run build`, output directory `dist` — Vercel detects both
   automatically once the root directory is set). Vercel picks up
   `frontend/vercel.json`'s rewrite on that same import; no environment
   variables are required for the proxy to work.

## Updating either service later

Both are connected directly to this GitHub branch — a normal `git push` to
it triggers a new build and deploy on each platform independently. There is
no separate deploy step to remember.

## What was deliberately NOT done

- **No `VITE_API_BASE_URL` set.** That variable exists in `lib/api.ts`
  specifically for a split-origin deployment that calls the backend directly
  from the browser; the Vercel-proxy approach above makes it unnecessary, and
  setting it would re-introduce the cross-site cookie problem this setup
  avoids.
- **No `DESK_CORS_ORIGINS` set on Render.** Same reason — the browser never
  makes a cross-origin request to Render under this setup, so there is
  nothing for CORS to allow.
- **No change to `raw_data/`'s harvester-only network policy.** Both
  `raw_data/` and `src/data/` (including the three `xgb_h{7,30,90}.ubj`
  models) are already committed to the repo and ship with the deploy as-is —
  the backend does not fetch or rebuild any of it at request time, on Render
  or anywhere else.
