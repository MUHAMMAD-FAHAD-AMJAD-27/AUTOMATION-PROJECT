"""GitHub Trending discovery adapter + scheduler wiring — Item 4a (2026-08-29).

Hermetic: no Postgres, no network, no GitHub. The adapter's Search API call is
monkeypatched at ``_search_repos``; the scheduler gate is exercised through a
fake ``adapters.github_trending_adapter`` injected into sys.modules so the lazy
import inside ``_run_github_trending`` never touches httpx.

Locked here:
  * QUERY BUILDER — created-window + star floor, one query per QUERY_FACET.
  * PAYLOAD — is_repo marker, url/handle attribution, drop on missing fields.
  * RUN — dedup by URL across facets, hard max_repos cap, dry-run prints JSON.
  * SCHEDULER GATE — fires only on open_source_repo run 1 (slot 3), swallows
    adapter/import failure, never wires anything else.
"""
from __future__ import annotations

import asyncio
import json
import sys
import types
from datetime import date, timedelta

import adapters.github_trending_adapter as gt


# --------------------------------------------------------------------------- #
# _build_queries
# --------------------------------------------------------------------------- #
def test_build_queries_one_per_facet_with_window_and_floor():
    qs = gt._build_queries(min_stars=100, within_days=14)
    assert len(qs) == len(gt.QUERY_FACETS)
    since = (date.today() - timedelta(days=14)).isoformat()
    assert qs[0] == f"created:>={since} stars:>=100"          # bare facet, no trailing space
    assert "topic:ai" in qs[1]
    assert all(f"created:>={since} stars:>=100" in q for q in qs)


# --------------------------------------------------------------------------- #
# _repo_to_payload
# --------------------------------------------------------------------------- #
def _repo(**over):
    base = {
        "html_url": "https://github.com/acme/cool-tool",
        "full_name": "acme/cool-tool",
        "description": "A self-hosted thing",
        "topics": ["ai", "self-hosted"],
        "homepage": "https://cool.tool",
        "stargazers_count": 320,
        "forks_count": 12,
        "language": "Python",
        "created_at": "2026-08-10T00:00:00Z",
        "pushed_at": "2026-08-28T00:00:00Z",
    }
    base.update(over)
    return base


def test_payload_stamps_is_repo_and_attributes_owner():
    p = gt._repo_to_payload(_repo())
    assert p is not None
    assert p["external_id"] == "https://github.com/acme/cool-tool"
    assert p["author_handle"] == "github:acme"
    assert p["extra"]["is_repo"] is True
    assert p["extra"]["stars"] == 320
    assert p["engagement"] == {"stars": 320, "forks": 12}
    # homepage is a distinct http url → included alongside the repo url
    assert p["urls"] == ["https://github.com/acme/cool-tool", "https://cool.tool"]
    assert "Topics: ai, self-hosted" in p["text"]


def test_payload_dropped_when_url_or_name_missing():
    assert gt._repo_to_payload(_repo(html_url="")) is None
    assert gt._repo_to_payload(_repo(full_name="", name="")) is None


def test_payload_omits_homepage_when_blank_or_same_as_repo():
    p = gt._repo_to_payload(_repo(homepage=""))
    assert p["urls"] == ["https://github.com/acme/cool-tool"]


# --------------------------------------------------------------------------- #
# _headers — optional token
# --------------------------------------------------------------------------- #
def test_headers_add_bearer_only_when_token_present(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert "Authorization" not in gt._headers()
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_x")
    assert gt._headers()["Authorization"] == "Bearer ghp_x"


# --------------------------------------------------------------------------- #
# run_github_trending — dedup, cap, dry-run
# --------------------------------------------------------------------------- #
def test_run_dedups_across_facets_and_caps(monkeypatch, capsys):
    # Facet 0 returns A,B; facet 1 returns B (dup),C; rest empty.
    pages = {
        gt.QUERY_FACETS[0]: [_repo(full_name="o/a", html_url="https://github.com/o/a"),
                             _repo(full_name="o/b", html_url="https://github.com/o/b")],
        gt.QUERY_FACETS[1]: [_repo(full_name="o/b", html_url="https://github.com/o/b"),
                             _repo(full_name="o/c", html_url="https://github.com/o/c")],
    }

    async def _fake_search(client, query, per_page):
        facet = query.split("stars:>=")[1].split(" ", 1)[1] if " " in query.split("stars:>=")[1] else ""
        return pages.get(facet, [])

    monkeypatch.setattr(gt, "_search_repos", _fake_search)

    total = asyncio.run(gt.run_github_trending(dry_run=True))
    assert total == 3  # A, B, C — B deduped

    printed = [json.loads(b) for b in _iter_json_objects(capsys.readouterr().out)]
    assert {p["external_id"] for p in printed} == {
        "https://github.com/o/a", "https://github.com/o/b", "https://github.com/o/c"}
    assert all(p["extra"]["is_repo"] for p in printed)


def test_run_respects_max_repos(monkeypatch, capsys):
    async def _fake_search(client, query, per_page):
        return [_repo(full_name=f"o/r{i}", html_url=f"https://github.com/o/r{i}")
                for i in range(50)]

    monkeypatch.setattr(gt, "_search_repos", _fake_search)
    total = asyncio.run(gt.run_github_trending(max_repos=5, dry_run=True))
    assert total == 5
    capsys.readouterr()  # drain


def test_run_returns_zero_when_no_matches(monkeypatch):
    async def _empty(client, query, per_page):
        return []
    monkeypatch.setattr(gt, "_search_repos", _empty)
    assert asyncio.run(gt.run_github_trending(dry_run=True)) == 0


# --------------------------------------------------------------------------- #
# Scheduler gate
# --------------------------------------------------------------------------- #
def _install_fake_trending(monkeypatch, *, run_github_trending):
    fake = types.ModuleType("adapters.github_trending_adapter")
    fake.run_github_trending = run_github_trending
    monkeypatch.setitem(sys.modules, "adapters.github_trending_adapter", fake)


def test_scheduler_fires_only_on_open_source_repo_run_1(monkeypatch):
    import scheduler
    calls: list[tuple] = []

    async def _fake(*a, **k):
        calls.append((a, k))
        return 0

    _install_fake_trending(monkeypatch, run_github_trending=_fake)

    scheduler._run_github_trending("open_source_repo", 1)
    assert len(calls) == 1

    for category in scheduler.RUN_CATEGORIES:
        for run_number in (1, 2, 3):
            if category == "open_source_repo" and run_number == 1:
                continue
            scheduler._run_github_trending(category, run_number)
    assert len(calls) == 1, "trending fired outside open_source_repo run 1"


def test_scheduler_fires_once_across_a_full_day():
    import scheduler
    slots = scheduler._build_slots()
    fires = [s for s in slots
             if s["category"] == "open_source_repo" and s["run_number"] == 1]
    assert len(fires) == 1
    assert fires[0]["slot"] == 3
    assert (fires[0]["hour"], fires[0]["minute"]) == (2, 40)


def test_scheduler_swallows_adapter_failure(monkeypatch):
    import scheduler

    async def _boom(*a, **k):
        raise RuntimeError("github 403")

    _install_fake_trending(monkeypatch, run_github_trending=_boom)
    scheduler._run_github_trending("open_source_repo", 1)  # must not raise


def test_scheduler_swallows_import_error(monkeypatch):
    import builtins
    import scheduler
    _real_import = builtins.__import__

    def _raise_import(name, *a, **k):
        if name == "adapters.github_trending_adapter":
            raise ImportError("boom")
        return _real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _raise_import)
    scheduler._run_github_trending("open_source_repo", 1)  # must not raise


# --------------------------------------------------------------------------- #
def _iter_json_objects(text: str):
    """Yield each top-level pretty-printed JSON object from concatenated stdout."""
    decoder = json.JSONDecoder()
    idx, n = 0, len(text)
    while idx < n:
        while idx < n and text[idx] in " \n\r\t":
            idx += 1
        if idx >= n:
            break
        obj, end = decoder.raw_decode(text, idx)
        yield json.dumps(obj)
        idx = end
