"""
Telegram ingestion adapter — Telethon (MTProto) — Phase 2 scaffold
==================================================================

Responsibilities
----------------
* Incremental pull of messages from monitored channels (watermarked by
  `channel_cursors.last_message_id`), so every run only fetches the delta.
* Auto-join discovery: periodically searches for relevant channels/terms and
  registers candidates in `discovered_channels` for review.
* Flood-wait discipline: never fights Telegram. On FloodWaitError we sleep
  exactly the required seconds (capped) and resume.

Setup
-----
1. Get api_id / api_hash at https://my.telegram.org/apps
2. Export env vars (Heroku config vars):
     TG_API_ID, TG_API_HASH, TG_PHONE, TG_PASSWORD (2FA, optional),
     TG_SESSION_DIR (writable dir; Heroku /tmp OK for one-off runs,
     but use a mounted disk or keep sessions local for the always-on worker)
3. Register source rows in Postgres:
     INSERT INTO sources (name, kind, config) VALUES
       ('telegram:default', 'telegram',
        '{"delta_pull": true, "search_terms": ["free credits", "student pack", "free tier", "coupon"]}');

Dependencies: pip install telethon psycopg python-dotenv

Rate safety (non-negotiable):
    - sequential requests per account, ~1 req / 1-2s average
    - one account = one identity = one session file
    - cap backfill to N messages per channel per run (see BACKFILL_LIMIT)

Note: DB writes run via asyncio.to_thread on the sync psycopg helper to keep
dependencies minimal. Swap to asyncpg/psycopg_pool for higher throughput.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass

from telethon import TelegramClient
from telethon.errors import FloodWaitError, SessionPasswordNeededError
from telethon.sessions import StringSession
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.messages import GetHistoryRequest, SearchRequest
from telethon.tl.types import Message, InputMessagesFilterEmpty

from crawler.db import connect, record_source_health, upsert_raw_item

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
log = logging.getLogger("adapter.telegram")


def _health(source_id: int | None, ok: bool, error: str | None = None) -> None:
    """Record a source-health snapshot; no-op when source_id is unknown."""
    if source_id is None:
        return
    with connect() as conn:
        record_source_health(conn, source_id, ok=ok, error=error)


@dataclass(frozen=True)
class TelegramCredentials:
    """Validated TG_* credentials needed to open an MTProto session."""

    api_id: int
    api_hash: str
    phone: str
    session_dir: str
    session_string: str | None = None


# Must be present AND non-blank. Note crawler.db calls load_dotenv(override=True),
# so a blank value in .env overrides a real exported shell value — hence the
# emptiness check, not just a presence check.
_REQUIRED_ENV = ("TG_API_ID", "TG_API_HASH", "TG_PHONE")


def _load_credentials() -> TelegramCredentials | None:
    """Read and validate the TG_* credentials.

    Returns None instead of raising when any required value is missing, blank,
    or (for TG_API_ID) not an integer, so a Telegram misconfiguration degrades
    to "this one adapter is disabled" rather than taking down the caller.
    """
    blank = [key for key in _REQUIRED_ENV if not os.environ.get(key, "").strip()]
    if blank:
        log.warning(
            "Telegram adapter disabled: %s missing or blank in the environment",
            ", ".join(blank),
        )
        return None

    raw_api_id = os.environ["TG_API_ID"].strip()
    try:
        api_id = int(raw_api_id)
    except ValueError:
        log.warning("Telegram adapter disabled: TG_API_ID=%r is not an integer", raw_api_id)
        return None

    session_dir = os.environ.get("TG_SESSION_DIR", "./sessions")
    # Heroku's filesystem is ephemeral, so a file-based .session is wiped on every
    # dyno restart (~daily) — forcing a re-login the unattended dyno cannot perform.
    # TG_SESSION_STRING (a portable StringSession) is therefore the preferred auth
    # on Heroku; the file path stays as the local-dev fallback. A blank value
    # overrides a real exported shell value (crawler.db load_dotenv(override=True)),
    # so treat "" as unset — same rule as the _REQUIRED_ENV emptiness check.
    session_string = os.environ.get("TG_SESSION_STRING", "").strip() or None
    return TelegramCredentials(
        api_id=api_id,
        api_hash=os.environ["TG_API_HASH"].strip(),
        phone=os.environ["TG_PHONE"].strip(),
        session_dir=session_dir,
        session_string=session_string,
    )


def _build_client(creds: TelegramCredentials) -> TelegramClient:
    """Construct a TelegramClient from the appropriate session backend.

    Prefers an in-memory StringSession when TG_SESSION_STRING is set (survives
    Heroku's ephemeral filesystem); otherwise falls back to a file-based session
    under session_dir for local development. Constructing the client does NOT
    connect to Telegram — authentication happens later in client.start().
    """
    if creds.session_string:
        log.info("Telegram: authenticating via TG_SESSION_STRING (in-memory session)")
        return TelegramClient(StringSession(creds.session_string), creds.api_id, creds.api_hash)
    os.makedirs(creds.session_dir, exist_ok=True)
    log.info("Telegram: using file-based session at %s/ingest.session", creds.session_dir)
    return TelegramClient(f"{creds.session_dir}/ingest", creds.api_id, creds.api_hash)

BACKFILL_LIMIT = 50          # max messages pulled per channel per run (delta safety)
SEARCH_CADENCE_SECONDS = 3600  # how often auto-discovery scans run
MONITOR_CADENCE_SECONDS = 300  # delta-pull cadence (fallback when no event loop)

# Auto-discovery seed terms (Phase 3), grouped by theme. Broad on purpose: every
# discovered channel lands as 'new' and passes the manual approval gate before
# any join (see _join_candidates / TG_AUTO_JOIN), so wide coverage costs nothing.
# Single source of truth — used both to seed the telegram:default source config
# and to drive _discovery_scan.
DISCOVERY_SEED_TERMS: list[str] = [
    # core
    "free credits", "student pack", "free tier", "coupon", "developer discount",
    # compute / GPU (recurring-credit patterns like Modal's monthly grant)
    "gpu credits", "compute credits", "free api credits", "llm api free",
    # programs / bundles
    "startup credits", "founder program", "cloud credits", "student developer pack",
    # aggregator / perk drops
    "developer perks", "free saas", "open source credits",
]


def _channels_to_monitor() -> list[tuple[str, str, int]]:
    """Return (source_name, channel_username, last_message_id) rows."""
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT s.name AS source_name, c.channel_username, c.last_message_id
                FROM channel_cursors c
                JOIN sources s ON s.id = c.source_id
                WHERE s.enabled
                """
            )
            return [(r["source_name"], r["channel_username"], r["last_message_id"]) for r in cur.fetchall()]


def _bump_cursor(source_name: str, channel_username: str, last_message_id: int) -> None:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE channel_cursors SET last_message_id = %s, updated_at = now()
                WHERE channel_username = %s
                  AND source_id = (SELECT id FROM sources WHERE name = %s)
                """,
                (last_message_id, channel_username, source_name),
            )
        conn.commit()


def _register_channel(source_name: str, username: str, title: str | None, members: int | None) -> None:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO discovered_channels (source_id, channel_username, title, member_count, status)
                VALUES ((SELECT id FROM sources WHERE name = %s), %s, %s, %s, 'new')
                ON CONFLICT (channel_username) DO NOTHING
                """,
                (source_name, username, title, members),
            )
        conn.commit()


async def _process_message(client: TelegramClient, source_name: str, channel_username: str, msg: Message) -> None:
    """Normalize a Telegram message into a raw_item (links + text + entities)."""
    if msg.id is None:
        return
    payload = {
        "channel": channel_username,
        "message_id": msg.id,
        "date": msg.date.isoformat() if msg.date else None,
        "text": msg.message or "",
        "urls": _extract_urls(msg),
        "media": {"type": type(msg.media).__name__ if msg.media else None},
        "views": getattr(msg, "views", None),
        "forwards": getattr(msg, "forwards", None),
    }
    if payload["text"] or payload["urls"]:
        loop = asyncio.get_running_loop()
        with connect() as conn:
            await loop.run_in_executor(
                None,
                lambda: upsert_raw_item(conn, _source_id(source_name), str(msg.id), payload),
            )
        log.debug("raw_item[%s #%s] %s", channel_username, msg.id, (msg.message or "")[:60])


def _extract_urls(msg: Message) -> list[str]:
    urls: list[str] = []
    for ent in msg.entities or []:
        if hasattr(ent, "url") and ent.url:
            urls.append(ent.url)
    if msg.message:
        import re

        urls += re.findall(r"https?://\S+", msg.message)
    return list(dict.fromkeys(urls))  # de-dup, keep order


def _source_id(source_name: str) -> int:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM sources WHERE name = %s", (source_name,))
            row = cur.fetchone()
    if row is None:
        raise RuntimeError(f"source not registered: {source_name}")
    return row["id"]


async def _delta_pull(client: TelegramClient, source_name: str, username: str, min_id: int) -> int:
    """Fetch new messages since the watermark; returns the new max message id."""
    max_id = min_id
    source_id: int | None = None
    try:
        source_id = _source_id(source_name)
        result = await client(GetHistoryRequest(
            peer=username,
            limit=BACKFILL_LIMIT,
            offset_id=0,
            offset_date=None,
            add_offset=0,
            min_id=min_id,
            max_id=0,
            hash=0,
        ))
        for msg in reversed(result.messages):  # oldest -> newest
            await _process_message(client, source_name, username, msg)
            if msg.id:
                max_id = max(max_id, msg.id)
        _health(source_id, ok=True)
    except FloodWaitError as exc:
        log.warning("FloodWait %ss on %s — sleeping", exc.seconds, username)
        await asyncio.sleep(min(exc.seconds, 900))
    except Exception as exc:  # noqa: BLE001 — adapter must never kill the loop
        log.exception("delta_pull failed for %s: %s", username, exc)
        _health(source_id, ok=False, error=str(exc))
    return max_id


async def _discovery_scan(client: TelegramClient, source_name: str, terms: list[str]) -> None:
    """Search Telegram for relevant channels and register candidates."""
    source_id = _source_id(source_name)
    for term in terms:
        try:
            result = await client(SearchRequest(
                q=term,
                limit=10,
                filters=InputMessagesFilterEmpty(),
                min_date=None,
                max_date=None,
                offset_id=0,
                add_offset=0,
                max_id=0,
                min_id=0,
                hash=0,
            ))
            for chat in result.chats:
                username = getattr(chat, "username", None)
                if not username:
                    continue
                await _register_channel(
                    source_name,
                    username,
                    getattr(chat, "title", None),
                    getattr(chat, "participants_count", None),
                )
            await asyncio.sleep(3)  # be gentle between searches
        except FloodWaitError as exc:
            await asyncio.sleep(min(exc.seconds, 900))
        except Exception as exc:  # noqa: BLE001
            log.exception("discovery scan failed for term %r", term)
            _health(source_id, ok=False, error=f"discovery scan failed for {term!r}: {exc}")


def _auto_join_enabled() -> bool:
    """Whether the discovery auto-join step may run. Default OFF.

    Two-stage human gate so nothing is ever joined blindly: discovery writes
    candidates as 'new' (never joined) -> a human promotes chosen rows to
    'approved' (see approve_channel / the `approve-channel` CLI) -> only then,
    and only when TG_AUTO_JOIN is on, does _join_candidates join them.
    """
    return os.environ.get("TG_AUTO_JOIN", "0").strip().lower() in ("1", "true", "yes", "on")


async def _join_candidates(client: TelegramClient, source_name: str) -> None:
    """Join channels a human has promoted to status='approved'.

    Gated: returns immediately unless TG_AUTO_JOIN is on, and only ever joins
    'approved' rows — never the 'new' rows discovery writes. This is the
    opt-in review loop (discovery -> manual approval -> join).
    """
    if not _auto_join_enabled():
        log.info("Auto-join disabled (TG_AUTO_JOIN off); leaving approved channels unjoined")
        return
    source_id = _source_id(source_name)
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT d.channel_username FROM discovered_channels d
                JOIN sources s ON s.id = d.source_id
                WHERE d.status = 'approved' AND s.name = %s
                LIMIT 5
                """,
                (source_name,),
            )
            candidates = [r["channel_username"] for r in cur.fetchall()]
    for username in candidates:
        try:
            await client(JoinChannelRequest(username))
            log.info("Joined %s", username)
            with connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE discovered_channels SET status='joined' WHERE channel_username=%s",
                        (username,),
                    )
                conn.commit()
            # create cursor row so the next delta pull picks it up
            with connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO channel_cursors (source_id, channel_username)
                        VALUES ((SELECT id FROM sources WHERE name=%s), %s)
                        ON CONFLICT (source_id, channel_username) DO NOTHING
                        """,
                        (source_name, username),
                    )
                conn.commit()
            await asyncio.sleep(2)
        except FloodWaitError as exc:
            await asyncio.sleep(min(exc.seconds, 900))
        except Exception as exc:  # noqa: BLE001
            log.warning("join failed for %s: %s", username, exc)
            _health(source_id, ok=False, error=f"join failed for {username}: {exc}")


def list_discovered(status: str | None = None) -> list[dict]:
    """Return discovered channels (optionally filtered by status), newest first.

    Read-only. Backs the `list-discovered` review CLI so a human can see what
    auto-discovery has queued before approving anything.
    """
    with connect() as conn:
        with conn.cursor() as cur:
            if status:
                cur.execute(
                    "SELECT id, channel_username, title, member_count, status, discovered_at "
                    "FROM discovered_channels WHERE status = %s ORDER BY id DESC",
                    (status,),
                )
            else:
                cur.execute(
                    "SELECT id, channel_username, title, member_count, status, discovered_at "
                    "FROM discovered_channels ORDER BY id DESC"
                )
            return list(cur.fetchall())


def approve_channel(channel_id: int) -> bool:
    """Promote one discovered channel 'new' -> 'approved'.

    Returns True iff a row changed (a 'new' row with that id existed). Approved
    rows are eligible for joining, but only when TG_AUTO_JOIN is on.
    """
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE discovered_channels SET status = 'approved' "
                "WHERE id = %s AND status = 'new'",
                (channel_id,),
            )
            changed = cur.rowcount
        conn.commit()
    return changed > 0


async def run_telegram_monitor() -> None:
    """Main loop: delta pulls + discovery on a cadence (worker dyno entrypoint).

    For a 3x/day batch model instead, call `sync_once()` from the pipeline
    one-off job — no always-on dyno needed.
    """
    creds = _load_credentials()
    if creds is None:
        log.warning("run_telegram_monitor: skipping Telegram monitor — no usable credentials")
        return

    client = _build_client(creds)
    await client.start(phone=creds.phone, password=lambda: os.environ.get("TG_PASSWORD", ""))
    me = await client.get_me()
    log.info("Connected as %s (id=%s)", me.username, me.id)

    # Register the default source if missing.
    default_config = json.dumps({"delta_pull": True, "search_terms": DISCOVERY_SEED_TERMS})
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO sources (name, kind, config)
                VALUES ('telegram:default', 'telegram', %s::jsonb)
                ON CONFLICT (name) DO NOTHING
                """,
                (default_config,),
            )
        conn.commit()

    source_name = "telegram:default"
    last_discovery = 0.0

    while True:
        channels = _channels_to_monitor()
        for source_name, username, min_id in channels:
            new_max = await _delta_pull(client, source_name, username, min_id)
            if new_max > min_id:
                await _bump_cursor(source_name, username, new_max)

        await _join_candidates(client, source_name)

        if asyncio.get_event_loop().time() - last_discovery > SEARCH_CADENCE_SECONDS:
            await _discovery_scan(client, source_name, DISCOVERY_SEED_TERMS)
            last_discovery = asyncio.get_event_loop().time()

        await asyncio.sleep(MONITOR_CADENCE_SECONDS)


async def sync_once() -> None:
    """One-shot batch sync for Heroku Scheduler (3x/day) — same logic as the
    always-on monitor but exits after a single pass over all tracked channels.
    Call this from the pipeline one-off job instead of run_telegram_monitor().
    """
    creds = _load_credentials()
    if creds is None:
        log.warning("sync_once: skipping Telegram sync — no usable credentials")
        return

    client = _build_client(creds)
    await client.start(phone=creds.phone, password=lambda: os.environ.get("TG_PASSWORD", ""))
    me = await client.get_me()
    log.info("sync_once connected as %s", me.username)

    source_name = "telegram:default"
    channels = _channels_to_monitor()
    for src, username, min_id in channels:
        new_max = await _delta_pull(client, src, username, min_id)
        if new_max > min_id:
            await _bump_cursor(src, username, new_max)

    await _join_candidates(client, source_name)
    await client.disconnect()
    log.info("sync_once complete — %d channels processed", len(channels))