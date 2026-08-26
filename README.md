# Developer Freebies Aggregation System — Phase 2/3 Scaffold

Automated discovery, verification, and Discord dispatch of developer/student freebies.
Sources (locked): Telegram, Twitter/X, Instagram, Facebook(→ manual mirror). Deploy
target: Heroku (credits) / AWS Free Tier fallback.
**Project root: `E:\AUTOMATION\automation system\`**

```
automation system/
├── schema.sql            # full PostgreSQL schema (raw_items, offers, fingerprints, dispatches…)
├── discord_dispatcher.py # webhook rich-embed delivery (idempotent, rate-limit aware)
├── Procfile              # Heroku process types (worker only for now)
├── docker-compose.yml    # local dev stack (postgres + redis)
├── DEPLOYMENT.md         # Heroku blueprint + AWS FT fallback + cost math
├── INGESTION_SPECS.md    # per-source adapter specs (Telegram/X/IG/FB)
├── requirements.txt      # pinned dependencies
├── pytest.ini
├── .env.example          # copy to .env and fill in
├── crawler/
│   ├── db.py             # shared psycopg helpers
│   ├── normalizer.py     # URL canonicalization + text/entity normalization
│   ├── verifier.py       # Pydantic v2 Offer schema + LLM extraction + liveness + dedup
│   ├── pipeline.py       # end-to-end orchestrator (raw_items → offers → Discord)
│   └── worker.py         # always-on worker entrypoint
├── tests/                # 29 unit tests (pytest, no network/DB required)
├── adapters/
│   ├── telegram_adapter.py  # Telethon: delta pull + discovery + join loop
│   └── social_stealth.py    # Patchright: stealth identities + XHR JSON capture
└── dashboard/            # Phase 4: Next.js 14 ops console (see dashboard/README.md)
```

---

## Quickstart (PowerShell — run inside `E:\AUTOMATION\automation system\`)

```powershell
# 0. one-time setup
cd "E:\AUTOMATION\automation system"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt              # or light set: httpx pydantic "psycopg[binary]" pytest
playwright install chromium                  # only for the stealth social adapters

# 1. start the stack (Postgres + Redis)
docker compose up -d

# 2. load schema
psql "postgresql://freebies:***@localhost:5432/freebies" -f schema.sql

# 3. configure env: copy .env.example -> .env, fill it in, then load it
Get-Content .env | ForEach-Object { if ($_ -match '^([^=]+)=(.*)$') { Set-Item -Path (["ENV:",$Matches[1]] -join '') -Value $Matches[2] } }

# 4. run the unit tests (29 tests, no network/DB needed)
python -m pytest -v

# 5. full pipeline dry-run (no writes, no Discord sends)
python -m crawler.pipeline --dry-run

# 6. real run: normalize -> verify -> dedup -> write -> dispatch
python -m crawler.pipeline --limit 25
python -m crawler.pipeline --source telegram:default --no-dispatch

# 7. dispatcher standalone (safe to re-run, idempotent)
python discord_dispatcher.py --dry-run
python discord_dispatcher.py --limit 10

# 8. Telegram monitor (needs TG_API_ID/TG_API_HASH/TG_PHONE, one-time login)
python -m crawler.worker
```

Python 3.10+.

---

## How it fits together

| Stage | Code | Notes |
|---|---|---|
| Ingest | `adapters/*` → `raw_items` | adapters output one normalized JSON shape; Telegram delta-pull is watermarked per channel |
| Normalize | `crawler/normalizer.py` | redirect resolution + tracking-param strip + text/URL cleaning |
| Verify | `crawler/verifier.py` | strict Pydantic `Offer` schema, LLM JSON-mode extraction, liveness probe, hash + cosine dedup |
| Dedup | `offer_fingerprints` | sha256 URL/title hashes exact; embeddings semantic (app-side cosine) |
| Orchestrate | `crawler/pipeline.py` | raw_items → normalizer → verifier → offers → dispatcher; `--dry-run` / `--source` / `--limit` |
| Dispatch | `discord_dispatcher.py` | claims rows atomically → Discord embeds → marks `sent` (re-run = no-op) |

## Customization guide

- **Categories & colors** → `CATEGORY_STYLE` in `discord_dispatcher.py` + `CATEGORIES`/`OFFER_TYPES` in `crawler/verifier.py`.
- **Tracking params** → `TRACKING_PARAMS` / `TRACKING_PREFIXES` in `crawler/normalizer.py`.
- **LLM provider/model** → `.env`: `LLM_BASE_URL` (any OpenAI-compatible endpoint), `LLM_MODEL`, `LLM_EMBED_MODEL`.
- **Semantic-dedup threshold** → `Deduplicator(semantic_threshold=...)` in `crawler/verifier.py` (default 0.92).
- **Batch size / pacing** → `--limit`, `WEBHOOK_PACE_SECONDS` (keep <30 msg/min total).
- **Monitor cadence** → `MONITOR_CADENCE_SECONDS`, `SEARCH_CADENCE_SECONDS` in `adapters/telegram_adapter.py`.
- **Channels to watch** → `channel_cursors` rows (via `discovered_channels` review or direct SQL).
- **Search terms** → `search_terms` in the source row's `config` + term lists in `social_stealth.py`.
- **Webhook target** → env var `DISCORD_WEBHOOK_URL`.
- **Heroku deploy** → follow `DEPLOYMENT.md` verbatim.

## Quality coverage

- **Idempotency:** `UNIQUE(offer_id, channel)` + status flow guarantee no double-sends
  across re-runs or concurrent workers (`claimed_at` lease); pipeline only processes
  raw_items with no offers row; fingerprints `ON CONFLICT DO NOTHING`.
- **Rate safety:** dispatcher paces under Discord's 30 msg/min, honors 429
  `Retry-After`; LLM extractor retries 429/5xx with backoff; Telegram adapter respects
  `FloodWaitError`; social adapters enforce per-identity pacing and tarpit breakers.
- **Failure states:** one bad item never kills a pipeline run (per-item try/except +
  `runs.stats` audit row); failed webhook sends recorded as `failed` and retried later;
  verifier short-circuits liveness failures *before* paying for LLM extraction.
- **Schema strictness:** `Offer` Pydantic v2 model validates/normalizes every LLM
  output (enum fallbacks, currency casing, title cleaning, confidence bounds,
  tz-aware expiry).
- **Not covered yet (Phase 4):** dashboard UI states, responsive layout, keyboard/ARIA.

## Verification status

- ✅ 29/29 unit tests passing (`python -m pytest`) — normalizer (URL cleaning, engagement-bait
  stripping, title normalization, raw-item mapping) and verifier (Offer schema validation,
  cosine/sha256 helpers, tz-aware expiry).
- ✅ `python -m py_compile` clean on every module, verified **inside `E:\AUTOMATION\automation system\`**.
- ⚠️ Not executed end-to-end: live LLM extraction, live liveness probes, real webhook
  send, live Telegram connect, Patchright flows — all need your credentials/env
  (`.env`) and a running Postgres.

Next build order: Heroku Scheduler wiring (pipeline already runnable as a one-off) →
dashboard (Phase 4, routed through the design system).