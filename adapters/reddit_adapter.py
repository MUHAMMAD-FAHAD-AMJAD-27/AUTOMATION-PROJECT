"""
Reddit public JSON adapter — zero auth, pure unauthenticated API
================================================================
Reddit exposes a fully public JSON endpoint for every subreddit:
  https://www.reddit.com/r/<subreddit>/search.json?q=...&sort=new&restrict_sr=1
  https://www.reddit.com/r/<subreddit>/new.json

No account, no OAuth, no scraping. Reddit's public API is rate-limited at
~1 req/sec per IP; we stay well under with gentle inter-request sleeps.

Usage:
    python -m adapters.reddit_adapter              # all configured subreddits
    python -m adapters.reddit_adapter --dry-run
    python -m adapters.reddit_adapter --sub learnprogramming

Source registration SQL at the bottom of this file.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
from datetime import datetime, timezone, timedelta
from urllib.parse import urlencode

import httpx

from crawler.db import connect, record_source_health, upsert_raw_item

log = logging.getLogger("adapter.reddit")


def _health(source_id: int | None, ok: bool, error: str | None = None) -> None:
    """Record a source-health snapshot; no-op when source_id is unknown (dry-run)."""
    if source_id is None:
        return
    with connect() as conn:
        record_source_health(conn, source_id, ok=ok, error=error)

BASE = "https://www.reddit.com"
LOOKBACK_SECONDS = 3 * 24 * 3600  # 3 days
MIN_SCORE = 3  # ignore brand-new zero-upvote posts (noise)
PAGE_LIMIT = 25  # posts per request (Reddit max is 100, but 25 is polite)

# Subreddits + per-sub search queries.
# Format: (subreddit, search_term_or_None)
# None = just pull /new, letting the pipeline filter via LLM.
TARGETS: list[tuple[str, str | None]] = [
    ("learnprogramming",  "free credits OR free tier OR student pack"),
    ("ChatGPTCoding",     "free OR promo OR deal OR credits"),
    ("LocalLLaMA",        "free tier OR API key OR credits"),
    ("webdev",            "free hosting OR free tier OR credits"),
    ("cscareerquestions", "free tools OR student pack OR github education"),
    ("programming",       "free tier OR open source free"),
    ("devops",            "free credits OR free tier"),
    ("freebies",          "developer OR coding OR cloud OR API"),
    ("SideProject",       "free tier OR launch deal OR lifetime"),
    ("MachineLearning",   "free credits OR free compute OR grant"),
]

# Flairs that are almost always high-signal in these subs.
HIGH_SIGNAL_FLAIRS = frozenset({
    "free", "deal", "resource", "freebie", "tools", "promo", "offer",
    "announcement", "project", "show hn",
})

# Keywords that bump a post's relevance even if it passes the score threshold.
SIGNAL_KEYWORDS = (
    "free tier", "free credits", "free api", "student pack", "promo code",
    "coupon", "discount", "free hosting", "free vps", "free domain",
    "free course", "free license", "free trial", "open source", "no credit card",
    "cursor", "lovable", "manus", "replit", "github education", "aws free",
    "gcp free", "azure free", "vercel free", "railway free", "supabase free",
    "netlify free", "cloudflare free", "hetzner", "fly.io free",
)


def _is_relevant(post: dict) -> bool:
    """Quick relevance filter before paying for DB write and LLM call."""
    text = (
        (post.get("title") or "")
        + " "
        + (post.get("selftext") or "")
        + " "
        + (post.get("link_flair_text") or "")
    ).lower()
    if any(kw in text for kw in SIGNAL_KEYWORDS):
        return True
    flair = (post.get("link_flair_text") or "").lower()
    return flair in HIGH_SIGNAL_FLAIRS


def _post_to_payload(post: dict, subreddit: str) -> dict:
    url = post.get("url") or f"https://www.reddit.com{post.get('permalink', '')}"
    body = (post.get("selftext") or "").strip()[:3000]
    text = f"{post.get('title', '')} {body}".strip()
    return {
        "external_id": post["id"],
        "text": text,
        "urls": [url],
        "author_handle": post.get("author", "[deleted]"),
        "published_at": datetime.fromtimestamp(
            post.get("created_utc", 0), tz=timezone.utc
        ).isoformat(),
        "engagement": {
            "score": post.get("score", 0),
            "upvote_ratio": post.get("upvote_ratio"),
            "num_comments": post.get("num_comments", 0),
        },
        "extra": {
            "subreddit": subreddit,
            "flair": post.get("link_flair_text"),
            "reddit_url": f"https://www.reddit.com{post.get('permalink', '')}",
            "is_self": post.get("is_self", False),
        },
    }


async def _fetch_posts(
    client: httpx.AsyncClient,
    subreddit: str,
    query: str | None,
) -> list[dict] | None:
    """Return matching posts, or None on a genuine source failure.

    None (404 / HTTP error) is distinct from [] (throttled or genuinely empty) so
    the caller can record accurate source health instead of treating a broken
    subreddit as a healthy-but-quiet one. A 429 is transient throttling, not source
    breakage, so it returns [] after backoff (same stance as telegram FloodWait)."""
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=LOOKBACK_SECONDS)
    posts: list[dict] = []

    if query:
        url = f"{BASE}/r/{subreddit}/search.json"
        params: dict = {
            "q": query,
            "sort": "new",
            "restrict_sr": "1",
            "limit": PAGE_LIMIT,
            "t": "week",
        }
    else:
        url = f"{BASE}/r/{subreddit}/new.json"
        params = {"limit": PAGE_LIMIT}

    try:
        resp = await client.get(url, params=params, timeout=15.0)
        if resp.status_code == 429:
            retry = float(resp.headers.get("Retry-After", 60))
            log.warning("Reddit 429 on r/%s — backing off %.0fs", subreddit, retry)
            await asyncio.sleep(retry + 2)
            return []
        if resp.status_code == 404:
            log.warning("r/%s not found or private", subreddit)
            return None
        resp.raise_for_status()
        children = resp.json().get("data", {}).get("children", [])
    except httpx.HTTPError as exc:
        log.warning("Reddit HTTP error r/%s: %s", subreddit, exc)
        return None

    for child in children:
        post = child.get("data", {})
        if not post.get("id"):
            continue
        created = datetime.fromtimestamp(post.get("created_utc", 0), tz=timezone.utc)
        if created < cutoff:
            continue
        if post.get("score", 0) < MIN_SCORE:
            continue
        if not _is_relevant(post):
            continue
        posts.append(post)

    return posts


def _ensure_source(source_name: str, subreddit: str, query: str | None) -> int:
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
                    json.dumps({
                        "subreddit": subreddit,
                        "query": query,
                        "adapter": "reddit_adapter",
                    }),
                ),
            )
            row = cur.fetchone()
            if row:
                conn.commit()
                return row["id"]
            cur.execute("SELECT id FROM sources WHERE name = %s", (source_name,))
            return cur.fetchone()["id"]


async def run_reddit(
    targets: list[tuple[str, str | None]] | None = None,
    dry_run: bool = False,
) -> int:
    targets = targets or TARGETS
    total = 0

    async with httpx.AsyncClient(
        headers={
            "User-Agent": "freebies-bot/1.0 (open-source research; not commercial)",
            "Accept": "application/json",
        },
        follow_redirects=True,
    ) as client:
        for subreddit, query in targets:
            source_name = f"reddit:{subreddit}"
            # Resolve source_id up front so a failed fetch is still recorded on the source.
            source_id = None if dry_run else _ensure_source(source_name, subreddit, query)

            posts = await _fetch_posts(client, subreddit, query)
            if posts is None:
                # Genuine fetch failure (404/HTTP error) — record it, don't mask as healthy.
                _health(source_id, ok=False, error=f"fetch failed for r/{subreddit}")
                continue
            log.info("r/%s (q=%r) -> %d relevant posts", subreddit, query, len(posts))

            try:
                for post in posts:
                    payload = _post_to_payload(post, subreddit)
                    if dry_run:
                        print(json.dumps(payload, ensure_ascii=False, indent=2))
                        total += 1
                        continue

                    with connect() as conn:
                        upsert_raw_item(conn, source_id, post["id"], payload)
                    total += 1
            except Exception as exc:  # noqa: BLE001 — record & move to next sub
                log.exception("ingest failed for r/%s", subreddit)
                _health(source_id, ok=False, error=f"ingest failed: {exc}")
                continue

            _health(source_id, ok=True)
            # Reddit asks for a courtesy delay between requests.
            await asyncio.sleep(2.0)

    log.info("Reddit adapter done: %d items", total)
    return total


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(description="Reddit public JSON adapter")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--sub", default=None, help="single subreddit to run")
    args = parser.parse_args()

    targets = TARGETS
    if args.sub:
        targets = [(t, q) for t, q in TARGETS if t == args.sub]
        if not targets:
            targets = [(args.sub, None)]

    asyncio.run(run_reddit(targets=targets, dry_run=args.dry_run))


if __name__ == "__main__":
    main()

# ─── Source registration SQL (run once; adapter auto-creates on first run) ────
# INSERT INTO sources (name, kind, config) VALUES
#   ('reddit:learnprogramming', 'web', '{"subreddit":"learnprogramming","adapter":"reddit_adapter"}'),
#   ('reddit:ChatGPTCoding',    'web', '{"subreddit":"ChatGPTCoding","adapter":"reddit_adapter"}'),
#   ('reddit:LocalLLaMA',       'web', '{"subreddit":"LocalLLaMA","adapter":"reddit_adapter"}'),
#   ('reddit:freebies',         'web', '{"subreddit":"freebies","adapter":"reddit_adapter"}'),
#   ('reddit:SideProject',      'web', '{"subreddit":"SideProject","adapter":"reddit_adapter"}'),
#   ('reddit:MachineLearning',  'web', '{"subreddit":"MachineLearning","adapter":"reddit_adapter"}');
