"""
scheduler.py — Staggered 27-slot APScheduler orchestrator
==========================================================
Distributes 9 categories × 3 daily runs = 27 executions across 24 hours,
one job every ~53 minutes, all sequential — zero concurrent LLM calls,
zero 429 rate-limit errors.

Slot formula (index 0-26):
    total_minutes(i) = round(i × 53.333...)
    hour(i)          = total_minutes(i) // 60
    minute(i)        = total_minutes(i) % 60
    category(i)      = CATEGORIES[i % 9]

State is persisted to a local SQLite job store so if the process restarts
mid-day it skips slots that already fired.

Usage:
    python scheduler.py            # run forever (blocking)
    python scheduler.py --dry-run  # print schedule table, no execution
    python scheduler.py --now <category>  # fire one category immediately
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from dotenv import load_dotenv

load_dotenv(override=True)

from crawler.categories import is_valid_category

log = logging.getLogger("scheduler")

# SECURITY: the scheduler is the always-on production process and drives every
# dispatch, so httpx's INFO "HTTP Request: <full url>" line would put live Discord
# webhook tokens into the Heroku log stream on every slot. Set at import time so
# it holds no matter which entrypoint (main(), --now, or a slot callback) runs.
logging.getLogger("httpx").setLevel(logging.WARNING)

# --------------------------------------------------------------------------- #
# Scheduling axis — must stay stable; index determines time slot.
# NOTE: this is NOT the offer taxonomy (that lives in crawler/categories.py).
# It's the set of category "lanes" the 27-slot scheduler cycles through, plus
# the `all_deals` sentinel meaning "run every source, unfiltered".
# --------------------------------------------------------------------------- #
ALL_DEALS_SENTINEL = "all_deals"
RUN_CATEGORIES: list[str] = [
    "cloud",
    "student_pack",
    "saas_deal",
    "open_source_repo",
    "coding_agents",
    "coupon",
    "llm_api_drop",
    "ai_tools",
    ALL_DEALS_SENTINEL,
]

# Fail fast if a run-category lane is renamed out of sync with the taxonomy.
_invalid = [
    c for c in RUN_CATEGORIES
    if c != ALL_DEALS_SENTINEL and not is_valid_category(c)
]
assert not _invalid, (
    f"scheduler RUN_CATEGORIES contains values not in crawler.categories: {_invalid}"
)

TOTAL_SLOTS = len(RUN_CATEGORIES) * 3   # 27
MINUTES_PER_SLOT = 1440 / TOTAL_SLOTS  # 53.333...

PIPELINE_LIMIT_PER_RUN = 30  # raw items to process per category run
ADAPTER_LIMIT_PER_RUN = 50   # items fetched per adapter pre-run

# github_adapter is capped PER REPO, not globally. ADAPTER_LIMIT_PER_RUN is a
# single counter consumed in TARGETS order, and ripienaar/free-for-dev (first in
# the list) alone parses 1237 entries — so the old ADAPTER_LIMIT_PER_RUN=50 never
# opened targets 1-15 and all 11 LLM-aggregator repos were unreachable in
# production. Measured 2026-08-26: sum(min(entries, 25)) over the 16 targets =
# 253 items/run, vs ~1726 for a global cap big enough to reach the tail.
GITHUB_MAX_PER_TARGET = 25

# deep_web_adapter had the SAME single-global-counter starvation github had.
# templates_for_run_category() preserves QUERY_TEMPLATES order, and the
# llm_api_drop lane carries 76 templates across 6 tags; a global cap of 50
# consumed in order let the FIRST tag's ~5 high-yield English queries eat the
# whole budget, so all 64 aggregator-tag queries (the 2026-08-26 OSINT sweep)
# executed ZERO searches (measured 2026-08-26 via stubbed run_deep_web).
#
# Fix: spread the SAME run budget evenly across the distinct tags on the lane
# (max_items_per_tag = ADAPTER_LIMIT_PER_RUN // n_tags — computed in
# _run_deep_web) instead of one global counter. Total writes/run stay
# ≈ ADAPTER_LIMIT_PER_RUN on every lane, so there is no volume blow-up on
# many-tag lanes and no under-feeding of single-tag lanes, while every tag is
# guaranteed to be reached. A FIXED per-tag cap was rejected: with lanes
# ranging from 1 tag (cloud) to 15 (all_deals) it would either starve the
# single-tag lanes or triple the volume on all_deals.


# --------------------------------------------------------------------------- #
# Slot math
# --------------------------------------------------------------------------- #
def _build_slots() -> list[dict]:
    """Return the 27-slot schedule as a list of dicts."""
    slots = []
    for i in range(TOTAL_SLOTS):
        total_min = round(i * MINUTES_PER_SLOT)
        slots.append({
            "slot": i,
            "hour": total_min // 60,
            "minute": total_min % 60,
            "category": RUN_CATEGORIES[i % len(RUN_CATEGORIES)],
            "run_number": i // len(RUN_CATEGORIES) + 1,
        })
    return slots


# --------------------------------------------------------------------------- #
# Per-slot job — runs the ingest adapters then the pipeline for one category
# --------------------------------------------------------------------------- #
def _run_slot(category: str, run_number: int) -> None:
    """Execute one category slot: ingest → pipeline → dispatch."""
    import asyncio as _asyncio

    log.info("=== SLOT START  category=%s  run=%d ===", category, run_number)

    # 1) Deep web adapter — pull fresh search results for this category
    _run_deep_web(category)

    # 2) Creator mirrors — fresh RSS feeds relevant to category
    _run_creator_mirrors()

    # 3) Firecrawl adapter — resourify.com scrape (once/day, all_deals slot)
    _run_firecrawl(category, run_number)

    # 3b) Dormant + new adapters, each gated to the categories/runs it serves
    #     so no single slot hammers every source (see helper docstrings).
    _run_github(category, run_number)   # curated mega-lists (once/day, OSS slot)
    _run_hn(category)                   # Hacker News (llm_api_drop slot)
    _run_reddit(category)               # Reddit freebies/deals (coupon slot)
    _run_devto(category)                # dev.to articles (ai_tools slot)
    _run_producthunt(category)          # Product Hunt launches (saas_deal slot)

    # 4) OpenRouter — ingest free-tier LLM models (llm_api_drop / ai_tools slots only)
    _run_openrouter(category)

    # 5) Pipeline — LLM verification + dedup + DB write + Discord dispatch
    stats = _asyncio.run(_run_pipeline(category))
    log.info("=== SLOT END  category=%s  run=%d  stats=%s ===", category, run_number, stats)


def _run_deep_web(category: str) -> None:
    try:
        from adapters.deep_web_adapter import run_deep_web, templates_for_run_category

        templates = templates_for_run_category(category)
        # Spread the run budget evenly across the distinct tags on this lane so
        # no head tag starves the tail (see the deep-web note by
        # GITHUB_MAX_PER_TARGET). n_tags ranges 1 (cloud) → 15 (all_deals).
        n_tags = len({tag for tag, _ in templates}) or 1
        per_tag = max(1, ADAPTER_LIMIT_PER_RUN // n_tags)
        asyncio.run(run_deep_web(
            templates=templates,
            lookback="d",
            max_items_per_tag=per_tag,
        ))
    except Exception as exc:
        log.warning("deep_web adapter failed: %s", exc)


def _run_creator_mirrors() -> None:
    try:
        from adapters.creator_mirrors import run_creator_mirrors
        asyncio.run(run_creator_mirrors(max_items=ADAPTER_LIMIT_PER_RUN))
    except Exception as exc:
        log.warning("creator_mirrors adapter failed: %s", exc)


def _run_firecrawl(category: str, run_number: int) -> None:
    """resourify.com is a slow-changing deal catalog, so scrape it once/day —
    not every one of the 27 slots. Gated to a single slot (all_deals, run 1);
    firecrawl's map ignores the category filter (it returns every /resources/
    URL regardless), so which lane it rides on is cosmetic — the gate just
    guarantees exactly one run per day. The adapter itself then dedups against
    already-ingested URLs so even that one run scrapes only genuinely new pages.
    This replaced an ungated per-slot call that re-scraped all 125 URLs ~27×/day
    with the paid LLM-extract format (the 2026-08 credit blowout)."""
    if category != "all_deals" or run_number != 1:
        return
    try:
        from adapters.firecrawl_adapter import run_firecrawl
        asyncio.run(run_firecrawl(category_filter=category))
    except Exception as exc:
        log.warning("firecrawl adapter failed: %s", exc)


def _run_openrouter(category: str) -> None:
    if category not in ("llm_api_drop", "ai_tools", "all_deals"):
        return
    try:
        from adapters.openrouter_adapter import run_openrouter
        asyncio.run(run_openrouter())
    except Exception as exc:
        log.warning("openrouter adapter failed: %s", exc)


def _run_github(category: str, run_number: int) -> None:
    """Curated mega-lists change slowly, so run once/day (run 1) on the OSS lane.

    Capped PER REPO (GITHUB_MAX_PER_TARGET), not globally: a global cap is one
    counter spent in TARGETS order, so free-for-dev's 1237 entries consumed the
    whole budget and starved every repo behind it — including all 11 LLM-aggregator
    lists. Per-target is order-independent and bounded at
    len(TARGETS) * GITHUB_MAX_PER_TARGET."""
    if category not in ("open_source_repo", "all_deals"):
        return
    if run_number != 1:
        return
    try:
        from adapters.github_adapter import run_github
        asyncio.run(run_github(
            max_items=None,
            max_per_target=GITHUB_MAX_PER_TARGET,
        ))
    except Exception as exc:
        log.warning("github adapter failed: %s", exc)


def _run_hn(category: str) -> None:
    """Hacker News (Algolia) — best signal for fresh LLM/API drops + Show HN launches."""
    if category not in ("llm_api_drop", "all_deals"):
        return
    try:
        from adapters.hn_adapter import run_hn
        asyncio.run(run_hn())
    except Exception as exc:
        log.warning("hn adapter failed: %s", exc)


def _run_reddit(category: str) -> None:
    """Reddit freebies/deals subs — best signal for coupon/promo-code drops."""
    if category not in ("coupon", "all_deals"):
        return
    try:
        from adapters.reddit_adapter import run_reddit
        asyncio.run(run_reddit())
    except Exception as exc:
        log.warning("reddit adapter failed: %s", exc)


def _run_devto(category: str) -> None:
    """dev.to articles — OSS launches + AI/devtool posts; capped like other feeds."""
    if category not in ("ai_tools", "all_deals"):
        return
    try:
        from adapters.devto_adapter import run_devto
        asyncio.run(run_devto(max_items=ADAPTER_LIMIT_PER_RUN))
    except Exception as exc:
        log.warning("devto adapter failed: %s", exc)


def _run_producthunt(category: str) -> None:
    """Product Hunt launches — new free/freemium SaaS + dev tools; capped."""
    if category not in ("saas_deal", "all_deals"):
        return
    try:
        from adapters.producthunt_adapter import run_producthunt
        asyncio.run(run_producthunt(max_items=ADAPTER_LIMIT_PER_RUN))
    except Exception as exc:
        log.warning("producthunt adapter failed: %s", exc)


async def _run_pipeline(category: str) -> dict:
    try:
        from crawler.pipeline import run_pipeline
        return await run_pipeline(
            limit=PIPELINE_LIMIT_PER_RUN,
            dry_run=False,
            dispatch=True,
            require_liveness=True,
        )
    except Exception as exc:
        log.error("pipeline failed for %s: %s", category, exc)
        return {"error": str(exc)}


# --------------------------------------------------------------------------- #
# Schedule builder
# --------------------------------------------------------------------------- #
def _build_scheduler():
    try:
        from apscheduler.schedulers.blocking import BlockingScheduler
        from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
        from apscheduler.triggers.cron import CronTrigger
        from apscheduler.executors.pool import ThreadPoolExecutor
    except ImportError:
        raise SystemExit(
            "APScheduler not installed. Run:\n"
            "  pip install apscheduler sqlalchemy"
        )

    db_path = ROOT / "scheduler_jobs.sqlite"
    jobstores = {
        "default": SQLAlchemyJobStore(url=f"sqlite:///{db_path}"),
    }
    executors = {
        # max_workers=1 ensures jobs never run concurrently
        "default": ThreadPoolExecutor(max_workers=1),
    }
    scheduler = BlockingScheduler(
        jobstores=jobstores,
        executors=executors,
        job_defaults={"coalesce": True, "max_instances": 1},
    )

    slots = _build_slots()
    for s in slots:
        job_id = f"slot_{s['slot']:02d}_{s['category']}_r{s['run_number']}"
        scheduler.add_job(
            _run_slot,
            trigger=CronTrigger(hour=s["hour"], minute=s["minute"]),
            id=job_id,
            name=f"{s['category']} run {s['run_number']}/3",
            args=[s["category"], s["run_number"]],
            replace_existing=True,
        )

    return scheduler, slots


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _print_schedule(slots: list[dict]) -> None:
    print(f"\n{'Slot':>4}  {'Time':>5}  {'Category':<20}  {'Run'}")
    print("-" * 44)
    for s in slots:
        print(f"{s['slot']:>4}  {s['hour']:02d}:{s['minute']:02d}  "
              f"{s['category']:<20}  {s['run_number']} of 3")
    print(f"\nTotal: {len(slots)} slots  |  gap: ~{MINUTES_PER_SLOT:.1f} min\n")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description="27-slot staggered freebie scheduler")
    parser.add_argument("--dry-run", action="store_true",
                        help="print schedule table only, do not start scheduler")
    parser.add_argument("--now", metavar="CATEGORY", default=None,
                        help="fire one category slot immediately and exit")
    args = parser.parse_args()

    slots = _build_slots()

    if args.dry_run:
        _print_schedule(slots)
        return

    if args.now:
        cat = args.now
        if cat not in RUN_CATEGORIES:
            print(f"Unknown category {cat!r}. Valid: {', '.join(RUN_CATEGORIES)}")
            sys.exit(1)
        log.info("Firing category %r immediately...", cat)
        _run_slot(cat, run_number=0)
        return

    _print_schedule(slots)
    log.info("Starting APScheduler with %d jobs (SQLite job store)...", len(slots))

    scheduler, _ = _build_scheduler()
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("Scheduler stopped.")


if __name__ == "__main__":
    main()
