# Graph Report - automation system  (2026-08-26)

## Corpus Check
- Corpus is ~42,196 words - fits in a single context window. You may not need a graph.

## Summary
- 682 nodes · 1323 edges · 38 communities (30 shown, 8 thin omitted)
- Extraction: 94% EXTRACTED · 6% INFERRED · 0% AMBIGUOUS · INFERRED: 77 edges (avg confidence: 0.87)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- Dashboard API & Pages
- Discord Dispatch, Schema & Core Concepts
- Scheduler & Deep-Web Search
- Offer Taxonomy & Verifier Tests
- Creator Mirrors Adapter
- Dashboard NPM Dependencies
- Firecrawl Adapter & CLI Runners
- URL/Text Normalizer & Tests
- Category Taxonomy & Canonicalization
- Dashboard TypeScript Config
- Pipeline Orchestrator
- Stealth Social Scraper
- Telegram Adapter
- Dashboard Auth & Ingest API
- OpenRouter Adapter
- Product Hunt Adapter
- Dev.to Adapter
- GitHub Lists Adapter
- Hacker News Adapter
- Reddit Adapter
- Dashboard Ingest UI
- Deduplicator
- Dashboard Layout & Nav
- HTML Text Extraction
- URL Canonicalizer
- Dashboard Login UI
- Step-3 Tail Script
- Category Definitions
- Liveness Probe
- PostCSS Config
- Category Sync Script
- Next.js Config
- Next.js Env Types
- Tailwind Config

## God Nodes (most connected - your core abstractions)
1. `connect()` - 49 edges
2. `upsert_raw_item()` - 26 edges
3. `record_source_health()` - 24 edges
4. `Offer` - 24 edges
5. `run_pipeline()` - 22 edges
6. `LLMExtractor` - 19 edges
7. `run_creator_mirrors()` - 18 edges
8. `compilerOptions` - 16 edges
9. `run_github()` - 15 edges
10. `run_deep_web()` - 14 edges

## Surprising Connections (you probably didn't know these)
- `Curated Free-LLM GitHub Targets` --references--> `_parse_markdown()`  [INFERRED]
  RESEARCH_LLM_AGGREGATORS.md → adapters/github_adapter.py
- `Curated Free-LLM GitHub Targets` --references--> `run_github()`  [INFERRED]
  RESEARCH_LLM_AGGREGATORS.md → adapters/github_adapter.py
- `Telegram Flood Discipline` --references--> `_delta_pull()`  [INFERRED]
  INGESTION_SPECS.md → adapters/telegram_adapter.py
- `Telegram Flood Discipline` --references--> `sync_once()`  [INFERRED]
  INGESTION_SPECS.md → adapters/telegram_adapter.py
- `Manual Ingest Fallback` --conceptually_related_to--> `upsert_raw_item()`  [INFERRED]
  dashboard/readme.md → crawler/db.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **End-to-end Freebies Pipeline** — crawler_normalizer, crawler_verifier, crawler_pipeline_run_pipeline, discord_dispatcher, crawler_worker [INFERRED 0.85]
- **Offer Data Lifecycle Tables** — schema_raw_items, schema_offers, schema_offer_fingerprints, schema_dispatches [INFERRED 0.85]
- **Ingestion Adapter Fleet** — adapters_telegram_adapter, adapters_social_stealth, adapters_deep_web_adapter, adapters_github_adapter [INFERRED 0.85]

## Communities (38 total, 8 thin omitted)

### Community 0 - "Dashboard API & Pages"
Cohesion: 0.06
Nodes (54): dynamic, GET(), dynamic, GET(), dynamic, GET(), dynamic, OffersPage() (+46 more)

### Community 1 - "Discord Dispatch, Schema & Core Concepts"
Cohesion: 0.08
Nodes (45): Any, Server-only DB Credentials, Exact + Semantic Dedup, Idempotent Atomic Dispatch, Local Embeddings (no API key), Manual Ingest Fallback, Pydantic v2 Offer Schema, Rate Safety & Backoff (+37 more)

### Community 2 - "Scheduler & Deep-Web Search"
Cohesion: 0.07
Nodes (45): _ddg_search(), _ensure_source(), _health(), main(), AsyncClient, Deep web research adapter — DuckDuckGo Search (primary, free, no key) + Serper…, Return the (tag, query) templates a scheduler run-category should search., ``max_items`` caps total raw_items written across all query templates in one… (+37 more)

### Community 3 - "Offer Taxonomy & Verifier Tests"
Cohesion: 0.07
Nodes (40): BaseModel, coerce_category(), coerce_offer_type(), is_valid_category(), Normalize an LLM-provided category to a known key, else 'other'., Normalize an LLM-provided offer_type to a known value, else 'other'., cosine(), Offer (+32 more)

### Community 4 - "Creator Mirrors Adapter"
Cohesion: 0.09
Nodes (37): _classify_signal(), _extract_github_repos(), _fetch_bento(), _fetch_rss_feed(), _fetch_telegram_webview(), _get_or_create_source(), _has_deal_hint(), _health() (+29 more)

### Community 5 - "Dashboard NPM Dependencies"
Cohesion: 0.05
Nodes (37): autoprefixer, dependencies, lucide-react, next, pg, react, react-dom, devDependencies (+29 more)

### Community 6 - "Firecrawl Adapter & CLI Runners"
Cohesion: 0.10
Nodes (30): _batch_scrape(), _ensure_source(), _filter_fresh(), _get_client(), _health(), main(), _map_site(), Firecrawl adapter — resourify.com + deal hub deep extraction… (+22 more)

### Community 7 - "URL/Text Normalizer & Tests"
Cohesion: 0.11
Nodes (31): clean_text(), clean_url(), extract_urls(), _is_tracking(), _looks_like_html(), normalize_raw_item(), normalize_title(), crawler/normalizer.py — URL canonicalization + text/entity normalization… (+23 more)

### Community 8 - "Category Taxonomy & Canonicalization"
Cohesion: 0.16
Nodes (15): category_prompt_block(), category_prompt_csv(), offer_type_prompt_csv(), crawler/categories.py — THE single source of truth for the offer taxonomy…, Verbose 'key — description' block for the single-item SYSTEM_PROMPT., Compact comma-separated key list for the batch prompt., CanonicalURL, NormalizedItem (+7 more)

### Community 9 - "Dashboard TypeScript Config"
Cohesion: 0.07
Nodes (26): compilerOptions, allowJs, esModuleInterop, incremental, isolatedModules, jsx, lib, module (+18 more)

### Community 10 - "Pipeline Orchestrator"
Cohesion: 0.14
Nodes (23): Ingest→Normalize→Verify→Dedup→Dispatch Pipeline, dispatch_new_offers(), fetch_unprocessed(), main(), mark_raw_item_attempt(), mark_run(), Connection, crawler/pipeline.py — end-to-end orchestrator… (+15 more)

### Community 11 - "Stealth Social Scraper"
Cohesion: 0.11
Nodes (16): demo_instagram_profiles(), demo_search_twitter(), Stealth social adapter scaffold — Twitter/X, Instagram (Patchright) — Phase 2…, Open a public timeline URL, scroll a little, return captured JSON., Example: capture tweets for hashtags/terms. Run once per identity per day., Example: public IG profiles of a curated deal-account list., One persistent browser identity (fingerprint + cookies + proxy)., Fetches public timelines and extracts link-bearing posts as raw items. (+8 more)

### Community 12 - "Telegram Adapter"
Cohesion: 0.17
Nodes (23): _bump_cursor(), _channels_to_monitor(), _delta_pull(), _discovery_scan(), _extract_urls(), _health(), _join_candidates(), _process_message() (+15 more)

### Community 13 - "Dashboard Auth & Ingest API"
Cohesion: 0.18
Nodes (13): dynamic, POST(), POST(), verifyApiKey(), ingestManualDeal(), COOKIE_MAX_AGE, COOKIE_NAME, safeEqualHex() (+5 more)

### Community 14 - "OpenRouter Adapter"
Cohesion: 0.18
Nodes (16): _ensure_source(), _health(), _is_free(), main(), _model_to_payload(), OpenRouter free-model adapter ============================= Polls the public…, Record a source-health snapshot; no-op when source_id is unknown (dry-run)., Return True when both prompt and completion cost are zero. (+8 more)

### Community 15 - "Product Hunt Adapter"
Cohesion: 0.17
Nodes (16): _ensure_source(), _entry_to_payload(), _fetch_feed(), _health(), _is_relevant(), main(), _parse_feed(), AsyncClient (+8 more)

### Community 16 - "Dev.to Adapter"
Cohesion: 0.19
Nodes (14): _article_to_payload(), _ensure_source(), _fetch_tag(), _health(), _is_relevant(), main(), AsyncClient, dev.to adapter — public REST API, no auth, structured JSON… (+6 more)

### Community 17 - "GitHub Lists Adapter"
Cohesion: 0.21
Nodes (14): _ensure_source(), _entry_to_payload(), _fetch_file_content(), _health(), main(), _parse_markdown(), ParsedEntry, AsyncClient (+6 more)

### Community 18 - "Hacker News Adapter"
Cohesion: 0.19
Nodes (13): _get_source_id(), _health(), _hit_to_payload(), main(), AsyncClient, Hacker News Algolia adapter — free, unauthenticated, structured JSON…, Record a source-health snapshot; no-op when source_id is unknown (dry-run)., Normalize an Algolia HN hit to the standard raw_item payload shape. (+5 more)

### Community 19 - "Reddit Adapter"
Cohesion: 0.24
Nodes (13): _ensure_source(), _fetch_posts(), _health(), _is_relevant(), main(), _post_to_payload(), AsyncClient, Reddit public JSON adapter — zero auth, pure unauthenticated API… (+5 more)

### Community 20 - "Dashboard Ingest UI"
Cohesion: 0.22
Nodes (9): dynamic, FLOW_STEPS, EMPTY, Errors, IngestForm(), handleBlur(), handleSubmit(), validate() (+1 more)

### Community 21 - "Deduplicator"
Cohesion: 0.33
Nodes (5): Deduplicator, DupCheckResult, Connection, Exact URL-hash + semantic (cosine) dedup against Postgres fingerprints., Reconnect if the connection was dropped by the server (Neon idle timeout).

### Community 22 - "Dashboard Layout & Nav"
Cohesion: 0.24
Nodes (6): metadata, NAV, Sidebar(), MOBILE_NAV, TITLES, Topbar()

### Community 23 - "HTML Text Extraction"
Cohesion: 0.29
Nodes (3): _HTMLTextExtractor, HTMLParser, Collects visible text and <a href> targets from an HTML fragment.

### Community 24 - "URL Canonicalizer"
Cohesion: 0.40
Nodes (3): AsyncClient, Async, concurrency-limited URL canonicalizer over one shared client., URLCanonicalizer

### Community 26 - "Step-3 Tail Script"
Cohesion: 0.67
Nodes (3): counts(), main(), run_step3_tail.py — prove the scheduler slot's pipeline->dispatch->Discord tail…

## Knowledge Gaps
- **76 isolated node(s):** `dynamic`, `dynamic`, `dynamic`, `dynamic`, `dynamic` (+71 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **8 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `connect()` connect `Reddit Adapter` to `Scheduler & Deep-Web Search`, `Creator Mirrors Adapter`, `Firecrawl Adapter & CLI Runners`, `Telegram Adapter`, `OpenRouter Adapter`, `Product Hunt Adapter`, `Dev.to Adapter`, `GitHub Lists Adapter`, `Hacker News Adapter`?**
  _High betweenness centrality (0.067) - this node is a cross-community bridge._
- **Why does `run_pipeline()` connect `Pipeline Orchestrator` to `Scheduler & Deep-Web Search`, `Firecrawl Adapter & CLI Runners`, `URL/Text Normalizer & Tests`, `Category Taxonomy & Canonicalization`, `Deduplicator`, `URL Canonicalizer`, `Step-3 Tail Script`?**
  _High betweenness centrality (0.063) - this node is a cross-community bridge._
- **Why does `Normalized Adapter Payload Contract` connect `Stealth Social Scraper` to `GitHub Lists Adapter`, `Scheduler & Deep-Web Search`, `Telegram Adapter`, `Discord Dispatch, Schema & Core Concepts`?**
  _High betweenness centrality (0.043) - this node is a cross-community bridge._
- **Are the 2 inferred relationships involving `upsert_raw_item()` (e.g. with `Manual Ingest Fallback` and `raw_items`) actually correct?**
  _`upsert_raw_item()` has 2 INFERRED edges - model-reasoned connections that need verification._
- **Are the 9 inferred relationships involving `Offer` (e.g. with `test_expires_at_tz_aware_when_provided_with_z()` and `test_offer_accepts_valid_payload()`) actually correct?**
  _`Offer` has 9 INFERRED edges - model-reasoned connections that need verification._
- **Are the 2 inferred relationships involving `run_pipeline()` (e.g. with `Scheduler One-off Batch Jobs` and `CanonicalURL`) actually correct?**
  _`run_pipeline()` has 2 INFERRED edges - model-reasoned connections that need verification._
- **What connects `dynamic`, `dynamic`, `dynamic` to the rest of the system?**
  _76 weakly-connected nodes found - possible documentation gaps or missing edges._