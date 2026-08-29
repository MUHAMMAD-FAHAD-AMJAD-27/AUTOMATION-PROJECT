"""Item 4b — notable_repo (non-deal) lane.

Locks the four seams that let GitHub-Trending repo-discovery items ride the
pipeline without disturbing the deal path:

  (1) heuristic_prefilter bypasses the no_deal_signal gate for extra.is_repo
      items, while junk/too-short guards still apply, and the deal path is
      unchanged for everything else.
  (2) extract_batch(mode="repo") sends REPO_BATCH_SYSTEM_PROMPT; the default
      mode="deal" still sends BATCH_SYSTEM_PROMPT byte-for-byte.
  (3) _run_ingest_stages partitions survivors by is_repo and calls _run_stage_b
      once per mode (deal default, repo relaxed).
  (4) dispatch is HELD for notable_repo until NOTABLE_REPO_DISPATCH_ENABLED is
      truthy: claim_offers excludes it and the gate does not fire on it.

Hermetic: no Postgres, no network, no LLM. Coroutines run via asyncio.run().
"""
from __future__ import annotations

import asyncio

import httpx

import crawler.pipeline as pipeline
import crawler.verifier as verifier
import discord_dispatcher
from crawler.normalizer import NormalizedItem
from crawler.verifier import (
    BATCH_SYSTEM_PROMPT,
    REPO_BATCH_SYSTEM_PROMPT,
    LLMExtractor,
    _Provider,
    heuristic_prefilter,
)


def _ni(text: str, *, is_repo: bool = False, urls=None) -> NormalizedItem:
    return NormalizedItem(
        raw_item_id=1,
        source_name="github:trending",
        source_kind="web",
        external_id="https://github.com/acme/tool",
        text=text,
        urls=urls or ["https://github.com/acme/tool"],
        extra={"is_repo": True} if is_repo else {},
    )


# --------------------------------------------------------------------------- #
# (1) prefilter bypass
# --------------------------------------------------------------------------- #
def test_repo_item_bypasses_no_deal_signal():
    # A repo blurb with zero deal keywords would normally be dropped.
    text = "Acme Tool is a fast static site generator written in Rust with plugins."
    assert heuristic_prefilter(_ni(text, is_repo=True)) == ""


def test_deal_path_unchanged_for_non_repo():
    text = "Acme Tool is a fast static site generator written in Rust with plugins."
    assert heuristic_prefilter(_ni(text, is_repo=False)) == "prefilter:no_deal_signal"


def test_repo_item_still_dropped_when_too_short():
    # is_repo does NOT exempt the too_short / junk guards above the bypass.
    assert heuristic_prefilter(_ni("tiny", is_repo=True)) == "prefilter:too_short"


def test_repo_item_still_dropped_on_junk_body():
    text = "This page has been removed and is no longer available anywhere online."
    assert heuristic_prefilter(_ni(text, is_repo=True)) == "prefilter:junk_body"


# --------------------------------------------------------------------------- #
# (2) prompt selection in extract_batch
# --------------------------------------------------------------------------- #
class _FakeResp:
    status_code = 200
    headers: dict = {}

    def __init__(self, content: str):
        self._content = content

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}

    def raise_for_status(self):
        return None


def _run_capturing_system_prompt(monkeypatch, mode: str) -> str:
    monkeypatch.setattr(verifier, "_load_providers",
                        lambda: [_Provider(api_key="k", base_url="https://p.test", model="m")])
    monkeypatch.delenv("LLM_DISTRIBUTION_ENABLED", raising=False)
    monkeypatch.setattr(LLMExtractor, "_build_batch_user_content", lambda self, items: "USER")
    # Parse to a single None so the batch "succeeds" and stops after one call.
    monkeypatch.setattr(LLMExtractor, "_parse_batch", lambda self, content, n: [None] * n)

    captured: dict = {}

    async def fake_post(self, url, json=None, headers=None):
        captured["system"] = json["messages"][0]["content"]
        return _FakeResp("{}")

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    items = [(_ni("x" * 40, is_repo=(mode == "repo")), None)]
    out = asyncio.run(LLMExtractor().extract_batch(items, mode=mode))
    assert out == [None]
    return captured["system"]


def test_repo_mode_uses_repo_prompt(monkeypatch):
    system = _run_capturing_system_prompt(monkeypatch, mode="repo")
    assert system.startswith(REPO_BATCH_SYSTEM_PROMPT.split("{", 1)[0][:40])
    assert "notable_repo" in system
    assert "curator of notable open-source" in system


def test_deal_mode_uses_deal_prompt(monkeypatch):
    system = _run_capturing_system_prompt(monkeypatch, mode="deal")
    assert system.startswith(BATCH_SYSTEM_PROMPT.split("{", 1)[0][:40])
    assert "precision extraction engine" in system


# --------------------------------------------------------------------------- #
# (3) pipeline partitions survivors by is_repo
# --------------------------------------------------------------------------- #
def test_ingest_partitions_deal_and_repo_modes(monkeypatch):
    """_run_ingest_stages must run Stage B once per mode: the deal survivors on
    mode='deal' and the is_repo survivors on mode='repo', never mixed."""
    calls: list[tuple[str, list[bool]]] = []

    async def fake_run_stage_b(conn, dedup, ext, survivors, stats, dry_run, mode="deal"):
        calls.append((mode, [s[1].extra.get("is_repo", False) for s in survivors]))

    class _FakeCanon:
        def __init__(self, client):
            pass

        async def canonicalize(self, url):
            from types import SimpleNamespace
            return SimpleNamespace(final=url, status=200)

    class _FakeProbe:
        def __init__(self, client):
            pass

        async def probe(self, url):
            from types import SimpleNamespace
            return SimpleNamespace(status="live")

    # rows[i]['id'] selects the fake normalized item (deal vs repo).
    fakes = {
        1: _ni("deal one " * 4, is_repo=False),
        2: _ni("repo one " * 4, is_repo=True),
        3: _ni("repo two " * 4, is_repo=True),
    }
    monkeypatch.setattr(pipeline, "normalize_raw_item", lambda row: fakes[row["id"]])
    monkeypatch.setattr(pipeline, "heuristic_prefilter", lambda ni: "")
    monkeypatch.setattr(pipeline, "Deduplicator", lambda conn: object())
    monkeypatch.setattr(pipeline, "URLCanonicalizer", _FakeCanon)
    monkeypatch.setattr(pipeline, "LivenessProbe", _FakeProbe)
    monkeypatch.setattr(pipeline, "_run_stage_b", fake_run_stage_b)

    rows = [{"id": 1}, {"id": 2}, {"id": 3}]
    stats: dict = {"no_url": 0, "prefiltered": 0, "liveness_reject": 0, "errors": 0}
    asyncio.run(pipeline._run_ingest_stages(
        conn=None, extractor=object(), rows=rows, stats=stats,
        dry_run=True, require_liveness=False,
    ))

    assert ("deal", [False]) in calls
    assert ("repo", [True, True]) in calls


# --------------------------------------------------------------------------- #
# (4) held dispatch
# --------------------------------------------------------------------------- #
def test_held_categories_flag_toggle(monkeypatch):
    monkeypatch.delenv("NOTABLE_REPO_DISPATCH_ENABLED", raising=False)
    assert discord_dispatcher.held_dispatch_categories() == ("notable_repo",)
    monkeypatch.setenv("NOTABLE_REPO_DISPATCH_ENABLED", "1")
    assert discord_dispatcher.held_dispatch_categories() == ()


def test_notable_repo_has_dedicated_webhook_env():
    assert discord_dispatcher.CATEGORY_WEBHOOK_ENV["notable_repo"] == "NOTABLE_REPO_WEBHOOK_URL"


class _FakeCursor:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.conn.executed.append((" ".join(str(sql).split()), params))

    def fetchall(self):
        return []

    def fetchone(self):
        return None


class _FakeConn:
    def __init__(self):
        self.executed: list[tuple[str, object]] = []

    def cursor(self, *a, **k):
        return _FakeCursor(self)

    def commit(self):
        pass

    def rollback(self):
        pass


def test_claim_offers_excludes_notable_repo_when_held(monkeypatch):
    monkeypatch.delenv("NOTABLE_REPO_DISPATCH_ENABLED", raising=False)
    conn = _FakeConn()
    discord_dispatcher.claim_offers(conn, channel="discord", limit=10, category=None)
    insert = [(s, p) for s, p in conn.executed if "INSERT INTO dispatches" in s][0]
    sql, params = insert
    assert "o.category <> ALL(%(held_categories)s)" in sql
    assert params["held_categories"] == ["notable_repo"]


def test_claim_offers_no_hold_when_flag_enabled(monkeypatch):
    monkeypatch.setenv("NOTABLE_REPO_DISPATCH_ENABLED", "true")
    conn = _FakeConn()
    discord_dispatcher.claim_offers(conn, channel="discord", limit=10, category=None)
    insert = [(s, p) for s, p in conn.executed if "INSERT INTO dispatches" in s][0]
    sql, params = insert
    assert "held_categories" not in sql
    assert "held_categories" not in params
