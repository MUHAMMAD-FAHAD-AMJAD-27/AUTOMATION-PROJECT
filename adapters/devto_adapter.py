"""
dev.to adapter — public REST API, no auth, structured JSON
==========================================================
dev.to (Forem) exposes a fully public articles API:
  https://dev.to/api/articles?tag=<tag>&per_page=<n>&top=<days>

No account, no key, no scraping — pure JSON. We poll a handful of
high-signal tags (opensource, devtools, ai, productivity) for recent
top articles, keep the ones that read like freebies/OSS launches, and
feed them to upsert_raw_item() for LLM verification.

Rate limit: dev.to allows generous unauthenticated polling; our 3×/day
scheduled runs hit it a handful of times per run, well within limits.

Usage:
    python -m adapters.devto_adapter              # all default tags
    python -m adapters.devto_adapter --dry-run    # print payloads, no DB write
    python -m adapters.devto_adapter --tag opensource
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging

import httpx

from crawler.db import connect, record_source_health, upsert_raw_item

log = logging.getLogger("adapter.devto")


def _health(source_id: int | None, ok: bool, error: str | None = None) -> None:
    """Record a source-health snapshot; no-op when source_id is unknown (dry-run)."""
    if source_id is None:
        return
    with connect() as conn:
        record_source_health(conn, source_id, ok=ok, error=error)


ARTICLES_URL = "https://dev.to/api/articles"
SOURCE_NAME = "devto:articles"
PER_PAGE = 30            # articles per tag per run
TOP_DAYS = 7            # most-reacted articles from the last N days
MIN_REACTIONS = 5       # skip near-zero-engagement noise

# High-signal dev.to tags for developer freebies / OSS launches.
DEFAULT_TAGS: list[str] = [
    "opensource",
    "devtools",
    "ai",
    "productivity",
    "webdev",
]

# Tags whose articles are relevant by default (no keyword filter needed).
_ALWAYS_RELEVANT_TAGS = frozenset({"opensource", "devtools"})

# Keywords that mark a broad-tag article as freebie/deal relevant.
SIGNAL_KEYWORDS = (
    "free", "open source", "open-source", "self-host", "self host",
    "credits", "free tier", "no credit card", "promo", "coupon", "discount",
    "lifetime", "student", "launch", "release", "alternative to", "api key",
)


def _is_relevant(article: dict, tag: str) -> bool:
    """Keep OSS/devtools articles by default; gate broad tags on freebie keywords."""
    if tag in _ALWAYS_RELEVANT_TAGS:
        return True
    haystack = " ".join([
        article.get("title") or "",
        article.get("description") or "",
        " ".join(article.get("tag_list") or []),
    ]).lower()
    return any(kw in haystack for kw in SIGNAL_KEYWORDS)


def _article_to_payload(article: dict, tag: str) -> dict:
    """Normalize a dev.to article to the standard raw_item payload shape."""
    title = article.get("title") or ""
    description = article.get("description") or ""
    url = article.get("url") or article.get("canonical_url") or ""
    user = article.get("user") or {}
    return {
        "external_id": f"devto:{article.get('id')}",
        "text": f"{title}\n{description}".strip(),
        "urls": [url] if url else [],
        "author_handle": f"devto:{user.get('username', 'unknown')}",
        "published_at": article.get("published_at"),
        "engagement": {
            "reactions": article.get("positive_reactions_count", 0),
            "comments": article.get("comments_count", 0),
        },
        "extra": {
            "tags": article.get("tag_list") or [],
            "query_tag": tag,
            "reading_time_minutes": article.get("reading_time_minutes"),
            "adapter": "devto_adapter",
        },
    }


def _ensure_source() -> int:
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
                    SOURCE_NAME,
                    json.dumps({
                        "url": ARTICLES_URL,
                        "tags": DEFAULT_TAGS,
                        "adapter": "devto_adapter",
                    }),
                ),
            )
            row = cur.fetchone()
            if row:
                conn.commit()
                return row["id"]
            cur.execute("SELECT id FROM sources WHERE name = %s", (SOURCE_NAME,))
            return cur.fetchone()["id"]


async def _fetch_tag(
    client: httpx.AsyncClient,
    tag: str,
    top_days: int,
) -> list[dict] | None:
    """Fetch top recent articles for one tag.

    Returns the article list, or None on a genuine fetch failure (non-200 /
    HTTP error) so the caller records accurate source health instead of masking
    a broken endpoint as healthy-but-empty. [] means fetched fine, no articles."""
    params = {"tag": tag, "per_page": PER_PAGE, "top": top_days}
    try:
        resp = await client.get(ARTICLES_URL, params=params, timeout=20.0)
        if resp.status_code != 200:
            log.warning("dev.to tag %r returned HTTP %d", tag, resp.status_code)
            return None
        data = resp.json()
    except (httpx.HTTPError, json.JSONDecodeError) as exc:
        log.warning("dev.to tag %r fetch error: %s", tag, exc)
        return None
    return data if isinstance(data, list) else []


async def run_devto(
    tags: list[str] | None = None,
    dry_run: bool = False,
    top_days: int = TOP_DAYS,
    max_items: int | None = None,
) -> int:
    """Ingest recent top dev.to articles for the configured tags.

    ``max_items`` caps total raw_items written across all tags in one run
    (None = uncapped, for manual CLI). The scheduler passes a cap so ingestion
    can't outrun the pipeline's per-slot drain rate."""
    tags = tags or DEFAULT_TAGS
    source_id = None if dry_run else _ensure_source()
    seen_ids: set[str] = set()  # dedup within this run across tags
    total = 0

    async with httpx.AsyncClient(
        headers={"User-Agent": "freebies-devto-adapter/1.0 (dev research tool)"},
        follow_redirects=True,
    ) as client:
        for tag in tags:
            if max_items is not None and total >= max_items:
                log.info("dev.to hit max_items=%d — stopping", max_items)
                break
            articles = await _fetch_tag(client, tag, top_days)
            if articles is None:
                _health(source_id, ok=False, error=f"fetch failed for tag {tag!r}")
                continue
            log.info("dev.to tag %r -> %d articles", tag, len(articles))

            for article in articles:
                if max_items is not None and total >= max_items:
                    break
                aid = str(article.get("id") or "")
                if not aid or aid in seen_ids:
                    continue
                if (article.get("positive_reactions_count") or 0) < MIN_REACTIONS:
                    continue
                if not _is_relevant(article, tag):
                    continue
                seen_ids.add(aid)

                payload = _article_to_payload(article, tag)
                if dry_run:
                    print(json.dumps(payload, ensure_ascii=False, indent=2))
                    total += 1
                    continue

                with connect() as conn:
                    upsert_raw_item(conn, source_id, payload["external_id"], payload)
                total += 1

            await asyncio.sleep(1.0)  # gentle pacing between tag queries

    _health(source_id, ok=True)
    log.info("dev.to adapter done: %d items written", total)
    return total


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(description="dev.to public REST API adapter")
    parser.add_argument("--dry-run", action="store_true", help="print payloads, no DB write")
    parser.add_argument("--tag", default=None, help="single dev.to tag to run")
    parser.add_argument("--top-days", type=int, default=TOP_DAYS,
                        help="most-reacted articles from the last N days")
    parser.add_argument("--max-items", type=int, default=None,
                        help="cap total raw_items written this run (default: uncapped)")
    args = parser.parse_args()

    tags = [args.tag] if args.tag else None
    asyncio.run(run_devto(
        tags=tags,
        dry_run=args.dry_run,
        top_days=args.top_days,
        max_items=args.max_items,
    ))


if __name__ == "__main__":
    main()

# ─── Source registration SQL (adapter auto-creates on first run) ──────────────
# INSERT INTO sources (name, kind, config) VALUES
#   ('devto:articles', 'web',
#    '{"url":"https://dev.to/api/articles","adapter":"devto_adapter"}');
