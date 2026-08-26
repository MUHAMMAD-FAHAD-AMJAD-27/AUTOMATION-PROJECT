"""
run_step3_tail.py — prove the scheduler slot's pipeline->dispatch->Discord tail
on the REAL raw_items already ingested by the (interrupted) all_deals slots.

This calls the exact same run_pipeline(...) the scheduler's _run_slot uses, so it
exercises: LLM extract -> url_hash + semantic dedup -> write offers -> dispatch to
Discord. Prints before/after counts, pipeline stats, dedup reject breakdown, and
newest offers. The Discord HTTP response is logged inline by discord_dispatcher.

Usage:
    cd "E:/AUTOMATION/automation system"
    PYTHONUTF8=1 python run_step3_tail.py
"""
from __future__ import annotations

import asyncio
import os

from dotenv import load_dotenv

load_dotenv(override=True)

import psycopg
from psycopg.rows import dict_row

DB = os.environ["DATABASE_URL"]


def counts(tag: str) -> dict[str, int]:
    conn = psycopg.connect(DB, row_factory=dict_row, connect_timeout=10)
    out: dict[str, int] = {}
    with conn.cursor() as cur:
        for t in ("raw_items", "offers", "dispatches"):
            cur.execute(f"SELECT count(*) AS n FROM {t}")
            out[t] = cur.fetchone()["n"]
    conn.close()
    print(f"[{tag}] raw_items={out['raw_items']}  offers={out['offers']}  dispatches={out['dispatches']}")
    return out


async def main() -> None:
    before = counts("BEFORE")

    from crawler.pipeline import run_pipeline
    stats = await run_pipeline(limit=30, dry_run=False, dispatch=True, require_liveness=True)
    print("PIPELINE STATS:", stats)

    after = counts("AFTER")
    print(
        f"DELTA: raw_items +{after['raw_items'] - before['raw_items']}  "
        f"offers +{after['offers'] - before['offers']}  "
        f"dispatches +{after['dispatches'] - before['dispatches']}"
    )

    conn = psycopg.connect(DB, row_factory=dict_row, connect_timeout=10)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT last_reject_reason, count(*) AS n FROM raw_items
            WHERE last_attempted_at > now() - interval '15 minutes'
            GROUP BY last_reject_reason ORDER BY n DESC
            """
        )
        print("RECENT REJECT REASONS (last 15 min):")
        for r in cur.fetchall():
            print(f"   {r['last_reject_reason']}: {r['n']}")

        cur.execute("SELECT id, title FROM offers ORDER BY id DESC LIMIT 6")
        print("NEWEST OFFERS:")
        for r in cur.fetchall():
            print(f"   #{r['id']}  {r['title']}")
    conn.close()


if __name__ == "__main__":
    asyncio.run(main())
