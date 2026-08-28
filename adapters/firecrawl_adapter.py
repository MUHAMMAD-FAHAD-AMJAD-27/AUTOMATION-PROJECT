"""
Firecrawl adapter — resourify.com + deal hub deep extraction
=============================================================
Uses the Firecrawl SDK (firecrawl-py) for headless JS-rendered extraction.

Pipeline:
  1. Map  — discover all /resources/* slugs via firecrawl.map()
  2. Filter — keep only pages modified in the last FRESHNESS_HOURS
             (read from JSON-LD dateModified embedded in each page)
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
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
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

FRESHNESS_HOURS = 48        # only scrape pages modified within this window
MAX_URLS_PER_BATCH = 20     # Firecrawl batch_scrape limit per call
INTER_BATCH_DELAY = 3.0     # seconds between batch calls

# Sites to cover — add more deal hubs here as needed
TARGET_SITES: list[str] = [
    "https://resourify.com",
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

# JSON-LD dateModified extractor
_DATE_MODIFIED_RE = re.compile(
    r'"dateModified"\s*:\s*"([^"]+)"', re.IGNORECASE
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
def _map_site(app, base_url: str, category_filter: str | None) -> list[str]:
    """Return all /resources/* URLs from the site map."""
    log.info("Mapping %s ...", base_url)
    try:
        result = app.map_url(base_url, include_subdomains=False)
        all_urls: list[str] = result.links or []
    except Exception as exc:
        log.warning("firecrawl.map failed for %s: %s", base_url, exc)
        return []

    resource_urls = [u for u in all_urls if "/resources/" in u]
    log.info("  %d resource URLs discovered", len(resource_urls))
    return resource_urls


# --------------------------------------------------------------------------- #
# Step 2 — Freshness filter: skip pages not updated recently
# --------------------------------------------------------------------------- #
async def _filter_fresh(
    urls: list[str],
    freshness_hours: int,
) -> list[str]:
    """Keep only URLs whose JSON-LD dateModified is within freshness_hours."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=freshness_hours)
    fresh: list[str] = []

    async with httpx.AsyncClient(
        headers={"User-Agent": "Mozilla/5.0 (compatible; FreebiesBot/1.0)"},
        follow_redirects=True,
        timeout=10.0,
    ) as client:
        for url in urls:
            try:
                resp = await client.get(url)
                m = _DATE_MODIFIED_RE.search(resp.text)
                if m:
                    dt = datetime.fromisoformat(m.group(1).replace("Z", "+00:00"))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    if dt >= cutoff:
                        fresh.append(url)
                else:
                    # No dateModified — include to be safe
                    fresh.append(url)
            except Exception:
                fresh.append(url)  # include on error, let Firecrawl handle it
            await asyncio.sleep(0.3)

    log.info("Freshness filter: %d → %d URLs (cutoff: %dh)", len(urls), len(fresh), freshness_hours)
    return fresh


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


def _result_to_payload(scraped_url: str, extract: dict) -> dict | None:
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
        "author_handle": "resourify.com",
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
    sites: list[str] | None = None,
    category_filter: str | None = None,
    freshness_hours: int = FRESHNESS_HOURS,
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

    for base_url in sites:
        source_name = f"firecrawl:{base_url.rstrip('/').split('/')[-1]}"
        # Resolve source_id up front so a failed map/scrape is still recorded on the source.
        source_id = None if dry_run else _ensure_source(source_name)

        # 1) Map
        all_urls = _map_site(app, base_url, category_filter)
        if not all_urls:
            _health(source_id, ok=False, error=f"map returned no URLs: {base_url}")
            continue

        if limit:
            all_urls = all_urls[:limit]

        # 2) Freshness filter
        fresh_urls = await _filter_fresh(all_urls, freshness_hours)
        if not fresh_urls:
            log.info("No fresh URLs for %s (all older than %dh)", base_url, freshness_hours)
            _health(source_id, ok=True)  # site reached fine, just nothing fresh
            continue

        # 3) Batch scrape
        log.info("Scraping %d fresh URLs from %s", len(fresh_urls), base_url)
        raw_results = _batch_scrape(app, fresh_urls)

        # 4) Write
        for item in raw_results:
            scraped_url = item.get("metadata", {}).get("sourceURL", "") or item.get("url", "")
            extract = item.get("extract") or {}
            if not extract:
                continue

            payload = _result_to_payload(scraped_url, extract)
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
    parser.add_argument("--freshness", type=int, default=FRESHNESS_HOURS,
                        help=f"hours lookback window (default {FRESHNESS_HOURS})")
    args = parser.parse_args()

    asyncio.run(run_firecrawl(
        category_filter=args.category,
        freshness_hours=args.freshness,
        dry_run=args.dry_run,
        limit=args.limit,
    ))


if __name__ == "__main__":
    main()
