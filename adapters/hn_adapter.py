"""
Hacker News Algolia adapter — free, unauthenticated, structured JSON
====================================================================
Uses the official Algolia search endpoint for HN:
  https://hn.algolia.com/api/v1/search?query=...&tags=story&numericFilters=...

No account, no session, no scraping — pure JSON API.

Setup: register source rows (see SQL at the bottom of this file).
Rate limit: Algolia asks for ≤ 10 000 req/day — our scheduled 3×/day
runs hit it ~12–15 times per run, well under.

Usage:
    python -m adapters.hn_adapter              # all configured sources
    python -m adapters.hn_adapter --dry-run    # print payloads, no DB write
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from datetime import datetime, timezone, timedelta

import httpx

from crawler.db import connect, record_source_health, upsert_raw_item

log = logging.getLogger("adapter.hn")


def _health(source_id: int | None, ok: bool, error: str | None = None) -> None:
    """Record a source-health snapshot; no-op when source_id is unknown (dry-run)."""
    if source_id is None:
        return
    with connect() as conn:
        record_source_health(conn, source_id, ok=ok, error=error)

ALGOLIA_URL = "https://hn.algolia.com/api/v1/search"
# Pull stories from the last N days (avoids re-processing old posts on first run).
LOOKBACK_DAYS = 3
# Stories with fewer points than this are usually noise.
MIN_POINTS = 5
PAGE_SIZE = 30  # Algolia default max is 50; 30 is plenty per query per run

# High-signal search terms for developer freebies.
DEFAULT_TERMS: list[str] = [
    "free tier developer",
    "free cloud credits",
    "free API key",
    "student developer pack",
    "free hosting",
    "open source LLM free",
    "Cursor free",
    "Lovable free",
    "Manus free credits",
    "free AI coding",
    "GitHub student pack",
    "free VPS",
    "free domain developer",
    "AWS free tier",
    "GCP free credits",
    "Azure free credits",
]


async def _search_term(
    client: httpx.AsyncClient,
    term: str,
    lookback_days: int,
) -> list[dict] | None:
    cutoff = int((datetime.now(timezone.utc) - timedelta(days=lookback_days)).timestamp())
    params = {
        "query": term,
        "tags": "story",
        "numericFilters": f"created_at_i>{cutoff},points>{MIN_POINTS}",
        "hitsPerPage": PAGE_SIZE,
        "restrictSearchableAttributes": "title,url,story_text",
    }
    try:
        resp = await client.get(ALGOLIA_URL, params=params, timeout=15.0)
        resp.raise_for_status()
        return resp.json().get("hits", [])
    except httpx.HTTPError as exc:
        log.warning("HN Algolia error for %r: %s", term, exc)
        return None


def _hit_to_payload(hit: dict, term: str) -> dict:
    """Normalize an Algolia HN hit to the standard raw_item payload shape."""
    url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit['objectID']}"
    return {
        "external_id": hit["objectID"],
        "text": f"{hit.get('title', '')} — {hit.get('story_text') or ''}".strip(" —"),
        "urls": [url] if url else [],
        "author_handle": hit.get("author", ""),
        "published_at": hit.get("created_at"),
        "engagement": {
            "points": hit.get("points", 0),
            "num_comments": hit.get("num_comments", 0),
        },
        "extra": {
            "hn_id": hit["objectID"],
            "search_term": term,
            "hn_url": f"https://news.ycombinator.com/item?id={hit['objectID']}",
        },
    }


def _get_source_id(conn, source_name: str) -> int | None:
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM sources WHERE name = %s", (source_name,))
        row = cur.fetchone()
    return row["id"] if row else None


async def run_hn(
    terms: list[str] | None = None,
    source_name: str = "hn:algolia",
    dry_run: bool = False,
    lookback_days: int = LOOKBACK_DAYS,
) -> int:
    terms = terms or DEFAULT_TERMS

    # Resolve the source id up front (never in dry-run) so a failed fetch is
    # still recorded against the source.
    source_id: int | None = None

    # Ensure the source row exists.
    if not dry_run:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO sources (name, kind, config)
                    VALUES (%s, 'web',
                            %s::jsonb)
                    ON CONFLICT (name) DO NOTHING
                    """,
                    (
                        source_name,
                        json.dumps({
                            "terms": terms,
                            "lookback_days": lookback_days,
                            "adapter": "hn_adapter",
                        }),
                    ),
                )
            conn.commit()
            source_id = _get_source_id(conn, source_name)

    seen_ids: set[str] = set()  # dedup within this run across terms
    total_written = 0

    if not dry_run and source_id is None:
        log.error("source %r not found in DB", source_name)
        return total_written

    async with httpx.AsyncClient(
        headers={"User-Agent": "freebies-hn-adapter/1.0 (dev research tool)"},
        follow_redirects=True,
    ) as client:
        for term in terms:
            hits = await _search_term(client, term, lookback_days)
            if hits is None:
                _health(source_id, ok=False, error=f"fetch failed for term {term!r}")
                continue
            log.info("HN term %r -> %d hits", term, len(hits))
            for hit in hits:
                oid = hit.get("objectID")
                if not oid or oid in seen_ids:
                    continue
                seen_ids.add(oid)

                payload = _hit_to_payload(hit, term)
                if dry_run:
                    print(json.dumps(payload, ensure_ascii=False, indent=2))
                    total_written += 1
                    continue

                with connect() as conn:
                    upsert_raw_item(conn, source_id, oid, payload)
                    total_written += 1

            await asyncio.sleep(0.5)  # gentle pacing between term queries

    # One health snapshot per source at the successful end of processing.
    _health(source_id, ok=True)

    log.info("HN adapter done: %d items %s", total_written,
             "would be written (dry-run)" if dry_run else "written")
    return total_written


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(description="Hacker News Algolia adapter")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--lookback-days", type=int, default=LOOKBACK_DAYS)
    parser.add_argument("--source-name", default="hn:algolia")
    args = parser.parse_args()

    asyncio.run(
        run_hn(
            dry_run=args.dry_run,
            lookback_days=args.lookback_days,
            source_name=args.source_name,
        )
    )


if __name__ == "__main__":
    main()

# ─── Source registration SQL ──────────────────────────────────────────────────
# INSERT INTO sources (name, kind, config) VALUES
#   ('hn:algolia', 'web', '{"adapter":"hn_adapter","lookback_days":3}');
