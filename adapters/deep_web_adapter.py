"""
Deep web research adapter — DuckDuckGo Search (primary, free, no key)
                           + Serper API (optional, higher volume)
=======================================================================
Performs targeted web searches for each configured category, extracts
top result URLs and snippets, and feeds them into upsert_raw_item() for
LLM verification.

Results are limited to the past week by default (timelimit="w") so only
fresh freebies make it into the pipeline. Pass --lookback to widen.

Primary engine: `ddgs` library (pip install ddgs)
  — free, no key, timelimit param filters by recency.
Fallback / higher-volume: Serper API (set SERPER_API_KEY env var)
  — 2 500 free searches/month on the free tier.

Usage:
    python -m adapters.deep_web_adapter                  # all categories
    python -m adapters.deep_web_adapter --dry-run
    python -m adapters.deep_web_adapter --category ai_apis
    python -m adapters.deep_web_adapter --lookback month  # d|w|m|y
    python -m adapters.deep_web_adapter --engine serper   # force Serper

Env vars:
    SERPER_API_KEY    — optional; enables Serper fallback/primary
    DEEP_WEB_ENGINE   — "ddg" (default) | "serper"
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
from dataclasses import dataclass

import httpx

from crawler.db import connect, record_source_health, upsert_raw_item

log = logging.getLogger("adapter.deep_web")


def _health(source_id: int | None, ok: bool, error: str | None = None) -> None:
    """Record a source-health snapshot; no-op when source_id is unknown (dry-run)."""
    if source_id is None:
        return
    with connect() as conn:
        record_source_health(conn, source_id, ok=ok, error=error)

SERPER_URL = "https://google.serper.dev/search"
MAX_RESULTS_PER_QUERY = 10   # top N results per query
INTER_QUERY_DELAY = 2.5      # seconds between queries (be polite)

# DDG timelimit values: "d" = past day, "w" = past week, "m" = past month
DEFAULT_LOOKBACK = "w"


# --------------------------------------------------------------------------- #
# Query templates — (category_tag, query_string)
# Simple natural-language queries only; no site: / OR / quoted operators.
# DDG handles these best and they return consistently high result counts.
# --------------------------------------------------------------------------- #
QUERY_TEMPLATES: list[tuple[str, str]] = [
    # --- AI tools & LLM APIs ---
    ("ai_apis",       "free LLM API key developer 2025 2026"),
    ("ai_apis",       "free AI API credits developer no credit card"),
    ("ai_apis",       "Groq free API tier llama developer"),
    ("ai_apis",       "OpenRouter free model API access"),
    ("ai_apis",       "Cursor free tier AI coding tool"),
    ("ai_apis",       "Lovable Manus OpenCode free plan developer"),
    # --- Cloud credits ---
    ("cloud_credits", "AWS free tier cloud credits developer signup"),
    ("cloud_credits", "Google Cloud free credits new account developer"),
    ("cloud_credits", "Hetzner free cloud VPS trial developer"),
    ("cloud_credits", "fly.io free tier deploy apps developer"),
    ("cloud_credits", "Railway Render free hosting plan developer"),
    ("cloud_credits", "DigitalOcean free cloud credit new developer"),
    # --- Student packs ---
    ("student_packs", "GitHub Student Developer Pack free tools"),
    ("student_packs", "JetBrains student license free IDE"),
    ("student_packs", "Figma education free student account"),
    ("student_packs", "Azure student free credits developer"),
    ("student_packs", "free developer tools student email edu"),
    # --- Open source free alternatives ---
    ("open_source",   "open source free alternative to Cursor Copilot"),
    ("open_source",   "free self-hosted AI coding assistant GitHub"),
    ("open_source",   "open source Notion alternative free self-hosted"),
    ("open_source",   "free open source developer tool launch GitHub"),
    # --- SaaS lifetime / promo deals ---
    ("saas_deals",    "SaaS lifetime deal developer tool AppSumo"),
    ("saas_deals",    "promo code developer tool free subscription"),
    ("saas_deals",    "indie hacker launch deal free plan developer"),
    ("saas_deals",    "1 year free SaaS developer tool coupon"),
    # --- LLM API drops ---
    ("llm_api_drop",  "free API key base URL AI model shared developer"),
    ("llm_api_drop",  "DeepSeek free API access developer key"),
    ("llm_api_drop",  "Venice AI free API key developer"),
    ("llm_api_drop",  "Qwen free model API developer access"),
    ("llm_api_drop",  "Together AI free credits new account"),
    # --- CS student geo/niche deals ---
    ("student_geo",   "free Google Gemini AI students education"),
    ("student_geo",   "free developer tools Pakistani students edu"),
    ("student_geo",   "GitHub Education Pack student discount free"),
    # --- VPS / hosting ---
    ("vps_hosting",   "free VPS hosting developer no credit card"),
    ("vps_hosting",   "free web hosting developer static sites"),
    ("vps_hosting",   "Cloudflare Pages Workers free hosting developer"),
    ("vps_hosting",   "Netlify Vercel free hosting tier developer"),

    # --- Category 9: api_drops_primary ---
    # Intercepts newly launched AI API services BEFORE influencers do.
    # Targets ProductHunt launches, GitHub READMEs, and HN Show HN posts.
    ("api_drops_primary", "free API credits no credit card required register today"),
    ("api_drops_primary", "new AI API free tier OpenAI compatible launch 2026"),
    ("api_drops_primary", "site:producthunt.com free developer API credits launch"),
    ("api_drops_primary", "site:news.ycombinator.com Show HN free API LLM model"),
    ("api_drops_primary", "site:github.com free API key credits OpenAI compatible"),
    ("api_drops_primary", "launched today free credits API register no card"),
    ("api_drops_primary", "site:appsumo.com lifetime deal developer API tool"),

    # --- Category 10: promo_code_drops ---
    # Catches specific promo/invite codes before they circulate in WhatsApp channels.
    ("promo_code_drops", "site:reddit.com/r/freebies promo code free months developer"),
    ("promo_code_drops", "site:reddit.com/r/selfhosted coupon code free premium"),
    ("promo_code_drops", "invite code free tier developer tool 2026"),
    ("promo_code_drops", "site:saasmantra.com free developer tool lifetime"),
    ("promo_code_drops", "site:dealmirror.com developer tool free deal"),
    ("promo_code_drops", "promo code free premium VPN tool months 2026"),
    ("promo_code_drops", "free 1 year subscription developer tool promo code"),

    # --- Category 11: curated_deal_hubs ---
    # Dedicated deal-aggregator hubs — high signal density, updated frequently.
    ("curated_deal_hubs", "site:joinsecret.com free developer tool startup deal"),
    ("curated_deal_hubs", "site:joinsecret.com AI tool credits free tier"),
    ("curated_deal_hubs", "site:toolify.ai free AI tool trial credits"),
    ("curated_deal_hubs", "site:toolify.ai new AI tool free plan launch"),
    ("curated_deal_hubs", "site:saasmantra.com lifetime deal developer tool"),
    ("curated_deal_hubs", "site:saasmantra.com AI tool discount code"),
    ("curated_deal_hubs", "site:alternativeto.net free open source alternative developer"),
    ("curated_deal_hubs", "site:alternativeto.net free tier AI coding tool"),

    # --- LLM-aggregator OSINT sweep (added 2026-08-26) ------------------------ #
    # Aggregators = one key, many models. These find the aggregator itself, not
    # an individual provider's free tier, so they complement the ai_apis /
    # llm_api_drop tags rather than duplicating them.
    ("llm_aggregator",    "best OpenRouter alternatives free tier comparison"),
    # Brand-probe. OmniRoute is a real, well-documented aggregator, so it is
    # wired as a named target. Deliberately NOT wired: gorouter.app (unverified
    # beyond "probably a rebranded QuantumNous/new-api instance"), true-sota.com
    # (unverifiable, title-only) and "SeekAI" (does not exist as an LLM
    # aggregator — a query naming it would only manufacture false positives).
    ("llm_aggregator",    "OmniRoute unified LLM API free tier models router"),

    # ── Chinese-market aggregators / 中转 / genuine free domestic tiers ──
    ("llm_aggregator_cn", "大模型 API 聚合 一个 key 多个模型 免费额度"),
    ("llm_aggregator_cn", "OpenAI API 中转站 免费 Claude API 代理 镜像站"),
    ("llm_aggregator_cn", "免费大模型 API 接口 GLM 智谱 Kimi Moonshot 免费额度 领取"),
    ("llm_aggregator_cn", "国产大模型 免费 API 额度 对比 智谱 通义千问 DeepSeek 硅基流动"),
    ("llm_aggregator_cn", "one-api new-api 开源 大模型网关 令牌分发 中转"),

    # ── Regional (India / SEA / JP / KR) ──
    # ja and ko were total locale gaps before this block; an English-language
    # Japan/Korea probe was tried and returned nothing, so these are
    # native-language only by design.
    ("llm_aggregator_intl", "India LLM API aggregator unified model router UPI rupee"),
    ("llm_aggregator_intl", "Southeast Asia LLM API gateway free tier Singapore Indonesia"),
    ("llm_aggregator_intl", "LLM API まとめ 無料 複数モデル 統合 ゲートウェイ"),
    ("llm_aggregator_intl", "무료 LLM API 통합 여러 모델 하나의 키 게이트웨이"),

    # ── Curated free-API lists (NOTE: overlaps github_adapter TARGETS by
    # design — the adapter parses the lists it already knows about, these
    # discover lists it does not. Dedup happens downstream on canonical_url.) ──
    ("free_api_lists",    "awesome free llm api github list maintained providers rate limits"),
    ("free_api_lists",    "directory of free AI APIs unlimited free LLM models list"),

    # ── Reliability / fraud signal → intended to feed a SUPPRESSION list, NOT
    # the offer feed. Tagged aggregator_fraud and excluded from every default
    # path; see NEVER_INGEST_TAGS below for why that exclusion is mandatory. ──
    ("aggregator_fraud",  "LLM API relay gateway exit scam refund not working review"),
    ("aggregator_fraud",  "中转站 跑路 骗局 假模型 降智 用户评价"),

    # ── Serper-only bucket. site:/OR/quoted syntax is unsupported or silently
    # ignored by DuckDuckGo, so these return junk or nothing under the default
    # ddg engine. Gated by SERPER_ONLY_TAGS below — never in the ddg set. ──
    ("llm_aggregator_serper", "site:github.com awesome free llm api providers rate limits"),
    ("llm_aggregator_serper", "site:news.ycombinator.com Show HN LLM router gateway free"),
    ("llm_aggregator_serper", "site:reddit.com/r/LocalLLaMA free API aggregator many models"),
]

# --------------------------------------------------------------------------- #
# Tags that must NOT run under the default template set.
# --------------------------------------------------------------------------- #
# Engine gate. These queries rely on site:/OR/quoted operators that the ddgs
# (DuckDuckGo) backend does not honour — under ddg they degrade to zero or
# irrelevant results and silently burn a query slot. Included only when the
# resolved engine is "serper".
SERPER_ONLY_TAGS: frozenset[str] = frozenset({"llm_aggregator_serper"})

# Safety gate. These queries surface scam/complaint writeups ABOUT aggregators —
# they are reliability signal, not offers. run_deep_web() calls upsert_raw_item()
# on EVERY result regardless of category_tag (see the write loop below: there is
# no tag-aware suppression stage anywhere in the pipeline yet), so any code path
# that reaches run_deep_web with these templates injects scam-report URLs
# straight into raw_items and onward to the offer feed.
#
# NOTE ON A DELIBERATE DEVIATION: the brief asked for these to be "reachable
# only via all_deals (orphan-tag pattern)". That is not achievable as stated —
# all_deals resolves to the FULL template list, so reaching them via all_deals
# is exactly what puts them into upsert_raw_item. The absolute requirement
# ("must NEVER flow into the offer feed") wins over the routing preference, so
# they are excluded from all_deals too and are reachable only by an explicit
# `--category aggregator_fraud` CLI run — a deliberate human act the scheduler
# never performs. Drop this frozenset to one line to reverse that once a real
# suppression consumer exists.
NEVER_INGEST_TAGS: frozenset[str] = frozenset({"aggregator_fraud"})


def _excluded_tags(engine: str) -> set[str]:
    """Tags to strip from any default/all_deals template selection."""
    excluded = set(NEVER_INGEST_TAGS)
    if engine != "serper":
        excluded |= SERPER_ONLY_TAGS
    return excluded


def default_templates(engine: str | None = None) -> list[tuple[str, str]]:
    """QUERY_TEMPLATES minus the tags gated off for this engine.

    This is the *only* thing that should be used as a default template set;
    referencing QUERY_TEMPLATES directly bypasses both gates.
    """
    engine = engine or os.environ.get("DEEP_WEB_ENGINE", "ddg")
    excluded = _excluded_tags(engine)
    return [(t, q) for t, q in QUERY_TEMPLATES if t not in excluded]


# --------------------------------------------------------------------------- #
# Scheduler run-category → deep-web query-tag mapping.
# Owned here (next to QUERY_TEMPLATES) rather than in the scheduler, so the
# tags and the queries they select can never drift apart. An empty list means
# "all templates". The scheduler's `all_deals` sentinel maps to [].
# --------------------------------------------------------------------------- #
RUN_CATEGORY_TO_TAGS: dict[str, list[str]] = {
    "cloud":            ["cloud_credits"],
    "student_pack":     ["student_packs", "student_geo"],
    "saas_deal":        ["saas_deals", "promo_code_drops"],
    "open_source_repo": ["open_source"],
    "coding_agents":    ["ai_apis"],
    "coupon":           ["promo_code_drops"],
    "llm_api_drop":     ["llm_api_drop", "api_drops_primary",
                         "llm_aggregator", "llm_aggregator_cn",
                         "llm_aggregator_intl", "free_api_lists"],
    "ai_tools":         ["ai_apis", "api_drops_primary", "llm_aggregator"],
    "all_deals":        [],  # all templates (minus gated tags — see default_templates)
}
# aggregator_fraud and llm_aggregator_serper are intentionally absent from every
# value above. Do not add them: the first would inject scam writeups into the
# offer feed, the second returns nothing under the default ddg engine.


def templates_for_run_category(
    run_category: str, engine: str | None = None
) -> list[tuple[str, str]]:
    """Return the (tag, query) templates a scheduler run-category should search.

    Gated tags are stripped in both branches: an explicit tag list can't request
    a gated tag (none of them appear in RUN_CATEGORY_TO_TAGS), and the empty-list
    "all templates" branch goes through default_templates() rather than the raw
    QUERY_TEMPLATES so all_deals cannot pull in fraud or serper-only queries.
    """
    tags = RUN_CATEGORY_TO_TAGS.get(run_category, [])
    if not tags:
        return default_templates(engine)
    excluded = _excluded_tags(engine or os.environ.get("DEEP_WEB_ENGINE", "ddg"))
    return [(cat, q) for cat, q in QUERY_TEMPLATES if cat in tags and cat not in excluded]


# Import-time guard: warn about query tags that no run-category can ever reach
# (e.g. `curated_deal_hubs`, `vps_hosting`) — they only fire under `all_deals`.
# Tags in SERPER_ONLY_TAGS / NEVER_INGEST_TAGS are excluded deliberately and are
# NOT reachable via all_deals either, so they are reported separately rather than
# as accidental orphans — otherwise the real signal here gets drowned out.
_reachable_tags = {t for tags in RUN_CATEGORY_TO_TAGS.values() for t in tags}
_all_tags = {cat for cat, _ in QUERY_TEMPLATES}
_gated_tags = SERPER_ONLY_TAGS | NEVER_INGEST_TAGS
_orphan_tags = _all_tags - _reachable_tags - _gated_tags
if _orphan_tags:
    log.warning(
        "deep_web query tags only reachable via all_deals (no dedicated run-category): %s",
        ", ".join(sorted(_orphan_tags)),
    )
if _gated_tags & _all_tags:
    log.info(
        "deep_web gated tags (excluded from all default paths by design): %s",
        ", ".join(sorted(_gated_tags & _all_tags)),
    )
# Fail loudly if a gated tag ever gets wired into a run-category — that would
# silently defeat the gate for every scheduler slot using that category.
_leaked = _gated_tags & _reachable_tags
if _leaked:
    raise RuntimeError(
        f"gated deep_web tags wired into RUN_CATEGORY_TO_TAGS: {sorted(_leaked)}. "
        "aggregator_fraud must never reach upsert_raw_item; llm_aggregator_serper "
        "returns nothing under the default ddg engine."
    )


# --------------------------------------------------------------------------- #
# Result shape
# --------------------------------------------------------------------------- #
@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str
    query: str
    category_tag: str


# --------------------------------------------------------------------------- #
# DuckDuckGo engine (ddgs library — renamed from duckduckgo-search)
# --------------------------------------------------------------------------- #
def _ddg_search(query: str, max_results: int, timelimit: str) -> list[dict]:
    try:
        from ddgs import DDGS  # type: ignore[import]
    except ImportError:
        try:
            from duckduckgo_search import DDGS  # type: ignore[import]  # legacy name
        except ImportError:
            log.error("ddgs not installed. Run: pip install ddgs")
            return []
    try:
        with DDGS() as ddgs:
            return list(ddgs.text(query, max_results=max_results, timelimit=timelimit))
    except Exception as exc:  # noqa: BLE001
        log.warning("DDG search failed for %r: %s", query[:60], exc)
        return []


# --------------------------------------------------------------------------- #
# Serper API engine
# --------------------------------------------------------------------------- #
async def _serper_search(
    client: httpx.AsyncClient,
    query: str,
    max_results: int,
    api_key: str,
) -> list[dict]:
    try:
        resp = await client.post(
            SERPER_URL,
            headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
            json={"q": query, "num": max_results},
            timeout=15.0,
        )
        resp.raise_for_status()
        data = resp.json()
        results = []
        for item in data.get("organic", []):
            results.append({
                "title": item.get("title", ""),
                "href": item.get("link", ""),
                "body": item.get("snippet", ""),
            })
        return results
    except httpx.HTTPError as exc:
        log.warning("Serper search failed for %r: %s", query[:60], exc)
        return []


# --------------------------------------------------------------------------- #
# Dedup cache (in-memory, across queries within a single run)
# --------------------------------------------------------------------------- #
def _url_hash(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:16]


# --------------------------------------------------------------------------- #
# Payload builder
# --------------------------------------------------------------------------- #
def _result_to_payload(result: SearchResult) -> dict:
    return {
        "external_id": _url_hash(result.url),
        "text": f"{result.title}\n{result.snippet}",
        "urls": [result.url],
        "author_handle": "deep_web_search",
        "published_at": None,
        "engagement": {},
        "extra": {
            "search_query": result.query,
            "category_tag": result.category_tag,
            "adapter": "deep_web_adapter",
        },
    }


# --------------------------------------------------------------------------- #
# Source management
# --------------------------------------------------------------------------- #
def _ensure_source(source_name: str, category_tag: str) -> int:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO sources (name, kind, config)
                VALUES (%s, 'web', %s::jsonb)
                ON CONFLICT (name) DO NOTHING
                RETURNING id
                """,
                (
                    source_name,
                    json.dumps({"category_tag": category_tag, "adapter": "deep_web_adapter"}),
                ),
            )
            row = cur.fetchone()
            if row:
                conn.commit()
                return row["id"]
            cur.execute("SELECT id FROM sources WHERE name = %s", (source_name,))
            return cur.fetchone()["id"]


# --------------------------------------------------------------------------- #
# Main runner
# --------------------------------------------------------------------------- #
async def run_deep_web(
    templates: list[tuple[str, str]] | None = None,
    engine: str | None = None,
    category_filter: str | None = None,
    lookback: str = DEFAULT_LOOKBACK,
    dry_run: bool = False,
    max_items: int | None = None,
) -> int:
    """``max_items`` caps total raw_items written across all query templates in one
    run (None = uncapped, for manual CLI). The scheduler passes a cap so ingestion
    can't outrun the pipeline's per-slot drain rate."""
    engine = engine or os.environ.get("DEEP_WEB_ENGINE", "ddg")
    serper_key = os.environ.get("SERPER_API_KEY", "")
    if engine == "serper" and not serper_key:
        log.error("engine=serper but SERPER_API_KEY not set; falling back to DDG")
        engine = "ddg"

    # Engine is resolved BEFORE templates so the gates see the *effective* engine,
    # including the serper->ddg downgrade above — otherwise a keyless serper run
    # would fall back to ddg while still carrying the serper-only queries.
    if category_filter:
        # An explicit single-tag request is a deliberate human act, so it is
        # allowed to select a gated tag (this is the only way to reach
        # aggregator_fraud, e.g. `--category aggregator_fraud`).
        templates = [
            (cat, q) for cat, q in (templates or QUERY_TEMPLATES) if cat == category_filter
        ]
        if not templates:
            log.warning("No templates found for category %r", category_filter)
            return 0
    else:
        # default_templates(), never raw QUERY_TEMPLATES — the raw list contains
        # the fraud and serper-only buckets.
        templates = templates or default_templates(engine)
        # Re-apply the gate to CALLER-SUPPLIED templates as well. scheduler.py
        # always passes templates= explicitly, and templates_for_run_category()
        # resolved the engine from the env BEFORE run_deep_web's serper->ddg
        # downgrade could happen — so `DEEP_WEB_ENGINE=serper` with no
        # SERPER_API_KEY would otherwise hand us the 3 site:-bearing queries and
        # then execute them under ddg. Gating at the point of use makes this
        # independent of how careful the caller was.
        excluded = _excluded_tags(engine)
        dropped = [(c, q) for c, q in templates if c in excluded]
        if dropped:
            log.warning(
                "dropped %d gated template(s) for engine=%s: %s",
                len(dropped), engine, sorted({c for c, _ in dropped}),
            )
            templates = [(c, q) for c, q in templates if c not in excluded]

    seen_hashes: set[str] = set()
    total = 0

    log.info("Deep web adapter: engine=%s lookback=%s templates=%d", engine, lookback, len(templates))

    async with httpx.AsyncClient(
        headers={"User-Agent": "freebies-research-bot/1.0"},
        follow_redirects=True,
    ) as client:
        for category_tag, query in templates:
            if max_items is not None and total >= max_items:
                log.info("deep_web hit max_items=%d — stopping", max_items)
                break
            log.info("[%s] %s", category_tag, query[:90])
            source_name = f"web:deep_search:{category_tag}"
            # Resolve source_id up front so a failed search is still recorded on the source.
            source_id = None if dry_run else _ensure_source(source_name, category_tag)

            if engine == "serper":
                raw_results = await _serper_search(client, query, MAX_RESULTS_PER_QUERY, serper_key)
            else:
                raw_results = await asyncio.get_event_loop().run_in_executor(
                    None, _ddg_search, query, MAX_RESULTS_PER_QUERY, lookback
                )

            log.info("  → %d results", len(raw_results))
            if not raw_results:
                # Both engines return [] on fetch error (they log & swallow), so treat
                # an empty result set as a failed fetch for this source.
                _health(source_id, ok=False, error=f"search returned no results: {query[:80]}")
                await asyncio.sleep(INTER_QUERY_DELAY)
                continue

            for item in raw_results:
                if max_items is not None and total >= max_items:
                    break
                url = item.get("href") or item.get("url") or ""
                if not url or not url.startswith("http"):
                    continue
                url_hash = _url_hash(url)
                if url_hash in seen_hashes:
                    continue
                seen_hashes.add(url_hash)

                result = SearchResult(
                    title=item.get("title", "").strip(),
                    url=url,
                    snippet=item.get("body", "").strip()[:500],
                    query=query,
                    category_tag=category_tag,
                )
                payload = _result_to_payload(result)

                if dry_run:
                    print(json.dumps(payload, ensure_ascii=False, indent=2))
                    total += 1
                    continue

                with connect() as conn:
                    upsert_raw_item(conn, source_id, url_hash, payload)
                total += 1

            _health(source_id, ok=True)
            await asyncio.sleep(INTER_QUERY_DELAY)

    log.info("Deep web adapter done: %d items written", total)
    return total


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(description="Deep web research adapter (DDG + Serper)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--engine", choices=["ddg", "serper"], default=None,
                        help="search engine (default: DEEP_WEB_ENGINE env var or ddg)")
    parser.add_argument("--category", default=None,
                        help="run only templates for this category tag")
    parser.add_argument(
        "--lookback", default=DEFAULT_LOOKBACK,
        choices=["d", "w", "m", "y"],
        help="result recency: d=day w=week(default) m=month y=year",
    )
    parser.add_argument(
        "--max-items", type=int, default=None,
        help="cap total raw_items written this run (default: uncapped)",
    )
    args = parser.parse_args()

    asyncio.run(run_deep_web(
        engine=args.engine,
        category_filter=args.category,
        lookback=args.lookback,
        dry_run=args.dry_run,
        max_items=args.max_items,
    ))


if __name__ == "__main__":
    main()
