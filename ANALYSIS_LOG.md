# Analysis Log — Developer-Freebies Intelligence Pipeline

> Append-only log of evidence-grounded analyses. **Newest entry on top.**
> This is the full-fidelity, version-controlled home for diagnoses that must
> survive context loss between sessions. Companion to `OPEN_ITEMS.md` (which
> tracks *what is outstanding*); this file records *what the live data showed
> and what it meant*. Every number here came from an actual query — if you
> re-run and the numbers have moved, add a new dated entry rather than editing
> an old one.

---

## 2026-08-29 — Pipeline backlog diagnosis: drain vs. ingest + newest-first starvation

**How gathered:** read-only `SELECT`s against the live Neon DB (owner pre-approved
read-only access for this analysis). No writes, no schema access.
**Window:** `raw_items.fetched_at` spans 2026-08-25 07:57 → 2026-08-29 01:48 ≈
**3.75 days**, 1903 rows → **~507 raw_items ingested/day**.

### The funnel (1903 raw_items, mutually-exclusive terminal states)

| Bucket | Count | % of corpus |
|---|---|---|
| Became an offer | 279 | 14.7% |
| Terminally rejected (`permanently_rejected`, no offer) | 683 | 35.9% |
| **Untouched backlog** (`attempts=0`, not rejected, no offer) | **917** | **48.2%** |
| Pending retry (`attempts>0`, not rejected, no offer) | 24 | 1.3% |

- **Resolved** (offer + terminally rejected) = 962 = **50.6%** → **~256/day** resolved
  vs **~507/day** ingested. Ingest is running ~2× the resolution rate.
- Of the 917 untouched: **684 are <24h old, 233 are >24h old** — 233 items the
  pipeline has already permanently skipped past and, under newest-first ordering,
  will keep skipping.
- Attempts semantics confirmed: of 279 offers, **270 had `attempts=0`**. A clean
  extraction leaves `attempts=0`; "processed" = *offer OR permanently_rejected*,
  nothing else. (This corrected an earlier misread that `attempts>0` meant "processed".)

### Why processed items produced no offer (707 attempted-no-offer)

| Reason | n | Stage |
|---|---|---|
| prefilter: no_deal_signal | 320 | cheap (pre-LLM) |
| llm_rejected | 296 | expensive (LLM call) |
| prefilter: too_short | 45 | cheap |
| dup:url | 34 | cheap |
| prefilter: junk_title | 8 | cheap |
| no_url / dup:semantic / liveness:soft_dead | 2 / 1 / 1 | cheap |

→ prefilter cheaply discards **373**; only **296** non-offers ever reached the LLM.
The prefilter is doing its job — junk is *not* burning LLM budget.

### Dispatch distribution (272 sent, by offer category)

llm_api_drop 81 · student 48 · student_pack 24 · tools 22 · cloud 18 ·
saas_deal 17 · ai_tools 14 · open_source_repo 12 · llm 12 · hosting 8 ·
coupon 8 · domain 3 · course 2 · other 2 · **coding_agents 1**.

Heavily skewed to `llm_api_drop` (30%); `coding_agents` gets a single dispatch
despite being a dedicated scheduler lane — partly a coverage gap in its
deep_web/firecrawl queries, partly backlog starvation upstream.

### Source yield extremes (raw → offers)

- `firecrawl:resourify.com` 126 → **108** (86% — the single workhorse source)
- `web:deep_search:student_geo` 80 → 28; several curated GitHub LLM lists convert
  well (18→17, 27→17, 23→16)
- **Zero-yield, high-volume:** `rss:producthunt_frontpage` 266 → **0**;
  `producthunt:frontpage` 68 → **0**; `devto:articles` 25 → **0**;
  ~10 `github:awesome-*` lists 25 → **0** each

### What the numbers mean

The pipeline **ingests ~2× faster than it drains, and drains newest-first**, so
~half of everything collected (917 items, 48%) is never looked at, and the oldest
items are structurally starved. This is the worst starvation direction for a
*freebies* feed: the items most likely to be time-expiring are the ones least
likely to ever be processed or dispatched. Every run still reports "success" —
this is a silent throughput ceiling, not a visible error.

It is **not** a "junk sources burn the LLM" problem (prefilter kills junk cheaply:
373 prefiltered vs 296 LLM-rejected) and **not** an over-strict extractor
(14.7% offer rate, 86% yield from the best source).

### Code root cause (verified in source)

- `crawler/pipeline.py:82` — `fetch_unprocessed(... ORDER BY r.fetched_at DESC
  LIMIT %s)` → **newest-first**. Anything not caught in its birth-slot's top-N
  window is buried deeper every subsequent slot and never revisited.
- `scheduler.py:80-81` — `PIPELINE_LIMIT_PER_RUN = 30` drained per slot, but each
  slot first ingests `ADAPTER_LIMIT_PER_RUN = 50` from several adapters
  (deep_web + creator_mirrors + firecrawl + gated github/hn/reddit/devto/
  producthunt/openrouter; github alone up to 253). Per-slot ingest ≈ 100–250;
  per-slot drain = 30.

### Unexplained gap to confirm before tuning (diagnose, don't patch)

Theoretical drain at LIMIT=30 × 27 slots/day = 810/day, but observed resolution is
only **~256/day**. That 3× gap means the pipeline is **not** hitting its configured
limit — cause unknown (candidates: slots skipped / process restarts, irregular
cadence over the window, or a sub-limit internal cap/liveness deferral in the
unread `run_pipeline` body, lines ~421-517). **Raising the limit may change nothing
until this gap is explained.** Next diagnostic step: read-only query of the `runs`
table for per-run resolved counts + actual run frequency.

### Recommendation (item 5, 2026-08-29)

Highest-leverage next lever = fix drain-vs-ingest so the backlog clears. Owner has
since decided (2026-08-29 instruction set): (1) **remove** the zero-yield
ProductHunt + dev.to sources; (2) **raise** `PIPELINE_LIMIT_PER_RUN` to a
math-backed value; (3) **keep** newest-first ordering deliberately (fresh drops are
time-critical), and add a small oldest-first backlog-sweep sub-quota so the aged
tail still clears without jumping ahead of fresh items. See `OPEN_ITEMS.md` for
live status of each.

### Why this lives here (repo file) and not in Graphify

Graphify turns input into an entity/relationship knowledge graph and emits
`graphify-out/*` artifacts. This diagnosis is a quantitative narrative — the exact
funnel counts and the reasoning chain are the payload, and a graph node would lose
that fidelity. Graphify output is also explicitly out-of-scope to commit alongside
functional changes. A committed Markdown log + a project memory pointer preserves
full fidelity, is version-controlled, and is discoverable by both the operator and
future sessions. Running `/graphify` remains available as a separate, explicit step
if a graph view is ever wanted.
