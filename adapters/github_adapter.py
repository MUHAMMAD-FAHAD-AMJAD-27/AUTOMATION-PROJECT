"""
GitHub adapter — parse canonical free-resource mega-lists via GitHub API
========================================================================
Fetches the raw Markdown of curated "free-for-dev" style repositories
and extracts every link entry with its surrounding section heading as
context (which becomes the offer title).

Uses the GitHub Contents API — unauthenticated for public repos, rate-
limited to 60 req/hr per IP. Each repo fetch is ONE API call (file
contents), so we burn ~5 calls per run well under that limit.

Set GITHUB_TOKEN env var to raise to 5 000 req/hr (a free personal
access token with no scopes is enough).

Usage:
    python -m adapters.github_adapter
    python -m adapters.github_adapter --dry-run
    python -m adapters.github_adapter --repo ripienaar/free-for-dev
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import os
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx

from crawler.db import connect, record_source_health, upsert_raw_item

log = logging.getLogger("adapter.github")


def _health(source_id: int | None, ok: bool, error: str | None = None) -> None:
    """Record a source-health snapshot; no-op when source_id is unknown (dry-run)."""
    if source_id is None:
        return
    with connect() as conn:
        record_source_health(conn, source_id, ok=ok, error=error)

GITHUB_API = "https://api.github.com"

# Each entry: (owner, repo, file_path_in_repo, source_name_tag)
TARGETS: list[tuple[str, str, str, str]] = [
    ("ripienaar",        "free-for-dev",              "README.md",   "github:free-for-dev"),
    # jnv/list-of-free-stuff removed 2026-08-26: repo 404s — GitHub API returns
    # "Not Found" for BOTH the repo and contents/README.md (confirmed twice), and
    # no same-name replacement exists on GitHub. Not a wrong-path/branch case.
    ("AchoArnold",       "discount-for-student-dev",   "README.md",   "github:student-dev-discounts"),
    ("piotrkulpinski",   "openalternative",             "README.md",   "github:openalternative"),
    ("awesome-selfhosted", "awesome-selfhosted",        "README.md",   "github:awesome-selfhosted"),
    # --- LLM-aggregator / free-API curated lists (added 2026-08-26) ------------ #
    # Verified to resolve with a root README.md before wiring. The §5.4 denylist
    # repos (cheahjs/* 404, alistaitsacle/* ToS-disabled, dan1471/* stolen keys)
    # are deliberately excluded. Ordering here is NOT load-bearing: the worker
    # runs run_github() uncapped, and the scheduler now passes max_per_target
    # instead of a global max_items, so no repo can starve the ones behind it.
    # (It could before: a global cap of 50 was fully consumed by free-for-dev's
    # 1237 entries and none of the repos below were ever reached in production.)
    #
    # Every entry below was fetched read-only through _fetch_file_content and
    # parsed with the real _parse_markdown(min_section_filter=True); the trailing
    # comment is the MEASURED entry count on 2026-08-26, not an estimate.
    #
    # Ordering: auto-updated / daily-synced lists first, then actively maintained,
    # then the remainder. Two of these yield almost nothing with the CURRENT parser
    # (_MD_LINK_RE matches only inline [text](url) links, and min_section_filter
    # needs a HIGH_SIGNAL_SECTION or the literal word "free" on the line). They are
    # wired anyway so a future _MD_LINK_RE broadening picks them up automatically;
    # the low yield is a parser limitation, not a bad source.
    ("raullenchai",      "free-llm-api-resources",     "README.md",   "github:free-llm-api-resources"),   # 27
    ("ClawLabsAI",       "free-ai-models",             "README.md",   "github:free-ai-models"),           # 18  auto-updates /24h
    ("nejib1",           "Free-LLM",                   "README.md",   "github:free-llm"),                 # 31  synced daily
    ("howardpen9",       "awesome-ai-api-proxy",       "README.md",   "github:awesome-ai-api-proxy"),     # 2   actively maintained; rest of file is §5 fraud-risk CN relays
    ("open-free-llm-api", "awesome-freellm-apis",      "README.md",   "github:awesome-freellm-apis"),     # 0   HTML/ref-style links only — inert until _MD_LINK_RE is broadened
    ("zukixa",           "cool-ai-stuff",              "README.md",   "github:cool-ai-stuff"),            # 8
    # Both of the next two repos are literally named "awesome-free-llm-apis".
    # sources.name is UNIQUE, so the bare github:<repo> convention would collapse
    # them into ONE source row and merge two different repos' items. Suffix by owner.
    ("amardeeplakshkar", "awesome-free-llm-apis",      "README.md",   "github:awesome-free-llm-apis-amardeeplakshkar"),  # 1
    ("mnfst",            "awesome-free-llm-apis",      "README.md",   "github:awesome-free-llm-apis-mnfst"),             # 28
    ("vava-nessa",       "free-coding-models",         "README.md",   "github:free-coding-models"),       # 23
    ("tatn",             "awesome-free-ai-apis",       "README.md",   "github:awesome-free-ai-apis"),     # 1
    ("12britz",          "awesome-free-models",        "README.md",   "github:awesome-free-models"),      # 82
]

# Section headings in free-for-dev that are highest signal for our pipeline.
HIGH_SIGNAL_SECTIONS = frozenset({
    "major cloud providers",
    "cloud management solutions",
    "source code repos",
    "apis, data and ml",
    "artificial intelligence",
    "paas",
    "baas",
    "low-code platform",
    "web hosting",
    "dns",
    "domain",
    "ide and code editing",
    "analytics, events and statistics",
    "email",
    "font",
    "forms",
    "storage and media processing",
    "design and ui",
    "security and pki",
    "authentication, authorization and user management",
    "serverless",
    "testing",
    "ci and cd",
    "monitoring",
    "crash and exception handling",
    "log management",
    "translation management",
    "feature toggles management platforms",
    "remote desktop tools",
    "game development",
    "education and career development",
    "payment and billing integration",
    "docker related",
    "vagrant related",
    "other free resources",
})

_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)]+)\)")
_HEADING_RE = re.compile(r"^#{1,6}\s+(.+)$", re.MULTILINE)


@dataclass
class ParsedEntry:
    section: str
    text: str
    url: str
    raw_line: str = ""


def _parse_markdown(content: str, min_section_filter: bool = True) -> list[ParsedEntry]:
    """Walk markdown line by line; yield link entries tagged with their h2/h3 section."""
    entries: list[ParsedEntry] = []
    current_section = "general"

    for line in content.splitlines():
        heading = _HEADING_RE.match(line)
        if heading:
            current_section = heading.group(1).strip().lower()
            continue

        if min_section_filter and current_section not in HIGH_SIGNAL_SECTIONS:
            # Still include if line explicitly mentions "free" — catches entries
            # in less-common sections that are still relevant.
            if "free" not in line.lower():
                continue

        for text, url in _MD_LINK_RE.findall(line):
            host = urlparse(url).netloc.lower()
            if not host:
                continue
            # Skip links that are just other GitHub repos or the repo's own links.
            if host in ("github.com", "raw.githubusercontent.com", "shields.io", "img.shields.io"):
                continue
            entries.append(ParsedEntry(
                section=current_section,
                text=text.strip(),
                url=url.strip(),
                raw_line=line.strip()[:300],
            ))

    return entries


def _entry_to_payload(entry: ParsedEntry, repo_full_name: str) -> dict:
    title_candidate = entry.text if len(entry.text) > 4 else entry.raw_line[:80]
    return {
        "external_id": entry.url,  # URL is the natural dedup key for static lists
        "text": f"{entry.section.title()}: {entry.raw_line}",
        "urls": [entry.url],
        "author_handle": repo_full_name,
        "published_at": None,
        "engagement": {},
        "extra": {
            "section": entry.section,
            "link_text": entry.text,
            "source_repo": repo_full_name,
        },
    }


async def _fetch_file_content(
    client: httpx.AsyncClient,
    owner: str,
    repo: str,
    path: str,
    token: str | None,
) -> str | None:
    headers: dict[str, str] = {"Accept": "application/vnd.github.v3+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    url = f"{GITHUB_API}/repos/{owner}/{repo}/contents/{path}"
    try:
        resp = await client.get(url, headers=headers, timeout=20.0)
        if resp.status_code == 403:
            remaining = resp.headers.get("x-ratelimit-remaining", "?")
            log.warning("GitHub rate limit hit (%s remaining). Set GITHUB_TOKEN.", remaining)
            return None
        if resp.status_code == 404:
            log.warning("%s/%s:%s not found", owner, repo, path)
            return None
        resp.raise_for_status()
        data = resp.json()
        encoded = data.get("content", "")
        return base64.b64decode(encoded.replace("\n", "")).decode("utf-8", errors="replace")
    except (httpx.HTTPError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        log.warning("GitHub fetch error %s/%s: %s", owner, repo, exc)
        return None


def _ensure_source(source_name: str, repo: str) -> int:
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
                    json.dumps({"repo": repo, "adapter": "github_adapter"}),
                ),
            )
            row = cur.fetchone()
            if row:
                conn.commit()
                return row["id"]
            cur.execute("SELECT id FROM sources WHERE name = %s", (source_name,))
            return cur.fetchone()["id"]


async def run_github(
    targets: list[tuple[str, str, str, str]] | None = None,
    dry_run: bool = False,
    max_items: int | None = None,
    max_per_target: int | None = None,
) -> int:
    """Ingest curated mega-lists.

    ``max_items`` caps total raw_items written across ALL targets in one run
    (None = uncapped, for manual CLI runs).

    ``max_per_target`` caps raw_items written per INDIVIDUAL target. Prefer this
    over ``max_items`` for scheduled runs: because ``max_items`` is a single
    global counter consumed in TARGETS order, a head-of-list repo that yields
    thousands of links starves every target behind it. Measured 2026-08-26:
    ripienaar/free-for-dev alone parses 1237 entries, so the old
    ``max_items=50`` never opened targets 1-15 at all and the 11 LLM-aggregator
    repos were dead on arrival. A global cap large enough to reach the tail
    (>1644) would ingest ~1726 items/run — 4.3x the pipeline's whole daily drain
    capacity. A per-target cap is order-independent and bounded at
    len(TARGETS) * max_per_target, so adding a target can never starve another.
    """
    targets = targets or TARGETS  # type: ignore[assignment]
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        log.info("GITHUB_TOKEN not set — using unauthenticated (60 req/hr limit)")

    total = 0
    async with httpx.AsyncClient(
        headers={"User-Agent": "freebies-github-adapter/1.0"},
        follow_redirects=True,
    ) as client:
        for owner, repo, path, source_name in targets:
            if max_items is not None and total >= max_items:
                log.info("GitHub adapter hit max_items=%d — stopping", max_items)
                break
            full_name = f"{owner}/{repo}"
            # Resolve source_id up front so a failed fetch is still recorded on the source.
            source_id = None if dry_run else _ensure_source(source_name, full_name)

            log.info("Fetching %s/%s", full_name, path)
            content = await _fetch_file_content(client, owner, repo, path, token)
            if content is None:
                _health(source_id, ok=False, error=f"fetch failed: {full_name}:{path}")
                continue

            entries = _parse_markdown(content, min_section_filter=True)
            log.info("%s -> %d entries parsed", full_name, len(entries))

            try:
                written_here = 0
                for entry in entries:
                    if max_items is not None and total >= max_items:
                        break
                    if max_per_target is not None and written_here >= max_per_target:
                        log.info(
                            "%s -> per-target cap %d reached (%d entries parsed, %d skipped)",
                            full_name, max_per_target, len(entries),
                            len(entries) - written_here,
                        )
                        break
                    payload = _entry_to_payload(entry, full_name)
                    if dry_run:
                        print(json.dumps(payload, ensure_ascii=False, indent=2))
                        total += 1
                        written_here += 1
                        continue
                    with connect() as conn:
                        upsert_raw_item(conn, source_id, entry.url, payload)
                    total += 1
                    written_here += 1
            except Exception as exc:  # noqa: BLE001 — record & move to next repo
                log.exception("ingest failed for %s", full_name)
                _health(source_id, ok=False, error=f"ingest failed: {exc}")
                continue

            _health(source_id, ok=True)
            await asyncio.sleep(1.0)  # stay polite between repo fetches

    log.info("GitHub adapter done: %d entries", total)
    return total


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(description="GitHub mega-list adapter")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--repo", default=None, help="owner/repo (e.g. ripienaar/free-for-dev)")
    parser.add_argument(
        "--max-items", type=int, default=None,
        help="cap total raw_items written this run (default: uncapped)",
    )
    parser.add_argument(
        "--max-per-target", type=int, default=None,
        help="cap raw_items written per repo (default: uncapped); prefer this over "
             "--max-items, which is a global counter that starves later targets",
    )
    args = parser.parse_args()

    targets = TARGETS
    if args.repo:
        parts = args.repo.split("/")
        if len(parts) == 2:
            # NOTE: this derives the source name verbatim from the repo name, so a
            # manual `--repo owner/Repo-Name` run creates source `github:Repo-Name`,
            # which may differ in CASE from the hardcoded name in TARGETS (e.g.
            # `--repo nejib1/Free-LLM` -> `github:Free-LLM`, while TARGETS uses
            # `github:free-llm`). The prod path (worker -> run_github over TARGETS)
            # is self-consistent; only ad-hoc CLI runs can spawn a divergent source.
            targets = [(parts[0], parts[1], "README.md", f"github:{parts[1]}")]

    asyncio.run(run_github(
        targets=targets,
        dry_run=args.dry_run,
        max_items=args.max_items,
        max_per_target=args.max_per_target,
    ))


if __name__ == "__main__":
    main()

# ─── Source registration SQL ──────────────────────────────────────────────────
# INSERT INTO sources (name, kind, config) VALUES
#   ('github:free-for-dev',         'web', '{"repo":"ripienaar/free-for-dev","adapter":"github_adapter"}'),
#   ('github:student-dev-discounts','web', '{"repo":"AchoArnold/discount-for-student-dev","adapter":"github_adapter"}');
