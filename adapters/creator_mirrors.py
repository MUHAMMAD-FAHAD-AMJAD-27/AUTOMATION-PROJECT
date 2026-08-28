"""
Creator bio-link mirror adapter (Linktree, Bento, Telegram web-view)
=====================================================================
Many tech creators who gate deals behind Instagram comments ALSO publish
those exact same links publicly within minutes on:
  1. Linktree / bio-link pages  — static JSON baked into __NEXT_DATA__
  2. Bento.me profiles          — public JSON API
  3. Telegram public channels   — t.me/s/<channel> (no login, pure HTML)

This adapter reads those public pages, extracts outbound deal links, and
writes them to raw_items.  No browser needed for Linktree/Bento (pure
httpx); Telegram web-view is also plain HTML over httpx.

Config-driven: creator profiles are stored in the sources.config JSONB
column so you can add/remove without touching code.

Usage:
    python -m adapters.creator_mirrors              # all DB-configured sources
    python -m adapters.creator_mirrors --dry-run
    python -m adapters.creator_mirrors --seed       # insert the default source rows

Source registration SQL and a starter creator list at the bottom.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree as ET

import httpx

from crawler.categories import is_valid_category
from crawler.db import connect, record_source_health, upsert_raw_item

log = logging.getLogger("adapter.creator_mirrors")


def _health(source_id: int | None, ok: bool, error: str | None = None) -> None:
    """Record a source-health snapshot; no-op when source_id is unknown (dry-run)."""
    if source_id is None:
        return
    with connect() as conn:
        record_source_health(conn, source_id, ok=ok, error=error)

# Category hints this adapter can emit (advisory pre-labels; the LLM makes the
# final call). Listed explicitly so an import-time guard catches any typo or a
# taxonomy rename that leaves these literals stale. See crawler/categories.py.
_SIGNAL_CATEGORIES: tuple[str, ...] = (
    "llm_api_drop", "saas_deal", "coupon", "open_source_repo", "student_pack",
)
_bad_signal = [c for c in _SIGNAL_CATEGORIES if not is_valid_category(c)]
assert not _bad_signal, f"creator_mirrors emits unknown categories: {_bad_signal}"

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)

# Domains we never want to emit as "deal links" (navigation noise).
_NOISE_DOMAINS = frozenset({
    "linktree.me", "linktr.ee", "bento.me", "t.me", "instagram.com",
    "twitter.com", "x.com", "tiktok.com", "youtube.com", "facebook.com",
    "linkedin.com", "beacons.ai", "bio.link", "taplink.cc",
    "fonts.googleapis.com", "schema.org", "ogp.me",
})

# Keywords that suggest a link is a deal/resource rather than social profile.
_DEAL_HINTS = (
    "free", "credit", "promo", "deal", "discount", "coupon", "pack",
    "student", "tier", "trial", "offer", "resource", "course", "api",
    "tool", "kit", "grant", "access", "launch",
)

# --------------------------------------------------------------------------- #
# Signal classifiers — regex patterns that tag the *type* of deal
# --------------------------------------------------------------------------- #

# LLM API drop: base_url + api_key combos dropped publicly by creators
_LLM_API_DROP_RE = re.compile(
    r"""
    (?:
        base[\s_-]?url\s*[=:]\s*https?://\S+   # base URL = https://...
      | api[\s_-]?key\s*[=:]\s*[A-Za-z0-9_\-]{20,}  # API Key = sk-...
      | (?:venice|opencode|openrouter|together|groq|fireworks|deepseek)
        [\s.-]ai\b                               # platform name mention
      | deepseek[\s.-]?v\d                       # DeepSeek v3/v4
      | qwen[\s.-]?\d                            # Qwen 3.5/3.8
      | free\s+(?:api|llm|model)\s+(?:key|access|credits?)
    )
    """,
    re.VERBOSE | re.IGNORECASE,
)

# SaaS promo codes
_PROMO_CODE_RE = re.compile(
    r"""
    (?:
        promo\s*(?:code|:)\s*[A-Z0-9_\-]{3,}  # Promo code: SAVE20
      | coupon\s*(?:code|:)\s*[A-Z0-9_\-]{3,}
      | use\s+(?:code|coupon)\s+[A-Z0-9_\-]{3,}
      | \d+\s*%\s+off\s+(?:with|code)          # 50% off with code
      | 1\s*year\s+free                         # 1 Year Free
      | heygen\s+trick                          # HeyGen trick
      | lifetime\s+(?:deal|access|free)
      | appsumo\b
    )
    """,
    re.VERBOSE | re.IGNORECASE,
)

# GitHub open-source repo link in post body
_GITHUB_REPO_RE = re.compile(
    r"https?://github\.com/(?!(?:login|signup|explore|topics|trending|features|marketplace|pricing))([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)"
)

# Geo / student deal signals
_GEO_STUDENT_RE = re.compile(
    r"""
    (?:
        \.edu\.pk\b                              # Pakistani .edu
      | \.edu\.bd\b                              # Bangladeshi .edu
      | \bNY\s+IP\b                              # New York IP trick
      | free\s+google\s+ai\s+pro                # Free Google AI Pro
      | gemini\s+(?:pro\s+)?free                # Gemini free/pro
      | google\s+one\s+ai                        # Google One AI
      | github\s+student                         # GitHub Student
      | github\s+education
      | jetbrains\s+student
      | figma\s+(?:for\s+)?education
      | student\s+(?:developer\s+)?pack
    )
    """,
    re.VERBOSE | re.IGNORECASE,
)

# --------------------------------------------------------------------------- #
# New signal patterns — intercept primary-source drops before influencers do
# --------------------------------------------------------------------------- #

# New AI API services and free credit drops (matches WhatsApp dump patterns)
_API_DROP_FRESH_RE = re.compile(
    r"""
    (?:
        free\s+api\s+credits?\s*(?:no\s+credit\s+card|without\s+card)?
      | \$\d+\s+in\s+free\s+(?:credits?|tokens?)
      | register\s+(?:and\s+get|to\s+get)\s+free
      | openai[\s-]compatible\s+api.*free
      | no\s+credit\s+card\s+(?:required|needed)
      | (?:free|unlimited)\s+(?:tokens?|requests?)\s+(?:per\s+)?day
      | daily\s+reset.*free
    )
    """,
    re.VERBOSE | re.IGNORECASE,
)

# Exact promo/invite codes in the text body
_PROMO_CODE_EXTRACT_RE = re.compile(
    r"""
    (?:
        (?:promo|coupon|invite|discount)\s+(?:code\s*)?[:\-]\s*([A-Z0-9_\-]{4,25})
      | use\s+(?:code|coupon)\s+([A-Z0-9_\-]{4,25})
      | code\s*:\s*([A-Z][A-Z0-9_\-]{3,24})
    )
    """,
    re.VERBOSE | re.IGNORECASE,
)

# Lifetime / 100%-off SaaS deals
_LIFETIME_DEAL_RE = re.compile(
    r"""
    (?:
        lifetime\s+(?:deal|access|license)
      | 100\s*%\s+off
      | (?:free\s+)?(?:1|one)\s+year\s+(?:free|premium|pro|subscription)
      | appsumo\b
      | saasmantra\b
      | pitchground\b
      | dealmirror\b
    )
    """,
    re.VERBOSE | re.IGNORECASE,
)

# GitHub repo drops and OSS launches that replace paid tools
_OSS_LAUNCH_RE = re.compile(
    r"""
    (?:
        open[\s-]?source\s+alternative\s+to
      | replaces?\s+(?:cursor|copilot|notion|linear|figma|jira|slack)
      | github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\s+
        (?:launched|released|v\d|star|fork)
      | show\s+hn\b
      | (?:just|today)\s+(?:launched|open[\s-]?sourced)
    )
    """,
    re.VERBOSE | re.IGNORECASE,
)


def _classify_signal(text: str) -> str | None:
    """Return the most specific freebie category hint found in `text`, or None."""
    if _LLM_API_DROP_RE.search(text):
        return "llm_api_drop"
    if _API_DROP_FRESH_RE.search(text):
        return "llm_api_drop"
    if _LIFETIME_DEAL_RE.search(text):
        return "saas_deal"
    if _PROMO_CODE_RE.search(text) or _PROMO_CODE_EXTRACT_RE.search(text):
        return "coupon"
    if _OSS_LAUNCH_RE.search(text) or _GITHUB_REPO_RE.search(text):
        return "open_source_repo"
    if _GEO_STUDENT_RE.search(text):
        return "student_pack"
    return None


def _extract_github_repos(text: str) -> list[str]:
    """Pull all github.com/<owner>/<repo> URLs out of text."""
    return [m.group(0) for m in _GITHUB_REPO_RE.finditer(text)]


# --------------------------------------------------------------------------- #
# Linktree  (__NEXT_DATA__ JSON embedded in page HTML)
# --------------------------------------------------------------------------- #
_NEXT_DATA_RE = re.compile(
    r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>([^<]+)</script>',
    re.DOTALL,
)


def _parse_linktree(html: str, handle: str) -> list[dict]:
    """Extract all links from a Linktree page's __NEXT_DATA__ blob."""
    m = _NEXT_DATA_RE.search(html)
    if not m:
        log.debug("linktree:%s — __NEXT_DATA__ not found", handle)
        return []
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return []

    # Path varies by Linktree version; try both known shapes.
    links_raw: list[dict] = []
    try:
        links_raw = (
            data["props"]["pageProps"]["account"]["links"]
        )
    except (KeyError, TypeError):
        pass
    if not links_raw:
        try:
            links_raw = (
                data["props"]["pageProps"]["links"]
            )
        except (KeyError, TypeError):
            pass

    results: list[dict] = []
    for link in links_raw:
        url = link.get("url") or link.get("link") or ""
        title = link.get("title") or link.get("label") or ""
        if not url or not url.startswith("http"):
            continue
        if _is_noise_url(url) and not _has_deal_hint(title + " " + url):
            continue
        results.append({"url": url, "title": title})
    return results


# --------------------------------------------------------------------------- #
# Bento.me  (public JSON endpoint)
# --------------------------------------------------------------------------- #
async def _fetch_bento(client: httpx.AsyncClient, handle: str) -> list[dict]:
    """Bento exposes a clean public API for every profile."""
    try:
        resp = await client.get(
            f"https://api.bento.me/v1/profile/{handle}",
            timeout=15.0,
        )
        if resp.status_code == 404:
            log.debug("bento:%s not found", handle)
            return []
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, json.JSONDecodeError) as exc:
        log.warning("bento:%s error: %s", handle, exc)
        return []

    results: list[dict] = []
    for block in data.get("blocks") or []:
        url = block.get("url") or block.get("link") or ""
        title = block.get("title") or block.get("label") or ""
        if not url or not url.startswith("http"):
            continue
        if _is_noise_url(url) and not _has_deal_hint(title + " " + url):
            continue
        results.append({"url": url, "title": title})
    return results


# --------------------------------------------------------------------------- #
# Telegram public web-view  (t.me/s/<channel>)
# --------------------------------------------------------------------------- #
class _TGHTMLParser(HTMLParser):
    """Minimal parser for t.me/s/<channel> HTML — extracts post text + links."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.posts: list[dict] = []
        self._in_msg = False
        self._current_text: list[str] = []
        self._current_urls: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        attr_dict = dict(attrs)
        cls = attr_dict.get("class") or ""
        if "tgme_widget_message_text" in cls or "tgme_widget_message_wrap" in cls:
            self._in_msg = True
        if tag == "a" and self._in_msg:
            href = attr_dict.get("href", "")
            if href.startswith("http") and not _is_noise_url(href):
                self._current_urls.append(href)

    def handle_endtag(self, tag: str) -> None:
        if tag == "div" and self._in_msg:
            if self._current_text or self._current_urls:
                self.posts.append({
                    "text": " ".join(self._current_text).strip()[:2000],
                    "urls": list(dict.fromkeys(self._current_urls)),
                })
            self._in_msg = False
            self._current_text.clear()
            self._current_urls.clear()

    def handle_data(self, data: str) -> None:
        if self._in_msg and data.strip():
            self._current_text.append(data.strip())


async def _fetch_telegram_webview(
    client: httpx.AsyncClient,
    channel: str,
) -> list[dict]:
    url = f"https://t.me/s/{channel}"
    try:
        resp = await client.get(url, timeout=15.0)
        if resp.status_code in (404, 403):
            log.warning("t.me/s/%s unavailable (%d)", channel, resp.status_code)
            return []
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        log.warning("t.me/s/%s HTTP error: %s", channel, exc)
        return []

    parser = _TGHTMLParser()
    try:
        parser.feed(resp.text)
    except Exception:  # noqa: BLE001
        pass

    # Keep only posts with at least one URL that looks like a deal.
    relevant = [
        p for p in parser.posts
        if p["urls"] or _has_deal_hint(p["text"])
    ]
    return relevant


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _is_noise_url(url: str) -> bool:
    host = urlparse(url).netloc.lower().lstrip("www.")
    return host in _NOISE_DOMAINS


def _has_deal_hint(text: str) -> bool:
    t = text.lower()
    return any(kw in t for kw in _DEAL_HINTS)


def _links_to_payload(
    links: list[dict],
    source_handle: str,
    platform: str,
    idx: int,
) -> dict:
    url = links[0]["url"] if links else ""
    title_parts = [l["title"] for l in links if l.get("title")]
    text = " | ".join(title_parts) or url
    all_urls = [l["url"] for l in links if l.get("url")]
    signal = _classify_signal(text + " " + " ".join(all_urls))
    github_repos = _extract_github_repos(" ".join(all_urls))
    return {
        "external_id": f"{platform}:{source_handle}:{idx}:{url}",
        "text": text,
        "urls": all_urls,
        "author_handle": f"{platform}:{source_handle}",
        "published_at": datetime.now(timezone.utc).isoformat(),
        "engagement": {},
        "extra": {
            "platform": platform,
            "handle": source_handle,
            "signal_category": signal,
            "github_repos": github_repos,
        },
    }


def _tg_post_to_payload(post: dict, channel: str, idx: int) -> dict:
    text = post["text"]
    urls = post["urls"]
    combined = text + " " + " ".join(urls)
    signal = _classify_signal(combined)
    github_repos = _extract_github_repos(combined)
    return {
        "external_id": f"tg_web:{channel}:{idx}",
        "text": text,
        "urls": urls,
        "author_handle": f"tg:{channel}",
        "published_at": datetime.now(timezone.utc).isoformat(),
        "engagement": {},
        "extra": {
            "platform": "telegram_web",
            "channel": channel,
            "signal_category": signal,
            "github_repos": github_repos,
        },
    }


def _get_or_create_source(source_name: str, platform: str, handle: str) -> int:
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
                        "platform": platform,
                        "handle": handle,
                        "adapter": "creator_mirrors",
                    }),
                ),
            )
            row = cur.fetchone()
            if row:
                conn.commit()
                return row["id"]
            cur.execute("SELECT id FROM sources WHERE name = %s", (source_name,))
            return cur.fetchone()["id"]


# --------------------------------------------------------------------------- #
# Main orchestrator
# --------------------------------------------------------------------------- #

# Default creator list — edit this or manage via DB sources.config.
# Format: (platform, handle)
# platform: "linktree" | "bento" | "telegram_web"
#
# NOTE (2026-08-27): linktree and bento entries are DISABLED — verified dead:
#   * linktr.ee/<handle>            → HTTP 403 (Linktree now bot-blocks
#                                     server-side scraping for every handle)
#   * api.bento.me/v1/profile/<h>   → HTTP 530 (origin unreachable for every
#                                     handle)
# They only produced per-run health failures and wasted fetch time. The
# _parse_linktree / _fetch_bento code is retained; re-add the tuples below to
# revive these platforms if/when they stop blocking. Telegram t.me/s/ channels
# remain live and are the sole creator-mirror source for now.
DEFAULT_CREATORS: list[tuple[str, str]] = [
    # --- Linktree profiles (DISABLED 2026-08-27: linktr.ee returns 403) ---
    # ("linktree", "github"),           # github's official linktree (stable)
    # ("linktree", "awsdevelopers"),
    # ("linktree", "googledevs"),
    # --- Bento.me profiles (DISABLED 2026-08-27: api.bento.me returns 530) ---
    # ("bento",    "theo"),             # theo.gg — prominent dev creator
    # ("bento",    "fireship"),
    # --- Public Telegram channels (no login, t.me/s/) ---
    ("telegram_web", "freestuffdev"),
    ("telegram_web", "dev_resources"),
    ("telegram_web", "programmingtools"),
    ("telegram_web", "cloudcredits"),
    ("telegram_web", "aitools_free"),
    # Added 2026-08-28 to close the open_source_repo gap. Both verified public
    # from a US dyno before landing here (t.me is ISP-blocked locally):
    # code_stars -> HTTP 200, 20 posts parsed; opensource_findings -> 200, 25.
    ("telegram_web", "code_stars"),
    ("telegram_web", "opensource_findings"),
]

# --------------------------------------------------------------------------- #
# RSS feed definitions — 7 primary-source feeds, all public, no auth required
# --------------------------------------------------------------------------- #
@dataclass
class RSSFeed:
    name: str
    url: str
    refresh_hours: int          # how often this feed should be polled
    category_hint: str | None   # pre-label if known


RSS_FEEDS: list[RSSFeed] = [
    RSSFeed(
        name="producthunt_frontpage",
        url="https://www.producthunt.com/feed",
        refresh_hours=3,
        category_hint="saas_deal",
    ),
    RSSFeed(
        name="hackernews_frontpage",
        url="https://news.ycombinator.com/rss",
        refresh_hours=2,
        category_hint=None,
    ),
    # DISABLED 2026-08-27: r/freebies is a CONSUMER freebies feed (retail
    # samples / coupons), not developer freebies. Its items match the "free/
    # coupon" prefilter hints and pass through, spending LLM budget on
    # off-target content. Code retained — uncomment to re-enable.
    # RSSFeed(
    #     name="reddit_freebies",
    #     url="https://www.reddit.com/r/freebies/.rss",
    #     refresh_hours=3,
    #     category_hint="coupon",
    # ),
    RSSFeed(
        name="reddit_sideproject",
        url="https://www.reddit.com/r/SideProject/.rss",
        refresh_hours=3,
        category_hint="saas_deal",
    ),
    RSSFeed(
        name="reddit_selfhosted",
        url="https://www.reddit.com/r/selfhosted/.rss",
        refresh_hours=4,
        category_hint="open_source_repo",
    ),
    RSSFeed(
        name="reddit_chatgpt_prompts",
        url="https://www.reddit.com/r/ChatGPTPromptEngineering/.rss",
        refresh_hours=6,
        category_hint="llm_api_drop",
    ),
    RSSFeed(
        name="github_trending_daily",
        url="https://github.com/trending?since=daily",
        refresh_hours=6,
        category_hint="open_source_repo",
    ),
]


# --------------------------------------------------------------------------- #
# RSS / Atom parser  (stdlib xml.etree — zero extra deps)
# --------------------------------------------------------------------------- #
def _parse_rss(xml_text: str) -> list[dict]:
    """Parse RSS 2.0 or Atom feed XML. Returns list of {title, url, summary, published_at}."""
    items: list[dict] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return items

    ns = {"atom": "http://www.w3.org/2005/Atom"}

    # RSS 2.0
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        url = (item.findtext("link") or "").strip()
        summary = (item.findtext("description") or "").strip()[:800]
        pub = item.findtext("pubDate") or ""
        if url and url.startswith("http"):
            items.append({"title": title, "url": url, "summary": summary, "published_at": pub})

    # Atom
    for entry in root.findall(".//atom:entry", ns) or root.findall(".//{http://www.w3.org/2005/Atom}entry"):
        title = (entry.findtext("{http://www.w3.org/2005/Atom}title") or "").strip()
        link_el = entry.find("{http://www.w3.org/2005/Atom}link")
        url = (link_el.get("href") if link_el is not None else "") or ""
        summary = (entry.findtext("{http://www.w3.org/2005/Atom}summary") or "").strip()[:800]
        pub = entry.findtext("{http://www.w3.org/2005/Atom}updated") or ""
        if url and url.startswith("http"):
            items.append({"title": title, "url": url, "summary": summary, "published_at": pub})

    return items


def _parse_github_trending_html(html: str) -> list[dict]:
    """Extract repo cards from GitHub /trending page HTML (no JS needed for SSR).

    GitHub's trending markup wraps each repo's name in
    ``<h2 class="h3 lh-condensed"><a ... href="/owner/repo">``. The anchor now
    carries several ``data-hydro-*`` attributes *before* ``href``, and its inner
    text is svg + owner/repo spans, so we match by the stable ``lh-condensed``
    heading class and read the slug straight from ``href`` (the reliable signal)
    rather than the brittle visible text.
    """
    items: list[dict] = []
    pattern = re.compile(
        r'<h2\s+class="[^"]*\blh-condensed\b[^"]*">\s*<a\b[^>]*\bhref="/([^/"]+/[^/"]+)"',
        re.DOTALL,
    )
    for m in pattern.finditer(html):
        slug = m.group(1).strip()
        url = f"https://github.com/{slug}"
        items.append({"title": slug, "url": url, "summary": "", "published_at": ""})
    return items[:25]  # top 25 is enough


async def _fetch_rss_feed(
    client: httpx.AsyncClient,
    feed: RSSFeed,
) -> list[dict] | None:
    """Fetch and parse one feed. Returns the item list, or None on a genuine fetch
    failure (non-200 / HTTP error) so the caller records accurate source health
    instead of masking a broken feed as a healthy-but-empty one. [] means the feed
    fetched fine but yielded no items."""
    try:
        resp = await client.get(feed.url, timeout=20.0)
        if resp.status_code != 200:
            log.warning("RSS %s returned HTTP %d", feed.name, resp.status_code)
            return None
    except httpx.HTTPError as exc:
        log.warning("RSS %s fetch error: %s", feed.name, exc)
        return None

    ct = resp.headers.get("content-type", "")
    if "html" in ct and "github.com/trending" in feed.url:
        return _parse_github_trending_html(resp.text)
    return _parse_rss(resp.text)


def _rss_item_to_payload(item: dict, feed: RSSFeed, idx: int) -> dict | None:
    """Convert a parsed RSS item to a upsert_raw_item payload."""
    url = item.get("url") or ""
    title = item.get("title") or ""
    summary = item.get("summary") or ""
    combined = f"{title} {summary}"

    # Signal-based category detection overrides feed-level hint
    signal = _classify_signal(combined) or feed.category_hint
    github_repos = _extract_github_repos(combined + " " + url)

    if not url or not url.startswith("http"):
        return None
    if _is_noise_url(url) and not _has_deal_hint(combined):
        return None
    if not _has_deal_hint(combined) and not github_repos and not signal:
        return None  # skip purely informational items

    return {
        "external_id": f"rss:{feed.name}:{idx}:{url[-40:]}",
        "text": f"{title}\n{summary}".strip(),
        "urls": [url],
        "author_handle": f"rss:{feed.name}",
        "published_at": item.get("published_at") or datetime.now(timezone.utc).isoformat(),
        "engagement": {},
        "extra": {
            "platform": "rss",
            "feed_name": feed.name,
            "signal_category": signal,
            "github_repos": github_repos,
            "adapter": "creator_mirrors",
        },
    }


async def run_rss_feeds(
    feeds: list[RSSFeed] | None = None,
    dry_run: bool = False,
    max_items: int | None = None,
) -> int:
    """Fetch and ingest all RSS feeds. Returns count of items written.

    ``max_items`` caps total raw_items written across all feeds in one run
    (None = uncapped, for manual CLI). The scheduler passes a cap so ingestion
    can't outrun the pipeline's per-slot drain rate."""
    feeds = feeds or RSS_FEEDS
    total = 0

    async with httpx.AsyncClient(
        headers={"User-Agent": BROWSER_UA},
        follow_redirects=True,
        timeout=20.0,
    ) as client:
        for idx_feed, feed in enumerate(feeds):
            if max_items is not None and total >= max_items:
                log.info("RSS feeds hit max_items=%d — stopping", max_items)
                break
            # Inter-feed pacing to avoid self-inflicted rate limits. Reddit's
            # public .rss is strict about bursts (back-to-back calls → 429), so
            # give reddit feeds a wider gap. Applied before every fetch except
            # the first — and, unlike the old tail sleep, also after a failed
            # fetch, so a 429 can't immediately trigger the next request.
            if idx_feed > 0:
                await asyncio.sleep(5.0 if "reddit.com" in feed.url else 1.5)
            source_name = f"rss:{feed.name}"
            # Resolve source_id up front so a failed fetch is still recorded on the feed's source.
            source_id = None if dry_run else _get_or_create_source(source_name, "rss", feed.name)

            log.info("RSS feed: %s", feed.name)
            items = await _fetch_rss_feed(client, feed)
            if items is None:
                # Genuine fetch failure (non-200/HTTP error) — record it, don't mask as healthy.
                _health(source_id, ok=False, error=f"fetch failed: {feed.name}")
                continue
            log.info("  %s → %d raw items", feed.name, len(items))

            payloads_written = 0

            try:
                for idx, item in enumerate(items):
                    if max_items is not None and total >= max_items:
                        break
                    payload = _rss_item_to_payload(item, feed, idx)
                    if not payload:
                        continue

                    if dry_run:
                        print(json.dumps(payload, ensure_ascii=False, indent=2))
                        total += 1
                        payloads_written += 1
                        continue

                    with connect() as conn:
                        upsert_raw_item(conn, source_id, payload["external_id"], payload)
                    total += 1
                    payloads_written += 1
            except Exception as exc:  # noqa: BLE001 — record & move to next feed
                log.exception("RSS ingest failed for %s", feed.name)
                _health(source_id, ok=False, error=f"ingest failed: {exc}")
                continue

            _health(source_id, ok=True)
            log.info("  %s → %d items %s", feed.name, payloads_written,
                     "would be written (dry-run)" if dry_run else "ingested")

    return total


async def run_creator_mirrors(
    creators: list[tuple[str, str]] | None = None,
    dry_run: bool = False,
    include_rss: bool = True,
    max_items: int | None = None,
) -> int:
    """``max_items`` caps total raw_items written across creator mirrors AND the
    RSS feeds in one run (None = uncapped, for manual CLI). The scheduler passes a
    cap so ingestion can't outrun the pipeline's per-slot drain rate."""
    creators = creators or DEFAULT_CREATORS
    total = 0

    async with httpx.AsyncClient(
        headers={"User-Agent": BROWSER_UA},
        follow_redirects=True,
        timeout=20.0,
    ) as client:
        for platform, handle in creators:
            if max_items is not None and total >= max_items:
                log.info("creator mirrors hit max_items=%d — stopping", max_items)
                break
            source_name = f"mirror:{platform}:{handle}"
            # Resolve source_id up front so a failed fetch is still recorded on the source.
            source_id = None if dry_run else _get_or_create_source(source_name, platform, handle)
            log.info("Fetching %s:%s", platform, handle)

            payloads: list[dict] = []

            if platform == "linktree":
                try:
                    resp = await client.get(f"https://linktr.ee/{handle}")
                    if resp.status_code == 200:
                        links = _parse_linktree(resp.text, handle)
                        if links:
                            payloads.append(_links_to_payload(links, handle, platform, 0))
                    elif resp.status_code == 404:
                        log.debug("linktree:%s not found", handle)
                except httpx.HTTPError as exc:
                    log.warning("linktree:%s error: %s", handle, exc)
                    _health(source_id, ok=False, error=f"linktree fetch error: {exc}")
                    continue

            elif platform == "bento":
                links = await _fetch_bento(client, handle)
                if links:
                    payloads.append(_links_to_payload(links, handle, platform, 0))

            elif platform == "telegram_web":
                posts = await _fetch_telegram_webview(client, handle)
                log.info("  t.me/s/%s -> %d relevant posts", handle, len(posts))
                for idx, post in enumerate(posts):
                    if not post["urls"] and not _has_deal_hint(post["text"]):
                        continue
                    payloads.append(_tg_post_to_payload(post, handle, idx))

            log.info("  %s:%s -> %d payload(s)", platform, handle, len(payloads))

            try:
                for i, payload in enumerate(payloads):
                    if dry_run:
                        print(json.dumps(payload, ensure_ascii=False, indent=2))
                        total += 1
                        continue
                    ext_id = payload.get("external_id") or f"{platform}:{handle}:{i}"
                    with connect() as conn:
                        upsert_raw_item(conn, source_id, ext_id, payload)
                    total += 1
            except Exception as exc:  # noqa: BLE001 — record & move to next creator
                log.exception("ingest failed for %s:%s", platform, handle)
                _health(source_id, ok=False, error=f"ingest failed: {exc}")
                continue

            _health(source_id, ok=True)
            await asyncio.sleep(2.5)  # polite inter-creator pacing

    # RSS feeds — primary source intercept
    if include_rss:
        rss_budget = None if max_items is None else max(0, max_items - total)
        if rss_budget == 0:
            log.info("max_items reached before RSS pass — skipping RSS feeds")
        else:
            log.info("Running RSS feed ingestion (%d feeds)...", len(RSS_FEEDS))
            rss_total = await run_rss_feeds(dry_run=dry_run, max_items=rss_budget)
            total += rss_total

    log.info("Creator mirrors adapter done: %d items", total)
    return total


def _seed_sources() -> None:
    """Insert default source rows for all DEFAULT_CREATORS."""
    with connect() as conn:
        with conn.cursor() as cur:
            for platform, handle in DEFAULT_CREATORS:
                cur.execute(
                    """
                    INSERT INTO sources (name, kind, config)
                    VALUES (%s, 'web', %s::jsonb)
                    ON CONFLICT (name) DO NOTHING
                    """,
                    (
                        f"mirror:{platform}:{handle}",
                        json.dumps({
                            "platform": platform,
                            "handle": handle,
                            "adapter": "creator_mirrors",
                        }),
                    ),
                )
        conn.commit()
    print(f"Seeded {len(DEFAULT_CREATORS)} creator mirror source rows.")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(description="Creator bio-link mirror adapter")
    parser.add_argument("--dry-run", action="store_true", help="print payloads, no DB write")
    parser.add_argument("--seed", action="store_true", help="insert default source rows and exit")
    parser.add_argument("--no-rss", action="store_true", help="skip RSS feed ingestion")
    parser.add_argument("--feeds-only", action="store_true", help="run only RSS feeds, skip creator mirrors")
    args = parser.parse_args()

    if args.seed:
        _seed_sources()
        return

    if args.feeds_only:
        asyncio.run(run_rss_feeds(dry_run=args.dry_run))
        return

    asyncio.run(run_creator_mirrors(dry_run=args.dry_run, include_rss=not args.no_rss))


if __name__ == "__main__":
    main()
