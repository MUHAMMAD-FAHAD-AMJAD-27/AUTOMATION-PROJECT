"""Dispatch-throughput fixes (Decision 3a).

Hermetic: no Postgres, no network, no LLM. A fake connection records the SQL
each function issues, so the ordering guarantees these fixes depend on are
asserted against the real statements instead of being described in a comment.

What is locked here:
  * dispatch runs on a slot that wrote nothing, as long as something is waiting
    (the old ``offers_written > 0`` gate skipped those slots outright — 10 of 55
    live runs took that path)
  * dispatch is skipped when the queue is genuinely empty
  * ``stats["dispatched"]`` reaches ``runs.stats`` (it used to be dropped)
  * the claim INSERT and the dry-run preview both order oldest-first, so a burst
    of new arrivals cannot re-bury older offers
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import crawler.pipeline as pipeline
import discord_dispatcher


class _FakeCursor:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.conn.executed.append((" ".join(str(sql).split()), params))

    def fetchone(self):
        return self.conn.fetchone_result

    def fetchall(self):
        return self.conn.fetchall_result


_DEFAULT = object()  # so a test can ask for fetchone() -> None explicitly


class _FakeConn:
    """Minimal psycopg.Connection stand-in that records every executed statement."""

    def __init__(self, fetchone_result=_DEFAULT, fetchall_result=_DEFAULT):
        self.executed: list[tuple[str, object]] = []
        self.commits = 0
        self.rollbacks = 0
        self.fetchone_result = {"id": 4242} if fetchone_result is _DEFAULT else fetchone_result
        self.fetchall_result = [] if fetchall_result is _DEFAULT else fetchall_result
        self.info = SimpleNamespace(dsn="postgresql://fake/fake")  # Deduplicator reads this

    def cursor(self, *a, **k):
        return _FakeCursor(self)

    def execute(self, *a, **k):        # Deduplicator._ensure_conn's "SELECT 1"
        return _FakeCursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _one_sql(conn, needle: str) -> str:
    """The single recorded statement containing `needle`; fails loudly otherwise."""
    hits = [sql for sql, _ in conn.executed if needle in sql]
    assert len(hits) == 1, f"expected 1 statement containing {needle!r}, got {len(hits)}"
    return hits[0]


def _params_for(conn, needle: str):
    return [p for sql, p in conn.executed if needle in sql][0]


# --------------------------------------------------------------------------- #
# 3a-1  the dispatch gate: has_undispatched_offers
# --------------------------------------------------------------------------- #
def test_has_undispatched_offers_true_when_queue_nonempty():
    assert pipeline.has_undispatched_offers(_FakeConn({"pending": True})) is True


def test_has_undispatched_offers_false_when_everything_sent():
    assert pipeline.has_undispatched_offers(_FakeConn({"pending": False})) is False


def test_has_undispatched_offers_treats_only_sent_as_done():
    """A pending/failed dispatch row must NOT count an offer as delivered — a row
    that failed to send has to stay visible to the gate or it is abandoned."""
    conn = _FakeConn({"pending": True})
    pipeline.has_undispatched_offers(conn, channel="discord")
    sql = _one_sql(conn, "SELECT EXISTS")
    assert "d.status = 'sent'" in sql
    assert "o.verification_status IN ('verified','live')" in sql
    assert "o.is_active" in sql
    assert _params_for(conn, "SELECT EXISTS") == ("discord",)


# --------------------------------------------------------------------------- #
# 3a-2  run_pipeline no longer ties dispatch to "did this run write anything"
# --------------------------------------------------------------------------- #
def _patch_pipeline(monkeypatch, *, queue_nonempty: bool):
    """Fake the DB + LLM so run_pipeline exercises only the gate + dispatch tail."""
    conn = _FakeConn()
    monkeypatch.setattr(pipeline.psycopg, "connect", lambda *a, **k: conn)
    monkeypatch.setattr(pipeline, "LLMExtractor", lambda *a, **k: SimpleNamespace())
    monkeypatch.setattr(pipeline, "fetch_unprocessed", lambda c, s, lim: [])
    monkeypatch.setattr(pipeline, "mark_run", lambda *a, **k: None)
    monkeypatch.setattr(pipeline, "has_undispatched_offers",
                        lambda c, *a, **k: queue_nonempty)
    dispatched = AsyncMock(return_value=7)
    monkeypatch.setattr(pipeline, "dispatch_new_offers", dispatched)
    persisted: list[tuple] = []
    monkeypatch.setattr(pipeline, "_repersist_run_stats",
                        lambda db, rid, status, stats: persisted.append((rid, status, dict(stats))))
    return conn, dispatched, persisted


def test_dispatch_runs_on_a_slot_that_wrote_nothing(monkeypatch):
    """THE regression this fixes. Zero offers written, but offers are waiting.

    Old gate: ``if dispatch and stats["offers_written"] > 0`` -> skipped, and the
    waiting offers sat until some later slot happened to write something.
    """
    _conn, dispatched, _persisted = _patch_pipeline(monkeypatch, queue_nonempty=True)
    stats = asyncio.run(pipeline.run_pipeline(dispatch=True))
    assert stats["offers_written"] == 0     # nothing new this slot ...
    dispatched.assert_awaited_once()        # ... and it dispatched regardless
    assert stats["dispatched"] == 7


def test_dispatch_skipped_when_nothing_is_waiting(monkeypatch):
    _conn, dispatched, persisted = _patch_pipeline(monkeypatch, queue_nonempty=False)
    stats = asyncio.run(pipeline.run_pipeline(dispatch=True))
    dispatched.assert_not_awaited()
    assert "dispatched" not in stats
    assert persisted == []                 # nothing happened, nothing to re-persist


def test_no_dispatch_flag_still_overrides_the_gate(monkeypatch):
    """--no-dispatch must stay absolute even with a full queue."""
    _conn, dispatched, _ = _patch_pipeline(monkeypatch, queue_nonempty=True)
    asyncio.run(pipeline.run_pipeline(dispatch=False))
    dispatched.assert_not_awaited()


def test_gate_query_failure_falls_back_to_offers_written(monkeypatch):
    """A broken gate query must not abort the run, and must not dispatch blindly."""
    _conn, dispatched, _ = _patch_pipeline(monkeypatch, queue_nonempty=True)

    def _boom(*a, **k):
        raise RuntimeError("gate query failed")

    monkeypatch.setattr(pipeline, "has_undispatched_offers", _boom)
    stats = asyncio.run(pipeline.run_pipeline(dispatch=True))
    assert stats["errors"] == 0             # run still completes
    dispatched.assert_not_awaited()         # offers_written == 0 -> fallback declines


# --------------------------------------------------------------------------- #
# 3a-3  stats["dispatched"] actually reaches runs.stats
# --------------------------------------------------------------------------- #
def test_dispatched_count_is_repersisted_into_run_stats(monkeypatch):
    """mark_run fires before dispatch, so without this re-persist the runs table
    recorded dispatched=NULL for every one of the 299 real sends."""
    _conn, _dispatched, persisted = _patch_pipeline(monkeypatch, queue_nonempty=True)
    asyncio.run(pipeline.run_pipeline(dispatch=True))
    assert len(persisted) == 1
    run_id, status, stats = persisted[0]
    assert run_id == 4242 and status == "success"
    assert stats["dispatched"] == 7


def test_dispatch_failure_is_recorded_and_repersisted(monkeypatch):
    _conn, dispatched, persisted = _patch_pipeline(monkeypatch, queue_nonempty=True)
    dispatched.side_effect = RuntimeError("webhook down")
    stats = asyncio.run(pipeline.run_pipeline(dispatch=True))
    assert "webhook down" in stats["dispatch_error"]
    assert persisted and "dispatch_error" in persisted[0][2]


# --------------------------------------------------------------------------- #
# 3a-4  oldest-first ordering, asserted against the real SQL
# --------------------------------------------------------------------------- #
def test_claim_insert_orders_oldest_first():
    """The step-1 INSERT decides WHICH offers get a dispatch row when more than
    `limit` are due. DESC meant only the newest `limit` ever got one, and every
    later run repeated that choice — older offers were re-buried indefinitely."""
    conn = _FakeConn(fetchall_result=[])
    discord_dispatcher.claim_offers(conn, channel="discord", limit=30, category=None)
    sql = _one_sql(conn, "INSERT INTO dispatches")
    assert "ORDER BY o.first_seen ASC" in sql
    assert "ORDER BY o.first_seen DESC" not in sql
    assert "LIMIT %(limit)s" in sql


def test_dry_run_preview_orders_oldest_first_and_stays_read_only():
    """The preview must show the same offers the live claim would pick, and must
    not insert, claim, or commit anything."""
    conn = _FakeConn(fetchall_result=[{"id": 1}])
    out = discord_dispatcher.claim_offers(
        conn, channel="discord", limit=30, category=None, dry_run=True
    )
    assert out == [{"id": 1}]
    sql = _one_sql(conn, "_dispatch_id")
    assert "o.first_seen ASC" in sql and "o.first_seen DESC" not in sql
    # read-only: one SELECT, no INSERT/UPDATE, rolled back, never committed
    assert len(conn.executed) == 1
    assert not any(w in s for s, _ in conn.executed for w in ("INSERT", "UPDATE"))
    assert conn.rollbacks == 1 and conn.commits == 0

