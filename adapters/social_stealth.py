"""
Stealth social adapter scaffold — Twitter/X, Instagram (Patchright) — Phase 2
=============================================================================

Architecture
------------
* One browser *identity* per social account. An identity = a persistent
  Playwright storage_state (cookies + localStorage) + fixed fingerprint
  (viewport, locale, timezone, UA) + sticky residential proxy IP.
  Identities are created ONCE by a human (log in by hand in headed mode),
  then reused headlessly. Never automate login.
* Leaves no trace of automation: no CDP leak signals (patchright patches
  these), humanized scrolling/delays, no parallel tabs per identity.
* Captures the JSON buried in XHR graphql responses rather than scraping
  rendered DOM (far more stable) — see `_on_response`.

Hard truths (read before building):
- Instagram Platform policies prohibit scraping without permission.
  Keep to public profile pages of a small curated list, very low volume.
- X requires login for most views now; expect breakage whenever they change
  their web client. Budget maintenance time or accept source churn.
- Facebook: effectively closed to scraping; do NOT stealth-scrape it.
  Use a human-curated mirror/topic list instead (see INGESTION_SPECS.md).

Dependencies:
    pip install patchright         # drop-in Playwright fork with CDP patches
    patchright install chromium    # browser binary (NOT `playwright install`)

One-time identity bootstrap (owner, interactive — cannot run headless):
    python -m adapters.social_stealth --login twitter     # -> identities/twitter-main.state.json
    python -m adapters.social_stealth --login instagram   # -> identities/ig-ro.state.json
The --login flow opens a HEADED browser reusing the exact same fingerprint the
headless fetch uses, so the saved cookies are minted under the fingerprint they
will later be replayed with.

Env vars (all optional unless noted):
    PROXY_URL            socks5://user:pass@host:port   (residential, sticky)
    SLACK_ALERT_WEBHOOK  optional alerting
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import random
from datetime import datetime, timezone
from pathlib import Path

from patchright.async_api import async_playwright

from crawler.db import connect, record_source_health, upsert_raw_item
from crawler.normalizer import extract_urls

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
log = logging.getLogger("adapter.social")

IDENTITY_DIR = Path(os.environ.get("IDENTITY_DIR", "./identities"))
MIN_DELAY, MAX_DELAY = 6.0, 15.0          # humanized pacing per nav action
PAGE_LOAD_PATIENCE = 20000                # ms

SOURCE_NAME_TWITTER = "twitter:devrel"
SOURCE_NAME_INSTAGRAM = "instagram:dealposts"

DEFAULT_TWITTER_TERMS = ["free credits", "#freecourse", "student pack promo"]
DEFAULT_INSTAGRAM_HANDLES: list[str] = []  # curated list must be supplied by the operator


class StealthIdentity:
    """One persistent browser identity (fingerprint + cookies + proxy)."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.state_path = IDENTITY_DIR / f"{name}.state.json"
        self.proxy_url = os.environ.get("PROXY_URL")  # sticky residential recommended

    # -- fingerprint (keep stable per identity) --------------------------------
    @property
    def context_options(self) -> dict:
        return {
            "storage_state": str(self.state_path) if self.state_path.exists() else None,
            "proxy": {"server": self.proxy_url} if self.proxy_url else None,
            "viewport": {"width": 1366, "height": 768},
            "locale": "en-US",
            "timezone_id": "America/New_York",
            "user_agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            ),
        }

    async def save_state(self, context) -> None:
        IDENTITY_DIR.mkdir(parents=True, exist_ok=True)
        await context.storage_state(path=str(self.state_path))


class SocialFetcher:
    """Fetches public timelines and extracts link-bearing posts as raw items."""

    def __init__(self, identity: StealthIdentity) -> None:
        self.identity = identity
        self._captured: list[dict] = []

    async def _on_response(self, response) -> None:
        """Trap XHR JSON (graphql/web API) before the page renders it away."""
        url = response.url
        content_type = response.headers.get("content-type", "")
        if "json" not in content_type:
            return
        # Twitter search timeline / IG profile graphql endpoints:
        if any(k in url for k in ("SearchTimeline", "UserTweets", "ProfilePage", "graphql")):
            try:
                body = await response.json()
            except Exception:  # noqa: BLE001
                return
            self._captured.append({"url": url, "body": body})
            self._captured = self._captured[-50:]  # bounded memory

    async def fetch(self, url: str, max_scrolls: int = 2) -> list[dict]:
        """Open a public timeline URL, scroll a little, return captured JSON."""
        self._captured.clear()
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(**self.identity.context_options)
            page = await context.new_page()
            page.on("response", self._on_response)

            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=PAGE_LOAD_PATIENCE)
                await self._human_wait(2, 5)

                for _ in range(max_scrolls):          # humanized scroll pattern
                    await page.mouse.wheel(0, random.randint(600, 1200))
                    await self._human_wait(1, 3)

                # Save evolved cookies back to the identity.
                await self.identity.save_state(context)

                # Login wall / captcha heuristics -> hard backoff + alert.
                detected = await self._detect_tarpit(page)
                if detected:
                    log.error("[%s] TARPIT detected on %s: %s", self.identity.name, url, detected)
                    await page.screenshot(path=str(IDENTITY_DIR / f"tarpit-{int(asyncio.get_event_loop().time())}.png"))
                    return []

                return list(self._captured)
            finally:
                await browser.close()

    async def _human_wait(self, lo: float, hi: float) -> None:
        await asyncio.sleep(random.uniform(lo, hi))

    async def _detect_tarpit(self, page) -> str | None:
        title = (await page.title()).lower()
        url = page.url.lower()
        if any(m in title for m in ("login", "log in", "sign in", "challenge", "verify")):
            return f"auth wall (title={title!r})"
        if "captcha" in title or "px-captcha" in url:
            return "captcha challenge"
        return None


# --------------------------------------------------------------------------- #
# JSON parsing — captured GraphQL payload -> normalized raw_item shape
# --------------------------------------------------------------------------- #
# X and Instagram nest the interesting records at a depth that shifts whenever
# they reship their web client, so a fixed jq-style path rots fast. Instead we
# walk the captured JSON tree looking for the *marker keys* each record type
# always carries (a tweet's "legacy"/"full_text"; an IG node's "shortcode"), and
# lift those out wherever they sit. Depth-agnostic = survives most reshuffles.


def _iter_nodes(obj):
    """Yield every dict nested anywhere inside a JSON value (self first)."""
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _iter_nodes(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _iter_nodes(v)


def _parse_iso_or_twitter_date(value: str | None) -> str | None:
    """Return an ISO-8601 string, converting Twitter's 'ddd MMM DD HH:MM:SS +0000 YYYY'."""
    if not value:
        return None
    try:
        # Twitter's created_at, e.g. "Wed Aug 27 12:00:00 +0000 2026".
        return datetime.strptime(value, "%a %b %d %H:%M:%S %z %Y").astimezone(
            timezone.utc
        ).isoformat()
    except (ValueError, TypeError):
        return value  # already ISO (dev.to-style) or unparseable — pass through


def _tweet_urls(legacy: dict) -> list[str]:
    """Expanded URLs from a tweet's entities, ignoring media/self-permalinks."""
    entities = legacy.get("entities") or {}
    out: list[str] = []
    for u in entities.get("urls") or []:
        expanded = u.get("expanded_url") or u.get("url")
        if expanded and not expanded.startswith("https://twitter.com/") \
                and not expanded.startswith("https://x.com/"):
            out.append(expanded)
    return out


def parse_twitter_payloads(captured: list[dict]) -> list[dict]:
    """Extract tweets from captured SearchTimeline/UserTweets JSON as raw_item payloads.

    A tweet result carries a ``legacy`` object with ``full_text``, ``id_str``,
    ``created_at`` and ``entities``; the author screen_name lives in the sibling
    ``core.user_results...legacy.screen_name``. We accept any node that has a
    ``legacy`` with both ``full_text`` and ``id_str`` and pull URLs from the text
    plus the entity list. Only link-bearing tweets become raw items — a freebie
    without a destination URL is not actionable downstream."""
    seen: set[str] = set()
    out: list[dict] = []
    for capture in captured:
        for node in _iter_nodes(capture.get("body")):
            legacy = node.get("legacy")
            if not isinstance(legacy, dict):
                continue
            tweet_id = legacy.get("id_str") or node.get("rest_id")
            full_text = legacy.get("full_text")
            if not tweet_id or not full_text or tweet_id in seen:
                continue

            # Author: look for a nested user screen_name within this tweet node.
            screen_name = None
            for sub in _iter_nodes(node.get("core")):
                sub_legacy = sub.get("legacy")
                if isinstance(sub_legacy, dict) and sub_legacy.get("screen_name"):
                    screen_name = sub_legacy["screen_name"]
                    break

            urls = extract_urls(full_text, extra=_tweet_urls(legacy))
            if not urls:
                continue  # no destination link -> not an actionable offer
            seen.add(tweet_id)

            out.append({
                "external_id": f"twitter:{tweet_id}",
                "text": full_text,
                "urls": urls,
                "author_handle": f"twitter:{screen_name}" if screen_name else "twitter:unknown",
                "published_at": _parse_iso_or_twitter_date(legacy.get("created_at")),
                "engagement": {
                    "likes": legacy.get("favorite_count", 0),
                    "retweets": legacy.get("retweet_count", 0),
                    "replies": legacy.get("reply_count", 0),
                    "views": legacy.get("view_count", 0),
                },
                "extra": {
                    "platform": "twitter",
                    "lang": legacy.get("lang"),
                    "adapter": "social_stealth",
                },
            })
    return out


def parse_instagram_payloads(captured: list[dict], handle: str) -> list[dict]:
    """Extract IG posts from captured profile graphql JSON as raw_item payloads.

    An IG media node carries a ``shortcode`` (permalink id) and its caption text
    is nested under ``edge_media_to_caption.edges[].node.text``. Bio links live on
    the user node (``external_url`` / ``bio_links``). Both media captions and the
    profile bio can carry the real offer URL, so we emit link-bearing media posts
    and, separately, a single 'bio link' pseudo-item when the profile exposes one."""
    seen: set[str] = set()
    out: list[dict] = []

    for capture in captured:
        for node in _iter_nodes(capture.get("body")):
            # --- profile bio link (one per profile) ---
            if node.get("external_url") and node.get("username"):
                bio_url = node["external_url"]
                bio_id = f"instagram:bio:{node['username']}"
                if bio_id not in seen:
                    seen.add(bio_id)
                    biography = node.get("biography") or ""
                    out.append({
                        "external_id": bio_id,
                        "text": f"{node['username']} bio: {biography}".strip(),
                        "urls": extract_urls(biography, extra=[bio_url]),
                        "author_handle": f"instagram:{node['username']}",
                        "published_at": None,
                        "engagement": {},
                        "extra": {
                            "platform": "instagram",
                            "kind": "bio_link",
                            "adapter": "social_stealth",
                        },
                    })

            # --- media post ---
            shortcode = node.get("shortcode")
            if not shortcode or shortcode in seen:
                continue
            caption = ""
            cap_edges = (node.get("edge_media_to_caption") or {}).get("edges") or []
            if cap_edges:
                caption = (cap_edges[0].get("node") or {}).get("text") or ""
            permalink = f"https://www.instagram.com/p/{shortcode}/"
            urls = extract_urls(caption)
            if not urls:
                continue  # image with no link in caption -> not actionable
            seen.add(shortcode)

            taken = node.get("taken_at_timestamp")
            out.append({
                "external_id": f"instagram:{shortcode}",
                "text": caption,
                "urls": urls,
                "author_handle": f"instagram:{handle}",
                "published_at": (
                    datetime.fromtimestamp(taken, tz=timezone.utc).isoformat()
                    if isinstance(taken, (int, float)) else None
                ),
                "engagement": {
                    "likes": (node.get("edge_liked_by") or {}).get("count", 0),
                    "comments": (node.get("edge_media_to_comment") or {}).get("count", 0),
                },
                "extra": {
                    "platform": "instagram",
                    "kind": "media",
                    "permalink": permalink,
                    "adapter": "social_stealth",
                },
            })
    return out


# --------------------------------------------------------------------------- #
# DB glue — source registration + health, mirroring the other adapters
# --------------------------------------------------------------------------- #
def _health(source_id: int | None, ok: bool, error: str | None = None) -> None:
    """Record a source-health snapshot; no-op when source_id is unknown (dry-run)."""
    if source_id is None:
        return
    with connect() as conn:
        record_source_health(conn, source_id, ok=ok, error=error)


def _ensure_source(source_name: str, kind: str, config: dict) -> int:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO sources (name, kind, config)
                VALUES (%s, %s, %s::jsonb)
                ON CONFLICT (name) DO NOTHING
                RETURNING id
                """,
                (source_name, kind, json.dumps({**config, "adapter": "social_stealth"})),
            )
            row = cur.fetchone()
            if row:
                conn.commit()
                return row["id"]
            cur.execute("SELECT id FROM sources WHERE name = %s", (source_name,))
            return cur.fetchone()["id"]


def _persist(source_id: int | None, payloads: list[dict], dry_run: bool) -> int:
    """Write parsed payloads to raw_items (or print them on dry-run). Returns count."""
    if dry_run:
        for p in payloads:
            print(json.dumps(p, ensure_ascii=False, indent=2))
        return len(payloads)
    written = 0
    for p in payloads:
        with connect() as conn:
            upsert_raw_item(conn, source_id, p["external_id"], p)
        written += 1
    return written


# --------------------------------------------------------------------------- #
# Runners — capture -> parse -> persist
# --------------------------------------------------------------------------- #
async def run_twitter(terms: list[str] | None = None, dry_run: bool = False) -> int:
    """Capture live-search tweets for each term and ingest link-bearing ones.

    Requires a hand-created ``identities/twitter-main.state.json`` (one-time manual
    login, see INGESTION_SPECS.md §2.2). Without it X serves an auth wall and the
    tarpit detector returns zero — the adapter degrades to a logged no-op, it does
    not crash."""
    terms = terms or DEFAULT_TWITTER_TERMS
    source_id = None if dry_run else _ensure_source(
        SOURCE_NAME_TWITTER, "twitter", {"terms": terms, "identity": "twitter-main"}
    )
    identity = StealthIdentity("twitter-main")
    fetcher = SocialFetcher(identity)
    total = 0
    try:
        for term in terms:
            url = f"https://x.com/search?q={term.replace(' ', '%20')}&f=live"
            captured = await fetcher.fetch(url, max_scrolls=3)
            payloads = parse_twitter_payloads(captured)
            log.info("term %r -> %d payload(s) captured, %d link-bearing tweet(s)",
                     term, len(captured), len(payloads))
            total += _persist(source_id, payloads, dry_run)
            await asyncio.sleep(random.uniform(MIN_DELAY, MAX_DELAY))
    except Exception as exc:  # noqa: BLE001 — record & surface, never crash the slot
        log.exception("twitter adapter failed")
        _health(source_id, ok=False, error=str(exc))
        return total
    _health(source_id, ok=True)
    log.info("twitter adapter done: %d items %s", total,
             "would be written (dry-run)" if dry_run else "written")
    return total


async def run_instagram(handles: list[str] | None = None, dry_run: bool = False) -> int:
    """Capture public IG profile grids for a curated handle list and ingest posts.

    Requires a hand-created ``identities/ig-ro.state.json`` (one-time manual login).
    Keep the handle list small and volume low — IG ToS restricts scraping (see
    INGESTION_SPECS.md §3). Degrades to a logged no-op without the identity."""
    handles = handles if handles is not None else DEFAULT_INSTAGRAM_HANDLES
    if not handles:
        log.warning("instagram: no handles configured — nothing to do")
        return 0
    source_id = None if dry_run else _ensure_source(
        SOURCE_NAME_INSTAGRAM, "instagram", {"handles": handles, "identity": "ig-ro"}
    )
    identity = StealthIdentity("ig-ro")          # read-only identity
    fetcher = SocialFetcher(identity)
    total = 0
    try:
        for handle in handles:
            captured = await fetcher.fetch(f"https://www.instagram.com/{handle}/", max_scrolls=2)
            payloads = parse_instagram_payloads(captured, handle)
            log.info("%s -> %d payload(s) captured, %d link-bearing item(s)",
                     handle, len(captured), len(payloads))
            total += _persist(source_id, payloads, dry_run)
            await asyncio.sleep(random.uniform(MIN_DELAY, MAX_DELAY))
    except Exception as exc:  # noqa: BLE001
        log.exception("instagram adapter failed")
        _health(source_id, ok=False, error=str(exc))
        return total
    _health(source_id, ok=True)
    log.info("instagram adapter done: %d items %s", total,
             "would be written (dry-run)" if dry_run else "written")
    return total


# --------------------------------------------------------------------------- #
# One-time identity bootstrap — headed manual login (owner, interactive)
# --------------------------------------------------------------------------- #
# platform -> (identity name, login URL). The identity name MUST match the one
# the headless runner uses (run_twitter -> "twitter-main", run_instagram ->
# "ig-ro") so the login writes the exact file the fetch later reads.
LOGIN_TARGETS: dict[str, tuple[str, str]] = {
    "twitter": ("twitter-main", "https://x.com/login"),
    "instagram": ("ig-ro", "https://www.instagram.com/accounts/login/"),
}


async def login(platform: str) -> None:
    """Open a HEADED browser so a human can log in ONCE, then persist the
    storage_state to ``identities/<name>.state.json``.

    Reuses the identical ``StealthIdentity.context_options`` the headless fetch
    uses (viewport / locale / timezone / UA + ``PROXY_URL``), so the session
    cookies are minted under the *same* fingerprint they will later be replayed
    with. A mismatch here (e.g. bootstrapping via ``patchright codegen``, which
    uses its own default context) is a common cause of a session that works once
    and is then challenged on first headless use. Cannot run headless — this is
    the one interactive step the scheduler dyno can never perform itself."""
    name, url = LOGIN_TARGETS[platform]
    identity = StealthIdentity(name)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        # Same context_options as SocialFetcher.fetch: storage_state is None
        # (no file yet), proxy + fingerprint identical to the headless path.
        context = await browser.new_context(**identity.context_options)
        page = await context.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=PAGE_LOAD_PATIENCE)
            log.info("[%s] a browser window is open at %s", name, url)
            log.info("[%s] log in by hand; wait until your home feed has fully loaded.", name)
            await asyncio.to_thread(
                input,
                f"\n>>> When you are fully logged in to {platform}, return here and press "
                f"Enter to save the identity... ",
            )
            await identity.save_state(context)
            log.info("[%s] identity saved -> %s", name, identity.state_path)
        finally:
            await browser.close()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(description="Stealth social adapter (Twitter/X + Instagram)")
    parser.add_argument("--platform", choices=("twitter", "instagram"), required=True)
    parser.add_argument("--login", action="store_true",
                        help="one-time: open a HEADED browser to log in by hand and save the identity")
    parser.add_argument("--dry-run", action="store_true", help="print payloads, no DB write")
    parser.add_argument("--term", action="append", default=None,
                        help="twitter search term (repeatable)")
    parser.add_argument("--handle", action="append", default=None,
                        help="instagram handle (repeatable)")
    args = parser.parse_args()

    if args.login:
        asyncio.run(login(args.platform))
    elif args.platform == "twitter":
        asyncio.run(run_twitter(terms=args.term, dry_run=args.dry_run))
    else:
        asyncio.run(run_instagram(handles=args.handle, dry_run=args.dry_run))


if __name__ == "__main__":
    # One-time: create the identity interactively (headed manual login) before the
    # first real run — see INGESTION_SPECS.md §2.2 / §3 for the login procedure.
    main()