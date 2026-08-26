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
]


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
    "llm_api_drop":     ["llm_api_drop", "api_drops_primary"],
    "ai_tools":         ["ai_apis", "api_drops_primary"],
    "all_deals":        [],  # all templates
}


def templates_for_run_category(run_category: str) -> list[tuple[str, str]]:
    """Return the (tag, query) templates a scheduler run-category should search."""
    tags = RUN_CATEGORY_TO_TAGS.get(run_category, [])
    if not tags:
        return list(QUERY_TEMPLATES)
    return [(cat, q) for cat, q in QUERY_TEMPLATES if cat in tags]


# Import-time guard: warn about query tags that no run-category can ever reach
# (e.g. `curated_deal_hubs`, `vps_hosting`) — they only fire under `all_deals`.
_reachable_tags = {t for tags in RUN_CATEGORY_TO_TAGS.values() for t in tags}
_all_tags = {cat for cat, _ in QUERY_TEMPLATES}
_orphan_tags = _all_tags - _reachable_tags
if _orphan_tags:
    log.warning(
        "deep_web query tags only reachable via all_deals (no dedicated run-category): %s",
        ", ".join(sorted(_orphan_tags)),
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
    templates = templates or QUERY_TEMPLATES
    if category_filter:
        templates = [(cat, q) for cat, q in templates if cat == category_filter]
        if not templates:
            log.warning("No templates found for category %r", category_filter)
            return 0

    engine = engine or os.environ.get("DEEP_WEB_ENGINE", "ddg")
    serper_key = os.environ.get("SERPER_API_KEY", "")
    if engine == "serper" and not serper_key:
        log.error("engine=serper but SERPER_API_KEY not set; falling back to DDG")
        engine = "ddg"

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
