"""
GitHub Trending discovery adapter — Search API (Item 4, part a, 2026-08-29)
===========================================================================
Surfaces genuinely NEW, fast-rising repositories — the "cool repo of the day"
content class (self-hosted tools, AI/LLM projects, dev tools) that the curated
mega-list adapter (github_adapter.py) structurally cannot see: that adapter only
harvests the *external* links inside "awesome-free" READMEs and explicitly drops
github.com repo links.

GitHub has no official Trending API. The reliable, ToS-clean substitute is the
**Search API** sorted by stars over a recent creation window:

    GET /search/repositories?q=created:>=<since> stars:>=<floor>&sort=stars&order=desc

A repo created in the last CREATED_WITHIN_DAYS that has already crossed a star
floor is, by definition, trending. This is a documented, rate-limited REST
endpoint — no HTML scraping of github.com/trending, no unofficial mirror.

    * Search API rate limit: 10 req/min unauthenticated, 30 req/min with a token.
    * Set GITHUB_TOKEN (a scopeless PAT) to use the higher limit — same token the
      curated github_adapter already reads.

IMPORTANT — depends on the Item-4b verifier lane. Everything this adapter writes
is a "notable repo", NOT a free-tier deal. The current deal-tuned verifier
(heuristic _DEAL_SIGNAL_RE prefilter + is_offer gate) would reject all of it. A
per-item ``extra.is_repo`` marker is stamped so the future non-deal verifier lane
can route these without a keyword. Do NOT deploy this adapter's scheduler wiring
until that lane lands, or every trending repo is dead-lettered as no_deal_signal.

Usage:
    python -m adapters.github_trending_adapter
    python -m adapters.github_trending_adapter --dry-run
    python -m adapters.github_trending_adapter --min-stars 100 --within-days 14
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from datetime import date, timedelta

import httpx

from crawler.db import connect, record_source_health, upsert_raw_item

log = logging.getLogger("adapter.github_trending")

GITHUB_API = "https://api.github.com"
SOURCE_NAME = "github:trending"

# Defaults — deliberately conservative so a scheduled run stays a small,
# bounded ingest (the scheduler gates it to once/day, OSS lane, run 1).
MIN_STARS = 50               # star floor: filters noise, keeps genuinely-rising repos
CREATED_WITHIN_DAYS = 30     # "new" window; a popular repo this young is trending
MAX_REPOS_PER_RUN = 25       # hard cap on raw_items written per run (across all queries)
PER_PAGE = 50                # Search API page size (max 100)

# Query facets (each gets `created:>=<since> stars:>=<floor>` prepended). The bare
# facet catches trending across ALL topics; the topic facets guarantee niche
# coverage (the WhatsApp sample was dominated by self-hosted + AI/LLM tooling).
QUERY_FACETS: list[str] = [
    "",                       # all-topic trending-new
    "topic:ai",
    "topic:self-hosted",
    "topic:developer-tools",
]


# --------------------------------------------------------------------------- #
# HTTP layer — mirrors github_adapter (optional GITHUB_TOKEN, UA, redirects)
# --------------------------------------------------------------------------- #
def _headers() -> dict[str, str]:
    headers = {
        "User-Agent": "freebies-hunter-trending/1.0",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


async def _search_repos(
    client: httpx.AsyncClient, query: str, per_page: int,
) -> list[dict]:
    """One Search API call. Returns the `items` list (empty on any failure)."""
    params = {"q": query, "sort": "stars", "order": "desc", "per_page": per_page}
    try:
        resp = await client.get(f"{GITHUB_API}/search/repositories", params=params)
        if resp.status_code == 403:
            log.warning("search rate-limited (403) for q=%r", query)
            return []
        resp.raise_for_status()
        return resp.json().get("items", []) or []
    except Exception as exc:
        log.warning("search failed for q=%r: %s", query, exc)
        return []


# --------------------------------------------------------------------------- #
# Payload — NormalizedItem-compatible dict for upsert_raw_item
# --------------------------------------------------------------------------- #
def _repo_to_payload(repo: dict) -> dict | None:
    """Convert a Search API repository object to an upsert_raw_item payload.

    Stamps ``extra.is_repo = True`` — the marker the future non-deal verifier
    lane (Item 4b) uses to route these past the deal-signal prefilter/is_offer
    gate without a keyword. Until that lane lands, these items are ingested but
    will be rejected downstream (see module docstring)."""
    html_url = (repo.get("html_url") or "").strip()
    full_name = (repo.get("full_name") or repo.get("name") or "").strip()
    if not html_url or not full_name:
        return None

    desc = (repo.get("description") or "").strip()
    topics: list[str] = repo.get("topics") or []
    homepage = (repo.get("homepage") or "").strip()
    stars = int(repo.get("stargazers_count") or 0)
    language = repo.get("language")

    urls = [html_url]
    if homepage.startswith("http") and homepage != html_url:
        urls.append(homepage)

    text = f"{full_name} ({stars}★): {desc}".rstrip()
    if topics:
        text += "\nTopics: " + ", ".join(topics)

    owner = full_name.split("/", 1)[0] if "/" in full_name else "github-trending"
    return {
        "external_id": html_url,
        "text": text,
        "urls": urls,
        "author_handle": f"github:{owner}",
        "published_at": repo.get("created_at"),
        "engagement": {
            "stars": stars,
            "forks": int(repo.get("forks_count") or 0),
        },
        "extra": {
            "adapter": "github_trending_adapter",
            "repo": full_name,
            "stars": stars,
            "language": language,
            "topics": topics,
            "created_at": repo.get("created_at"),
            "pushed_at": repo.get("pushed_at"),
            "homepage": homepage or None,
            "is_repo": True,     # future non-deal verifier lane marker (Item 4b)
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
                (SOURCE_NAME, json.dumps({"adapter": "github_trending_adapter"})),
            )
            row = cur.fetchone()
            if row:
                conn.commit()
                return row["id"]
            cur.execute("SELECT id FROM sources WHERE name = %s", (SOURCE_NAME,))
            return cur.fetchone()["id"]


def _health(source_id: int | None, ok: bool, error: str | None = None) -> None:
    """Record a source-health snapshot; no-op when source_id is unknown (dry-run)."""
    if source_id is None:
        return
    with connect() as conn:
        record_source_health(conn, source_id, ok=ok, error=error)


# --------------------------------------------------------------------------- #
# Query builder
# --------------------------------------------------------------------------- #
def _build_queries(min_stars: int, within_days: int) -> list[str]:
    """`created:>=<since> stars:>=<floor>` + each facet, in QUERY_FACETS order."""
    since = (date.today() - timedelta(days=within_days)).isoformat()
    base = f"created:>={since} stars:>={min_stars}"
    return [f"{base} {facet}".strip() for facet in QUERY_FACETS]


# --------------------------------------------------------------------------- #
# Main runner
# --------------------------------------------------------------------------- #
async def run_github_trending(
    min_stars: int = MIN_STARS,
    within_days: int = CREATED_WITHIN_DAYS,
    max_repos: int = MAX_REPOS_PER_RUN,
    dry_run: bool = False,
) -> int:
    """Discover trending-new repos across QUERY_FACETS, dedup by URL, cap at
    max_repos, and write NormalizedItem payloads. Returns count written."""
    queries = _build_queries(min_stars, within_days)
    per_page = min(PER_PAGE, max_repos)

    source_id = None if dry_run else _ensure_source()
    seen_urls: set[str] = set()
    payloads: list[dict] = []

    async with httpx.AsyncClient(
        headers=_headers(), follow_redirects=True, timeout=30.0
    ) as client:
        for query in queries:
            if len(payloads) >= max_repos:
                break
            items = await _search_repos(client, query, per_page)
            log.info("q=%r → %d repos", query, len(items))
            for repo in items:
                if len(payloads) >= max_repos:
                    break
                payload = _repo_to_payload(repo)
                if not payload or payload["external_id"] in seen_urls:
                    continue
                seen_urls.add(payload["external_id"])
                payloads.append(payload)

    if not payloads:
        _health(source_id, ok=False, error="no repos matched")
        log.info("GitHub Trending: 0 repos discovered")
        return 0

    written = 0
    for payload in payloads:
        if dry_run:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            written += 1
            continue
        with connect() as conn:
            upsert_raw_item(conn, source_id, payload["external_id"], payload)
        written += 1

    _health(source_id, ok=True)
    log.info("GitHub Trending done: %d repos %s", written,
             "would be written (dry-run)" if dry_run else "written")
    return written


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(description="GitHub Trending discovery via Search API")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--min-stars", type=int, default=MIN_STARS,
                        help=f"star floor (default {MIN_STARS})")
    parser.add_argument("--within-days", type=int, default=CREATED_WITHIN_DAYS,
                        help=f"creation window in days (default {CREATED_WITHIN_DAYS})")
    parser.add_argument("--max-repos", type=int, default=MAX_REPOS_PER_RUN,
                        help=f"hard cap on repos written per run (default {MAX_REPOS_PER_RUN})")
    args = parser.parse_args()

    asyncio.run(run_github_trending(
        min_stars=args.min_stars,
        within_days=args.within_days,
        max_repos=args.max_repos,
        dry_run=args.dry_run,
    ))


if __name__ == "__main__":
    main()
