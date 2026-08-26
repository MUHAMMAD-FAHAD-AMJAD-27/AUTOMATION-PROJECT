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
    pip install patchright   # drop-in Playwright fork with CDP patches
    playwright install chromium

Env vars (all optional unless noted):
    PROXY_URL            socks5://user:pass@host:port   (residential, sticky)
    SLACK_ALERT_WEBHOOK  optional alerting
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
from pathlib import Path

from patchright.async_api import async_playwright

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
log = logging.getLogger("adapter.social")

IDENTITY_DIR = Path(os.environ.get("IDENTITY_DIR", "./identities"))
MIN_DELAY, MAX_DELAY = 6.0, 15.0          # humanized pacing per nav action
PAGE_LOAD_PATIENCE = 20000                # ms


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


async def demo_search_twitter(terms: list[str]) -> None:
    """Example: capture tweets for hashtags/terms. Run once per identity per day."""
    identity = StealthIdentity("twitter-main")
    fetcher = SocialFetcher(identity)
    for term in terms:
        url = f"https://x.com/search?q={term.replace(' ', '%20')}&f=live"
        captured = await fetcher.fetch(url, max_scrolls=3)
        log.info("term %r -> %d JSON payload(s) captured", term, len(captured))
        # TODO(phase 3): walk the captured JSON with jq-style paths
        # (tweet_results -> legacy: full_text, id_str, url entities, created_at)
        # and feed them into crawler.db.upsert_raw_item(...).
        await asyncio.sleep(random.uniform(MIN_DELAY, MAX_DELAY))


async def demo_instagram_profiles(handles: list[str]) -> None:
    """Example: public IG profiles of a curated deal-account list."""
    identity = StealthIdentity("ig-ro")          # read-only identity
    fetcher = SocialFetcher(identity)
    for handle in handles:
        captured = await fetcher.fetch(f"https://www.instagram.com/{handle}/", max_scrolls=2)
        log.info("%s -> %d payload(s)", handle, len(captured))
        await asyncio.sleep(random.uniform(MIN_DELAY, MAX_DELAY))


if __name__ == "__main__":
    # First run: create the identity interactively (login once by hand):
    #   python -c "from adapters.social_stealth import StealthIdentity; import asyncio;"
    # Follow INGESTION_SPECS.md §social for the one-time login procedure.
    asyncio.run(demo_search_twitter(["free credits", "#freecourse", "student pack promo"]))