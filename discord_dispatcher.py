#!/usr/bin/env python3
"""
Discord Rich-Embed Dispatcher — Developer Freebies Aggregation System
========================================================================

Polls PostgreSQL for verified, active, not-yet-broadcast offers and routes
each one to a category-specific Discord webhook, or falls back to the default.

Multi-webhook routing
---------------------
Set per-category webhook URLs in the environment to fan out to dedicated channels:

    DISCORD_WEBHOOK_URL                   — default / fallback (required)
    DISCORD_WEBHOOK_LLM_API_DROP          — #ai-apis channel
    DISCORD_WEBHOOK_OPEN_SOURCE_REPO      — #github-tools channel
    DISCORD_WEBHOOK_STUDENT_PACK          — #student-deals channel
    DISCORD_WEBHOOK_SAAS_DEAL             — #saas-deals channel
    DISCORD_WEBHOOK_AI_TOOLS              — #ai-tools channel
    DISCORD_WEBHOOK_CODING_AGENTS         — #coding-agents channel
    DISCORD_WEBHOOK_CLOUD                 — #cloud-credits channel
    DISCORD_WEBHOOK_COUPON                — #coupons channel

Any category without a dedicated env var falls back to DISCORD_WEBHOOK_URL.

Enhanced embeds
---------------
When the LLM extracted promo_code / base_url / github_repo, those appear as
prominent top fields in the embed so users see them instantly.

Design notes
------------
* Idempotent & worker-safe: UNIQUE(offer_id, channel) prevents double-sends.
* Rate-limited: 2.5 s between messages (~24 msg/min, safely under 30/min).
* Honors 429 Retry-After. Retries 5xx with exponential backoff.
* --dry-run prints exact payloads, sends nothing.

Usage
-----
    python discord_dispatcher.py --dry-run
    python discord_dispatcher.py --limit 10
    python discord_dispatcher.py --category llm_api_drop
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from typing import Any

import httpx
import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row

from crawler.categories import CATEGORY_DEFS

load_dotenv(override=True)

log = logging.getLogger("dispatcher")

# SECURITY: httpx logs every request's full URL at INFO. A Discord webhook URL
# *is* its own credential (id + 68-char token), so leaving this at INFO writes a
# live secret to stdout on every send — and on Heroku, into the log stream.
# Set at import time, not inside main(): pipeline.dispatch_new_offers() imports
# this module and calls run_batch() directly, so a main()-only guard would leave
# the production scheduler path leaking. Application-level INFO is unaffected.
logging.getLogger("httpx").setLevel(logging.WARNING)

# Category -> (emoji badge, Discord decimal color), derived from the single
# source of truth in crawler/categories.py.
CATEGORY_STYLE: dict[str, tuple[str, int]] = {
    d.key: (d.emoji, d.color) for d in CATEGORY_DEFS
}

# Category -> env-var name for per-channel webhook routing.
# Missing = fall back to DISCORD_WEBHOOK_URL. This fallback is INTENTIONAL:
# categories without a dedicated channel env var (e.g. llm, hosting, domain,
# tools, course, other) all share the default DISCORD_WEBHOOK_URL channel.
CATEGORY_WEBHOOK_ENV: dict[str, str] = {
    "llm_api_drop":     "DISCORD_WEBHOOK_LLM_API_DROP",
    "open_source_repo": "DISCORD_WEBHOOK_OPEN_SOURCE_REPO",
    "student_pack":     "DISCORD_WEBHOOK_STUDENT_PACK",
    "student":          "DISCORD_WEBHOOK_STUDENT_PACK",   # student → same channel
    "saas_deal":        "DISCORD_WEBHOOK_SAAS_DEAL",
    "ai_tools":         "DISCORD_WEBHOOK_AI_TOOLS",
    "coding_agents":    "DISCORD_WEBHOOK_CODING_AGENTS",
    "cloud":            "DISCORD_WEBHOOK_CLOUD",
    "coupon":           "DISCORD_WEBHOOK_COUPON",
    # Item 4b: notable_repo has its own channel env var already wired here so the
    # operator can drop in NOTABLE_REPO_WEBHOOK_URL now; dispatch stays HELD (see
    # HELD_DISPATCH_FLAGS below) until the enable flag is flipped, so this webhook
    # sits unused until then and never falls back to the default deal channel.
    "notable_repo":     "NOTABLE_REPO_WEBHOOK_URL",
}

# Item 4b — HELD dispatch lane. Offers in these categories are fully ingested,
# classified, and stored, but withheld from Discord dispatch until their enable
# flag is explicitly truthy. notable_repo (GitHub-Trending discovery) collects
# silently for operator review; flip NOTABLE_REPO_DISPATCH_ENABLED=1 to release
# it (optionally after pointing NOTABLE_REPO_WEBHOOK_URL at a dedicated channel).
# Enforced in claim_offers() below and mirrored by pipeline.has_undispatched_offers
# so the dispatch gate doesn't fire on content that can't yet be sent.
HELD_DISPATCH_FLAGS: dict[str, str] = {
    "notable_repo": "NOTABLE_REPO_DISPATCH_ENABLED",
}


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def held_dispatch_categories() -> tuple[str, ...]:
    """Categories currently withheld from dispatch (their enable-flag is off).

    Empty once every held category's flag is truthy, at which point dispatch
    behaves exactly as it did before Item 4b for those categories.
    """
    return tuple(
        cat for cat, flag in HELD_DISPATCH_FLAGS.items() if not _env_truthy(flag)
    )


# --------------------------------------------------------------------------- #
# notable_repo daily cap + diversity trim (Item 1)
# --------------------------------------------------------------------------- #
# notable_repo is a discovery lane fed by GitHub-Trending: a single hot topic can
# spawn a whole cluster of near-duplicate repos in one day (the DeepSeek-Harness
# wave surfaced 6 of 19 offers in a single run). Releasing that lane unbounded
# would flood the channel with one trend. So once NOTABLE_REPO_DISPATCH_ENABLED
# is on, we (a) cap notable_repo sends per UTC day at NOTABLE_REPO_DAILY_CAP, and
# (b) when more candidates than the remaining daily budget exist, keep the most
# *diverse* subset (greedy max-coverage over {github org} ∪ {significant title
# tokens}) rather than the arbitrary oldest-N. The rest are excluded from this
# run via an offer-id filter threaded through claim_offers — they are neither
# inserted, claimed, nor sent, but remain untouched in the DB for a later day
# (nothing is deleted). Deal categories are entirely unaffected: the exclusion
# list only ever contains notable_repo offer ids.
NOTABLE_REPO_DAILY_CAP = 8

# Generic words stripped before building a repo's diversity "tags", so unrelated
# repos don't falsely cluster on boilerplate ("free ai tool ...").
_DIVERSITY_STOPWORDS: frozenset[str] = frozenset({
    "the", "for", "and", "with", "from", "your", "you", "this", "that", "are",
    "was", "has", "have", "all", "any", "can", "get", "use", "new", "via", "not",
    "but", "app", "api", "ml", "llm", "tool", "tools", "open", "source", "free",
    "github", "repo", "repos", "cli", "sdk", "lib", "library", "framework",
    "based", "using", "model", "models", "project", "code", "data", "self",
    "hosted", "awesome", "list", "simple", "fast", "best",
})

# Split a title into candidate tokens on any run of non-alphanumerics.
_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")


def _env_int(name: str, default: int) -> int:
    """Read a non-negative int env override; fall back to default on unset/invalid."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        val = int(raw)
    except ValueError:
        log.warning("Invalid %s=%r; using default %d", name, raw, default)
        return default
    return val if val >= 0 else default


def _repo_org(github_repo: str | None, author_handle: str | None) -> str:
    """Best-effort GitHub org/owner for an offer, for org-diversity trimming."""
    src = (github_repo or "").strip()
    if src:
        # Accept full URLs or "owner/name"; owner is the segment before the repo.
        tail = src.split("github.com/", 1)[-1].strip("/")
        parts = [p for p in tail.split("/") if p]
        if parts:
            return parts[0].lower()
    return (author_handle or "").strip().lower()


def _diversity_tags(offer: dict[str, Any]) -> frozenset[str]:
    """Tags describing an offer's org + topic, used to spread the daily subset."""
    raw = offer.get("raw") or {}
    org = _repo_org(raw.get("github_repo"), offer.get("author_handle"))
    tags: set[str] = set()
    if org:
        tags.add(f"org:{org}")
    title = (offer.get("title") or "").lower()
    for tok in _TOKEN_SPLIT.split(title):
        if len(tok) >= 3 and tok not in _DIVERSITY_STOPWORDS:
            tags.add(tok)
    return frozenset(tags)


def _diversify(candidates: list[dict[str, Any]], k: int) -> list[dict[str, Any]]:
    """Pick k offers maximizing tag diversity (greedy max new-tag coverage).

    candidates arrive oldest-first; ties in marginal coverage break to the
    earliest (oldest) candidate, preserving bounded-latency intent."""
    if k >= len(candidates):
        return list(candidates)
    remaining = list(enumerate(candidates))  # keep original order for tie-breaks
    tag_cache = {i: _diversity_tags(c) for i, c in remaining}
    chosen: list[dict[str, Any]] = []
    seen: set[str] = set()
    while remaining and len(chosen) < k:
        # max new tags; tie-break on smallest original index (oldest first_seen).
        best_pos = max(
            range(len(remaining)),
            key=lambda p: (len(tag_cache[remaining[p][0]] - seen), -remaining[p][0]),
        )
        idx, cand = remaining.pop(best_pos)
        chosen.append(cand)
        seen |= tag_cache[idx]
    return chosen


def notable_repo_exclusions(conn: psycopg.Connection, channel: str) -> list[int]:
    """Offer ids to exclude from this dispatch run to honor the notable_repo cap.

    Returns [] when the lane is still held (the category-level hold_filter already
    covers it) or when every pending candidate fits under the remaining daily
    budget. Otherwise returns the surplus (least-diverse) notable_repo ids to skip
    this run. Read-only — never mutates or deletes anything."""
    if "notable_repo" in held_dispatch_categories():
        return []
    cap = _env_int("NOTABLE_REPO_DAILY_CAP", NOTABLE_REPO_DAILY_CAP)
    day_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT count(*) AS n
            FROM dispatches d
            JOIN offers o ON o.id = d.offer_id
            WHERE d.channel = %(channel)s
              AND d.status = 'sent'
              AND d.sent_at >= %(day_start)s
              AND o.category = 'notable_repo'
            """,
            {"channel": channel, "day_start": day_start},
        )
        sent_today = cur.fetchone()["n"]
        remaining = max(0, cap - sent_today)

        cur.execute(
            """
            SELECT o.id, o.title, o.author_handle, o.raw, o.first_seen
            FROM offers o
            WHERE o.category = 'notable_repo'
              AND o.verification_status IN ('verified','live')
              AND o.is_active
              AND NOT EXISTS (
                  SELECT 1 FROM dispatches d
                  WHERE d.offer_id = o.id
                    AND d.channel = %(channel)s
                    AND d.status = 'sent'
              )
            ORDER BY o.first_seen ASC
            """,
            {"channel": channel},
        )
        candidates = cur.fetchall()

    if not candidates:
        return []
    if remaining <= 0:
        log.info("notable_repo daily cap reached (%d sent today); holding %d candidate(s)",
                 sent_today, len(candidates))
        return [c["id"] for c in candidates]
    if len(candidates) <= remaining:
        return []
    chosen_ids = {c["id"] for c in _diversify(candidates, remaining)}
    excluded = [c["id"] for c in candidates if c["id"] not in chosen_ids]
    log.info(
        "notable_repo cap: %d sent today, budget %d, %d candidate(s) → keeping %d most-diverse, deferring %d",
        sent_today, remaining, len(candidates), len(chosen_ids), len(excluded),
    )
    return excluded


def resolve_webhook(category: str, default_url: str) -> str:
    """Return the correct webhook URL for a given category."""
    env_var = CATEGORY_WEBHOOK_ENV.get(category)
    if env_var:
        url = os.environ.get(env_var, "").strip()
        if url:
            return url
    return default_url

EMBED_LIMITS = {"title": 256, "description": 4096, "field_name": 256, "field_value": 1024}
WEBHOOK_PACE_SECONDS = 2.5   # ~24 msg/min worst case, safely under Discord's 30/min
MAX_ATTEMPTS = 4

# Mirror of the dispatches.channel CHECK in schema.sql
# (CHECK (channel IN ('discord','email','web'))). Kept here so run_batch can
# reject a bad channel with a clear ValueError instead of letting it reach the
# INSERT and surface as an opaque Postgres constraint violation. If the schema
# CHECK ever changes, change this too.
ALLOWED_CHANNELS = frozenset({"discord", "email", "web"})

# Lease semantics: when a dispatcher worker stamps claimed_at on a pending
# row, it owns that row for DISPATCH_LEASE_MINUTES. If a process dies between
# the lease stamp and the actual webhook send, the row would otherwise be
# orphaned (status='pending' with claimed_at set, no re-claimer). 15 min
# gives ~10x headroom over the worst-case send+retry wall time (~90s) while
# capping operator-visible recovery delay at a quarter hour.
DISPATCH_LEASE_MINUTES = 15

# Re-claim circuit-breaker: a 'failed' row that has been attempted this many
# times is no longer auto-reclaimed. Logged as a warning so the operator can
# inspect it manually. 20 attempts ~= ~20 dispatcher runs of pain before
# giving up — generous enough to ride out a transient webhook outage.
MAX_RECLAIM_ATTEMPTS = 20


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def truncate(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        log.error("Missing required environment variable %s", name)
        raise SystemExit(2)
    return value


def humanize_expiry(expires_at: datetime | None) -> str:
    if not expires_at:
        return "No expiration"
    delta = expires_at - datetime.now(timezone.utc)
    if expires_at.tzinfo is None:
        delta = expires_at.replace(tzinfo=timezone.utc) - datetime.now(timezone.utc)
    if delta.total_seconds() < 0:
        return f"⚠️ Expired {-delta.days}d ago"
    if delta.total_seconds() < 3600:
        return f"⏰ {int(delta.total_seconds() // 60)}m left"
    if delta.days < 1:
        return f"⏰ {int(delta.total_seconds() // 3600)}h left"
    return f"⏰ In {delta.days}d"


# --------------------------------------------------------------------------- #
# Embed formatting
# --------------------------------------------------------------------------- #
def build_embed(offer: dict[str, Any]) -> dict[str, Any]:
    category = offer.get("category") or "other"
    offer_type = offer.get("offer_type") or "other"
    badge, color = CATEGORY_STYLE.get(category, CATEGORY_STYLE["other"])

    # Pull structured extras out of the raw JSONB audit column.
    raw_extra: dict[str, Any] = offer.get("raw") or {}
    promo_code  = raw_extra.get("promo_code")
    base_url    = raw_extra.get("base_url")
    github_repo = raw_extra.get("github_repo")
    exact_steps: list[str] = raw_extra.get("exact_steps") or []

    # --- priority fields at the TOP of the embed ---
    fields: list[dict[str, Any]] = []
    if promo_code:
        fields.append({
            "name": "🎟 Promo Code",
            "value": f"```{promo_code}```",
            "inline": False,
        })
    if base_url:
        fields.append({
            "name": "🔗 API Base URL",
            "value": truncate(f"`{base_url}`", EMBED_LIMITS["field_value"]),
            "inline": False,
        })
    if github_repo:
        fields.append({
            "name": "📦 Repository",
            "value": truncate(github_repo, EMBED_LIMITS["field_value"]),
            "inline": False,
        })

    # --- standard metadata fields ---
    fields += [
        {"name": "🏷 Type",    "value": f"{badge} {category.replace('_',' ').title()} · {offer_type.title()}", "inline": True},
        {"name": "💰 Value",   "value": _format_value(offer), "inline": True},
        {"name": "⏳ Expires", "value": humanize_expiry(offer.get("expires_at")), "inline": True},
    ]

    requirements = offer.get("requirements") or []
    if requirements:
        if isinstance(requirements, dict):
            # Pydantic Requirements model dumped to dict
            req_parts = []
            for k, v in requirements.items():
                if v:
                    req_parts.extend(v if isinstance(v, list) else [str(v)])
            req_text = " • ".join(req_parts)
        else:
            req_text = " • ".join(str(r) for r in requirements)
        if req_text:
            fields.append({"name": "✅ Requirements", "value": truncate(req_text, EMBED_LIMITS["field_value"])})

    if exact_steps:
        steps_text = "\n".join(f"{i+1}. {s}" for i, s in enumerate(exact_steps[:6]))
        fields.append({
            "name": "📋 How to claim",
            "value": truncate(steps_text, EMBED_LIMITS["field_value"]),
            "inline": False,
        })

    embed: dict[str, Any] = {
        "title": truncate(offer["title"], EMBED_LIMITS["title"]),
        "url": offer.get("canonical_url") or offer["url"],
        "description": truncate(offer.get("description") or "", EMBED_LIMITS["description"]),
        "color": color,
        "fields": fields,
        "footer": {
            "text": "via %s · first seen %s"
            % (offer.get("source_name", "unknown"), offer.get("first_seen", "?"))
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if offer.get("author_handle"):
        embed["author"] = {"name": str(offer["author_handle"])}
    return embed


def _format_value(offer: dict[str, Any]) -> str:
    value, currency = offer.get("value"), offer.get("currency")
    if value is None:
        return "—"
    return f"{currency or ''} {float(value):,.0f}".strip()


# --------------------------------------------------------------------------- #
# Database: claim-and-send in one flow
# --------------------------------------------------------------------------- #
def claim_offers(
    conn: psycopg.Connection,
    channel: str,
    limit: int,
    category: str | None,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    """Claim due offers atomically. Returns full offer rows for the batch.

    Three reclaim cases, all handled in a single UPDATE so the dispatcher
    is the only worker that can win any one row in any one call:

      (a) Brand-new pending row inserted in this same call:
          status='pending' AND claimed_at IS NULL
      (b) Pending row whose previous lease expired
          (DISPATCH_LEASE_MINUTES since claimed_at):
          status='pending' AND claimed_at < now() - lease_interval
      (c) Previously failed row, immediately reclaimable:
          status='failed' AND attempts < MAX_RECLAIM_ATTEMPTS

    `attempts` is incremented on every claim — a re-claim ceiling of
    MAX_RECLAIM_ATTEMPTS protects against poison rows retried forever. Rows
    at the ceiling are logged as a warning and skipped.

    When ``dry_run`` is True this is a strictly read-only preview: it returns
    the offers that would be dispatched without inserting, claiming, or
    committing anything (see the inline note below).
    """
    cat_filter = "AND o.category = %(category)s" if category else ""
    lease_interval = f"{DISPATCH_LEASE_MINUTES} minutes"
    params: dict[str, Any] = {
        "channel": channel,
        "limit": limit,
        "category": category,
        "lease_interval": lease_interval,
        "max_reclaim": MAX_RECLAIM_ATTEMPTS,
    }

    # Item 4b — HELD dispatch: exclude categories whose enable flag is still off
    # (notable_repo until NOTABLE_REPO_DISPATCH_ENABLED is truthy). Applied to
    # every offer-selecting query below (dry-run preview, ceiling scan, INSERT,
    # and the claim UPDATE's inner subquery) so held offers are never inserted
    # into dispatches, never claimed, and never sent — even when an operator
    # passes --category notable_repo explicitly. Once the flag flips, held is
    # empty and hold_filter is "" — dispatch is byte-for-byte the prior behavior.
    held = held_dispatch_categories()
    hold_filter = ""
    if held:
        hold_filter = "AND o.category <> ALL(%(held_categories)s)"
        params["held_categories"] = list(held)

    # Item 1 — notable_repo daily cap + diversity trim. When the lane is live,
    # compute the surplus notable_repo offer ids to defer this run (over the daily
    # budget / least diverse) and exclude them from every offer-selecting query
    # below. The list only ever holds notable_repo ids, so deal categories are
    # untouched; deferred offers stay in the DB for a later day (never deleted).
    id_filter = ""
    exclude_ids = notable_repo_exclusions(conn, channel)
    if exclude_ids:
        id_filter = "AND o.id <> ALL(%(exclude_ids)s)"
        params["exclude_ids"] = exclude_ids

    # DRY-RUN: preview ONLY — must not mutate the dispatches table.
    # Previously run_batch() called claim_offers() unconditionally and only
    # skipped the *webhook send* under dry_run, so a "preview" still ran the
    # step-1 INSERT and the step-2 claim UPDATE: it inserted pending rows and
    # stamped claimed_at / bumped attempts on real rows. That is exactly how
    # orphaned pending rows (claimed-but-never-sent) were created. Here we run a
    # single read-only projection of the offers that WOULD be dispatched —
    # same eligibility filter + created_at ordering + limit as the live claim —
    # with no INSERT, no UPDATE, and no commit. It is an approximation of the
    # live claim ordering: brand-new offers have no dispatch row yet, so they
    # sort last (COALESCE created_at -> +infinity). Fine for a preview; the
    # real claim path below is unchanged. Ordering mirrors the live claim's
    # oldest-first policy (o.first_seen ASC) so the preview shows the same offers
    # the live run would pick, not the newest ones.
    if dry_run:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT o.*, s.name AS source_name, d.id AS _dispatch_id
                FROM offers o
                JOIN raw_items r ON r.id = o.raw_item_id
                JOIN sources  s ON s.id = r.source_id
                LEFT JOIN dispatches d
                       ON d.offer_id = o.id AND d.channel = %(channel)s
                WHERE o.verification_status IN ('verified','live')
                  AND o.is_active
                  AND (d.id IS NULL OR d.status <> 'sent')
                  {cat_filter}
                  {hold_filter}
                  {id_filter}
                ORDER BY COALESCE(d.created_at, 'infinity'::timestamptz) ASC,
                         o.first_seen ASC
                LIMIT %(limit)s
                """,
                params,
            )
            preview = cur.fetchall()
        conn.rollback()  # release the read-only txn; guarantee nothing persists
        return preview

    with conn.cursor() as cur:
        # 0) Log any failed rows at the re-claim ceiling so the operator
        #    can inspect them manually. Read-only — doesn't affect the
        #    UPDATE in step 2.
        cur.execute(
            f"""
            SELECT d.id, d.offer_id, d.attempts
            FROM dispatches d
            JOIN offers o ON o.id = d.offer_id
            WHERE d.channel = %(channel)s
              AND d.status = 'failed'
              AND d.attempts >= %(max_reclaim)s
              {cat_filter}
              {hold_filter}
              {id_filter}
            """,
            params,
        )
        ceiling_rows = cur.fetchall()
        for r in ceiling_rows:
            log.warning(
                "Skipping dispatch at re-claim ceiling: dispatch_id=%s offer_id=%s attempts=%s (manual intervention required)",
                r["id"], r["offer_id"], r["attempts"],
            )

        # 1) Atomically insert a pending dispatch row for every due offer
        #    that has NO dispatch row at all. The NOT EXISTS now blocks
        #    on row existence (any status), not just 'sent' — that lets
        #    step 2 reclaim existing pending/failed rows instead of
        #    creating duplicates that would collide on the UNIQUE
        #    constraint.
        #
        #    ORDER BY o.first_seen ASC (oldest-first) is load-bearing. It used to
        #    be DESC: when more than `limit` offers had no dispatch row, only the
        #    NEWEST `limit` got one, and every later run repeated that choice, so
        #    a fresh burst of arrivals kept re-burying the same older offers.
        #    Measured effect of that starvation on live data: median verified→sent
        #    latency 0.07 h but p90 53.6 h and max 66.4 h, with 78 of 299 offers
        #    sent more than a day late. Oldest-first makes the wait bounded — an
        #    offer can be passed over at most until the queue ahead of it drains,
        #    instead of indefinitely.
        cur.execute(
            f"""
            INSERT INTO dispatches (offer_id, channel, status)
            SELECT o.id, %(channel)s, 'pending'
            FROM offers o
            WHERE o.verification_status IN ('verified','live')
              AND o.is_active
              AND NOT EXISTS (
                  SELECT 1 FROM dispatches d
                  WHERE d.offer_id = o.id AND d.channel = %(channel)s
              )
              {cat_filter}
              {hold_filter}
              {id_filter}
            ORDER BY o.first_seen ASC
            LIMIT %(limit)s
            ON CONFLICT (offer_id, channel) DO NOTHING
            """,
            params,
        )

        # 2) Lease a row this run owns. Three paths in one UPDATE:
        #    (a) brand new pending — claimed_at IS NULL
        #    (b) pending whose lease expired — claimed_at older than lease
        #    (c) failed with attempts below the ceiling
        # All three increment attempts as the re-claim counter.
        cur.execute(
            f"""
            UPDATE dispatches d
            SET    claimed_at = now(),
                   attempts   = d.attempts + 1
            WHERE  d.channel = %(channel)s
              AND  (
                       (d.status = 'pending' AND d.claimed_at IS NULL)
                    OR (d.status = 'pending' AND d.claimed_at < now() - %(lease_interval)s::interval)
                    OR (d.status = 'failed'  AND d.attempts < %(max_reclaim)s)
                   )
              AND  d.id IN (
                       SELECT d2.id FROM dispatches d2
                       JOIN offers o ON o.id = d2.offer_id
                       WHERE d2.channel = %(channel)s
                         AND o.verification_status IN ('verified','live')
                         AND o.is_active
                         AND d2.status <> 'sent'
                         {cat_filter}
                         {hold_filter}
                         {id_filter}
                       ORDER BY d2.created_at
                       LIMIT %(limit)s
                   )
            RETURNING d.id AS dispatch_id, d.offer_id
            """,
            params,
        )
        claimed = cur.fetchall()

        # 3) Load the full offers for the claimed ids.
        if not claimed:
            conn.commit()
            return []
        offer_ids = [row["offer_id"] for row in claimed]
        cur.execute(
            """
            SELECT o.*, s.name AS source_name
            FROM offers o
            JOIN raw_items r ON r.id = o.raw_item_id
            JOIN sources  s ON s.id = r.source_id
            WHERE o.id = ANY(%s)
            """,
            (offer_ids,),
        )
        offers = cur.fetchall()
        dispatch_ids = {row["offer_id"]: row["dispatch_id"] for row in claimed}
        for offer in offers:
            offer["_dispatch_id"] = dispatch_ids[offer["id"]]
        conn.commit()
        return offers


def mark_dispatch(
    conn: psycopg.Connection,
    dispatch_id: int,
    status: str,
    error: str | None = None,
    meta: dict[str, Any] | None = None,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            # NB: attempts is NOT incremented here. It is the re-claim counter,
            # and claim_offers already bumps it once per lease under the same
            # atomic UPDATE that grants ownership (see the MAX_RECLAIM_ATTEMPTS
            # ceiling it guards). mark_dispatch only records the *outcome* of an
            # attempt that has already been counted — incrementing here too made
            # a single successful send read as attempts=2 and burned reclaim
            # budget twice as fast.
            """
            UPDATE dispatches
            SET status = %(status)s,
                last_error = %(error)s,
                message_meta = %(meta)s,
                sent_at = CASE WHEN %(status)s = 'sent' THEN now() ELSE sent_at END
            WHERE id = %(id)s
            """,
            {"status": status, "error": error, "meta": json.dumps(meta or {}), "id": dispatch_id},
        )
    conn.commit()


# --------------------------------------------------------------------------- #
# Discord webhook delivery
# --------------------------------------------------------------------------- #
async def send_embed(client: httpx.AsyncClient, webhook_url: str, embed: dict[str, Any]) -> dict[str, Any]:
    """Send one embed message with 429/5xx retry + exponential backoff."""
    payload = {"embeds": [embed]}
    # wait=true makes Discord respond 200 + the created message object (which
    # carries the message id) instead of a bodiless 204, so we can persist the
    # id for later edit/delete. Passed as a query param, not baked into the URL,
    # to leave the bearer token in the URL path untouched.
    params = {"wait": "true"}
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            resp = await client.post(webhook_url, params=params, json=payload)
        except httpx.HTTPError as exc:
            log.warning("Network error (attempt %d/%d): %s", attempt, MAX_ATTEMPTS, exc)
            await asyncio.sleep(min(2**attempt, 30))
            continue

        if resp.status_code in (200, 204):  # Discord success (200 + body when wait=true)
            message_id = None
            if resp.status_code == 200:
                try:
                    message_id = resp.json().get("id")
                except ValueError:  # non-JSON body; not expected with wait=true
                    message_id = None
            return {"ok": True, "message": "sent", "message_id": message_id}

        if resp.status_code == 429:  # rate limited -> honor Retry-After
            retry_after = float(resp.headers.get("Retry-After", 2**attempt))
            log.warning("Rate limited, retrying in %.1fs", retry_after)
            await asyncio.sleep(retry_after + 0.5)
            continue

        if 500 <= resp.status_code < 600:
            log.warning("Server error %d (attempt %d/%d)", resp.status_code, attempt, MAX_ATTEMPTS)
            await asyncio.sleep(min(2**attempt, 30))
            continue

        log.error("Webhook rejected payload: %d %s", resp.status_code, resp.text[:300])
        return {"ok": False, "message": f"HTTP {resp.status_code} {resp.text[:200]}"}

    return {"ok": False, "message": "max attempts reached"}


async def run_batch(database_url: str, webhook_url: str | None, limit: int, category: str | None, dry_run: bool, channel: str = "discord") -> int:
    if channel not in ALLOWED_CHANNELS:
        raise ValueError(
            f"channel {channel!r} not in {sorted(ALLOWED_CHANNELS)} "
            "(must match the dispatches.channel CHECK constraint)"
        )
    offers = []
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        offers = claim_offers(conn, channel=channel, limit=limit, category=category, dry_run=dry_run)

    if not offers:
        log.info("Nothing to dispatch.")
        return 0

    log.info("Dispatching %d offer(s)%s", len(offers), " [DRY RUN]" if dry_run else "")
    default_url = webhook_url or ""

    sent = 0
    async with httpx.AsyncClient(timeout=20.0) as client:
        for offer in offers:
            embed = build_embed(offer)
            if dry_run:
                print(json.dumps(embed, ensure_ascii=False, indent=2))
                sent += 1  # previewed; dry-run has no send-failure mode
                continue
            if not default_url:
                log.error("DISCORD_WEBHOOK_URL not set (and not --dry-run); aborting send.")
                raise SystemExit(2)

            target_url = resolve_webhook(offer.get("category") or "other", default_url)
            result = await send_embed(client, target_url, embed)
            with psycopg.connect(database_url) as conn:
                mark_dispatch(
                    conn,
                    dispatch_id=offer["_dispatch_id"],
                    status="sent" if result["ok"] else "failed",
                    error=None if result["ok"] else result["message"],
                    meta={
                        "title": offer["title"],
                        "url": offer.get("canonical_url") or offer["url"],
                        "webhook_env": CATEGORY_WEBHOOK_ENV.get(offer.get("category") or "", "default"),
                        "message_id": result.get("message_id"),
                    },
                )
            if result["ok"]:
                sent += 1
                log.info("Sent [%s → %s]: %s",
                         offer.get("category", "?"),
                         CATEGORY_WEBHOOK_ENV.get(offer.get("category") or "", "default"),
                         offer["title"][:80])
            else:
                log.error("Failed: %s -> %s", offer["title"][:80], result["message"])

            await asyncio.sleep(WEBHOOK_PACE_SECONDS)

    return sent


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main() -> int:
    parser = argparse.ArgumentParser(description="Dispatch verified freebie offers to a Discord webhook.")
    parser.add_argument("--dry-run", action="store_true", help="print embed JSON, send nothing")
    parser.add_argument("--category", choices=sorted(CATEGORY_STYLE), default=None)
    parser.add_argument("--limit", type=int, default=10, help="max offers per run (default 10)")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    database_url = env("DATABASE_URL")
    webhook_url = None if args.dry_run else env("DISCORD_WEBHOOK_URL")

    sent = asyncio.run(run_batch(database_url, webhook_url, args.limit, args.category, args.dry_run))
    log.info("Done. %d offer(s) handled.", sent)
    return 0


if __name__ == "__main__":
    sys.exit(main())