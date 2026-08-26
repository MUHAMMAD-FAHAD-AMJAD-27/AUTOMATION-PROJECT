"""
crawler/normalizer.py — URL canonicalization + text/entity normalization
=========================================================================
Stage 2 of the pipeline: turns raw_items payloads into clean, canonical
NormalizedItem objects ready for LLM verification.

Responsibilities
----------------
* URL canonicalization: resolve redirects (HEAD, GET fallback), strip tracking
  params (utm_*, fbclid, gclid, ref, ...), lowercase host, drop ``www.``,
  default ports, fragments, and trailing slashes.
* Text cleaning: HTML → plain text (also harvests <a href>), markdown stripped,
  engagement bait / bot tags removed, emoji runs collapsed, shout-caps toned
  down, trailing hashtag runs dropped.
* Title normalization: clickbait-ish titles turned into clean sentence case.

All text functions are pure (unit-testable); only URLCanonicalizer is async
(network-bound via a shared httpx.AsyncClient).
"""
from __future__ import annotations

import asyncio
import html as html_lib
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

# --------------------------------------------------------------------------- #
# Tracking-parameter stripping
# --------------------------------------------------------------------------- #
TRACKING_PARAMS = {
    "fbclid", "gclid", "dclid", "gbraid", "wbraid", "msclkid", "twclid",
    "yclid", "ysclid", "igsh", "igshid", "tiktok_rider", "ttclid",
    "gad_source", "gad_campaignid", "cmpid", "cvid", "ogclid",
    "mc_cid", "mc_eid", "ref", "ref_src", "ref_url", "referrer",
    "fb_ref", "fb_source", "share_source", "share_medium", "share_id",
    "spm", "scm", "branch_match_id", "oly_anon_id", "oly_enc_id",
}
TRACKING_PREFIXES = (
    "utm_", "hsa_", "pk_", "mc_", "elq", "vero_", "trk",
    "_ga", "_gl", "_branch", "orchestrator_",
)

DEFAULT_PORTS = {"http": 80, "https": 443}
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def _is_tracking(param: str) -> bool:
    p = param.lower()
    return p in TRACKING_PARAMS or p.startswith(TRACKING_PREFIXES)


def clean_url(url: str) -> str:
    """Strip tracking params / fragments / noise from a URL (no network)."""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url.strip()

    scheme = parts.scheme.lower() or "https"
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    port = parts.port
    netloc = host if (port is None or port == DEFAULT_PORTS.get(scheme)) else f"{host}:{port}"

    path = parts.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")

    kept = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=False)
            if not _is_tracking(k)]
    query = urlencode(kept)

    return urlunsplit((scheme, netloc, path, query, ""))


# --------------------------------------------------------------------------- #
# Redirect resolution
# --------------------------------------------------------------------------- #
@dataclass
class CanonicalURL:
    original: str
    final: str          # post-redirect URL (== original when unresolved)
    canonical: str      # cleaned final URL — the dedup/hash key
    status: int | None  # last HTTP status (None => network error)
    error: str | None = None


class URLCanonicalizer:
    """Async, concurrency-limited URL canonicalizer over one shared client."""

    def __init__(self, client: httpx.AsyncClient, concurrency: int = 5) -> None:
        self.client = client
        self._sem = asyncio.Semaphore(concurrency)

    async def canonicalize(self, url: str) -> CanonicalURL:
        async with self._sem:
            final, status, error = await self._resolve(url)
            return CanonicalURL(
                original=url,
                final=final,
                canonical=clean_url(final),
                status=status,
                error=error,
            )

    async def _resolve(self, url: str) -> tuple[str, int | None, str | None]:
        try:
            resp = await self.client.head(
                url, follow_redirects=True, timeout=8.0, headers=BROWSER_HEADERS
            )
            if resp.status_code in (405, 501):  # HEAD unsupported -> GET
                resp = await self.client.get(
                    url, follow_redirects=True, timeout=10.0, headers=BROWSER_HEADERS
                )
            return str(resp.url), resp.status_code, None
        except httpx.HTTPError as exc:
            # Unresolvable (DNS / TLS / timeout): keep the original URL.
            return url, None, f"{type(exc).__name__}: {exc}"


# --------------------------------------------------------------------------- #
# Text cleaning
# --------------------------------------------------------------------------- #
class _HTMLTextExtractor(HTMLParser):
    """Collects visible text and <a href> targets from an HTML fragment."""

    _SKIP = {"script", "style", "noscript", "template"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.chunks: list[str] = []
        self.hrefs: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SKIP:
            self._skip_depth += 1
        if tag == "a":
            href = dict(attrs).get("href")
            if href and href.startswith(("http://", "https://")):
                self.hrefs.append(href)

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0 and data.strip():
            self.chunks.append(data)


# Engagement bait / bot-tag patterns (case-insensitive).
_ENGAGEMENT_RES = [
    re.compile(r"(?i)\b(?:like|share|retweet|RT|subscribe|follow\s+(?:us|our|for)|"
               r"join\s+(?:our\s+)?(?:channel|group)|turn\s+on\s+notifications|"
               r"enable\s+post\s+notifications|hit\s+the\s+bell|smash\s+that\s+\w+|"
               r"click\s+the\s+link\s+in\s+bio|link\s+in\s+bio|tap\s+the\s+link|"
               r"tag\s+a\s+friend|comment\s+below|forward\s+this)\b[:!,.\s]*"),
    re.compile(r"(?i)@\w{3,}"),                      # @mentions (bot tags)
    re.compile(r"(?i)\b(?:https?://)?t\.me/\S+"),    # telegram promo links
]
_EMOJI_RUN = re.compile(r"([\U0001F300-\U0001FAFF\u2600-\u27BF])\1+")
_HASHTAG_TAIL = re.compile(r"(\s*#[\w-]+)+\s*$")
_MD_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_MD_EMPH = re.compile(r"([*_~`]{1,3})(\S.*?\S)\1")
_MD_HEADER = re.compile(r"^\s{0,3}#{1,6}\s*", re.MULTILINE)
_PUNCT_RUN = re.compile(r"([!?])\1{1,}")
_WS_RUN = re.compile(r"[ \t\u00A0]{2,}")
_NEWLINE_RUN = re.compile(r"\n{3,}")
_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.IGNORECASE)


def _looks_like_html(text: str) -> bool:
    return bool(re.search(r"</?[a-z][\w-]*\s*[^>]*>", text, re.IGNORECASE)) or "&amp;" in text


def _strip_markdown(text: str) -> str:
    text = _MD_IMAGE.sub("", text)
    text = _MD_LINK.sub(r"\1", text)
    text = _MD_EMPH.sub(r"\2", text)
    text = _MD_HEADER.sub("", text)
    return text


def clean_text(raw: str, *, max_len: int = 4000) -> str:
    """HTML/markdown → clean plain text with engagement bait removed."""
    html_links: list[str] = []
    text = raw or ""

    if _looks_like_html(text):
        parser = _HTMLTextExtractor()
        try:
            parser.feed(text)
            html_links = parser.hrefs
            text = " ".join(parser.chunks)
        except Exception:  # noqa: BLE001 — fall back to tag stripping
            text = re.sub(r"<[^>]+>", " ", text)

    text = html_lib.unescape(text)
    text = _strip_markdown(text)

    for pattern in _ENGAGEMENT_RES:
        text = pattern.sub(" ", text)

    text = _EMOJI_RUN.sub(r"\1", text)          # 🔥🔥🔥 -> 🔥
    text = _HASHTAG_TAIL.sub("", text)
    text = _PUNCT_RUN.sub(r"\1", text)          # !!!  -> !
    text = _WS_RUN.sub(" ", text)
    text = _NEWLINE_RUN.sub("\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    return text.strip()[:max_len]


def extract_urls(text: str, *, extra: list[str] | None = None) -> list[str]:
    """All http(s) URLs in text (plus optional extras), de-duplicated, order kept."""
    urls: list[str] = []
    for match in _URL_RE.findall(text or ""):
        url = match.rstrip(".,;:!?")
        if url not in urls:
            urls.append(url)
    for url in extra or []:
        if url and url not in urls:
            urls.append(url)
    return urls


def normalize_title(title: str, *, max_len: int = 200) -> str:
    """Clean a headline: strip lead emojis/punct, tone down SHOUTING, collapse spaces."""
    t = (title or "").strip()
    t = re.sub(r"^[\s\U0001F300-\U0001FAFF\u2600-\u27BF\|•·—–\-\|\:\[\(\d\.\)]+", "", t)
    t = _EMOJI_RUN.sub(r"\1", t)
    t = _PUNCT_RUN.sub(r"\1", t)           # !!! -> !

    letters = [c for c in t if c.isalpha()]
    if letters and len(letters) >= 8 and sum(c.isupper() for c in letters) / len(letters) > 0.7:
        t = t.lower()

    t = re.sub(r"\s+", " ", t).strip()
    if t and t[0].islower():
        t = t[0].upper() + t[1:]
    return t[:max_len].strip()


# --------------------------------------------------------------------------- #
# Raw item → NormalizedItem
# --------------------------------------------------------------------------- #
@dataclass
class NormalizedItem:
    raw_item_id: int
    source_name: str
    source_kind: str
    external_id: str
    text: str
    urls: list[str] = field(default_factory=list)
    author_handle: str | None = None
    published_at: str | None = None
    engagement: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)


def normalize_raw_item(row: dict) -> NormalizedItem:
    """row = a raw_items row joined with its source (dict from psycopg)."""
    payload: dict = row.get("raw_payload") or {}
    text = payload.get("text") or ""
    html_links = payload.get("urls") or []
    cleaned = clean_text(text)
    return NormalizedItem(
        raw_item_id=row["id"],
        source_name=row["source_name"],
        source_kind=row.get("source_kind") or payload.get("kind") or "unknown",
        external_id=str(row.get("external_id") or payload.get("external_id") or ""),
        text=cleaned,
        urls=extract_urls(cleaned, extra=[str(u) for u in html_links if u]),
        author_handle=payload.get("author_handle"),
        published_at=payload.get("published_at"),
        engagement=payload.get("engagement") or {},
        extra=payload.get("extra") or {},
    )