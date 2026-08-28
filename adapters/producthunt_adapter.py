"""
Product Hunt adapter — public RSS feed, no auth
===============================================
Product Hunt publishes its daily launches as a public RSS/Atom feed:
  https://www.producthunt.com/feed

No account, no key. We parse the feed with the stdlib XML parser (zero
extra deps), keep launches that read like developer / free / freemium
tools, and feed them to upsert_raw_item() for LLM verification.

The feed is general-audience, so a lightweight keyword relevance filter
trims consumer-app noise before we pay for a DB write + LLM call.

Usage:
    python -m adapters.producthunt_adapter              # front-page feed
    python -m adapters.producthunt_adapter --dry-run    # print payloads, no DB write
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
from datetime import datetime, timezone
from xml.etree import ElementTree as ET

import httpx

from crawler.db import connect, record_source_health, upsert_raw_item

log = logging.getLogger("adapter.producthunt")


def _health(source_id: int | None, ok: bool, error: str | None = None) -> None:
    """Record a source-health snapshot; no-op when source_id is unknown (dry-run)."""
    if source_id is None:
        return
    with connect() as conn:
        record_source_health(conn, source_id, ok=ok, error=error)


FEED_URL = "https://www.producthunt.com/feed"
SOURCE_NAME = "producthunt:frontpage"
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)

# Keywords that mark a PH launch as developer/free-tool relevant.
SIGNAL_KEYWORDS = (
    "free", "open source", "open-source", "self-host", "self host",
    "api", "sdk", "developer", "dev tool", "devtool", "cli", "framework",
    "no code", "no-code", "credits", "free tier", "freemium", "beta",
    "ai", "llm", "coding", "database", "hosting", "deploy", "automation",
    "lifetime", "student", "boilerplate", "template", "plugin", "library",
)

# XML namespaces seen in the PH feed (Atom + Media RSS).
_ATOM = "{http://www.w3.org/2005/Atom}"


def _parse_feed(xml_text: str) -> list[dict]:
    """Parse the Product Hunt feed (RSS 2.0 or Atom). Returns launch dicts."""
    items: list[dict] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        log.warning("producthunt feed parse error: %s", exc)
        return items

    # RSS 2.0 <item>
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        url = (item.findtext("link") or "").strip()
        summary = (item.findtext("description") or "").strip()[:1000]
        pub = item.findtext("pubDate") or ""
        guid = (item.findtext("guid") or url).strip()
        if url:
            items.append({"title": title, "url": url, "summary": summary,
                          "published_at": pub, "guid": guid})

    # Atom <entry>
    for entry in root.findall(f".//{_ATOM}entry"):
        title = (entry.findtext(f"{_ATOM}title") or "").strip()
        link_el = entry.find(f"{_ATOM}link")
        url = (link_el.get("href") if link_el is not None else "") or ""
        summary = (
            entry.findtext(f"{_ATOM}summary")
            or entry.findtext(f"{_ATOM}content")
            or ""
        ).strip()[:1000]
        pub = entry.findtext(f"{_ATOM}updated") or entry.findtext(f"{_ATOM}published") or ""
        guid = (entry.findtext(f"{_ATOM}id") or url).strip()
        if url:
            items.append({"title": title, "url": url, "summary": summary,
                          "published_at": pub, "guid": guid})

    return items


def _is_relevant(item: dict) -> bool:
    """Trim consumer-app noise before paying for a DB write + LLM call."""
    haystack = f"{item.get('title', '')} {item.get('summary', '')}".lower()
    return any(kw in haystack for kw in SIGNAL_KEYWORDS)


def _entry_to_payload(item: dict, idx: int) -> dict:
    """Normalize a Product Hunt launch to the standard raw_item payload shape."""
    url = item.get("url") or ""
    guid = item.get("guid") or url
    title = item.get("title") or ""
    summary = item.get("summary") or ""
    return {
        "external_id": f"producthunt:{guid}",
        "text": f"{title}\n{summary}".strip(),
        "urls": [url] if url else [],
        "author_handle": "producthunt.com",
        "published_at": item.get("published_at") or datetime.now(timezone.utc).isoformat(),
        "engagement": {},
        "extra": {
            "platform": "producthunt",
            "feed_index": idx,
            "adapter": "producthunt_adapter",
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
                    json.dumps({"url": FEED_URL, "adapter": "producthunt_adapter"}),
                ),
            )
            row = cur.fetchone()
            if row:
                conn.commit()
                return row["id"]
            cur.execute("SELECT id FROM sources WHERE name = %s", (SOURCE_NAME,))
            return cur.fetchone()["id"]


async def _fetch_feed(client: httpx.AsyncClient) -> list[dict] | None:
    """Fetch + parse the Product Hunt feed.

    Returns the launch list, or None on a genuine fetch failure (non-200 / HTTP
    error) so the caller records accurate source health instead of masking a
    broken endpoint as healthy-but-empty. [] means fetched fine, nothing parsed."""
    try:
        resp = await client.get(FEED_URL, timeout=20.0)
        if resp.status_code != 200:
            log.warning("producthunt feed returned HTTP %d", resp.status_code)
            return None
        return _parse_feed(resp.text)
    except httpx.HTTPError as exc:
        log.warning("producthunt feed fetch error: %s", exc)
        return None


async def run_producthunt(
    dry_run: bool = False,
    max_items: int | None = None,
) -> int:
    """Ingest recent Product Hunt launches that read like developer freebies.

    ``max_items`` caps total raw_items written in one run (None = uncapped, for
    manual CLI). The scheduler passes a cap so ingestion can't outrun the
    pipeline's per-slot drain rate."""
    source_id = None if dry_run else _ensure_source()
    seen_ids: set[str] = set()
    total = 0

    async with httpx.AsyncClient(
        headers={"User-Agent": BROWSER_UA},
        follow_redirects=True,
    ) as client:
        launches = await _fetch_feed(client)
        if launches is None:
            _health(source_id, ok=False, error="feed fetch failed")
            return 0
        log.info("producthunt feed -> %d launches", len(launches))

        for idx, item in enumerate(launches):
            if max_items is not None and total >= max_items:
                break
            guid = item.get("guid") or item.get("url") or ""
            if not guid or guid in seen_ids:
                continue
            if not _is_relevant(item):
                continue
            seen_ids.add(guid)

            payload = _entry_to_payload(item, idx)
            if dry_run:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
                total += 1
                continue

            with connect() as conn:
                upsert_raw_item(conn, source_id, payload["external_id"], payload)
            total += 1

    _health(source_id, ok=True)
    log.info("producthunt adapter done: %d items %s", total,
             "would be written (dry-run)" if dry_run else "written")
    return total


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(description="Product Hunt public RSS adapter")
    parser.add_argument("--dry-run", action="store_true", help="print payloads, no DB write")
    parser.add_argument("--max-items", type=int, default=None,
                        help="cap total raw_items written this run (default: uncapped)")
    args = parser.parse_args()

    asyncio.run(run_producthunt(dry_run=args.dry_run, max_items=args.max_items))


if __name__ == "__main__":
    main()

# ─── Source registration SQL (adapter auto-creates on first run) ──────────────
# INSERT INTO sources (name, kind, config) VALUES
#   ('producthunt:frontpage', 'web',
#    '{"url":"https://www.producthunt.com/feed","adapter":"producthunt_adapter"}');
