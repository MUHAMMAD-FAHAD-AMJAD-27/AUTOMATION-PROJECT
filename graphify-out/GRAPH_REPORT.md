# Graph Report - automation system  (2026-08-29)

## Corpus Check
- cluster-only mode — file stats not available

## Summary
- 1201 nodes · 2276 edges · 69 communities (51 shown, 18 thin omitted)
- Extraction: 95% EXTRACTED · 5% INFERRED · 0% AMBIGUOUS · INFERRED: 107 edges (avg confidence: 0.89)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `632a024d`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- queries.ts
- test_provider_scheduler.py
- LLMExtractor
- discord_dispatcher.py
- run.py
- firecrawl_adapter.py
- test_normalizer.py
- scheduler.py
- devDependencies
- test_github_trending.py
- test_notable_repo_lane.py
- categories.py
- creator_mirrors.py
- test_social_stealth.py
- deep_web_adapter.py
- compilerOptions
- run_pipeline
- Deduplicator
- test_dispatch_throughput.py
- verifier.py
- _run_stage_b
- telegram_adapter.py
- test_pipeline_concurrency.py
- connect
- upsert_raw_item
- shared.ts
- hn_adapter.py
- test_telegram_adapter.py
- producthunt_adapter.py
- _proxy_config
- github_adapter.py
- social_stealth.py
- ingest/page.tsx
- _FakeConn
- login
- Neon Postgres (permanent existing DB)
- test_creator_mirrors.py
- openrouter_adapter.py
- sync_once
- layout.tsx
- parse_instagram_payloads
- SocialFetcher
- _TGHTMLParser
- StealthIdentity
- _build_client
- login/page.tsx
- Scheduler Process (27-slot orchestrator)
- _auto_join_enabled
- next.config.mjs
- next-env.d.ts
- postcss.config.mjs
- tailwind.config.ts
- ddgs (renamed DuckDuckGo search, preferred)
- _tmp_predeploy_baseline.py
- HTMLParser
- Any
- NormalizedItem
- firecrawl-py 4.38.0 (pinned exact)
- httpx (HTTP client)
- patchright (browser automation adapter)
- pydantic (data validation)
- pytest (testing)
- python-dotenv (env loading)
- _FakeCursor

## God Nodes (most connected - your core abstractions)
1. `connect()` - 57 edges
2. `LLMExtractor` - 31 edges
3. `Offer` - 30 edges
4. `upsert_raw_item()` - 25 edges
5. `FakeClock` - 23 edges
6. `record_source_health()` - 23 edges
7. `_FakeConn` - 22 edges
8. `run_pipeline()` - 20 edges
9. `parse_twitter_payloads()` - 19 edges
10. `run_creator_mirrors()` - 18 edges

## Surprising Connections (you probably didn't know these)
- `Pydantic v2 Offer Schema` --references--> `LLMExtractor`  [INFERRED]
  README.md → crawler/verifier.py
- `Telegram Flood Discipline` --references--> `_delta_pull()`  [INFERRED]
  INGESTION_SPECS.md → adapters/telegram_adapter.py
- `Manual Ingest Fallback` --conceptually_related_to--> `upsert_raw_item()`  [INFERRED]
  dashboard/readme.md → crawler/db.py
- `Curated Free-LLM GitHub Targets` --references--> `_parse_markdown()`  [INFERRED]
  RESEARCH_LLM_AGGREGATORS.md → adapters/github_adapter.py
- `Curated Free-LLM GitHub Targets` --references--> `run_github()`  [INFERRED]
  RESEARCH_LLM_AGGREGATORS.md → adapters/github_adapter.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Heroku process types (worker/web/scheduler)** — deployment_scheduler_process, deployment_worker_process, deployment_web_process [EXTRACTED 1.00]
- **Graceful semantic-dedup degradation on Heroku** — deployment_app_side_embeddings, requirements_torch_sentence_transformers, requirements_psycopg [INFERRED 0.75]
- **Ingestion Adapter Fleet** — adapters_telegram_adapter, adapters_social_stealth, adapters_deep_web_adapter, adapters_github_adapter [INFERRED 0.85]
- **Offer Data Lifecycle Tables** — schema_raw_items, schema_offers, schema_offer_fingerprints, schema_dispatches [INFERRED 0.85]
- **End-to-end Freebies Pipeline** — crawler_normalizer, crawler_verifier, crawler_pipeline_run_pipeline, discord_dispatcher, crawler_worker [INFERRED 0.85]

## Communities (69 total, 18 thin omitted)

### Community 0 - "queries.ts"
Cohesion: 0.06
Nodes (55): dynamic, GET(), dynamic, GET(), dynamic, GET(), dynamic, OffersPage() (+47 more)

### Community 1 - "test_provider_scheduler.py"
Cohesion: 0.06
Nodes (49): BreakerState, _CircuitBreaker, estimate_tokens(), _float_env(), _int_env(), ProviderLimits, ProviderScheduler, _ProviderState (+41 more)

### Community 2 - "LLMExtractor"
Cohesion: 0.06
Nodes (51): BaseModel, offer_type_prompt_csv(), cosine(), _fence_untrusted(), LLMExtractor, _NoVerdict, Offer, _Provider (+43 more)

### Community 3 - "discord_dispatcher.py"
Cohesion: 0.07
Nodes (49): Server-only DB Credentials, Exact + Semantic Dedup, Idempotent Atomic Dispatch, Local Dev Stack (Postgres + Redis), Manual Ingest Fallback, Pydantic v2 Offer Schema, Rate Safety & Backoff, _load_st_model() (+41 more)

### Community 4 - "run.py"
Cohesion: 0.07
Nodes (35): get_database_url(), _all(), gather(), main(), _one(), crawler/status.py — read-only system health summary (Phase 21).…, Format the snapshot dict into a human-readable report string., Connect read-only, gather the snapshot, print it. Returns exit code. (+27 more)

### Community 5 - "firecrawl_adapter.py"
Cohesion: 0.07
Nodes (42): _batch_scrape(), _ensure_source(), _filter_unseen(), _get_client(), _health(), main(), _map_site(), Firecrawl adapter — multi deal-hub deep extraction… (+34 more)

### Community 6 - "test_normalizer.py"
Cohesion: 0.07
Nodes (39): CanonicalURL, clean_text(), clean_url(), extract_urls(), _HTMLTextExtractor, _is_tracking(), _looks_like_html(), normalize_raw_item() (+31 more)

### Community 7 - "scheduler.py"
Cohesion: 0.08
Nodes (40): _build_scheduler(), _build_slots(), main(), _print_schedule(), scheduler.py — Staggered 27-slot APScheduler orchestrator…, Return the 27-slot schedule as a list of dicts., Execute one category slot: ingest → pipeline → dispatch., Curated mega-lists change slowly, so run once/day (run 1) on the OSS lane.… (+32 more)

### Community 8 - "devDependencies"
Cohesion: 0.05
Nodes (37): autoprefixer, dependencies, lucide-react, next, pg, react, react-dom, devDependencies (+29 more)

### Community 9 - "test_github_trending.py"
Cohesion: 0.10
Nodes (30): _build_queries(), _ensure_source(), _headers(), _health(), main(), AsyncClient, GitHub Trending discovery adapter — Search API (Item 4, part a, 2026-08-29)…, Convert a Search API repository object to an upsert_raw_item payload. Stamps… (+22 more)

### Community 10 - "test_notable_repo_lane.py"
Cohesion: 0.09
Nodes (19): heuristic_prefilter(), Cheap local regex pass to drop obvious junk BEFORE it reaches the LLM. Returns…, _FakeConn, _FakeCursor, _FakeResp, _ni(), NormalizedItem, Item 4b — notable_repo (non-deal) lane. Locks the four seams that let GitHub-… (+11 more)

### Community 11 - "categories.py"
Cohesion: 0.08
Nodes (27): category_prompt_block(), category_prompt_csv(), CategoryDef, coerce_category(), coerce_offer_type(), is_valid_category(), crawler/categories.py — THE single source of truth for the offer taxonomy…, Normalize an LLM-provided category to a known key, else 'other'. (+19 more)

### Community 12 - "creator_mirrors.py"
Cohesion: 0.14
Nodes (30): _classify_signal(), _extract_github_repos(), _fetch_bento(), _fetch_rss_feed(), _fetch_telegram_webview(), _get_or_create_source(), _has_deal_hint(), _health() (+22 more)

### Community 13 - "test_social_stealth.py"
Cohesion: 0.11
Nodes (28): _is_junk_url(), _parse_iso_or_twitter_date(), parse_twitter_payloads(), Return an ISO-8601 string, converting Twitter's 'ddd MMM DD HH:MM:SS +0000…, True for structurally absurd URLs — keyword-stuffed spam, not real links., Extract tweets from captured SearchTimeline/UserTweets JSON as raw_item…, _capture_with_url(), Hermetic tests for adapters.social_stealth JSON parsers. No network, no DB, no… (+20 more)

### Community 14 - "deep_web_adapter.py"
Cohesion: 0.13
Nodes (28): _ddg_search(), default_templates(), _ensure_source(), _excluded_tags(), _health(), main(), AsyncClient, Deep web research adapter — DuckDuckGo Search (primary, free, no key) + Serper… (+20 more)

### Community 15 - "compilerOptions"
Cohesion: 0.07
Nodes (26): compilerOptions, allowJs, esModuleInterop, incremental, isolatedModules, jsx, lib, module (+18 more)

### Community 16 - "run_pipeline"
Cohesion: 0.12
Nodes (22): fetch_unprocessed(), main(), mark_run(), Re-write ``runs.stats`` after the dispatch stage has run. ``mark_run`` fires…, raw_items with no offers row yet (left join), newest first., _repersist_run_stats(), run_pipeline(), counts() (+14 more)

### Community 17 - "Deduplicator"
Cohesion: 0.27
Nodes (7): Deduplicator, DupCheckResult, Connection, Exact URL-hash + scoped title-hash + semantic (cosine) dedup against Postgres…, Reconnect if the connection was dropped by the server (Neon idle timeout)., Exact-title dedup, scoped to the SAME category inside ``recent_days``. Closes…, Run the gates cheapest-first, returning on the first hit.…

### Community 18 - "test_dispatch_throughput.py"
Cohesion: 0.10
Nodes (30): has_undispatched_offers(), True when at least one dispatchable offer has never been sent on ``channel``.…, sha256_hex(), _dedup(), _FakeConn, _one_sql(), _params_for(), Dispatch-throughput (Decision 3a) + scoped title-hash dedup (Decision 3b).… (+22 more)

### Community 19 - "verifier.py"
Cohesion: 0.16
Nodes (13): dispatch_new_offers(), crawler/pipeline.py — end-to-end orchestrator…, Import and run the standalone dispatcher against the same DB. Awaited directly…, clean_or_fallback(), _distribution_enabled(), LivenessProbe, LivenessResult, AsyncClient (+5 more)

### Community 20 - "_run_stage_b"
Cohesion: 0.15
Nodes (20): BaseException, _extract_chunks_concurrent(), mark_raw_item_attempt(), _process_batch_result(), Any, CanonicalURL, Connection, Insert the canonical offer + fingerprint. Returns the new offer id. (+12 more)

### Community 21 - "telegram_adapter.py"
Cohesion: 0.18
Nodes (19): _delta_pull(), _discovery_scan(), _extract_urls(), _health(), _join_candidates(), _process_message(), Telegram ingestion adapter — Telethon (MTProto) — Phase 2 scaffold…, Normalize a Telegram message into a raw_item (links + text + entities). (+11 more)

### Community 22 - "test_pipeline_concurrency.py"
Cohesion: 0.19
Nodes (14): _FakeDedup, _FakeExtractor, _fresh_stats(), _patch_write_layer(), Stage-B concurrency tests for crawler.pipeline (Distribution Step 3). Exercise…, One Stage-A survivor tuple: (row, normalized, primary, live)., extract_batch returns one offer per item (title=primary.canonical), or a per-…, Fake out the DB write layer; return (writes, marks) recording lists. (+6 more)

### Community 23 - "connect"
Cohesion: 0.15
Nodes (19): _article_to_payload(), _ensure_source(), _fetch_tag(), _health(), _is_relevant(), main(), AsyncClient, dev.to adapter — public REST API, no auth, structured JSON… (+11 more)

### Community 24 - "upsert_raw_item"
Cohesion: 0.16
Nodes (18): _ensure_source(), _fetch_posts(), _health(), _is_relevant(), main(), _post_to_payload(), AsyncClient, Reddit public JSON adapter — zero auth, pure unauthenticated API… (+10 more)

### Community 25 - "shared.ts"
Cohesion: 0.18
Nodes (13): dynamic, POST(), POST(), verifyApiKey(), ingestManualDeal(), COOKIE_MAX_AGE, COOKIE_NAME, safeEqualHex() (+5 more)

### Community 26 - "hn_adapter.py"
Cohesion: 0.16
Nodes (16): _get_source_id(), _health(), _hit_to_payload(), main(), AsyncClient, Hacker News Algolia adapter — free, unauthenticated, structured JSON…, Record a source-health snapshot; no-op when source_id is unknown (dry-run)., Normalize an Algolia HN hit to the standard raw_item payload shape. (+8 more)

### Community 27 - "test_telegram_adapter.py"
Cohesion: 0.14
Nodes (13): _load_credentials(), Read and validate the TG_* credentials. Returns None instead of raising when…, fixture, _clean_env(), Hermetic tests for adapters.telegram_adapter session handling. No network, no…, Records the session argument instead of opening a real client., _RecordingClient, _StubClient (+5 more)

### Community 28 - "producthunt_adapter.py"
Cohesion: 0.17
Nodes (16): _ensure_source(), _entry_to_payload(), _fetch_feed(), _health(), _is_relevant(), main(), _parse_feed(), AsyncClient (+8 more)

### Community 29 - "_proxy_config"
Cohesion: 0.12
Nodes (15): _proxy_config(), Split a proxy URL into Playwright's ``{server, username, password}`` form.…, With no proxy configured the whole feature stays inert — new_context() expects…, A password containing @ or : must be percent-encoded in the env var; the split…, IP-whitelisted proxies have no user/pass — passing empty strings would be…, A bare host:port isn't URL-shaped (urlsplit finds no hostname). Hand the…, Chromium supports SOCKS5 only without auth, and the runtime failure looks…, test_proxy_config_defaults_missing_scheme_and_keeps_portless_host() (+7 more)

### Community 30 - "github_adapter.py"
Cohesion: 0.21
Nodes (14): _ensure_source(), _entry_to_payload(), _fetch_file_content(), _health(), main(), _parse_markdown(), ParsedEntry, AsyncClient (+6 more)

### Community 31 - "social_stealth.py"
Cohesion: 0.23
Nodes (13): _ensure_source(), _health(), main(), _persist(), Stealth social adapter scaffold — Twitter/X, Instagram (Patchright) — Phase 2…, Expanded URLs from a tweet's entities, ignoring media/self-permalinks., Record a source-health snapshot; no-op when source_id is unknown (dry-run)., Write parsed payloads to raw_items (or print them on dry-run). Returns count. (+5 more)

### Community 32 - "ingest/page.tsx"
Cohesion: 0.22
Nodes (9): dynamic, FLOW_STEPS, EMPTY, Errors, IngestForm(), handleBlur(), handleSubmit(), validate() (+1 more)

### Community 34 - "login"
Cohesion: 0.25
Nodes (11): login(), Open a HEADED browser so a human can log in ONCE, then persist the…, _mock_playwright_chain(), Build an async_playwright() stand-in whose chain records its calls. ``cookies``…, A name-only match is not proof — a cleared session can leave the cookie name…, An X session cookie must not satisfy an Instagram login (and vice versa) — the…, test_login_gate_checks_the_platform_specific_cookie(), test_login_refuses_to_save_when_auth_cookie_absent() (+3 more)

### Community 35 - "Neon Postgres (permanent existing DB)"
Cohesion: 0.20
Nodes (11): App-side REAL[] embeddings (no pgvector), AWS Free Tier Fallback, Heroku Config Vars (secrets), Dashboard to PostgreSQL secure path, Heroku Architecture (primary deploy target), Heroku Redis (mini add-on), Neon Postgres (permanent existing DB), Deployment Security Checklist (+3 more)

### Community 36 - "test_creator_mirrors.py"
Cohesion: 0.29
Nodes (9): _parse_github_trending_html(), _parse_rss(), Parse RSS 2.0 or Atom feed XML. Returns list of {title, url, summary,…, Extract repo cards from GitHub /trending page HTML (no JS needed for SSR).…, Hermetic tests for adapters.creator_mirrors parsing helpers. No network, no DB.…, test_parse_github_trending_caps_at_25(), test_parse_github_trending_extracts_slugs(), test_parse_github_trending_ignores_non_heading_links() (+1 more)

### Community 37 - "openrouter_adapter.py"
Cohesion: 0.31
Nodes (9): _ensure_source(), _health(), _is_free(), main(), _model_to_payload(), OpenRouter free-model adapter ============================= Polls the public…, Record a source-health snapshot; no-op when source_id is unknown (dry-run)., Return True when both prompt and completion cost are zero. (+1 more)

### Community 38 - "sync_once"
Cohesion: 0.24
Nodes (10): _bump_cursor(), _channels_to_monitor(), Return (source_name, channel_username, last_message_id) rows., One-shot batch sync for Heroku Scheduler (3x/day) — same logic as the always-on…, sync_once(), Normalized Adapter Payload Contract, Telegram Flood Discipline, Stealth Tarpit Breaker (+2 more)

### Community 39 - "layout.tsx"
Cohesion: 0.24
Nodes (6): metadata, NAV, Sidebar(), MOBILE_NAV, TITLES, Topbar()

### Community 40 - "parse_instagram_payloads"
Cohesion: 0.22
Nodes (9): _ig_caption_text(), _iter_nodes(), parse_instagram_payloads(), Yield every dict nested anywhere inside a JSON value (self first)., Caption text across IG web-client shapes. Current (2026) ``graphql/query``…, Extract IG posts from captured profile graphql JSON as raw_item payloads. An IG…, test_instagram_empty_capture_yields_nothing(), test_instagram_null_caption_does_not_crash() (+1 more)

### Community 41 - "SocialFetcher"
Cohesion: 0.32
Nodes (4): Fetches public timelines and extracts link-bearing posts as raw items., Trap XHR JSON (graphql/web API) before the page renders it away., Open a public timeline URL, scroll a little, return captured JSON., SocialFetcher

### Community 42 - "_TGHTMLParser"
Cohesion: 0.29
Nodes (3): Minimal parser for t.me/s/<channel> HTML — extracts post text + links., _TGHTMLParser, HTMLParser

### Community 43 - "StealthIdentity"
Cohesion: 0.29
Nodes (4): One persistent browser identity (fingerprint + cookies + proxy)., StealthIdentity, test_context_options_proxy_is_none_without_proxy_url(), test_context_options_uses_the_split_proxy()

### Community 44 - "_build_client"
Cohesion: 0.47
Nodes (6): _build_client(), Construct a TelegramClient from the appropriate session backend. Prefers an in-…, Validated TG_* credentials needed to open an MTProto session., TelegramCredentials, test_build_client_falls_back_to_file_session(), test_build_client_prefers_string_session()

### Community 46 - "Scheduler Process (27-slot orchestrator)"
Cohesion: 0.33
Nodes (6): Eco Dynos (1000 h/mo shared pool), Scheduler Process (27-slot orchestrator), Worker Process (Telegram realtime monitor), APScheduler (27-slot scheduler), SQLAlchemy (APScheduler job store), telethon (Telegram MTProto client)

### Community 47 - "_auto_join_enabled"
Cohesion: 0.67
Nodes (3): _auto_join_enabled(), Whether the discovery auto-join step may run. Default OFF. Two-stage human gate…, test_auto_join_enabled_truth_table()

## Knowledge Gaps
- **94 isolated node(s):** `DbError`, `IngestInput`, `OfferFilters`, `OverviewStats`, `Errors` (+89 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **18 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `connect()` connect `connect` to `run.py`, `firecrawl_adapter.py`, `openrouter_adapter.py`, `sync_once`, `test_github_trending.py`, `creator_mirrors.py`, `deep_web_adapter.py`, `telegram_adapter.py`, `upsert_raw_item`, `hn_adapter.py`, `producthunt_adapter.py`, `github_adapter.py`, `social_stealth.py`?**
  _High betweenness centrality (0.090) - this node is a cross-community bridge._
- **Why does `run_pipeline()` connect `run_pipeline` to `LLMExtractor`, `scheduler.py`, `test_dispatch_throughput.py`, `verifier.py`, `_run_stage_b`, `hn_adapter.py`?**
  _High betweenness centrality (0.039) - this node is a cross-community bridge._
- **Why does `LLMExtractor` connect `LLMExtractor` to `discord_dispatcher.py`, `run.py`, `test_notable_repo_lane.py`, `run_pipeline`, `verifier.py`, `_run_stage_b`?**
  _High betweenness centrality (0.038) - this node is a cross-community bridge._
- **Are the 9 inferred relationships involving `LLMExtractor` (e.g. with `Pydantic v2 Offer Schema` and `_extract_chunks_concurrent()`) actually correct?**
  _`LLMExtractor` has 9 INFERRED edges - model-reasoned connections that need verification._
- **Are the 13 inferred relationships involving `Offer` (e.g. with `test_expires_at_tz_aware_when_provided_with_z()` and `test_offer_accepts_valid_payload()`) actually correct?**
  _`Offer` has 13 INFERRED edges - model-reasoned connections that need verification._
- **Are the 2 inferred relationships involving `upsert_raw_item()` (e.g. with `Manual Ingest Fallback` and `raw_items`) actually correct?**
  _`upsert_raw_item()` has 2 INFERRED edges - model-reasoned connections that need verification._
- **What connects `DbError`, `IngestInput`, `OfferFilters` to the rest of the system?**
  _94 weakly-connected nodes found - possible documentation gaps or missing edges._