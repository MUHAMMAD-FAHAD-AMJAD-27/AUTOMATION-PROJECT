# Deployment Blueprint — Phase 2

**Primary: Heroku** (you have **$290.28 platform credits** — confirmed on your billing
page; $0 usage, no Eco plan yet). **Fallback: AWS Free Tier.** Heroku's US region also
achieves your goal of running outside the local ISP's network, which bypasses local
Telegram blocks.

---

## 1. Heroku Architecture

```
┌───────────────────────────── Heroku app (region: us) ─────────────────────────────┐
│                                                                                    │
│  PROCESS TYPES                    ADD-ONS                                          │
│  ┌──────────────────────┐        ┌──────────────────────────────┐                 │
│  │ worker (1x, always)  │        │ Heroku Postgres (mini, $5)   │                 │
│  │ python -m crawler.   │ ─────► │  - PG 15/16, 10 connections  │                 │
│  │        worker        │        │  - ⚠️ NO pgvector: embeddings│                 │
│  │  = Telegram realtime │        │    stored as REAL[], computed│                 │
│  │    monitor + APSched │        │    app-side (fine <10k rows) │                 │
│  └──────────────────────┘        └──────────────────────────────┘                 │
│  ┌──────────────────────┐        ┌──────────────────────────────┐                 │
│  │ web (Phase 4 only)   │        │ Heroku Redis (mini, $5)      │                 │
│  │ Next.js dashboard    │ ─────► │  - rate-limit token buckets  │                 │
│  │  (omit until then)   │        │  - dedup bloom, session cache│                 │
│  └──────────────────────┘        └──────────────────────────────┘                 │
│                                                                                    │
│  ONE-OFF JOBS (free Heroku Scheduler add-on — NOT dynos):                          │
│    ▸ python -m crawler.pipeline        daily @06:00 UTC   (ingest+normalize+verify)│
│    ▸ python -m crawler.pipeline        daily @14:00 UTC                            │
│    ▸ python -m crawler.pipeline        daily @22:00 UTC                            │
│    ▸ python discord_dispatcher.py --limit 10   30 min after each ingest            │
│    ▸ python run.py ingest-github       daily @05:30 UTC   (GitHub mega-lists)      │
│    ▸ python run.py ingest-reddit       daily @05:45 UTC   (Reddit public JSON)     │
│    ▸ python run.py ingest-hn           daily @05:50 UTC   (HN Algolia)             │
│    ▸ python run.py ingest-mirrors      daily @05:55 UTC   (Linktree/Bento/TG web)  │
└────────────────────────────────────────────────────────────────────────────────────┘
```

**Why this shape:** the only thing that *must* run continuously is the Telegram monitor
(short-poll keeps the MTProto session warm and catches floods early). Everything else is
batch + idempotent, so it belongs in Scheduler one-offs — which only burn dyno hours
*while executing*.

### Procfile

```procfile
web: npm --prefix dashboard run start
# worker: python -m crawler.worker      # superseded by scheduler — kept recoverable
scheduler: python scheduler.py
```

`scheduler` is the only process scaled up. `worker` is commented out rather than
deleted so it stays recoverable (re-enable it only if the Telegram realtime monitor
is ever given working credentials). `web` stays unscaled until the dashboard exists —
scaling `web` up would burn dyno hours for nothing.

### Add-on configuration

| Add-on | Plan | $/mo | Why |
|---|---|---|---|
| ~~`heroku-postgresql`~~ | — | **$0** | **Not used. Neon is the permanent DB — do not provision.** |
| `heroku-redis` | `mini` | $5 | 100 MB — rate buckets, dedup scratch |
| `heroku-scheduler` | free | $0 | not required; the 27 slots run in-process |
| **Eco dynos** | 1000 h/mo | $5 | shared pool: scheduler 744 h ≈ 74% of pool |

**Monthly burn ≈ $15 → covered by your $290.28 credits for ~19 months.**

### Config vars (never in git)

```
DATABASE_URL          <- SET MANUALLY to the existing Neon pooler URL.
                         NOT injected by an add-on. Never provision a new Postgres.
REDIS_URL             <- injected by the Redis add-on
LLM_API_KEY / LLM_BASE_URL / LLM_MODEL      <- required; the pipeline cannot extract without these
LLM_FALLBACK_1_* / LLM_FALLBACK_2_*         <- optional provider fallback chain
DISCORD_WEBHOOK_URL   <- your webhook (plus optional per-category DISCORD_WEBHOOK_<CATEGORY>)
TG_API_ID / TG_API_HASH / TG_PHONE / TG_PASSWORD   <- Telegram only; blank = adapter skipped
FIRECRAWL_API_KEY     <- required by the firecrawl adapter
PROXY_URL             <- residential proxy (social adapters; optional at start)
GITHUB_TOKEN          <- free PAT (no scopes); raises GitHub API limit to 5 000 req/hr
DASHBOARD_API_KEY     <- Phase 4
```

### Deploy steps

> ⚠️ **DO NOT provision a new Postgres instance — doing so orphans all live
> production data.** The system's database is a **permanent, existing Neon
> instance** that already holds live `raw_items`, `offers`, `dispatches`,
> `sources` and `runs`. There is no `heroku addons:create heroku-postgresql`
> step and no `schema.sql` load: the schema is already applied on Neon. Point
> `DATABASE_URL` at Neon and verify connectivity **before** deploying.

```bash
git add -A && git commit -m "deploy"            # repo already initialised
heroku create freebies-hunter --region us --stack heroku-24

# --- Database: point at the EXISTING Neon instance. Do NOT provision one. ---
# Copy the pooler URL from .env (Neon console -> Connection string -> Pooled).
heroku config:set DATABASE_URL="postgresql://<user>:<pass>@<endpoint>-pooler.<region>.aws.neon.tech/neondb?sslmode=require"

# Confirm connectivity with a READ-ONLY query before going further.
# Expect non-zero counts; zeros mean you are pointed at the wrong database.
heroku run "python -c \"import os,psycopg; c=psycopg.connect(os.environ['DATABASE_URL']); \
print(c.execute('select count(*) from raw_items').fetchone(), \
      c.execute('select count(*) from offers').fetchone())\""

heroku config:set LLM_API_KEY=... LLM_BASE_URL=... LLM_MODEL=...
heroku config:set DISCORD_WEBHOOK_URL=...
heroku config:set DISABLE_COLLECTSTATIC=1         # python buildpack hygiene
heroku ps:scale scheduler=1:eco worker=0 web=0    # scheduler is the only always-on process
git push heroku main
```

Then add the Scheduler jobs (dashboard UI → Resources → Heroku Scheduler → Add job).

### Dyno-hour budgeting (stay inside 1000 h/mo)

**`scheduler` is the single always-on process.** It supersedes `worker`: the 27-slot
orchestrator already runs every ingest adapter *and* the pipeline *and* dispatch
(`scheduler.py:99-127`), whereas `worker` only ingests and never calls the pipeline.
The one thing `worker` did that `scheduler` does not is the Telegram realtime
monitor, which is credential-dead (blank `TG_*` in `.env`) and now degrades to a
logged skip rather than a crash.

- `scheduler` 24/7 = **744 h/mo**.
- `worker` scaled to 0 = **0 h** (was 744 h).
- `web` scaled to 0 = **0 h** until the dashboard ships.
- No Heroku Scheduler one-offs needed: the 27 slots are in-process, so the former
  ≈45 h of pipeline/dispatcher one-offs drops to **0 h**.

**Total ≈ 744 h/mo of the 1000 h Eco pool (74%), leaving ~256 h headroom** — down
from the previous ≈790 h estimate, and now with no second always-on dyno.

- Do **not** add extra always-on dynos (each costs ~744 h/mo). Running `scheduler`
  and `worker` together would be ≈1488 h and **exceeds the pool**.
- Eco dynos sleep on inactivity. The scheduler fires a slot every ~53 min
  (`MINUTES_PER_SLOT = 53.33`), and one slot's unconditional ingest measures
  **18.8 min** of continuous work, so the dyno is never idle long enough to be a
  concern. If Eco sleeping does bite, the fix is
  `heroku ps:scale scheduler=1:standard-1x` ($0.25/h ≈ $182/mo — only if the
  credits budget justifies it).

---

## 2. Dashboard → PostgreSQL (secure path, Phase 4 preview)

**Rule: `DATABASE_URL` (or any DB credential) never leaves the server side.**

- Next.js reads the DB **in server components / API routes** using a `pg` Pool with
  `DATABASE_URL` from Heroku config vars (server-only; never `NEXT_PUBLIC_*`).
- Browser never talks to Postgres. It talks to your API routes:
  `GET /api/offers?category=&status=&limit=` , `GET /api/offers/[id]`, `GET /api/stats`.
- Auth for single-user: an API key compared with a constant-time helper, or NextAuth
  credentials — both backed by `DASHBOARD_API_KEY`.
- Query guardrails in every route: `LIMIT`/`OFFSET` caps, read-only roles in SQL
  (`CREATE ROLE dashboard_readonly LOGIN; GRANT SELECT ON ...`), and a 5s statement
  timeout via the Pool config. Heroku Postgres enforces TLS automatically.
- Optional later: put the dashboard behind Cloudflare Tunnel (free) instead of exposing
  the Heroku URL publicly.

---

## 3. AWS Free Tier Fallback

Same architecture, different substrate; ~$0/mo if you respect the limits.

| Resource | Free-tier allowance | You run | Hidden-cost traps |
|---|---|---|---|
| EC2 `t3.micro` (or `t2.micro`) | 750 h/mo | 24/7 = 744 h ✅ | A **second** instance → billed |
| RDS `db.t3.micro` (single-AZ) | 750 h/mo + 20 GB gp2 | 24/7 ✅ | Multi-AZ, provisioned IOPS, snapshots >20 GB = billed |
| Egress | 100 GB/mo | scraping + webhooks ≈ small | Stay under; large proxy egress adds up |
| Elastic IP | free while attached | attached ✅ | Detached EIP = ~$3.60/mo |
| NAT Gateway | **not included** | don't create one | ~$32/mo + data — the #1 surprise bill |
| CloudWatch | basic metrics free | basic only | Detailed monitoring = $30/instance/mo |

**Cheaper alternative:** skip RDS entirely and run Postgres 16 in Docker on the same EC2
(`docker-compose.yml` is ready) — one instance serves both roles, and you get
`pgvector` for the optional HNSW index (RDS PG15+ supports it too, if you stay with RDS).

Launch checklist:

1. Region **us-east-1** (free-tier available; good Telegram reachability).
2. One `t3.micro`, 20 GB gp3 root volume, default VPC, **public subnet** (no NAT).
3. Security group: SSH from your IP only; Postgres bound to localhost or a private
   connection; nothing else public until the dashboard exists.
4. `docker compose up -d` (postgres + redis) + your workers via `systemd` units.
5. **AWS Budgets alarm at $1 and $5** with email alerts — non-negotiable guardrail.
6. Instance scheduler (optional): stop the box between 00:00–06:00 if you want the
   750 h to cover test instances too.

### Heroku vs AWS — decision summary

| | Heroku (primary) | AWS FT (fallback) |
|---|---|---|
| Monthly cash | ~$15 from $290.28 credits (≈19 mo) | ~$0 |
| Setup time | ~1 h | ~0.5 day |
| Ops burden | trivial (add-ons, scheduler) | systemd, self-managed PG |
| pgvector | ❌ (REAL[] app-side) | ✅ (optional HNSW) |
| Risk | Eco sleep quirks | Free-tier limit surprises |

**Recommendation:** start on Heroku now (credits make it effectively free for a year+);
revisit AWS only if you outgrow Heroku Postgres mini or want the vector index.

---

## 4. Security checklist

- [ ] Secrets only in config vars / vault; `.gitignore` covers `.env`, `*.state.json`, `sessions/`
- [ ] `DATABASE_URL` never in frontend bundles; read-only DB role for the dashboard
- [ ] Telegram session account is a dedicated account you can afford to lose
- [ ] Discord webhook URL treated as a secret (anyone with it can post to the channel)
- [ ] Scheduler commands pinned to the repo (`python -m crawler.pipeline`) — no shell strings
- [ ] AWS fallback: Budgets alarm + no NAT/EIP/extra instances