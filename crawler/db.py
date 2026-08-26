"""Shared DB helpers for the freebies pipeline (psycopg 3, dict rows)."""
from __future__ import annotations

import os

import psycopg
from psycopg.rows import dict_row
from dotenv import load_dotenv

# load_dotenv(override=True) lets .env win over the real shell/host environment.
# An EMPTY placeholder line in .env (e.g. `SERPER_API_KEY=`) therefore silently
# BLANKS a value the operator exported in the shell — the same footgun class as
# DATABASE_URL. Capture the shell value first and restore it if (and only if)
# .env blanked it; a genuine non-empty .env value still wins, so normal override
# semantics are unchanged.
#
# SCOPE: only SERPER_API_KEY is guarded here. The same hazard affects every other
# key that ships EMPTY in .env — TG_API_ID, TG_API_HASH, TG_PHONE, TG_PASSWORD,
# GITHUB_TOKEN, DASHBOARD_API_KEY — and the other load_dotenv(override=True)
# call sites (scheduler.py, crawler/verifier.py). Those are intentionally left
# alone for now; extending this guard to them is a separate change.
_shell_serper = os.environ.get("SERPER_API_KEY")
load_dotenv(override=True)
if (_shell_serper or "").strip() and not (os.environ.get("SERPER_API_KEY") or "").strip():
    os.environ["SERPER_API_KEY"] = _shell_serper


def get_database_url() -> str:
    url = os.environ.get("DATABASE_URL", "postgresql://freebies:freebies@localhost:5432/freebies")
    # Heroku exposes postgres:// (libpq scheme); psycopg accepts it directly.
    return url


def connect() -> psycopg.Connection:
    conn = psycopg.connect(get_database_url(), row_factory=dict_row)
    return conn


def record_source_health(
    conn: psycopg.Connection,
    source_id: int,
    ok: bool,
    error: str | None = None,
) -> None:
    """Merge a health snapshot into sources.health (JSONB).

    Persists last-success and last-error independently so a source that succeeds
    then fails still shows both timestamps. ``consecutive_failures`` resets to 0 on
    success and increments on failure — a simple circuit-breaker signal the
    dashboard reads. Best-effort: never let health bookkeeping abort an adapter.
    """
    try:
        with conn.cursor() as cur:
            if ok:
                cur.execute(
                    """
                    UPDATE sources
                    SET health = COALESCE(health, '{}'::jsonb) || jsonb_build_object(
                            'last_success', now()::text,
                            'consecutive_failures', 0
                        ),
                        updated_at = now()
                    WHERE id = %s
                    """,
                    (source_id,),
                )
            else:
                cur.execute(
                    """
                    UPDATE sources
                    SET health = COALESCE(health, '{}'::jsonb) || jsonb_build_object(
                            'last_error', now()::text,
                            'last_error_message', %s,
                            'consecutive_failures',
                                COALESCE((health->>'consecutive_failures')::int, 0) + 1
                        ),
                        updated_at = now()
                    WHERE id = %s
                    """,
                    (error or "unknown error", source_id),
                )
        conn.commit()
    except Exception:  # noqa: BLE001 — health bookkeeping must never kill an adapter
        conn.rollback()


def upsert_raw_item(conn: psycopg.Connection, source_id: int, external_id: str, payload: dict) -> int:
    """Insert a raw item; returns its DB id (existing row if duplicate)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO raw_items (source_id, external_id, raw_payload)
            VALUES (%s, %s, %s)
            ON CONFLICT (source_id, external_id) DO UPDATE
                SET raw_payload = EXCLUDED.raw_payload, fetched_at = now()
            RETURNING id
            """,
            (source_id, external_id, psycopg.types.json.Jsonb(payload)),
        )
        row = cur.fetchone()
    conn.commit()
    return row["id"]