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
}


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
            ORDER BY o.first_seen DESC
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
            """
            UPDATE dispatches
            SET status = %(status)s,
                attempts = attempts + 1,
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
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            resp = await client.post(webhook_url, json=payload)
        except httpx.HTTPError as exc:
            log.warning("Network error (attempt %d/%d): %s", attempt, MAX_ATTEMPTS, exc)
            await asyncio.sleep(min(2**attempt, 30))
            continue

        if resp.status_code == 204:  # Discord success
            return {"ok": True, "message": "sent"}

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


async def run_batch(database_url: str, webhook_url: str | None, limit: int, category: str | None, dry_run: bool) -> int:
    offers = []
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        offers = claim_offers(conn, channel="discord", limit=limit, category=category)

    if not offers:
        log.info("Nothing to dispatch.")
        return 0

    log.info("Dispatching %d offer(s)%s", len(offers), " [DRY RUN]" if dry_run else "")
    default_url = webhook_url or ""

    async with httpx.AsyncClient(timeout=20.0) as client:
        for offer in offers:
            embed = build_embed(offer)
            if dry_run:
                print(json.dumps(embed, ensure_ascii=False, indent=2))
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
                    },
                )
            if result["ok"]:
                log.info("Sent [%s → %s]: %s",
                         offer.get("category", "?"),
                         CATEGORY_WEBHOOK_ENV.get(offer.get("category") or "", "default"),
                         offer["title"][:80])
            else:
                log.error("Failed: %s -> %s", offer["title"][:80], result["message"])

            await asyncio.sleep(WEBHOOK_PACE_SECONDS)

    return len(offers)


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