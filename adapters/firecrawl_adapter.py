"""
Firecrawl adapter — multi deal-hub deep extraction
====================================================
Uses the Firecrawl SDK (firecrawl-py) for headless JS-rendered extraction.
Covers a list of deal hubs (TARGET_SITES): resourify.com, aicredits.dev,
dealify.com — each mapped, deduped, and scraped independently against its own
`firecrawl:<host>` source row.

Pipeline (per site):
  1. Map  — discover all individual-offer slugs (URLs containing the site's
             path_fragment, e.g. /resources/, /submissions/, /products/) via
             firecrawl.map()
  2. Dedup — drop URLs already ingested within RESCRAPE_AFTER_DAYS (queried
             from raw_items); only genuinely new / stale pages survive. This
             is the credit guard: the paid LLM-extract scrape in step 3 only
             ever sees unseen URLs, so a re-run of an unchanged catalog is free.
  3. Batch scrape — extract structured deal data via LLM extraction schema
  4. Emit — write NormalizedItem-compatible payloads to upsert_raw_item()

Env vars:
    FIRECRAWL_API_KEY   — required

Usage:
    python -m adapters.firecrawl_adapter              # full run
    python -m adapters.firecrawl_adapter --dry-run
    python -m adapters.firecrawl_adapter --category llm_api_drop
    python -m adapters.firecrawl_adapter --limit 20
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
from dataclasses import dataclass
from typing import Any

from dotenv import load_dotenv

from crawler.db import connect, record_source_health, upsert_raw_item

load_dotenv(override=True)
log = logging.getLogger("adapter.firecrawl")


def _health(source_id: int | None, ok: bool, error: str | None = None) -> None:
    """Record a source-health snapshot; no-op when source_id is unknown (dry-run)."""
    if source_id is None:
        return
    with connect() as conn:
        record_source_health(conn, source_id, ok=ok, error=error)

RESCRAPE_AFTER_DAYS = 14    # re-scrape a page at most once per this window
MAX_URLS_PER_BATCH = 20     # Firecrawl batch_scrape limit per call
INTER_BATCH_DELAY = 3.0     # seconds between batch calls

# Item 4 — credit guard. A hard ceiling on how many URLs any one site may scrape
# in a single run, independent of how many unseen URLs its map returns. This is
# the durable fix for cold-start / re-scrape credit bursts: dealify.com's first
# run mapped 767 product pages and scraped every one in a single paid burst
# (0 offers yet, all still draining the pipeline backlog). With a per-run cap the
# same catalog is instead spread across successive daily runs — the dedup filter
# skips the pages already scraped, so a large catalog is mined a bounded slice at
# a time and no single day can spike credit consumption. Per-site overrides live
# on SiteConfig; this is the default when a site doesn't set its own.
DEFAULT_MAX_URLS_PER_RUN = 60


@dataclass(frozen=True)
class SiteConfig:
    """A deal-hub to map + scrape. ``path_fragment`` restricts the map to that
    site's individual deal/offer pages (everything else — category indexes,
    blog, auto-generated compare pages — is dropped before the paid scrape).
    ``handle`` is the author_handle stamped on every offer from the site.
    ``rescrape_after_days`` and ``max_urls_per_run`` let a slow-changing or
    low-yield site (e.g. dealify.com) refresh less often and scrape fewer pages
    per run, keeping Firecrawl credit-efficient and a minority contributor."""
    base_url: str
    path_fragment: str
    handle: str
    rescrape_after_days: int = RESCRAPE_AFTER_DAYS
    max_urls_per_run: int = DEFAULT_MAX_URLS_PER_RUN


# Sites to cover — add more deal hubs here as needed. Each is mapped + deduped +
# scraped independently (its own `firecrawl:<host>` source row), and all ride the
# single once/day all_deals-run-1 slot the scheduler gates firecrawl to.
TARGET_SITES: list[SiteConfig] = [
    # resourify.com — mixed discounts / credits / lifetime deals; /resources/<slug>.
    #   High-yield (78% of scraped pages become offers); default cadence + cap.
    SiteConfig("https://resourify.com", "/resources/", "resourify.com"),
    # aicredits.dev — free AI API / cloud / GPU / student credits; /submissions/<id>-<slug>.
    #   New signal class (credits, not discounts), clean structure, ~195 pages.
    SiteConfig("https://aicredits.dev", "/submissions/", "aicredits.dev"),
    # dealify.com — curated SaaS lifetime deals (Shopify); /products/<slug>.
    #   path_fragment drops /collections indexes so only product pages are scraped.
    #   Biggest catalog (767 pages) and slowest-changing, so refresh only monthly
    #   and scrape a smaller slice per run — this is where the credit risk lives.
    SiteConfig("https://dealify.com", "/products/", "dealify.com",
               rescrape_after_days=30, max_urls_per_run=40),
]

# Category path fragments on resourify.com
RESOURIFY_CATEGORY_PATHS: list[str] = [
    "/category/freebies",
    "/category/credits",
    "/category/lifetime-deals",
    "/category/discounts",
    "/category/open-source",
]

# Firecrawl extraction schema — tells the LLM exactly what to pull
EXTRACT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title":          {"type": "string"},
        "description":    {"type": "string"},
        "direct_url":     {"type": "string",
                           "description": "The direct claim/offer URL (not the resourify page)"},
        "promo_code":     {"type": "string",
                           "description": "Exact promo/coupon/invite code string if present"},
        "value":          {"type": "number",
                           "description": "Numeric dollar/credit value if explicitly stated"},
        "currency":       {"type": "string", "description": "3-letter ISO currency code"},
        "expires_at":     {"type": "string",
                           "description": "ISO-8601 expiry datetime if stated, else null"},
        "claiming_steps": {"type": "array", "items": {"type": "string"},
                           "description": "Ordered steps to claim the offer"},
        "category":       {"type": "string",
                           "description": "One of: cloud, llm, llm_api_drop, student, "
                                          "student_pack, saas_deal, coupon, ai_tools, "
                                          "coding_agents, open_source_repo, other"},
        "is_offer":       {"type": "boolean",
                           "description": "true if this page contains an actionable free/discounted offer"},
    },
    "required": ["title", "is_offer"],
}

EXTRACT_PROMPT = (
    "Extract developer freebie deal details. Focus on: "
    "lifetime deals, 100% off promo codes, student discounts, AI API credit drops, "
    "free tier registrations, and VPN/tool free offers. "
    "Populate claiming_steps with the exact numbered instructions from the page. "
    "Extract the promo_code verbatim if present. "
    "Set is_offer=false for pages that are purely informational with no actionable deal."
)


# --------------------------------------------------------------------------- #
# Firecrawl SDK wrapper (graceful import)
# --------------------------------------------------------------------------- #
def _get_client():
    api_key = os.environ.get("FIRECRAWL_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "FIRECRAWL_API_KEY not set. Add it to .env:\n  FIRECRAWL_API_KEY=fc-..."
        )
    try:
        # firecrawl-py >=4.x repurposed the top-level `FirecrawlApp` class for
        # its paper/GitHub-search client. The classic map/batch-scrape REST
        # methods now live on `V1FirecrawlApp`.
        from firecrawl import V1FirecrawlApp  # type: ignore[import]
        return V1FirecrawlApp(api_key=api_key)
    except ImportError:
        raise RuntimeError(
            "firecrawl-py not installed. Run:\n  pip install firecrawl-py"
        )


# --------------------------------------------------------------------------- #
# Step 1 — Map: discover all resource slugs
# --------------------------------------------------------------------------- #
def _map_site(app, site: SiteConfig, category_filter: str | None) -> list[str]:
    """Return all individual-offer URLs (those containing site.path_fragment)."""
    log.info("Mapping %s ...", site.base_url)
    try:
        result = app.map_url(site.base_url, include_subdomains=False)
        all_urls: list[str] = result.links or []
    except Exception as exc:
        log.warning("firecrawl.map failed for %s: %s", site.base_url, exc)
        return []

    resource_urls = [u for u in all_urls if site.path_fragment in u]
    log.info("  %d offer URLs discovered (fragment %s)",
             len(resource_urls), site.path_fragment)
    return resource_urls


# --------------------------------------------------------------------------- #
# Step 2 — Dedup filter: skip URLs already ingested recently
# --------------------------------------------------------------------------- #
# Replaces the old JSON-LD `dateModified` freshness check, which failed OPEN:
# resourify pages carry no matching dateModified, so the "include to be safe"
# fallback passed all 125 URLs every run and the paid LLM-extract scrape re-ran
# the whole catalog ~27×/day (the 2026-08 credit blowout). This filter instead
# fails CLOSED against what we have already stored: a URL is scraped only if its
# hash is absent from raw_items for this source within RESCRAPE_AFTER_DAYS, so an
# unchanged catalog costs nothing on subsequent runs and stale pages still get a
# periodic refresh once they age out of the window.
def _seen_external_ids(source_id: int, within_days: int) -> set[str]:
    """external_ids ingested for this source within the last `within_days`."""
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT external_id FROM raw_items
                WHERE source_id = %s
                  AND fetched_at > now() - make_interval(days => %s)
                """,
                (source_id, within_days),
            )
            return {row["external_id"] for row in cur.fetchall()}


def _filter_unseen(
    urls: list[str],
    source_id: int | None,
    within_days: int,
) -> list[str]:
    """Keep only URLs not already ingested within `within_days`.

    In dry-run there is no source_id to dedup against, so all URLs pass (a
    dry-run is a manual, rare, read-only inspection — never the scheduled path)."""
    if source_id is None:
        return urls
    seen = _seen_external_ids(source_id, within_days)
    unseen = [u for u in urls if _url_hash(u) not in seen]
    log.info(
        "Dedup filter: %d → %d URLs (%d already seen within %dd)",
        len(urls), len(unseen), len(urls) - len(unseen), within_days,
    )
    return unseen


# --------------------------------------------------------------------------- #
# Step 3 — Batch scrape with extraction schema
# --------------------------------------------------------------------------- #
def _batch_scrape(app, urls: list[str]) -> list[dict]:
    """Call Firecrawl batch_scrape with extract format. Returns raw result list."""
    from firecrawl.v1.client import V1JsonConfig

    results: list[dict] = []
    for i in range(0, len(urls), MAX_URLS_PER_BATCH):
        chunk = urls[i: i + MAX_URLS_PER_BATCH]
        log.info("  batch %d/%d  (%d URLs)",
                 i // MAX_URLS_PER_BATCH + 1,
                 -(-len(urls) // MAX_URLS_PER_BATCH),
                 len(chunk))
        try:
            batch_result = app.batch_scrape_urls(
                chunk,
                formats=["extract"],
                extract=V1JsonConfig(**{"schema": EXTRACT_SCHEMA, "prompt": EXTRACT_PROMPT}),
            )
            # batch_scrape_urls returns a V1BatchScrapeStatusResponse; .data is a
            # list of V1FirecrawlDocument, whose .extract/.metadata are plain dicts.
            for doc in batch_result.data or []:
                results.append({
                    "url": doc.url,
                    "extract": doc.extract or {},
                    "metadata": doc.metadata or {},
                })
        except Exception as exc:
            log.warning("batch_scrape failed for chunk starting %s: %s", chunk[0], exc)
        if i + MAX_URLS_PER_BATCH < len(urls):
            import time
            time.sleep(INTER_BATCH_DELAY)
    return results


# --------------------------------------------------------------------------- #
# Step 4 — Build payload & write to DB
# --------------------------------------------------------------------------- #
def _url_hash(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:20]


def _result_to_payload(scraped_url: str, extract: dict, handle: str) -> dict | None:
    """Convert a Firecrawl extract result to a upsert_raw_item payload."""
    if not extract.get("is_offer", False):
        return None

    title = (extract.get("title") or "").strip()
    if not title or len(title) < 4:
        return None

    direct_url = (extract.get("direct_url") or scraped_url).strip()
    claiming_steps: list[str] = extract.get("claiming_steps") or []

    return {
        "external_id": _url_hash(scraped_url),
        "text": f"{title}\n{extract.get('description') or ''}\n"
                + "\n".join(f"{i+1}. {s}" for i, s in enumerate(claiming_steps)),
        "urls": [direct_url, scraped_url] if direct_url != scraped_url else [scraped_url],
        "author_handle": handle,
        "published_at": None,
        "engagement": {},
        "extra": {
            "adapter": "firecrawl_adapter",
            "source_url": scraped_url,
            "promo_code": extract.get("promo_code"),
            "value": extract.get("value"),
            "currency": extract.get("currency"),
            "expires_at": extract.get("expires_at"),
            "claiming_steps": claiming_steps,
            "category_hint": extract.get("category"),
        },
    }


def _ensure_source(source_name: str) -> int:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO sources (name, kind, config)
                VALUES (%s, 'web', %s::jsonb)
                ON CONFLICT (name) DO NOTHING
                RETURNING id
                """,
                (source_name, json.dumps({"adapter": "firecrawl_adapter", "site": source_name})),
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
async def run_firecrawl(
    sites: list[SiteConfig] | None = None,
    category_filter: str | None = None,
    rescrape_after_days: int = RESCRAPE_AFTER_DAYS,
    dry_run: bool = False,
    limit: int | None = None,
) -> int:
    sites = sites or TARGET_SITES
    total = 0

    try:
        app = _get_client()
    except RuntimeError as exc:
        log.error("%s", exc)
        return 0

    for site in sites:
        # source_name preserves the existing `firecrawl:<host>` row so each site's
        # dedup history is scoped to itself (resourify keeps firecrawl:resourify.com).
        source_name = f"firecrawl:{site.handle}"
        # Resolve source_id up front so a failed map/scrape is still recorded on the source.
        source_id = None if dry_run else _ensure_source(source_name)

        # 1) Map
        all_urls = _map_site(app, site, category_filter)
        if not all_urls:
            _health(source_id, ok=False, error=f"map returned no URLs: {site.base_url}")
            continue

        if limit:
            all_urls = all_urls[:limit]

        # 2) Dedup filter — drop URLs already ingested within the window so the
        #    paid extract scrape only ever sees genuinely new / stale pages. The
        #    window is per-site (dealify refreshes monthly, others every 14d);
        #    an explicit --rescrape-after-days on the CLI still overrides all sites.
        window = rescrape_after_days if rescrape_after_days != RESCRAPE_AFTER_DAYS \
            else site.rescrape_after_days
        fresh_urls = _filter_unseen(all_urls, source_id, window)
        if not fresh_urls:
            log.info("No unseen URLs for %s (all scraped within %dd)", site.base_url, window)
            _health(source_id, ok=True)  # site reached fine, nothing new to scrape
            continue

        # 2b) Per-run credit cap — never scrape more than the site's per-run
        #     ceiling in one go. A large unseen backlog (e.g. dealify's cold-start
        #     catalog) is mined a bounded slice per day; the dedup filter above
        #     skips already-scraped pages on the next run, so the rest follow
        #     across subsequent runs instead of spiking credits in a single burst.
        if site.max_urls_per_run and len(fresh_urls) > site.max_urls_per_run:
            log.info("Capping %s: %d unseen → %d this run (per-run credit cap)",
                     site.base_url, len(fresh_urls), site.max_urls_per_run)
            fresh_urls = fresh_urls[:site.max_urls_per_run]

        # 3) Batch scrape
        log.info("Scraping %d unseen URLs from %s", len(fresh_urls), site.base_url)
        raw_results = _batch_scrape(app, fresh_urls)

        # 4) Write
        for item in raw_results:
            scraped_url = item.get("metadata", {}).get("sourceURL", "") or item.get("url", "")
            extract = item.get("extract") or {}
            if not extract:
                continue

            payload = _result_to_payload(scraped_url, extract, site.handle)
            if not payload:
                continue

            if dry_run:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
                total += 1
                continue

            with connect() as conn:
                upsert_raw_item(conn, source_id, payload["external_id"], payload)
            total += 1

        _health(source_id, ok=True)

    log.info("Firecrawl adapter done: %d items %s", total,
             "would be written (dry-run)" if dry_run else "written")
    return total


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(description="Firecrawl adapter for deal site extraction")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--category", default=None, help="filter by pipeline category name")
    parser.add_argument("--limit", type=int, default=None, help="max URLs to scrape per site")
    parser.add_argument("--rescrape-after-days", type=int, default=RESCRAPE_AFTER_DAYS,
                        help=f"re-scrape a page at most once per N days (default {RESCRAPE_AFTER_DAYS})")
    args = parser.parse_args()

    asyncio.run(run_firecrawl(
        category_filter=args.category,
        rescrape_after_days=args.rescrape_after_days,
        dry_run=args.dry_run,
        limit=args.limit,
    ))


if __name__ == "__main__":
    main()
