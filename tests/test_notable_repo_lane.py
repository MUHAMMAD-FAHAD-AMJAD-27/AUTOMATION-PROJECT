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
        # notable_repo_exclusions() runs a COUNT(*) AS n first; default it to 0
        # so the enabled-flag path computes an empty exclusion set on this fake.
        return {"n": 0}


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


# --------------------------------------------------------------------------- #
# Item 1 — notable_repo daily cap + diversity trim
# --------------------------------------------------------------------------- #
def test_env_int_reads_override_and_falls_back(monkeypatch):
    monkeypatch.setenv("NOTABLE_REPO_DAILY_CAP", "3")
    assert discord_dispatcher._env_int("NOTABLE_REPO_DAILY_CAP", 8) == 3
    monkeypatch.setenv("NOTABLE_REPO_DAILY_CAP", "-2")   # negative → default
    assert discord_dispatcher._env_int("NOTABLE_REPO_DAILY_CAP", 8) == 8
    monkeypatch.setenv("NOTABLE_REPO_DAILY_CAP", "nan")  # invalid → default
    assert discord_dispatcher._env_int("NOTABLE_REPO_DAILY_CAP", 8) == 8
    monkeypatch.delenv("NOTABLE_REPO_DAILY_CAP", raising=False)
    assert discord_dispatcher._env_int("NOTABLE_REPO_DAILY_CAP", 8) == 8


def test_repo_org_from_url_handle_and_slug():
    assert discord_dispatcher._repo_org("https://github.com/deepseek-ai/harness", None) == "deepseek-ai"
    assert discord_dispatcher._repo_org("acme/tool", None) == "acme"
    assert discord_dispatcher._repo_org(None, "SomeHandle") == "somehandle"
    assert discord_dispatcher._repo_org(None, None) == ""


def _repo_offer(oid: int, title: str, org: str):
    return {"id": oid, "title": title, "author_handle": org,
            "raw": {"github_repo": f"https://github.com/{org}/proj{oid}"},
            "first_seen": oid}


def test_diversify_spreads_across_orgs_and_topics():
    # A trend wave: 4 near-duplicate "DeepSeek Harness" repos across distinct orgs,
    # plus 3 unrelated repos. Picking 3 should NOT return three DSH clones.
    cands = [
        _repo_offer(1, "DeepSeek Harness desktop client", "org-a"),
        _repo_offer(2, "DeepSeek Harness web client", "org-b"),
        _repo_offer(3, "DeepSeek Harness routing suite", "org-c"),
        _repo_offer(4, "DeepSeek Harness anchored standard", "org-d"),
        _repo_offer(5, "Postgres vector search extension", "vendor-x"),
        _repo_offer(6, "Rust terminal spreadsheet editor", "vendor-y"),
        _repo_offer(7, "Kubernetes cost dashboard", "vendor-z"),
    ]
    chosen = discord_dispatcher._diversify(cands, 3)
    assert len(chosen) == 3
    dsh = sum(1 for c in chosen if "deepseek" in c["title"].lower())
    assert dsh <= 1, f"expected the trend wave trimmed to ≤1, got {dsh}"


def test_diversify_returns_all_when_k_exceeds_candidates():
    cands = [_repo_offer(1, "one repo here", "a"), _repo_offer(2, "two repo here", "b")]
    assert discord_dispatcher._diversify(cands, 5) == cands


class _ScriptedCursor:
    """Cursor whose fetchone/fetchall return queued results in call order."""

    def __init__(self, results):
        self._results = list(results)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self._current = self._results.pop(0)

    def fetchone(self):
        return self._current

    def fetchall(self):
        return self._current


class _ScriptedConn:
    def __init__(self, results):
        self._results = results

    def cursor(self, *a, **k):
        return _ScriptedCursor(self._results)


def test_exclusions_empty_when_held(monkeypatch):
    monkeypatch.delenv("NOTABLE_REPO_DISPATCH_ENABLED", raising=False)
    # Held: returns [] without touching the DB (no scripted results needed).
    assert discord_dispatcher.notable_repo_exclusions(_ScriptedConn([]), "discord") == []


def test_exclusions_none_when_under_budget(monkeypatch):
    monkeypatch.setenv("NOTABLE_REPO_DISPATCH_ENABLED", "1")
    monkeypatch.setenv("NOTABLE_REPO_DAILY_CAP", "8")
    cands = [_repo_offer(1, "alpha repo", "a"), _repo_offer(2, "beta repo", "b")]
    conn = _ScriptedConn([{"n": 0}, cands])   # 0 sent today, 2 candidates ≤ 8
    assert discord_dispatcher.notable_repo_exclusions(conn, "discord") == []


def test_exclusions_defers_surplus_over_budget(monkeypatch):
    monkeypatch.setenv("NOTABLE_REPO_DISPATCH_ENABLED", "1")
    monkeypatch.setenv("NOTABLE_REPO_DAILY_CAP", "2")
    cands = [_repo_offer(i, f"repo {i} here", f"org{i}") for i in range(1, 6)]  # 5 candidates
    conn = _ScriptedConn([{"n": 0}, cands])   # budget 2 → defer 3
    excluded = discord_dispatcher.notable_repo_exclusions(conn, "discord")
    assert len(excluded) == 3
    assert set(excluded).issubset({c["id"] for c in cands})


def test_exclusions_defers_all_when_cap_reached(monkeypatch):
    monkeypatch.setenv("NOTABLE_REPO_DISPATCH_ENABLED", "1")
    monkeypatch.setenv("NOTABLE_REPO_DAILY_CAP", "2")
    cands = [_repo_offer(1, "alpha repo", "a"), _repo_offer(2, "beta repo", "b")]
    conn = _ScriptedConn([{"n": 2}, cands])   # already 2 sent today → defer all
    excluded = discord_dispatcher.notable_repo_exclusions(conn, "discord")
    assert set(excluded) == {1, 2}

