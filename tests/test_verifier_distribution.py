"""Wiring tests for LLM_DISTRIBUTION_ENABLED in crawler.verifier.

Exercise the flag-gated ProviderScheduler wiring in extract_batch():
  (a) flag-off -> scheduler never built, fixed-order loop unchanged
  (b) flag-on  -> scheduler picks a non-primary provider first
  (c) flag-on  -> pick() None degrades to the fixed-order chain
  (d) flag-on  -> record() gets real usage.total_tokens, else the estimate

Coroutines are driven with asyncio.run() so the suite needs no
pytest-asyncio mode configuration.
"""
from __future__ import annotations

import asyncio

import httpx

import crawler.verifier as verifier
from crawler.provider_scheduler import estimate_tokens
from crawler.verifier import LLMExtractor, _Provider

_ITEMS = [(object(), None)]  # opaque; _build_batch_user_content is stubbed


def _providers(n: int = 3) -> list[_Provider]:
    return [
        _Provider(api_key=f"k{i}", base_url=f"https://p{i}.test", model=f"m{i}")
        for i in range(n)
    ]


def _make_extractor(monkeypatch, providers, *, distribution: bool) -> LLMExtractor:
    monkeypatch.setattr(verifier, "_load_providers", lambda: providers)
    if distribution:
        monkeypatch.setenv("LLM_DISTRIBUTION_ENABLED", "true")
    else:
        monkeypatch.delenv("LLM_DISTRIBUTION_ENABLED", raising=False)
    # Selection logic is independent of prompt content — stub the builder so
    # tests need no fully-populated NormalizedItem objects.
    monkeypatch.setattr(LLMExtractor, "_build_batch_user_content", lambda self, items: "BATCH")
    return LLMExtractor()


# --------------------------------------------------------------------------- #
# (a) flag-off: scheduler is never constructed; fixed-order loop is used
# --------------------------------------------------------------------------- #
def test_flag_off_scheduler_not_built_and_fixed_order(monkeypatch):
    provs = _providers(3)
    ext = _make_extractor(monkeypatch, provs, distribution=False)
    assert ext._scheduler is None

    order: list[str] = []

    async def fake_fixed(self, provider, content, n):
        order.append(provider.base_url)
        return [None] * n  # success on the first provider tried

    async def boom(self, *a, **k):
        raise AssertionError("metered path must not run when the flag is off")

    monkeypatch.setattr(LLMExtractor, "_try_provider_batch", fake_fixed)
    monkeypatch.setattr(LLMExtractor, "_try_provider_batch_metered", boom)

    out = asyncio.run(ext.extract_batch(_ITEMS))
    assert out == [None]
    assert order == [provs[0].base_url]  # started at primary, no scheduler call


# --------------------------------------------------------------------------- #
# (b) flag-on: scheduler skips the (throttled) primary and picks the next
# --------------------------------------------------------------------------- #
def test_flag_on_picks_non_primary_first(monkeypatch):
    provs = _providers(3)
    ext = _make_extractor(monkeypatch, provs, distribution=True)
    assert ext._scheduler is not None
    # 429 on the primary trips its breaker + drains its buckets -> pick() skips it.
    ext._scheduler.record(provs[0].base_url, status=429)

    order: list[str] = []

    async def fake_metered(self, provider, content, n):
        order.append(provider.base_url)
        return [None] * n, None, 200

    monkeypatch.setattr(LLMExtractor, "_try_provider_batch_metered", fake_metered)

    out = asyncio.run(ext.extract_batch(_ITEMS))
    assert out == [None]
    assert order[0] == provs[1].base_url  # scheduler chose the open primary's successor


# --------------------------------------------------------------------------- #
# (c) flag-on: pick() -> None degrades to the exact fixed-order chain
# --------------------------------------------------------------------------- #
def test_flag_on_pick_none_falls_back_to_fixed_order(monkeypatch):
    provs = _providers(3)
    ext = _make_extractor(monkeypatch, provs, distribution=True)
    for p in provs:
        ext._scheduler.record(p.base_url, status=429)  # every breaker open

    order: list[str] = []

    async def fake_metered(self, provider, content, n):
        order.append(provider.base_url)
        return [None] * n, None, 200

    monkeypatch.setattr(LLMExtractor, "_try_provider_batch_metered", fake_metered)

    out = asyncio.run(ext.extract_batch(_ITEMS))
    assert out == [None]
    assert order[0] == provs[0].base_url  # nothing free -> start at primary (fixed order)


# --------------------------------------------------------------------------- #
# (d) flag-on: record() receives real usage.total_tokens, else the estimate
# --------------------------------------------------------------------------- #
class _FakeResp:
    def __init__(self, data, status=200):
        self._data = data
        self.status_code = status
        self.headers = {}

    def json(self):
        return self._data

    def raise_for_status(self):
        return None


def _run_usage_case(monkeypatch, usage_field):
    provs = _providers(1)
    ext = _make_extractor(monkeypatch, provs, distribution=True)
    monkeypatch.setattr(LLMExtractor, "_parse_batch", lambda self, content, n: [None] * n)

    data = {"choices": [{"message": {"content": "{}"}}]}
    if usage_field is not None:
        data["usage"] = usage_field

    async def fake_post(self, url, json=None, headers=None):
        return _FakeResp(data)

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    captured: list[dict] = []
    orig = ext._scheduler.record

    def spy(key, **kw):
        captured.append(kw)
        return orig(key, **kw)

    monkeypatch.setattr(ext._scheduler, "record", spy)

    out = asyncio.run(ext.extract_batch(_ITEMS))
    assert out == [None]
    return captured


def test_flag_on_records_real_usage_tokens(monkeypatch):
    captured = _run_usage_case(monkeypatch, {"total_tokens": 1234})
    assert captured and captured[0]["success"] is True
    assert captured[0]["tokens"] == 1234


def test_flag_on_records_estimate_when_usage_absent(monkeypatch):
    captured = _run_usage_case(monkeypatch, None)
    assert captured and captured[0]["success"] is True
    assert captured[0]["tokens"] == estimate_tokens("BATCH")

