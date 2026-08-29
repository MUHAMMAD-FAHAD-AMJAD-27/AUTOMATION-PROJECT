"""Twitter (social_stealth) scheduler wiring — Item 1 (2026-08-29).

Hermetic: no Postgres, no network, no browser, no patchright. The real
``adapters.social_stealth`` imports patchright at module top, so these tests
inject a fake module into ``sys.modules`` before ``scheduler._run_twitter``'s
lazy ``from adapters.social_stealth import run_twitter`` runs. That keeps the
gate assertions independent of whether a browser stack is installed.

What is locked here:

  * FREQUENCY GATE — ``scheduler._run_twitter`` invokes ``run_twitter`` on
    exactly one slot/day (``all_deals`` run 1, the 07:07 UTC slot) and no-ops on
    every other lane/run — the same once/day gate as firecrawl/github, chosen for
    account safety with a single identity and no proxy.
  * FAILURE ISOLATION — a raising adapter is swallowed to a ``log.warning`` and
    never propagates, so a bad social run cannot abort the slot's pipeline.
  * INSTAGRAM STAYS DARK — the scheduler wires only Twitter; no instagram call
    site exists.
"""
from __future__ import annotations

import sys
import types

import scheduler


def _install_fake_social(monkeypatch, *, run_twitter):
    """Put a fake ``adapters.social_stealth`` in sys.modules so the lazy import
    inside ``_run_twitter`` resolves to it without importing patchright."""
    fake = types.ModuleType("adapters.social_stealth")
    fake.run_twitter = run_twitter
    monkeypatch.setitem(sys.modules, "adapters.social_stealth", fake)


# --------------------------------------------------------------------------- #
# Frequency gate
# --------------------------------------------------------------------------- #
def test_twitter_runs_only_on_all_deals_run_1(monkeypatch):
    calls: list[tuple] = []

    async def _fake_run_twitter(*args, **kwargs):
        calls.append((args, kwargs))
        return 0

    _install_fake_social(monkeypatch, run_twitter=_fake_run_twitter)

    # The single slot that should fire it.
    scheduler._run_twitter("all_deals", 1)
    assert len(calls) == 1

    # Every other lane / run must no-op — no adapter call at all.
    for category in scheduler.RUN_CATEGORIES:
        for run_number in (1, 2, 3):
            if category == "all_deals" and run_number == 1:
                continue
            scheduler._run_twitter(category, run_number)
    assert len(calls) == 1, "twitter fired outside the all_deals run-1 slot"


def test_twitter_fires_once_across_a_full_day():
    """Across the 27-slot day the gate opens exactly once (slot 8, 07:07 UTC)."""
    slots = scheduler._build_slots()
    fires = [s for s in slots
             if s["category"] == "all_deals" and s["run_number"] == 1]
    assert len(fires) == 1
    only = fires[0]
    assert only["slot"] == 8
    assert (only["hour"], only["minute"]) == (7, 7)


def test_twitter_called_with_defaults_no_max_items(monkeypatch):
    """run_twitter takes only (terms, dry_run); the scheduler calls it bare so
    both default (DEFAULT_TWITTER_TERMS, live). It has no max_items knob."""
    seen: list[tuple] = []

    async def _fake_run_twitter(*args, **kwargs):
        seen.append((args, kwargs))
        return 3

    _install_fake_social(monkeypatch, run_twitter=_fake_run_twitter)
    scheduler._run_twitter("all_deals", 1)
    assert seen == [((), {})]


# --------------------------------------------------------------------------- #
# Failure isolation
# --------------------------------------------------------------------------- #
def test_twitter_adapter_failure_is_swallowed(monkeypatch):
    """A raising adapter must not propagate out of the slot callback."""
    async def _boom(*args, **kwargs):
        raise RuntimeError("auth wall / patchright missing")

    _install_fake_social(monkeypatch, run_twitter=_boom)
    # Must not raise.
    scheduler._run_twitter("all_deals", 1)


def test_twitter_import_error_is_swallowed(monkeypatch):
    """If social_stealth (or patchright) is unimportable on the dyno, the gated
    slot degrades to a logged warning instead of crashing the pipeline."""
    def _raise_import(name, *a, **k):
        if name == "adapters.social_stealth":
            raise ImportError("No module named 'patchright'")
        return _real_import(name, *a, **k)

    import builtins
    _real_import = builtins.__import__
    monkeypatch.setattr(builtins, "__import__", _raise_import)
    # Must not raise.
    scheduler._run_twitter("all_deals", 1)


# --------------------------------------------------------------------------- #
# Instagram stays dark
# --------------------------------------------------------------------------- #
def test_scheduler_does_not_wire_instagram():
    """No instagram call site — Item 1 wires Twitter only."""
    import inspect
    src = inspect.getsource(scheduler)
    assert "run_instagram" not in src
    assert "_run_instagram" not in src
