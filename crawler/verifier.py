"""
crawler/verifier.py — LLM structured extraction + liveness + dedup engine
=========================================================================
Stage 3 of the pipeline: takes a NormalizedItem and produces a verified,
schema-valid Offer (or rejects it), then deduplicates against PostgreSQL.

Components
----------
1. `Offer` — strict Pydantic v2 schema (the LLM output contract).
2. `LLMExtractor` — OpenAI-compatible chat completion in JSON mode with a
   hardened system prompt. Tries providers in order (primary → fallbacks).
   Configure via LLM_API_KEY / LLM_FALLBACK_1_API_KEY … env vars.
3. `LivenessProbe` — async HEAD/GET with redirect following; classifies
   `live / soft_dead / dead / unreachable`.
4. `Deduplicator` — exact SHA-256 URL-hash check against
   `offer_fingerprints`, plus local cosine-similarity over stored embeddings
   (REAL[] in Postgres; brute-force is sub-ms at single-user scale).
5. `verify_item()` — orchestrates all of the above into one verdict.

Env vars
--------
Primary provider:  LLM_API_KEY, LLM_BASE_URL, LLM_MODEL
Fallback 1:        LLM_FALLBACK_1_API_KEY, LLM_FALLBACK_1_BASE_URL, LLM_FALLBACK_1_MODEL
Fallback 2:        LLM_FALLBACK_2_API_KEY, LLM_FALLBACK_2_BASE_URL, LLM_FALLBACK_2_MODEL
(up to LLM_FALLBACK_9_* supported)

Embeddings: local sentence-transformers (all-MiniLM-L6-v2, 384-dim).
No external embedding API needed. Semantic dedup is skipped gracefully if
the library is not installed.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx
import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row
from pydantic import BaseModel, Field, field_validator

from crawler.categories import (
    category_prompt_block,
    category_prompt_csv,
    coerce_category,
    coerce_offer_type,
    offer_type_prompt_csv,
)
from crawler.normalizer import CanonicalURL, NormalizedItem
from crawler.provider_scheduler import ProviderScheduler, estimate_tokens

load_dotenv(override=True)


def _distribution_enabled() -> bool:
    """Feature flag for capacity-aware provider distribution (Phase 18 Step 2).

    OFF unless ``LLM_DISTRIBUTION_ENABLED`` is explicitly truthy. When off, the
    scheduler is never even constructed and extract_batch() runs the exact
    fixed-order fallback loop it always has.
    """
    return os.environ.get("LLM_DISTRIBUTION_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )

log = logging.getLogger("verifier")

# --------------------------------------------------------------------------- #
# 1. Offer schema (Pydantic v2 — the strict LLM output contract)
# The category / offer_type taxonomy lives in crawler/categories.py.
# --------------------------------------------------------------------------- #


class Requirements(BaseModel):
    geography: list[str] = Field(default_factory=list)
    enrollment: list[str] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list)


class Offer(BaseModel):
    """Strict offer record — the only shape allowed out of the LLM."""

    is_offer: bool = Field(description="true only if an actionable free/discounted dev resource")
    title: str = Field(min_length=4, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    url: str = Field(min_length=8)
    category: str = Field(default="other")
    offer_type: str = Field(default="other")
    value: float | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, max_length=3)
    expires_at: datetime | None = None
    requirements: Requirements = Field(default_factory=Requirements)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    reasons: list[str] = Field(default_factory=list)
    # --- structured extraction fields (populated when the LLM finds them) ---
    promo_code: str | None = Field(default=None, max_length=120,
                                   description="exact promo/coupon code string if present")
    base_url: str | None = Field(default=None, max_length=500,
                                 description="API base URL for LLM/API drops")
    github_repo: str | None = Field(default=None, max_length=300,
                                    description="github.com/owner/repo if offer is an OSS project")
    exact_steps: list[str] = Field(default_factory=list,
                                   description="ordered step-by-step instructions to claim the offer")

    @field_validator("category")
    @classmethod
    def _valid_category(cls, v: str) -> str:
        return coerce_category(v)

    @field_validator("offer_type")
    @classmethod
    def _valid_offer_type(cls, v: str) -> str:
        return coerce_offer_type(v)

    @field_validator("currency")
    @classmethod
    def _valid_currency(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip().upper()[:3]
        return v or None

    @field_validator("title")
    @classmethod
    def _clean_title(cls, v: str) -> str:
        from crawler.normalizer import normalize_title

        cleaned = normalize_title(v)
        if len(cleaned) < 4:
            raise ValueError("title too short after cleaning")
        return cleaned


_JUNK_TITLE_RE = re.compile(
    r"^(page not found|404|access denied|sign in|log ?in|forbidden|"
    r"just a moment|are you a human|verify you are human|cloudflare|"
    r"attention required|too many requests|rate limit(ed)?|unauthorized)\b",
    re.IGNORECASE,
)
_JUNK_BODY_RE = re.compile(
    r"(enable javascript to (continue|view)|checking your browser|"
    r"ddos protection by cloudflare|this page (isn.t working|doesn.t exist)|"
    r"error 404|http 404|page (has been )?(removed|deleted)|no longer available|"
    r"campaign has ended|offer (has )?ended|sold out)",
    re.IGNORECASE,
)
_DEAL_SIGNAL_RE = re.compile(
    r"(free|credit|promo|coupon|discount|deal|trial|tier|student|grant|"
    r"lifetime|off\b|code:?\s*\w|api[\s_-]?key|giveaway|voucher)",
    re.IGNORECASE,
)
_MIN_TEXT_LEN = 24


def heuristic_prefilter(item: "NormalizedItem") -> str:
    """
    Cheap local regex pass to drop obvious junk BEFORE it reaches the LLM.

    Returns "" if the item should proceed to LLM extraction, otherwise a
    short reason string explaining why it was dropped. This trades a small
    false-negative rate (a handful of real offers filtered out) for a large
    cut in LLM API calls — the pipeline only pays for extraction on items
    that plausibly contain a deal.
    """
    text = (item.text or "").strip()
    if len(text) < _MIN_TEXT_LEN:
        return "prefilter:too_short"

    first_line = text.splitlines()[0].strip() if text else ""
    if _JUNK_TITLE_RE.match(first_line):
        return "prefilter:junk_title"
    if _JUNK_BODY_RE.search(text):
        return "prefilter:junk_body"
    if not _DEAL_SIGNAL_RE.search(text):
        return "prefilter:no_deal_signal"
    return ""


SYSTEM_PROMPT = """You are a precision extraction engine for developer/student freebies.
Input may be noisy: Telegram/WhatsApp chat dumps, fragmented sentences, heavy emoji, mixed \
languages, or raw web snippets. Extract signal from the noise.

Output ONLY a single valid JSON object matching this schema. No markdown fences, no commentary.

=== SCHEMA FIELDS ===
is_offer        bool   — true ONLY if there is a real, actionable free/discounted resource for
                         developers or students. False for: pure engagement bait, "DM me" gates,
                         referral pyramids, crypto airdrops, job ads, vague reposts with no link.
title           str    — Clean sentence-case headline, ≤12 words, no ALL CAPS, no emoji runs, no hashtags.
description     str|null — 1–3 factual sentences. null if title is self-explanatory.
url             str    — Single most actionable link: offer/landing/repo page. NOT a social profile.
                         If multiple links exist, prefer github.com > official product site > other.
category        str    — MUST be one of the allowed values (see below).
offer_type      str    — MUST be one of the allowed values (see below).
value           num|null — Numeric amount ONLY if explicitly stated (e.g. "$300 credits" → 300). Never invent.
currency        str|null — 3-letter ISO code (USD, EUR…). null if not stated.
expires_at      str|null — ISO-8601 datetime if a deadline exists. Relative ("ends Friday") →
                         best-effort absolute date using today: {today}. null if unknown.
requirements    obj    — {{geography:[...], enrollment:[...], steps:[...]}}
                         geography: country/region restrictions. enrollment: "student email",
                         "credit card required", ".edu email", "18+". steps: sign-up actions.
confidence      float  — 0.0–1.0. Your certainty this is real and actionable.
reasons         list   — Up to 3 short strings explaining the verdict.

promo_code      str|null — Exact promo/coupon/invite code if explicitly present in the text
                           (e.g. "CLAUDE50", "STUDENT2026"). null if absent. Do NOT invent.
base_url        str|null — API base URL if the offer is an LLM/AI API drop
                           (e.g. "https://api.venice.ai/v1"). null if not applicable.
github_repo     str|null — Full github.com/owner/repo URL if the offer is an open-source project.
                           null if not applicable.
exact_steps     list   — Ordered list of concrete steps to claim this offer, extracted verbatim
                         from the text. Empty list if no steps are described.

=== CATEGORY VALUES ===
{categories}

=== OFFER_TYPE VALUES ===
{offer_types}

=== NOISE HANDLING ===
- Fragmented Telegram text (e.g. "bhai ye try karo", "🔥🔥 free key:") — extract what's there.
- Mixed languages — extract English field values even if surrounding text is Urdu/Hindi/Arabic.
- Emoji-heavy text — ignore decorative emoji; extract factual content.
- Multiple offers in one post — extract ONLY the single best/clearest one (highest confidence).
- "Forward this" / "Join channel" / "DM for key" — set is_offer=false unless a direct link exists.

Output ONLY the JSON object."""


BATCH_SYSTEM_PROMPT = """You are a precision extraction engine for developer/student freebies.
You will receive multiple posts in one request, each labeled [POST N] (0-indexed). Input may be \
noisy: Telegram/WhatsApp chat dumps, fragmented sentences, heavy emoji, mixed languages, or raw \
web snippets. Extract signal from the noise, independently, for EACH post.

Output ONLY a single valid JSON object: {{"results": [ ... ]}} where "results" is an array with \
EXACTLY one entry per input post, in the SAME ORDER as the [POST N] labels. No markdown fences, \
no commentary. Every entry must be a JSON object matching the schema below (never omit an entry —
use is_offer=false for posts that don't qualify).

=== SCHEMA FIELDS (per result entry) ===
is_offer        bool   — true ONLY if there is a real, actionable free/discounted resource for
                         developers or students. False for: pure engagement bait, "DM me" gates,
                         referral pyramids, crypto airdrops, job ads, vague reposts with no link.
title           str    — Clean sentence-case headline, ≤12 words, no ALL CAPS, no emoji runs, no hashtags.
description     str|null — 1–3 factual sentences. null if title is self-explanatory.
url             str    — Single most actionable link: offer/landing/repo page. NOT a social profile.
                         If multiple links exist, prefer github.com > official product site > other.
category        str    — MUST be one of the allowed values (see below).
offer_type      str    — MUST be one of the allowed values (see below).
value           num|null — Numeric amount ONLY if explicitly stated (e.g. "$300 credits" → 300). Never invent.
currency        str|null — 3-letter ISO code (USD, EUR…). null if not stated.
expires_at      str|null — ISO-8601 datetime if a deadline exists. Relative ("ends Friday") →
                         best-effort absolute date using today: {today}. null if unknown.
requirements    obj    — {{geography:[...], enrollment:[...], steps:[...]}}
                         geography: country/region restrictions. enrollment: "student email",
                         "credit card required", ".edu email", "18+". steps: sign-up actions.
confidence      float  — 0.0–1.0. Your certainty this is real and actionable.
reasons         list   — Up to 3 short strings explaining the verdict.

promo_code      str|null — Exact promo/coupon/invite code if explicitly present in the text
                           (e.g. "CLAUDE50", "STUDENT2026"). null if absent. Do NOT invent.
base_url        str|null — API base URL if the offer is an LLM/AI API drop
                           (e.g. "https://api.venice.ai/v1"). null if not applicable.
github_repo     str|null — Full github.com/owner/repo URL if the offer is an open-source project.
                           null if not applicable.
exact_steps     list   — Ordered list of concrete steps to claim this offer, extracted verbatim
                         from the text. Empty list if no steps are described.

=== CATEGORY VALUES ===
{categories}

=== OFFER_TYPE VALUES ===
{offer_types}

=== NOISE HANDLING ===
- Fragmented Telegram text (e.g. "bhai ye try karo", "🔥🔥 free key:") — extract what's there.
- Mixed languages — extract English field values even if surrounding text is Urdu/Hindi/Arabic.
- Emoji-heavy text — ignore decorative emoji; extract factual content.
- Multiple offers in one post — extract ONLY the single best/clearest one (highest confidence).
- "Forward this" / "Join channel" / "DM for key" — set is_offer=false unless a direct link exists.
- Posts are independent — never merge fields across [POST N] boundaries.

Output ONLY {{"results": [...]}} with one entry per input post, same order."""


# --------------------------------------------------------------------------- #
# 2. Local sentence-transformers embedder (free, no API key)
# --------------------------------------------------------------------------- #
_ST_MODEL_NAME = "all-MiniLM-L6-v2"


def _load_st_model():
    """Lazily load the sentence-transformers model. Returns None on import failure."""
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore[import]
        model = SentenceTransformer(_ST_MODEL_NAME)
        log.info("Loaded local embedding model: %s", _ST_MODEL_NAME)
        return model
    except Exception as exc:  # noqa: BLE001
        log.warning("sentence-transformers not available (%s) — semantic dedup disabled", exc)
        return None


_st_model = None   # module-level cache; loaded once on first embed call


def _local_embed(texts: list[str]) -> list[list[float]] | None:
    global _st_model
    if _st_model is None:
        _st_model = _load_st_model()
    if _st_model is None:
        return None
    try:
        vecs = _st_model.encode(texts, show_progress_bar=False)
        return [v.tolist() for v in vecs]
    except Exception as exc:  # noqa: BLE001
        log.warning("Local embedding failed: %s", exc)
        return None


# --------------------------------------------------------------------------- #
# 3. LLM extractor — multi-provider with automatic fallback
# --------------------------------------------------------------------------- #
@dataclass
class _Provider:
    api_key: str
    base_url: str
    model: str


def _load_providers() -> list[_Provider]:
    """
    Build the ordered provider list from environment variables.

    Primary:   LLM_API_KEY / LLM_BASE_URL / LLM_MODEL
    Fallbacks: LLM_FALLBACK_1_API_KEY / … / LLM_FALLBACK_9_API_KEY
               LLM_FALLBACK_1_BASE_URL / LLM_FALLBACK_1_MODEL  (etc.)

    Preconfigured defaults (used if only key is set):
      Groq    → https://api.groq.com/openai/v1   / llama-3.3-70b-versatile
      OpenRouter → https://openrouter.ai/api/v1  / meta-llama/llama-3.3-70b-instruct:free
      Mistral → https://api.mistral.ai/v1        / mistral-small-latest
    """
    _defaults: dict[str, tuple[str, str]] = {
        # Detect provider from key prefix and set sensible defaults
        "gsk_":    ("https://api.groq.com/openai/v1",       "llama-3.3-70b-versatile"),
        "sk-or-":  ("https://openrouter.ai/api/v1",         "meta-llama/llama-3.3-70b-instruct:free"),
    }

    def _defaults_for(key: str, env_prefix: str) -> tuple[str, str]:
        for prefix, (url, model) in _defaults.items():
            if key.startswith(prefix):
                return url, model
        # Mistral key doesn't have a unique prefix — check for Mistral base URL
        base = os.environ.get(f"{env_prefix}BASE_URL", "").rstrip("/")
        if base:
            model = os.environ.get(f"{env_prefix}MODEL", "mistral-small-latest")
            return base, model
        return "https://api.openai.com/v1", "gpt-4.1-mini"

    providers: list[_Provider] = []

    primary_key = os.environ.get("LLM_API_KEY", "").strip()
    if primary_key:
        default_url, default_model = _defaults_for(primary_key, "LLM_")
        providers.append(_Provider(
            api_key=primary_key,
            base_url=os.environ.get("LLM_BASE_URL", default_url).rstrip("/"),
            model=os.environ.get("LLM_MODEL", default_model),
        ))

    for n in range(1, 10):
        key = os.environ.get(f"LLM_FALLBACK_{n}_API_KEY", "").strip()
        if not key:
            continue
        default_url, default_model = _defaults_for(key, f"LLM_FALLBACK_{n}_")
        providers.append(_Provider(
            api_key=key,
            base_url=os.environ.get(f"LLM_FALLBACK_{n}_BASE_URL", default_url).rstrip("/"),
            model=os.environ.get(f"LLM_FALLBACK_{n}_MODEL", default_model),
        ))

    if not providers:
        log.warning("No LLM providers configured — set LLM_API_KEY in .env")
    else:
        log.info("LLM providers: %s", [p.base_url for p in providers])
    return providers


class _NoVerdict:
    """Sentinel distinct from ``None`` in batch extraction results.

    ``None`` means the LLM evaluated an item and judged it NOT an offer — a
    terminal verdict that will not change on retry, so the item can be
    dead-lettered. ``NO_VERDICT`` means no LLM verdict could be obtained at all
    (no providers configured, or every provider failed), so the item must be
    retried rather than permanently rejected. Collapsing the two into ``None``
    would silently dead-letter never-evaluated items during any provider outage.
    """

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debug aid only
        return "NO_VERDICT"


NO_VERDICT = _NoVerdict()


class LLMExtractor:
    """
    Structured extraction via any OpenAI-compatible /chat/completions endpoint.
    Tries providers in order (primary → fallback 1 → fallback 2 …) on failure.
    Embeddings are handled locally via sentence-transformers (no API cost).
    """

    def __init__(self, timeout: float = 45.0) -> None:
        self.providers = _load_providers()
        self.timeout = timeout
        # Capacity-aware distribution (Phase 18 Step 2), OFF by default.
        # Only when LLM_DISTRIBUTION_ENABLED is truthy do we build a scheduler
        # keyed by base_url (RPM/TPM + breaker knobs come from SchedulerConfig
        # / ProviderScheduler.from_env). Otherwise self._scheduler stays None
        # and every code path below is byte-for-byte the fixed-order chain.
        # Assumption: provider base_urls are distinct (they are for the current
        # Groq/OpenRouter/Mistral/Gemini chain). Two providers sharing a
        # base_url would collide in this map — flagged as a future concern if
        # multi-key-same-endpoint fan-out is ever added.
        self._scheduler: ProviderScheduler | None = None
        self._by_key: dict[str, _Provider] = {}
        if self.providers and _distribution_enabled():
            self._by_key = {p.base_url: p for p in self.providers}
            self._scheduler = ProviderScheduler.from_env(
                [p.base_url for p in self.providers]
            )
            log.info(
                "LLM distribution ENABLED — scheduler over %d providers",
                len(self.providers),
            )

    async def extract(self, item: NormalizedItem, primary_url: CanonicalURL | None = None) -> Offer | None:
        if not self.providers:
            log.error("No LLM providers configured; skipping extraction")
            return None
        user_content = self._build_user_content(item, primary_url)
        for provider in self.providers:
            result = await self._try_provider(provider, user_content)
            if result is not None:
                return result
            log.info("Provider %s failed, trying next fallback…", provider.base_url)
        log.error("All %d LLM providers exhausted", len(self.providers))
        return None

    async def extract_batch(
        self,
        items: list[tuple[NormalizedItem, CanonicalURL | None]],
    ) -> list[Offer | None | _NoVerdict]:
        """
        Extract offers for up to BATCH_SIZE items in a single LLM call.

        Cuts API calls by ~90% vs. one-request-per-item: the model receives
        an array of numbered posts and returns an array of results in the
        same order. Falls back to per-item `extract()` if the batched call
        fails or returns a malformed/mismatched-length array, so a single
        bad batch never silently drops items.
        """
        if not items:
            return []
        if not self.providers:
            log.error("No LLM providers configured; skipping batch extraction")
            return [NO_VERDICT] * len(items)

        # Distribution flag-on: capacity-aware provider selection. When the flag
        # is off self._scheduler is None, this branch is skipped, and the loop
        # below runs unchanged — no scheduler method is ever called.
        if self._scheduler is not None:
            return await self._extract_batch_scheduled(items)

        user_content = self._build_batch_user_content(items)
        for provider in self.providers:
            results = await self._try_provider_batch(provider, user_content, len(items))
            if results is not None:
                return results
            log.info("Provider %s failed batch, trying next fallback…", provider.base_url)

        log.warning("All providers failed batch extraction — falling back to per-item calls")
        out: list[Offer | None | _NoVerdict] = []
        for item, primary_url in items:
            res = await self.extract(item, primary_url=primary_url)
            # In this degraded path a None is ambiguous (genuine non-offer vs. the
            # per-item call also failing), so treat it as "no verdict" and let the
            # item be retried rather than risk dead-lettering an unevaluated item.
            out.append(res if res is not None else NO_VERDICT)
        return out

    async def _extract_batch_scheduled(
        self,
        items: list[tuple[NormalizedItem, CanonicalURL | None]],
    ) -> list[Offer | None | _NoVerdict]:
        """Flag-on variant of the batch loop (LLM_DISTRIBUTION_ENABLED truthy).

        Differs from the fixed-order loop in exactly one way: instead of always
        starting at ``self.providers[0]``, it asks the scheduler which provider
        to try FIRST (capacity-aware, skipping rate-exhausted / circuit-open
        slots), then falls through the remaining providers in the original
        fixed order. If ``pick()`` returns None (nothing free), it degrades to
        the pure fixed-order chain, so it can never do worse than flag-off.

        Outcomes are fed back via ``scheduler.record()`` with the real
        ``usage.total_tokens`` when the response reports it, else the request
        estimate. The returned list stays positionally aligned to ``items`` —
        identical ordering contract to extract_batch(), preserving the
        ``zip(chunk, offers)`` alignment in pipeline.py.
        """
        assert self._scheduler is not None  # guaranteed by caller
        user_content = self._build_batch_user_content(items)
        est = estimate_tokens(user_content)

        start_key = self._scheduler.pick(est)
        if start_key is None:
            # Nothing free per the buckets/breakers — degrade to the exact
            # fixed-order chain (never worse than today).
            ordered = list(self.providers)
        else:
            start = self._by_key[start_key]
            idx = self.providers.index(start)
            ordered = self.providers[idx:] + self.providers[:idx]

        for provider in ordered:
            results, tokens, status = await self._try_provider_batch_metered(
                provider, user_content, len(items)
            )
            if results is not None:
                self._scheduler.record(
                    provider.base_url,
                    tokens=tokens if tokens is not None else est,
                    status=200,
                    success=True,
                )
                return results
            self._scheduler.record(
                provider.base_url, tokens=None, status=status, success=False
            )
            log.info("Provider %s failed batch, trying next…", provider.base_url)

        log.warning("All providers failed batch extraction — falling back to per-item calls")
        out: list[Offer | None | _NoVerdict] = []
        for item, primary_url in items:
            res = await self.extract(item, primary_url=primary_url)
            out.append(res if res is not None else NO_VERDICT)
        return out

    async def _try_provider_batch_metered(
        self, provider: _Provider, user_content: str, expected_len: int
    ) -> tuple[list[Offer | None] | None, int | None, int | None]:
        """Metered twin of ``_try_provider_batch`` used only by the flag-on path.

        Behaves identically to ``_try_provider_batch`` (same payload, headers,
        3-attempt loop, 429/auth handling) but additionally returns
        ``(results, total_tokens, status)`` so the caller can feed
        ``scheduler.record()``:
          * ``results``      — parsed list, or None on failure/malformed output.
          * ``total_tokens`` — ``usage.total_tokens`` if the response reports it,
                               else None (caller substitutes the estimate).
          * ``status``       — the most relevant HTTP status observed (200 on a
                               clean parse; None when the parse itself failed so
                               record() classifies it as a generic failure, not
                               a false success).

        Kept as the single batch-transport implementation: the flag-OFF
        ``_try_provider_batch`` is a thin wrapper that calls this and drops the
        ``(tokens, status)`` extras, so both paths share one code path and can
        no longer drift (Phase 20 tech-debt 3a consolidation).
        """
        payload = {
            "model": provider.model,
            "temperature": 0,
            "reasoning_effort": "low",
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": BATCH_SYSTEM_PROMPT.format(
                        today=datetime.now(timezone.utc).date().isoformat(),
                        categories=category_prompt_csv(),
                        offer_types=offer_type_prompt_csv(),
                    ),
                },
                {"role": "user", "content": user_content},
            ],
        }
        headers = {
            "Authorization": f"Bearer {provider.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/freebies-aggregator",
            "X-Title": "Freebies Aggregator",
        }
        last_status: int | None = None
        for attempt in range(1, 4):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    resp = await client.post(
                        f"{provider.base_url}/chat/completions",
                        json=payload,
                        headers=headers,
                    )
                last_status = resp.status_code
                if resp.status_code == 429:
                    retry_after = float(resp.headers.get("Retry-After", 2 ** attempt))
                    log.warning("[%s] 429 (batch) — retrying in %.1fs", provider.base_url, retry_after)
                    await asyncio.sleep(retry_after + 0.5)
                    continue
                if resp.status_code in (401, 403):
                    log.warning("[%s] auth error %d — skipping provider", provider.base_url, resp.status_code)
                    return None, None, resp.status_code
                resp.raise_for_status()
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                parsed = self._parse_batch(content, expected_len)
                tokens: int | None = None
                usage = data.get("usage") if isinstance(data, dict) else None
                if isinstance(usage, dict):
                    tokens = usage.get("total_tokens")
                # A None parse is a failure, not a success: return status=None so
                # record() counts it as a generic failure rather than a 2xx win.
                return parsed, tokens, (200 if parsed is not None else None)
            except httpx.HTTPStatusError as exc:
                last_status = exc.response.status_code
                log.warning("[%s] HTTP %s (batch attempt %d/3)", provider.base_url, exc.response.status_code, attempt)
                await asyncio.sleep(2 ** attempt)
            except (httpx.HTTPError, KeyError, json.JSONDecodeError) as exc:
                log.warning("[%s] batch call failed (attempt %d/3): %s", provider.base_url, attempt, exc)
                await asyncio.sleep(2 ** attempt)
        return None, None, last_status

    async def _try_provider(self, provider: _Provider, user_content: str) -> Offer | None:
        payload = {
            "model": provider.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": SYSTEM_PROMPT.format(
                        today=datetime.now(timezone.utc).date().isoformat(),
                        categories=category_prompt_block(),
                        offer_types=offer_type_prompt_csv(),
                    ),
                },
                {"role": "user", "content": user_content},
            ],
        }
        headers = {
            "Authorization": f"Bearer {provider.api_key}",
            "Content-Type": "application/json",
            # OpenRouter requires this header for free-tier routing
            "HTTP-Referer": "https://github.com/freebies-aggregator",
            "X-Title": "Freebies Aggregator",
        }
        for attempt in range(1, 4):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    resp = await client.post(
                        f"{provider.base_url}/chat/completions",
                        json=payload,
                        headers=headers,
                    )
                if resp.status_code == 429:
                    retry_after = float(resp.headers.get("Retry-After", 2 ** attempt))
                    log.warning("[%s] 429 — retrying in %.1fs", provider.base_url, retry_after)
                    await asyncio.sleep(retry_after + 0.5)
                    continue
                if resp.status_code in (401, 403):
                    log.warning("[%s] auth error %d — skipping provider", provider.base_url, resp.status_code)
                    return None   # wrong key → move to next provider immediately
                resp.raise_for_status()
                content = resp.json()["choices"][0]["message"]["content"]
                return self._parse(content)
            except httpx.HTTPStatusError as exc:
                log.warning("[%s] HTTP %s (attempt %d/3)", provider.base_url, exc.response.status_code, attempt)
                await asyncio.sleep(2 ** attempt)
            except (httpx.HTTPError, KeyError, json.JSONDecodeError) as exc:
                log.warning("[%s] call failed (attempt %d/3): %s", provider.base_url, attempt, exc)
                await asyncio.sleep(2 ** attempt)
        return None

    async def _try_provider_batch(
        self, provider: _Provider, user_content: str, expected_len: int
    ) -> list[Offer | None] | None:
        """Fixed-order (flag-off) batch call: parsed results, or None on failure.

        Thin wrapper over ``_try_provider_batch_metered`` — the metered twin
        already performs the identical transport (same payload, headers,
        3-attempt loop, 429/auth handling, ``_parse_batch``) and additionally
        returns ``(tokens, status)`` for the scheduler. This path just discards
        those two extras, so the two methods can no longer drift apart. The
        ``results`` element is byte-for-byte what this method returned before
        consolidation in every branch (429→retry, 401/403→None, clean parse→
        ``_parse_batch`` result, exhausted retries→None).
        """
        results, _tokens, _status = await self._try_provider_batch_metered(
            provider, user_content, expected_len
        )
        return results

    def _build_user_content(self, item: NormalizedItem, primary_url: CanonicalURL | None) -> str:
        parts = [
            f"SOURCE: {item.source_name} ({item.source_kind})",
            f"AUTHOR: {item.author_handle or 'n/a'}",
            f"PUBLISHED: {item.published_at or 'n/a'}",
        ]
        if item.urls:
            parts.append("URLS: " + " | ".join(item.urls[:6]))
        if primary_url and primary_url.status:
            parts.append(f"RESOLVED PRIMARY URL: {primary_url.final} (HTTP {primary_url.status})")
        parts.append("POST TEXT:\n" + (item.text[:3000] or "(no text)"))
        return "\n".join(parts)

    def _build_batch_user_content(
        self, items: list[tuple[NormalizedItem, CanonicalURL | None]]
    ) -> str:
        blocks = []
        for idx, (item, primary_url) in enumerate(items):
            lines = [
                f"[POST {idx}]",
                f"SOURCE: {item.source_name} ({item.source_kind})",
                f"AUTHOR: {item.author_handle or 'n/a'}",
            ]
            if item.urls:
                lines.append("URLS: " + " | ".join(item.urls[:6]))
            if primary_url and primary_url.status:
                lines.append(f"RESOLVED PRIMARY URL: {primary_url.final} (HTTP {primary_url.status})")
            lines.append("TEXT:\n" + (item.text[:2000] or "(no text)"))
            blocks.append("\n".join(lines))
        return "\n\n".join(blocks)

    def _parse(self, content: str) -> Offer | None:
        data = self._parse_json_object(content)
        if data is None or not data.get("is_offer", False):
            return None
        try:
            return Offer.model_validate(data)
        except Exception as exc:  # noqa: BLE001
            log.warning("Offer validation failed: %s", exc)
            return None

    def _parse_batch(self, content: str, expected_len: int) -> list[Offer | None] | None:
        data = self._parse_json_object(content)
        if data is None:
            return None
        results_raw = data.get("results") if isinstance(data, dict) else data
        if not isinstance(results_raw, list) or len(results_raw) != expected_len:
            log.warning(
                "Batch response shape mismatch (expected %d, got %s) — treating as failure",
                expected_len, type(results_raw),
            )
            return None

        out: list[Offer | None] = []
        for entry in results_raw:
            if not isinstance(entry, dict) or not entry.get("is_offer", False):
                out.append(None)
                continue
            try:
                out.append(Offer.model_validate(entry))
            except Exception as exc:  # noqa: BLE001
                log.warning("Batch offer validation failed: %s", exc)
                out.append(None)
        return out

    def _parse_json_object(self, content: str) -> dict | list | None:
        text = content.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            log.warning("LLM returned non-JSON: %.80s", text)
            return None

    async def embed(self, texts: list[str]) -> list[list[float]] | None:
        """Local embeddings via sentence-transformers. No API call. No cost."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _local_embed, texts)


# --------------------------------------------------------------------------- #
# 4. Liveness probe
# --------------------------------------------------------------------------- #
@dataclass
class LivenessResult:
    status: str            # live | soft_dead | dead | unreachable
    http_status: int | None
    final_url: str | None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "live"


SOFT_DEAD_MARKERS = ("expired", "no longer available", "campaign has ended",
                     "offer ended", "sold out", "page not found")


class LivenessProbe:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client

    async def probe(self, url: str) -> LivenessResult:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        try:
            resp = await self.client.get(url, follow_redirects=True, timeout=12.0, headers=headers)
        except httpx.HTTPError as exc:
            return LivenessResult("unreachable", None, None, f"{type(exc).__name__}: {exc}")

        body_snippet = ""
        if resp.status_code == 200 and "text/html" in resp.headers.get("content-type", ""):
            body_snippet = resp.text[:4000].lower()

        if 200 <= resp.status_code < 300:
            if any(m in body_snippet for m in SOFT_DEAD_MARKERS):
                return LivenessResult("soft_dead", resp.status_code, str(resp.url))
            return LivenessResult("live", resp.status_code, str(resp.url))
        if resp.status_code in (401, 403, 429):
            return LivenessResult("live", resp.status_code, str(resp.url))  # exists but gated
        if resp.status_code in (404, 410):
            return LivenessResult("dead", resp.status_code, str(resp.url))
        if resp.status_code >= 500:
            return LivenessResult("unreachable", resp.status_code, str(resp.url))
        return LivenessResult("soft_dead", resp.status_code, str(resp.url))


# --------------------------------------------------------------------------- #
# 5. Deduplication engine
# --------------------------------------------------------------------------- #
@dataclass
class DupCheckResult:
    is_dup: bool
    existing_offer_id: int | None = None
    similarity: float = 0.0
    basis: str = ""       # url_hash | semantic | ""


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


class Deduplicator:
    """Exact URL-hash + semantic (cosine) dedup against Postgres fingerprints."""

    def __init__(self, conn: psycopg.Connection, semantic_threshold: float = 0.92,
                 recent_days: int = 90, embed_dim: int = 384) -> None:
        self.conn = conn
        self._db_url = conn.info.dsn
        self.semantic_threshold = semantic_threshold
        self.recent_days = recent_days
        self.embed_dim = embed_dim

    def _ensure_conn(self) -> None:
        """Reconnect if the connection was dropped by the server (Neon idle timeout)."""
        try:
            self.conn.execute("SELECT 1")
        except Exception:
            self.conn = psycopg.connect(self._db_url, row_factory=dict_row)

    # -- exact ----------------------------------------------------------------
    def check_url_hash(self, canonical_url: str) -> DupCheckResult:
        self._ensure_conn()
        url_hash = sha256_hex(canonical_url)
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT f.offer_id FROM offer_fingerprints f
                WHERE f.url_hash = %s
                """,
                (url_hash,),
            )
            row = cur.fetchone()
        if row:
            return DupCheckResult(True, row["offer_id"], 1.0, "url_hash")
        return DupCheckResult(False)

    # -- semantic ---------------------------------------------------------------
    def check_semantic(self, embedding: list[float]) -> DupCheckResult:
        if not embedding:
            return DupCheckResult(False)
        self._ensure_conn()
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT f.offer_id, f.embedding
                FROM offer_fingerprints f
                JOIN offers o ON o.id = f.offer_id
                WHERE o.first_seen > now() - %s::interval
                LIMIT 5000
                """,
                (f"{self.recent_days} days",),
            )
            rows = cur.fetchall()
        best_id, best_sim = None, 0.0
        for r in rows:
            offer_id, stored = r["offer_id"], r["embedding"]
            sim = cosine(embedding, list(stored or []))
            if sim > best_sim:
                best_id, best_sim = offer_id, sim
        if best_sim >= self.semantic_threshold:
            return DupCheckResult(True, best_id, round(best_sim, 4), "semantic")
        return DupCheckResult(False, similarity=round(best_sim, 4))

    def check(self, canonical_url: str, embedding: list[float] | None = None) -> DupCheckResult:
        result = self.check_url_hash(canonical_url)
        if result.is_dup:
            return result
        if embedding:
            return self.check_semantic(embedding)
        return DupCheckResult(False)


# --------------------------------------------------------------------------- #
# 6. Verdict orchestration
# --------------------------------------------------------------------------- #
@dataclass
class VerificationVerdict:
    offer: Offer | None
    liveness: LivenessResult | None
    dup: DupCheckResult | None
    canonical: CanonicalURL | None
    skipped_reason: str = ""    # "" when the item was fully processed

    @property
    def accepted(self) -> bool:
        return self.offer is not None and not (self.dup and self.dup.is_dup)


async def verify_item(
    item: NormalizedItem,
    extractor: LLMExtractor,
    probe: LivenessProbe,
    deduplicator: Deduplicator,
    primary_url: CanonicalURL | None = None,
    require_liveness: bool = True,
) -> VerificationVerdict:
    """Full verification for one normalized item.

    Flow: choose primary URL -> heuristic pre-filter -> liveness -> LLM
    extract -> exact dedup -> semantic dedup. Any rejection is
    short-circuited with `skipped_reason`.
    """
    # 1) pick the primary URL
    chosen: CanonicalURL | None = primary_url
    if chosen is None and item.urls:
        chosen = CanonicalURL(original=item.urls[0], final=item.urls[0],
                              canonical=clean_or_fallback(item.urls[0]), status=None)
    if chosen is None:
        return VerificationVerdict(None, None, None, None,
                                   skipped_reason="no_url")

    # 1b) cheap local regex pass — drop obvious junk before paying for
    # liveness probes or LLM calls at all.
    prefilter_reason = heuristic_prefilter(item)
    if prefilter_reason:
        return VerificationVerdict(None, None, None, chosen, skipped_reason=prefilter_reason)

    # 2) liveness (cheap) before paying for LLM extraction
    live = await probe.probe(chosen.final)
    if require_liveness and live.status in ("dead", "soft_dead"):
        return VerificationVerdict(None, live, None, chosen, skipped_reason=f"liveness:{live.status}")

    # 3) LLM structured extraction
    offer = await extractor.extract(item, primary_url=chosen)
    if offer is None:
        return VerificationVerdict(offer=None, liveness=live, dup=None, canonical=chosen,
                                   skipped_reason="llm_rejected_or_failed")

    # 4) dedup (exact, then semantic)
    canonical_url = chosen.canonical or clean_or_fallback(chosen.final)
    dup = deduplicator.check(canonical_url)
    if dup.is_dup:
        return VerificationVerdict(offer, live, dup, chosen, skipped_reason=f"dup:{dup.basis}")

    embedding = None
    embeds = await extractor.embed([f"{offer.title}\n{offer.description or ''}"])
    if embeds:
        embedding = embeds[0][:256]  # cap dims for REAL[] storage efficiency
        dup = deduplicator.check_semantic(embedding)
        if dup.is_dup:
            return VerificationVerdict(offer, live, dup, chosen, skipped_reason=f"dup:{dup.basis}")

    # 5) attach liveness-derived confidence boost/penalty
    if live.ok:
        offer.confidence = min(1.0, offer.confidence * 1.05)
    if live.status == "unreachable":
        offer.confidence = max(0.0, offer.confidence * 0.7)

    verdict = VerificationVerdict(offer, live, dup, chosen)
    verdict.offer.__dict__["_embedding"] = embedding  # stash for the writer
    return verdict


def clean_or_fallback(url: str) -> str:
    from crawler.normalizer import clean_url

    return clean_url(url)