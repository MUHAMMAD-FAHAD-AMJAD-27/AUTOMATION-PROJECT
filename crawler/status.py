"""
crawler/status.py — read-only system health summary (Phase 21).
================================================================
A single, side-effect-free snapshot of the live pipeline for manual
verification. Runs ONLY SELECTs against the database and reads the LLM
provider config from the environment; it never writes a row, never calls an
LLM/Discord endpoint, and never commits a transaction.

Run it with either:
    python -m crawler.status
    python run.py status

Prints:
  * offers      — total / active / breakdown by verification_status
  * dispatches  — sent / pending / failed (+ recent failure samples)
  * raw_items   — total / unprocessed (mirrors pipeline.fetch_unprocessed) /
                  permanently_rejected
  * providers   — each configured LLM provider's base_url + model ONLY
                  (api_key values are NEVER printed), plus the
                  LLM_DISTRIBUTION_ENABLED flag state
  * runs        — last successful run timestamp + recent runs + anything
                  worth attention (failed / partial / still-'running')

SAFETY: fully read-only. Safe to run any number of times, against the live
Neon database, with zero risk of mutating data.
"""
from __future__ import annotations

import sys
from urllib.parse import urlsplit

from crawler.db import connect, get_database_url
from crawler.verifier import _distribution_enabled, _load_providers


def _redacted_target() -> str:
    """host/dbname of DATABASE_URL, with any user:password stripped out."""
    try:
        p = urlsplit(get_database_url())
        return f"{p.hostname or '?'}{p.path or ''}"
    except Exception:  # noqa: BLE001 — cosmetic only
        return "(unparseable DATABASE_URL)"


def _one(cur, sql: str, params: tuple = ()) -> dict:
    cur.execute(sql, params)
    return cur.fetchone() or {}


def _all(cur, sql: str, params: tuple = ()) -> list[dict]:
    cur.execute(sql, params)
    return cur.fetchall()


def gather(conn) -> dict:
    """Collect the snapshot. SELECT-only; opens no transaction we commit."""
    data: dict = {}
    with conn.cursor() as cur:
        data["offers"] = _one(
            cur,
            "SELECT count(*) AS total, "
            "count(*) FILTER (WHERE is_active) AS active FROM offers",
        )
        data["offers_by_status"] = _all(
            cur,
            "SELECT verification_status AS status, count(*) AS n "
            "FROM offers GROUP BY verification_status ORDER BY n DESC",
        )
        data["dispatches"] = _all(
            cur,
            "SELECT status, count(*) AS n FROM dispatches "
            "GROUP BY status ORDER BY status",
        )
        data["dispatch_failures"] = _all(
            cur,
            "SELECT id, offer_id, attempts, last_error FROM dispatches "
            "WHERE status = 'failed' ORDER BY id DESC LIMIT 5",
        )
        # Mirror crawler.pipeline.fetch_unprocessed's eligibility predicate so the
        # 'unprocessed' count matches exactly what a pipeline run would pick up.
        data["raw_items"] = _one(
            cur,
            "SELECT "
            "  count(*) AS total, "
            "  count(*) FILTER (WHERE permanently_rejected) AS rejected, "
            "  count(*) FILTER ("
            "    WHERE s.enabled AND NOT r.permanently_rejected "
            "      AND NOT EXISTS (SELECT 1 FROM offers o WHERE o.raw_item_id = r.id)"
            "  ) AS unprocessed "
            "FROM raw_items r JOIN sources s ON s.id = r.source_id",
        )
        data["last_success"] = _one(
            cur,
            "SELECT id, flow_key, COALESCE(finished_at, started_at) AS ts "
            "FROM runs WHERE status = 'success' "
            "ORDER BY COALESCE(finished_at, started_at) DESC LIMIT 1",
        )
        data["runs_by_status"] = _all(
            cur,
            "SELECT status, count(*) AS n FROM runs GROUP BY status ORDER BY status",
        )
        data["recent_runs"] = _all(
            cur,
            "SELECT id, flow_key, status, started_at, finished_at "
            "FROM runs ORDER BY id DESC LIMIT 5",
        )
    return data


def render(data: dict) -> str:
    """Format the snapshot dict into a human-readable report string."""
    out: list[str] = []
    out.append("=" * 60)
    out.append("  FREEBIES PIPELINE - SYSTEM STATUS  (read-only snapshot)")
    out.append("=" * 60)
    out.append(f"DB: {_redacted_target()}  (connected OK)")

    o = data["offers"]
    out.append("")
    out.append("OFFERS")
    out.append(f"  total:  {o.get('total', 0)}     active: {o.get('active', 0)}")
    by = data["offers_by_status"]
    if by:
        breakdown = "  ".join(f"{r['status']}={r['n']}" for r in by)
        out.append(f"  by verification_status:  {breakdown}")

    out.append("")
    out.append("DISPATCHES")
    dmap = {r["status"]: r["n"] for r in data["dispatches"]}
    out.append(
        f"  sent: {dmap.get('sent', 0)}   "
        f"pending: {dmap.get('pending', 0)}   "
        f"failed: {dmap.get('failed', 0)}"
    )
    for f in data["dispatch_failures"]:
        err = (f.get("last_error") or "").splitlines()[0][:80]
        out.append(f"    - failed dispatch #{f['id']} (offer {f['offer_id']}, "
                   f"{f['attempts']} attempts): {err}")

    r = data["raw_items"]
    out.append("")
    out.append("RAW ITEMS")
    out.append(f"  total: {r.get('total', 0)}   "
               f"unprocessed (pipeline would pick up): {r.get('unprocessed', 0)}   "
               f"permanently_rejected: {r.get('rejected', 0)}")

    out.append("")
    out.append("LLM PROVIDERS  (base_url + model only - keys never shown)")
    providers = _load_providers()
    if not providers:
        out.append("  (none configured — LLM_API_KEY is unset)")
    for i, p in enumerate(providers):
        role = "primary " if i == 0 else f"fallback{i}"
        out.append(f"  [{i}] {role}  {p.base_url}   {p.model}")
    flag = "ON" if _distribution_enabled() else "OFF"
    out.append(f"  LLM_DISTRIBUTION_ENABLED: {flag}")

    out.append("")
    out.append("RUNS")
    ls = data["last_success"]
    if ls:
        out.append(f"  last success: run #{ls['id']} ({ls['flow_key']}) at {ls['ts']}")
    else:
        out.append("  last success: (none recorded)")
    rmap = {r["status"]: r["n"] for r in data["runs_by_status"]}
    out.append(f"  by status:  success={rmap.get('success', 0)}  "
               f"partial={rmap.get('partial', 0)}  "
               f"failed={rmap.get('failed', 0)}  "
               f"running={rmap.get('running', 0)}")
    if rmap.get("running", 0):
        out.append(f"  [!] {rmap['running']} run(s) still 'running' - should be 0 "
                   f"when no pipeline/scheduler is live (stale rows).")
    out.append("  recent:")
    for run in data["recent_runs"]:
        out.append(f"    #{run['id']:<4} {run['status']:<8} {run['flow_key']:<10} "
                   f"started {run['started_at']}  finished {run['finished_at']}")

    out.append("=" * 60)
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    """Connect read-only, gather the snapshot, print it. Returns exit code."""
    with connect() as conn:
        data = gather(conn)
    print(render(data))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
