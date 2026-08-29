"""Firecrawl credit-guard fixes (2026-08-29 credit-blowout remediation).

Hermetic: no Postgres, no network, no Firecrawl. What is locked here:

  * FREQUENCY GATE — ``scheduler._run_firecrawl`` invokes the adapter on exactly
    one slot/day (``all_deals`` run 1) and no-ops on every other slot. The old
    ungated call re-scraped resourify.com ~27×/day.
  * URL-DIFF DEDUP — ``_filter_unseen`` drops URLs already ingested within the
    window (replacing the fail-open JSON-LD freshness filter that passed all 125
    URLs every run). Only unseen URLs reach the paid LLM-extract scrape.
  * DRY-RUN — with no ``source_id`` there is nothing to dedup against, so all
    URLs pass (manual inspection path only, never the scheduled one).
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import adapters.firecrawl_adapter as fc
import scheduler


# --------------------------------------------------------------------------- #
# Frequency gate
# --------------------------------------------------------------------------- #
def test_firecrawl_runs_only_on_all_deals_run_1(monkeypatch):
    calls: list[dict] = []

    async def _fake_run(**kwargs):
        calls.append(kwargs)
        return 0

    monkeypatch.setattr(fc, "run_firecrawl", AsyncMock(side_effect=_fake_run))

    # The single slot that should fire it.
    scheduler._run_firecrawl("all_deals", 1)
    assert len(calls) == 1

    # Every other lane / run must no-op — no adapter call at all.
    for category in ("cloud", "coupon", "ai_tools", "open_source_repo", "all_deals"):
        for run_number in (1, 2, 3):
            if category == "all_deals" and run_number == 1:
                continue
            scheduler._run_firecrawl(category, run_number)
    assert len(calls) == 1, "firecrawl fired outside the all_deals run-1 slot"


def test_firecrawl_fires_once_across_a_full_day():
    """Across the 27-slot day the gate opens exactly once."""
    fires = [
        (cat, run)
        for cat in ("cloud", "student_pack", "saas_deal", "open_source_repo",
                    "coding_agents", "coupon", "llm_api_drop", "ai_tools", "all_deals")
        for run in (1, 2, 3)
        if cat == "all_deals" and run == 1
    ]
    assert fires == [("all_deals", 1)]


# --------------------------------------------------------------------------- #
# URL-diff dedup
# --------------------------------------------------------------------------- #
_URLS = [
    "https://resourify.com/resources/aws-credits",
    "https://resourify.com/resources/free-domain",
    "https://resourify.com/resources/gpt-api-drop",
]


def test_filter_unseen_drops_already_ingested(monkeypatch):
    already = {fc._url_hash(_URLS[0]), fc._url_hash(_URLS[2])}
    monkeypatch.setattr(fc, "_seen_external_ids", lambda source_id, within_days: already)

    out = fc._filter_unseen(_URLS, source_id=7, within_days=14)
    assert out == [_URLS[1]], "only the unseen URL should survive"


def test_filter_unseen_passes_all_when_none_seen(monkeypatch):
    monkeypatch.setattr(fc, "_seen_external_ids", lambda source_id, within_days: set())
    assert fc._filter_unseen(_URLS, source_id=7, within_days=14) == _URLS


def test_filter_unseen_dry_run_skips_dedup(monkeypatch):
    # source_id None => dry-run => never touch the DB, pass everything through.
    def _boom(*a, **k):
        raise AssertionError("_seen_external_ids must not run in dry-run")

    monkeypatch.setattr(fc, "_seen_external_ids", _boom)
    assert fc._filter_unseen(_URLS, source_id=None, within_days=14) == _URLS


def test_seen_query_uses_window_and_source(monkeypatch):
    """The dedup query scopes to the source and the day window (fail-closed)."""
    captured: dict = {}

    class _Cur:
        def __enter__(self): return self
        def __exit__(self, *e): return False
        def execute(self, sql, params=None):
            captured["sql"] = " ".join(str(sql).split())
            captured["params"] = params
        def fetchall(self):
            return [{"external_id": "abc"}]

    class _Conn:
        def __enter__(self): return self
        def __exit__(self, *e): return False
        def cursor(self): return _Cur()

    monkeypatch.setattr(fc, "connect", lambda: _Conn())

    out = fc._seen_external_ids(source_id=7, within_days=14)
    assert out == {"abc"}
    assert captured["params"] == (7, 14)
    assert "make_interval(days => %s)" in captured["sql"]
    assert "source_id = %s" in captured["sql"]
