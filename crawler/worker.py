"""Always-on ingestion worker entrypoint (Heroku `worker` dyno).

Runs two concurrent loops:
  1. Telegram realtime monitor (MTProto delta pull, always-on).
  2. Batch ingest loop — GitHub, Reddit, HN, Creator Mirrors — fires every
     BATCH_CADENCE_SECONDS (default 6 h).  These are cheap HTTP calls that
     don't need a persistent connection, so one pass per N hours is enough.
"""
from __future__ import annotations

import asyncio
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
log = logging.getLogger("worker")

BATCH_CADENCE_SECONDS = 6 * 3600  # run web adapters every 6 hours


async def _batch_ingest_loop() -> None:
    """Fire GitHub / Reddit / HN / mirrors adapters on a cadence."""
    from adapters.github_adapter import run_github
    from adapters.reddit_adapter import run_reddit
    from adapters.hn_adapter import run_hn
    from adapters.creator_mirrors import run_creator_mirrors

    while True:
        log.info("batch ingest: starting web adapters")
        try:
            n = await run_github()
            log.info("github adapter: %d items", n)
        except Exception:  # noqa: BLE001
            log.exception("github adapter failed")

        try:
            n = await run_reddit()
            log.info("reddit adapter: %d items", n)
        except Exception:  # noqa: BLE001
            log.exception("reddit adapter failed")

        try:
            n = await run_hn()
            log.info("hn adapter: %d items", n)
        except Exception:  # noqa: BLE001
            log.exception("hn adapter failed")

        try:
            n = await run_creator_mirrors()
            log.info("creator mirrors adapter: %d items", n)
        except Exception:  # noqa: BLE001
            log.exception("creator mirrors adapter failed")

        log.info("batch ingest: done — sleeping %dh", BATCH_CADENCE_SECONDS // 3600)
        await asyncio.sleep(BATCH_CADENCE_SECONDS)


async def main() -> None:
    from adapters.telegram_adapter import run_telegram_monitor

    log.info("Starting worker: telegram monitor + batch ingest loop")
    # return_exceptions=True is load-bearing: without it, one task raising kills
    # every sibling task and the whole dyno. Each result is inspected below so a
    # failure is logged loudly rather than silently swallowed.
    task_names = ("telegram monitor", "batch ingest loop")
    results = await asyncio.gather(
        run_telegram_monitor(),
        _batch_ingest_loop(),
        return_exceptions=True,
    )

    for name, result in zip(task_names, results):
        if isinstance(result, BaseException):
            log.error("worker task %r died: %s: %s", name, type(result).__name__, result,
                      exc_info=result)
        else:
            log.warning("worker task %r exited (returned %r) — it was expected to run forever",
                        name, result)


if __name__ == "__main__":
    asyncio.run(main())
