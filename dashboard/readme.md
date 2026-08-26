# Freebies Ops Dashboard

Phase 4 management dashboard for the developer-freebies aggregation system.
Next.js 14 (App Router) + Tailwind CSS + Lucide icons, reading directly from the
same PostgreSQL database the pipeline writes to. Server Components and API
routes only — **zero database credentials ever reach the browser**.

## Stack

- Next.js 14 App Router, React 18, TypeScript (strict)
- Tailwind CSS 3.4, custom dark token set (bronze accent `#D8B46A`)
- `pg` Pool singleton server-side (`lib/db.ts`, tagged `server-only`)
- Auth: shared `DASHBOARD_API_KEY` — Edge middleware (Web Crypto, constant-time)
  + double-check on the write route (node crypto)

## Views

| Route | Purpose |
|---|---|
| `/` | Overview: active offers, pending queue, total dispatches, last run status, 7-day discovery bars, category split |
| `/offers` | Feed with filters: search, category, verification status, active/expired toggle |
| `/sources` | Adapter health, sync watermarks (`channel_cursors`), discovered channels, recent runs |
| `/ingest` | Manual paste form → writes `raw_items` under the `manual:dashboard` source |
| `/login` | Single operator gate (sets httpOnly cookie) |

API routes: `GET /api/stats` · `GET /api/offers` · `GET /api/sources` ·
`POST /api/ingest` · `POST /api/login` · `GET /api/logout`.
All data pages and read routes are `force-dynamic` (no build-time DB access).

## Run

```bash
cd "E:\AUTOMATION\automation system\dashboard"
npm install
copy .env.example .env        # set DATABASE_URL + DASHBOARD_API_KEY

npm run dev        # http://localhost:3000
# or
npm run build && npm run start
```

`DASHBOARD_API_KEY` can be any long random string; keep it out of git.

First visit → `/login`, enter the key. The cookie lasts 30 days.

## Data flow for manual ingestion

`POST /api/ingest` → upserts source `manual:dashboard` → inserts
`raw_items(external_id = manual-<ts>)` → next `python run.py pipeline` run picks
it up: normalize → verify (liveness probe before LLM extraction) → dispatch to
Discord. Nothing else to do in the UI.

## Extend

- **Colors / fonts:** `tailwind.config.ts` tokens + `app/globals.css` variables.
- **Category palette:** `components/badges.tsx` (`CATEGORY_COLORS`) — keep in sync
  with the Python dispatcher's `CATEGORY_EMOJI/DISCORD_COLORS`.
- **New KPI:** add a query function in `lib/queries.ts`, render in `app/page.tsx`.
- **Page:** create `app/<name>/page.tsx` (server component) + optional API route.
- **Deploy on Heroku:** re-use the pipeline app; buildpack `heroku/nodejs`, env
  `DATABASE_URL` already set, add `DASHBOARD_API_KEY`. Run with a web dyno.