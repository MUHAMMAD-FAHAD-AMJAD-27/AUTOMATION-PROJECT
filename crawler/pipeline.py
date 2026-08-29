"""
crawler/pipeline.py — end-to-end orchestrator
==============================================
Stage 4: reads unprocessed raw_items, normalizes, verifies (LLM + liveness),
deduplicates, writes canonical offers + fingerprints, then hands new offers
to the Discord dispatcher.

CLI (run from the project root, E:\\AUTOMATION\\automation system\\):
    python -m crawler.pipeline --dry-run                # full flow, no writes, no sends
    python -m crawler.pipeline                           # process everything due
    python -m crawler.pipeline --source telegram:default # one source only
    python -m crawler.pipeline --limit 25                # cap items per run
    python -m crawler.pipeline --no-dispatch             # skip Discord send
    python -m crawler.pipeline --require-liveness=false  # accept unreachable URLs

Environment: DATABASE_URL, LLM_API_KEY (+ optional LLM_BASE_URL / LLM_MODEL /
LLM_EMBED_MODEL), DISCORD_WEBHOOK_URL (only when dispatching).
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any

import httpx
import psycopg
from psycopg.rows import dict_row

from crawler.normalizer import CanonicalURL, URLCanonicalizer, normalize_raw_item
from crawler.provider_scheduler import SchedulerConfig
from crawler.verifier import (
    Deduplicator,
    LLMExtractor,
    LivenessProbe,
    NO_VERDICT,
    VerificationVerdict,
    _distribution_enabled,
    clean_or_fallback,
    heuristic_prefilter,
    verify_item,
    sha256_hex,
)

BATCH_SIZE = 10   # items per LLM extraction call — cuts API calls ~90% vs. 1-per-item
MAX_ATTEMPTS = 3  # after this many failed pipeline passes, a raw_item is dead-lettered

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
log = logging.getLogger("pipeline")

# SECURITY: suppress httpx's INFO "HTTP Request: <full url>" line. The pipeline
# hands off to discord_dispatcher in-process, and a Discord webhook URL is itself
# a credential — at INFO it would be written to stdout on every dispatch. Also
# keeps canonicalizer/liveness probe URLs out of the log. App INFO is unaffected.
logging.getLogger("httpx").setLevel(logging.WARNING)


# --------------------------------------------------------------------------- #
# DB helpers
# --------------------------------------------------------------------------- #
def fetch_unprocessed(conn: psycopg.Connection, source: str | None, limit: int) -> list[dict]:
    """raw_items with no offers row yet (left join), newest first."""
    sql = """
        SELECT r.id, r.source_id, r.external_id, r.raw_payload, r.fetched_at,
               s.name AS source_name, s.kind AS source_kind
        FROM raw_items r
        JOIN sources s ON s.id = r.source_id
        WHERE s.enabled
          AND NOT r.permanently_rejected
          AND NOT EXISTS (SELECT 1 FROM offers o WHERE o.raw_item_id = r.id)
    """
    params: list[Any] = []
    if source:
        sql += " AND s.name = %s"
        params.append(source)
    sql += " ORDER BY r.fetched_at DESC LIMIT %s"
    params.append(limit)
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return list(cur.fetchall())


def mark_raw_item_attempt(
    conn: psycopg.Connection,
    raw_item_id: int,
    reason: str,
    permanent: bool = False,
) -> None:
    """Record that a raw_item was processed but produced no offer.

    Increments ``attempts`` and stamps the reason. Flips ``permanently_rejected``
    when the verdict is terminal (``permanent=True``) or the item has now been
    tried ``MAX_ATTEMPTS`` times. Without this, non-offer items are re-fetched and
    re-run through the LLM on every future run — the core budget leak Phase 2 fixes.

    Best-effort: never let bookkeeping abort the pipeline. Commits its own row so
    the mark survives even if a later item in the run throws.
    """
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE raw_items
                SET attempts = attempts + 1,
                    last_attempted_at = now(),
                    last_reject_reason = %s,
                    permanently_rejected = (%s OR attempts + 1 >= %s)
                WHERE id = %s
                """,
                (reason, permanent, MAX_ATTEMPTS, raw_item_id),
            )
        conn.commit()
    except Exception:  # noqa: BLE001 — bookkeeping must never kill the run
        log.exception("mark_raw_item_attempt failed for raw_item_id=%s", raw_item_id)
        conn.rollback()


def write_offer(
    conn: psycopg.Connection,
    verdict: VerificationVerdict,
    normalized,
) -> int | None:
    """Insert the canonical offer + fingerprint. Returns the new offer id."""
    offer = verdict.offer
    assert offer is not None
    canonical = verdict.canonical
    embedding = getattr(offer, "_embedding", None)

    requirements = offer.requirements.model_dump()
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO offers (raw_item_id, url, canonical_url, title, description,
                                category, offer_type, value, currency, expires_at,
                                requirements, author_handle, engagement,
                                verification_status, confidence, is_active, raw)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING id
            """,
            (
                normalized.raw_item_id,
                canonical.original if canonical else offer.url,
                canonical.canonical if canonical else offer.url,
                offer.title,
                offer.description,
                offer.category,
                offer.offer_type,
                offer.value,
                offer.currency,
                offer.expires_at,
                psycopg.types.json.Jsonb(requirements),
                normalized.author_handle,
                psycopg.types.json.Jsonb(normalized.engagement),
                "verified" if verdict.liveness and verdict.liveness.ok else "unconfirmed",
                round(offer.confidence, 3),
                True,
                psycopg.types.json.Jsonb(
                    {
                        "reasons": offer.reasons,
                        "liveness": verdict.liveness.status if verdict.liveness else None,
                        "http_status": verdict.liveness.http_status if verdict.liveness else None,
                        "promo_code": offer.promo_code,
                        "base_url": offer.base_url,
                        "github_repo": offer.github_repo,
                        "exact_steps": offer.exact_steps,
                        "is_evergreen": offer.is_evergreen,
                        "verification": offer.verification,
                        "eligibility_required": offer.eligibility_required,
                    }
                ),
            ),
        )
        offer_id = cur.fetchone()["id"]

        cur.execute(
            """
            INSERT INTO offer_fingerprints (offer_id, url_hash, title_hash, embedding)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (offer_id) DO NOTHING
            """,
            (
                offer_id,
                sha256_hex(canonical.canonical if canonical else offer.url),
                sha256_hex(offer.title.lower()),
                embedding or [],
            ),
        )
    return offer_id


def mark_run(conn: psycopg.Connection, run_id: int, status: str, stats: dict) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE runs SET status=%s, finished_at=now(), stats=%s WHERE id=%s
            """,
            (status, psycopg.types.json.Jsonb(stats), run_id),
        )
    conn.commit()


def has_undispatched_offers(conn: psycopg.Connection, channel: str = "discord") -> bool:
    """True when at least one dispatchable offer has never been sent on ``channel``.

    This is the dispatch gate. It replaces ``stats["offers_written"] > 0``, which
    tied dispatching to *this* run happening to write a new offer: in a slot that
    wrote nothing the dispatch stage was skipped entirely, so an offer that missed
    an earlier slot's limit waited for the next slot that happened to write
    something. Measured on live data, 10 of 55 runs wrote zero offers and
    therefore dispatched nothing, which is a direct contributor to the 66-hour
    worst-case verified-to-sent latency (median was ~4 minutes).

    Cheap by design: a single EXISTS that short-circuits on the first hit, so the
    common "nothing waiting" case costs one index probe per run.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT EXISTS (
                SELECT 1
                FROM offers o
                WHERE o.verification_status IN ('verified','live')
                  AND o.is_active
                  AND NOT EXISTS (
                      SELECT 1 FROM dispatches d
                      WHERE d.offer_id = o.id
                        AND d.channel  = %s
                        AND d.status   = 'sent'
                  )
            ) AS pending
            """,
            (channel,),
        )
        row = cur.fetchone()
    return bool(row and row["pending"])


def _repersist_run_stats(db_url: str, run_id: int, status: str, stats: dict) -> None:
    """Re-write ``runs.stats`` after the dispatch stage has run.

    ``mark_run`` fires *before* dispatch on purpose, so a crash mid-dispatch still
    leaves a finished audit row. The side effect was that ``stats["dispatched"]``,
    which only exists after dispatch returns, never reached the table — every
    ``runs`` row had ``dispatched`` absent/NULL, and the DB could not answer "how
    much did this run actually send?" despite 299 real sends.

    Uses its own short-lived connection rather than holding the pipeline
    connection open across the paced webhook sends (2.5 s per message), which on
    Neon would risk an idle-timeout disconnect. Best-effort: bookkeeping must
    never turn a successful dispatch into a failed run.
    """
    try:
        with psycopg.connect(db_url, row_factory=dict_row, connect_timeout=5) as conn:
            mark_run(conn, run_id, status, stats)
    except Exception:  # noqa: BLE001 — metrics must never kill the run
        log.exception("could not re-persist dispatch stats for run_id=%s", run_id)


# --------------------------------------------------------------------------- #
# Discord dispatch (in-process invocation of the dispatcher module)
# --------------------------------------------------------------------------- #
async def dispatch_new_offers(dry_run: bool, limit: int = 10) -> int:
    """Import and run the standalone dispatcher against the same DB.

    Awaited directly (no nested asyncio.run) because run_pipeline is already
    executing inside an event loop — asyncio.run() cannot be called from
    within a running loop.
    """
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import discord_dispatcher

    webhook = None if dry_run else os.environ.get("DISCORD_WEBHOOK_URL", "")
    if not dry_run and not webhook:
        log.error("DISCORD_WEBHOOK_URL not set — skipping dispatch stage")
        return 0
    return await discord_dispatcher.run_batch(
        database_url=os.environ.get(
            "DATABASE_URL", "postgresql://freebies:***@localhost:5432/freebies"
        ),
        webhook_url=webhook or None,
        limit=limit,
        category=None,
        dry_run=dry_run,
    )


# --------------------------------------------------------------------------- #
# Stage B: batched LLM extraction + sequential write
# --------------------------------------------------------------------------- #
async def _process_batch_result(
    conn: psycopg.Connection,
    deduplicator: Deduplicator,
    extractor: LLMExtractor,
    chunk: list[tuple[dict, Any, CanonicalURL, Any]],
    offers_or_exc: list | BaseException,
    stats: dict,
    dry_run: bool,
) -> None:
    """Write one chunk's extraction result to the DB — strictly sequential.

    ``offers_or_exc`` is either the per-item offers list returned by
    ``extract_batch`` (positionally aligned to ``chunk``) or the ``Exception``
    that batch raised. Both the flag-off and flag-on paths funnel through this
    single helper, so the dedup/embed/write body and the whole-batch-failure
    handling are defined exactly once. The caller invokes it with ``await`` one
    chunk at a time, in chunk order, keeping the write stage sequential and each
    chunk's ``(chunk, offers)`` pairing intact regardless of how extraction ran.
    """
    if isinstance(offers_or_exc, BaseException):
        exc = offers_or_exc
        stats["errors"] += len(chunk)
        # log.error(exc_info=…) renders the same traceback as the previous
        # inline log.exception(); used here because in the concurrent path the
        # exception is captured by gather() rather than active in an except block.
        log.error(
            "batch extraction failed for chunk starting raw_item_id=%s",
            chunk[0][0]["id"], exc_info=exc,
        )
        # Whole-batch failure is usually a transient provider outage —
        # retry (permanent=False); MAX_ATTEMPTS dead-letters if it persists.
        if not dry_run:
            for row, *_ in chunk:
                mark_raw_item_attempt(
                    conn, row["id"], f"batch_error:{type(exc).__name__}", permanent=False
                )
        return

    offers = offers_or_exc
    for (row, normalized, primary, live), offer in zip(chunk, offers):
        try:
            if offer is NO_VERDICT:
                # No LLM verdict could be obtained (no providers, or every
                # provider failed). The item was never actually evaluated, so
                # retry (permanent=False) — MAX_ATTEMPTS still dead-letters it
                # if the outage persists. Distinct from a real rejection below.
                stats["llm_unavailable"] += 1
                if not dry_run:
                    mark_raw_item_attempt(conn, row["id"], "llm_unavailable", permanent=False)
                continue

            if offer is None:
                stats["llm_rejected"] += 1
                # The LLM evaluated this item and judged it NOT an offer — a
                # verdict that won't change on retry. Dead-letter immediately
                # (permanent=True) instead of burning MAX_ATTEMPTS batch calls
                # re-asking the same question every future run.
                if not dry_run:
                    mark_raw_item_attempt(conn, row["id"], "llm_rejected", permanent=True)
                continue

            canonical_url = primary.canonical or clean_or_fallback(primary.final)
            dup = deduplicator.check(canonical_url)
            if dup.is_dup:
                stats["dup"] += 1
                # A dup stays a dup — dead-letter so we never re-extract it.
                if not dry_run:
                    mark_raw_item_attempt(conn, row["id"], "dup:url", permanent=True)
                continue

            embedding = None
            embeds = await extractor.embed([f"{offer.title}\n{offer.description or ''}"])
            if embeds:
                embedding = embeds[0][:256]
                dup = deduplicator.check_semantic(embedding)
                if dup.is_dup:
                    stats["dup"] += 1
                    if not dry_run:
                        mark_raw_item_attempt(conn, row["id"], "dup:semantic", permanent=True)
                    continue

            if live.ok:
                offer.confidence = min(1.0, offer.confidence * 1.05)
            if live.status == "unreachable":
                offer.confidence = max(0.0, offer.confidence * 0.7)

            verdict = VerificationVerdict(offer, live, dup, primary)
            verdict.offer.__dict__["_embedding"] = embedding

            if dry_run:
                log.info("[dry-run] would write: %r -> %s", offer.title, offer.url)
                stats["offers_written"] += 1
            else:
                offer_id = write_offer(conn, verdict, normalized)
                if offer_id:
                    stats["offers_written"] += 1
                    log.info("offer #%s: %s", offer_id, offer.title)
        except Exception as exc:  # noqa: BLE001 — one bad item never kills the run
            stats["errors"] += 1
            log.exception("item failed (post-extract): raw_item_id=%s", row["id"])
            if not dry_run:
                mark_raw_item_attempt(
                    conn, row["id"], f"error:{type(exc).__name__}", permanent=False
                )


async def _extract_chunks_concurrent(
    extractor: LLMExtractor,
    chunks: list[list[tuple[dict, Any, CanonicalURL, Any]]],
) -> list:
    """Run each chunk's ``extract_batch`` concurrently, bounded by a semaphore.

    Used only on the distribution flag-ON path. The cap is
    ``LLM_MAX_CONCURRENT_BATCHES`` (SchedulerConfig default 3). ``asyncio.gather``
    preserves input order, so the returned list is positionally aligned to
    ``chunks``; ``return_exceptions=True`` turns a single failed batch into that
    element's value instead of cancelling the others, so the sequential write
    stage can pair results back with ``zip(chunks, results)`` and dead-letter
    just the failed chunk. Only extraction is concurrent here — the write stage
    the caller runs afterwards stays strictly sequential.
    """
    cap = max(1, SchedulerConfig.from_env().max_concurrent_batches)
    sem = asyncio.Semaphore(cap)

    async def _one(chunk):
        async with sem:
            return await extractor.extract_batch(
                [(normalized, primary) for _, normalized, primary, _ in chunk]
            )

    return await asyncio.gather(
        *(_one(chunk) for chunk in chunks), return_exceptions=True
    )


async def _run_stage_b(
    conn: psycopg.Connection,
    deduplicator: Deduplicator,
    extractor: LLMExtractor,
    survivors: list[tuple[dict, Any, CanonicalURL, Any]],
    stats: dict,
    dry_run: bool,
) -> None:
    """Batched LLM extraction (BATCH_SIZE items/call) + sequential write.

    Distribution OFF (default): byte-for-byte the pre-Phase-20 behavior —
    extract one chunk, write that chunk, move to the next. Extraction and the
    write stage stay interleaved and strictly sequential.

    Distribution ON (``LLM_DISTRIBUTION_ENABLED`` truthy): the ProviderScheduler
    spreads load across providers, so up to ``LLM_MAX_CONCURRENT_BATCHES``
    extraction calls run concurrently (``asyncio.gather`` + ``Semaphore``). The
    write/dedup/embed stage still runs strictly sequentially in chunk order via
    the shared ``_process_batch_result`` helper, and each chunk keeps its
    ``(chunk, offers)`` pairing, so DB write ordering and dedup semantics are
    identical to the sequential path.
    """
    chunks = [survivors[i: i + BATCH_SIZE]
              for i in range(0, len(survivors), BATCH_SIZE)]

    if _distribution_enabled():
        results = await _extract_chunks_concurrent(extractor, chunks)
        for chunk, offers_or_exc in zip(chunks, results):
            await _process_batch_result(
                conn, deduplicator, extractor, chunk, offers_or_exc, stats, dry_run
            )
    else:
        for chunk in chunks:
            try:
                offers = await extractor.extract_batch(
                    [(normalized, primary) for _, normalized, primary, _ in chunk]
                )
            except Exception as exc:  # noqa: BLE001 — one bad batch never kills the run
                offers = exc
            await _process_batch_result(
                conn, deduplicator, extractor, chunk, offers, stats, dry_run
            )


async def _run_ingest_stages(
    conn: psycopg.Connection,
    extractor: LLMExtractor,
    rows: list[dict],
    stats: dict,
    dry_run: bool,
    require_liveness: bool,
) -> None:
    """Stage A (normalize + prefilter + liveness) then Stage B (LLM extract + write).

    Lifted verbatim out of ``run_pipeline`` so the dispatch stage can be reached
    on runs that fetched nothing. This body used to sit inline *after* an early
    ``return stats`` that fired when ``rows`` was empty, which meant a slot with
    no new raw_items skipped dispatch entirely — even with offers sitting
    undispatched. Behaviour for a non-empty ``rows`` is unchanged.
    """
    deduplicator = Deduplicator(conn)
    async with httpx.AsyncClient(follow_redirects=True) as client:
        canonicalizer = URLCanonicalizer(client)
        probe = LivenessProbe(client)

        # --- Stage A: normalize + local regex pre-filter + liveness --- #
        # Cheap, per-item, no LLM cost. Survivors are queued for the
        # batched LLM extraction stage below.
        survivors: list[tuple[dict, Any, CanonicalURL, Any]] = []  # (row, normalized, primary, live)
        for row in rows:
            try:
                normalized = normalize_raw_item(row)
                if not normalized.urls:
                    stats["no_url"] += 1
                    # No URL can ever become an offer — dead-letter immediately.
                    if not dry_run:
                        mark_raw_item_attempt(conn, row["id"], "no_url", permanent=True)
                    continue

                prefilter_reason = heuristic_prefilter(normalized)
                if prefilter_reason:
                    stats["prefiltered"] = stats.get("prefiltered", 0) + 1
                    log.debug("prefiltered raw_item %s (%s)", normalized.raw_item_id, prefilter_reason)
                    # Regex prefilter is deterministic — the verdict won't change on retry.
                    if not dry_run:
                        mark_raw_item_attempt(
                            conn, row["id"], f"prefiltered:{prefilter_reason}", permanent=True
                        )
                    continue

                primary = await canonicalizer.canonicalize(normalized.urls[0])
                live = await probe.probe(primary.final)
                if require_liveness and live.status in ("dead", "soft_dead"):
                    stats["liveness_reject"] += 1
                    # A dead link may recover — retry a few times, then give up.
                    if not dry_run:
                        mark_raw_item_attempt(
                            conn, row["id"], f"liveness:{live.status}", permanent=False
                        )
                    continue

                survivors.append((row, normalized, primary, live))
            except Exception as exc:  # noqa: BLE001 — one bad item never kills the run
                stats["errors"] += 1
                log.exception("item failed (prefilter/liveness): raw_item_id=%s", row["id"])
                if not dry_run:
                    mark_raw_item_attempt(
                        conn, row["id"], f"error:{type(exc).__name__}", permanent=False
                    )

        # --- Stage B: batched LLM extraction (BATCH_SIZE items/call) --- #
        # Sequential extract->write when distribution is OFF (default);
        # concurrent extraction (bounded by LLM_MAX_CONCURRENT_BATCHES) with
        # a still-sequential write stage when it is ON. See _run_stage_b.
        await _run_stage_b(conn, deduplicator, extractor, survivors, stats, dry_run)


# --------------------------------------------------------------------------- #
# Main orchestrator
# --------------------------------------------------------------------------- #
async def run_pipeline(
    source: str | None = None,
    limit: int = 100,
    dry_run: bool = False,
    dispatch: bool = True,
    require_liveness: bool = True,
) -> dict:
    stats: dict[str, Any] = {
        "dry_run": dry_run,
        "fetched": 0, "no_url": 0, "llm_rejected": 0, "llm_unavailable": 0, "dup": 0,
        "liveness_reject": 0, "offers_written": 0, "errors": 0,
    }

    db_url = os.environ.get("DATABASE_URL", "postgresql://freebies:***@localhost:5432/freebies")
    extractor = LLMExtractor()

    try:
        conn = psycopg.connect(db_url, row_factory=dict_row, connect_timeout=5)
    except psycopg.OperationalError as exc:
        raise SystemExit(
            f"Cannot reach PostgreSQL at {db_url.split('@')[-1]} — "
            f"start it first (`docker compose up -d`) or set DATABASE_URL.\n{exc}"
        )

    with conn:
        # run audit row
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO runs (flow_key) VALUES ('pipeline') RETURNING id"
            )
            run_id = cur.fetchone()["id"]
        conn.commit()

        rows = fetch_unprocessed(conn, source, limit)
        stats["fetched"] = len(rows)
        if rows:
            await _run_ingest_stages(
                conn, extractor, rows, stats, dry_run, require_liveness
            )
        else:
            log.info("No unprocessed raw_items%s.", f" for source {source!r}" if source else "")

        run_status = "success" if stats["errors"] == 0 else "partial"
        mark_run(conn, run_id, run_status, stats)

        # Evaluate the dispatch gate while this connection is still open — the
        # dispatch stage itself runs after the `with conn:` block closes.
        dispatch_due = False
        if dispatch:
            try:
                dispatch_due = has_undispatched_offers(conn)
            except Exception:  # noqa: BLE001 — never let the gate query kill a run
                log.exception(
                    "undispatched-offer check failed; falling back to the "
                    "offers_written gate for this run"
                )
                dispatch_due = stats["offers_written"] > 0

    # Dispatch whenever something is actually waiting, not only when THIS run
    # wrote an offer. See has_undispatched_offers() for why the old
    # `offers_written > 0` gate could hold an offer back for up to 66 hours.
    if dispatch_due:
        try:
            # limit (not a hardcoded 10) so --limit / PIPELINE_LIMIT_PER_RUN
            # actually governs the dispatch tail and the backlog can drain.
            sent = await dispatch_new_offers(dry_run=dry_run, limit=limit)
            stats["dispatched"] = sent
        except Exception as exc:  # noqa: BLE001
            log.exception("dispatch stage failed: %s", exc)
            stats["dispatch_error"] = str(exc)
        # Only now does stats carry the dispatch outcome; the mark_run above ran
        # before dispatch, which is why runs.stats never contained "dispatched".
        _repersist_run_stats(db_url, run_id, run_status, stats)

    log.info("pipeline done: %s", stats)
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description="Freebies pipeline: raw_items -> offers -> Discord")
    parser.add_argument("--dry-run", action="store_true", help="no writes, no sends")
    parser.add_argument("--source", default=None, help="source name filter (e.g. telegram:default)")
    parser.add_argument("--limit", type=int, default=100, help="max raw items to process")
    parser.add_argument("--no-dispatch", action="store_true", help="skip Discord dispatch stage")
    parser.add_argument("--require-liveness", default="true",
                        help="reject dead links before LLM (default true; pass false to allow)")
    args = parser.parse_args()

    require_liveness = args.require_liveness.lower() not in ("false", "0", "no")

    stats = asyncio.run(
        run_pipeline(
            source=args.source,
            limit=args.limit,
            dry_run=args.dry_run,
            dispatch=not args.no_dispatch,
            require_liveness=require_liveness,
        )
    )
    return 0 if stats.get("errors", 0) == 0 else 1


if __name__ == "__main__":
    sys.exit(main())