"""
OpenRouter free-model adapter
=============================
Polls the public OpenRouter model catalog and ingests models where both
prompt and completion pricing are zero (free-tier LLM API drops).

No authentication required — the /models endpoint is unauthenticated.
Pricing fields in the API response are strings ("0", "0.000001", etc.),
so we parse them as floats before comparison.

Usage:
    python -m adapters.openrouter_adapter
    python -m adapters.openrouter_adapter --dry-run
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from datetime import datetime, timezone

import httpx
from dotenv import load_dotenv

from crawler.db import connect, record_source_health, upsert_raw_item

load_dotenv(override=True)

log = logging.getLogger("adapter.openrouter")


def _health(source_id: int | None, ok: bool, error: str | None = None) -> None:
    """Record a source-health snapshot; no-op when source_id is unknown (dry-run)."""
    if source_id is None:
        return
    with connect() as conn:
        record_source_health(conn, source_id, ok=ok, error=error)

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
SOURCE_NAME = "openrouter:free-models"


def _is_free(pricing: dict) -> bool:
    """Return True when both prompt and completion cost are zero."""
    try:
        prompt = float(pricing.get("prompt", "1") or "1")
        completion = float(pricing.get("completion", "1") or "1")
        return prompt == 0.0 and completion == 0.0
    except (TypeError, ValueError):
        return False


def _model_to_payload(model: dict) -> dict:
    model_id: str = model.get("id", "")
    name: str = model.get("name") or model_id
    description: str = model.get("description") or ""
    context_length: int | None = model.get("context_length")

    desc_parts = [description] if description else []
    if context_length:
        desc_parts.append(f"Context: {context_length:,} tokens.")
    desc_parts.append("Free tier: $0 prompt + $0 completion via OpenRouter.")

    url = f"https://openrouter.ai/{model_id}"

    text_lines = [
        f"Free LLM API via OpenRouter: {name}",
        " ".join(desc_parts),
        f"Model ID: {model_id}",
        f"URL: {url}",
    ]

    return {
        "external_id": model_id,
        "text": "\n".join(filter(None, text_lines)),
        "urls": [url],
        "author_handle": "openrouter.ai",
        "published_at": datetime.now(timezone.utc).isoformat(),
        "engagement": {},
        "extra": {
            "model_id": model_id,
            "context_length": context_length,
            "pricing": model.get("pricing", {}),
            "source_adapter": "openrouter_adapter",
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
                (
                    SOURCE_NAME,
                    json.dumps({
                        "url": OPENROUTER_MODELS_URL,
                        "adapter": "openrouter_adapter",
                        "filter": "free_tier_only",
                    }),
                ),
            )
            row = cur.fetchone()
            if row:
                conn.commit()
                return row["id"]
            cur.execute("SELECT id FROM sources WHERE name = %s", (SOURCE_NAME,))
            return cur.fetchone()["id"]


async def run_openrouter(dry_run: bool = False) -> int:
    headers = {
        "User-Agent": "freebies-openrouter-adapter/1.0",
        "Accept": "application/json",
    }

    # Resolve source_id before the fetch so fetch/parse failures still get recorded.
    source_id = None if dry_run else _ensure_source()

    async with httpx.AsyncClient(follow_redirects=True, timeout=20.0) as client:
        try:
            resp = await client.get(OPENROUTER_MODELS_URL, headers=headers)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            log.error("OpenRouter fetch failed: %s", exc)
            _health(source_id, ok=False, error=f"fetch failed: {exc}")
            return 0

        try:
            data = resp.json()
        except json.JSONDecodeError as exc:
            log.error("OpenRouter response not valid JSON: %s", exc)
            _health(source_id, ok=False, error=f"invalid json: {exc}")
            return 0

    models: list[dict] = data.get("data", [])
    if not models:
        log.warning("OpenRouter response contained no models (got keys: %s)", list(data.keys()))
        _health(source_id, ok=False, error="empty model catalog")
        return 0

    free_models = [m for m in models if _is_free(m.get("pricing") or {})]
    log.info("OpenRouter: %d total models, %d free-tier", len(models), len(free_models))

    if dry_run:
        for m in free_models:
            payload = _model_to_payload(m)
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        return len(free_models)

    written = 0
    try:
        for m in free_models:
            payload = _model_to_payload(m)
            with connect() as conn:
                upsert_raw_item(conn, source_id, m["id"], payload)
            written += 1
    except Exception as exc:  # noqa: BLE001 — record the failure, don't crash the slot
        log.exception("OpenRouter ingest failed after %d writes", written)
        _health(source_id, ok=False, error=f"ingest failed: {exc}")
        return written

    _health(source_id, ok=True)
    log.info("OpenRouter adapter done: %d free models ingested", written)
    return written


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(description="OpenRouter free-model adapter")
    parser.add_argument("--dry-run", action="store_true",
                        help="print payloads only, no DB writes")
    args = parser.parse_args()

    asyncio.run(run_openrouter(dry_run=args.dry_run))


if __name__ == "__main__":
    main()

# ─── Source registration SQL ──────────────────────────────────────────────────
# INSERT INTO sources (name, kind, config) VALUES
#   ('openrouter:free-models', 'web',
#    '{"url":"https://openrouter.ai/api/v1/models","adapter":"openrouter_adapter","filter":"free_tier_only"}');
