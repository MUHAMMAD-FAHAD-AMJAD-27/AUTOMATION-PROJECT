"""Unit tests for crawler.provider_scheduler (pure logic — no API, no DB).

Uses an injected FakeClock so bucket refill and breaker cooldown are tested
deterministically without real sleeps.
"""
from __future__ import annotations

import pytest

from crawler.provider_scheduler import (
    BreakerState,
    ProviderLimits,
    ProviderScheduler,
    SchedulerConfig,
    _CircuitBreaker,
    _TokenBucket,
    estimate_tokens,
)


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


# --------------------------------------------------------------------------- #
# Token bucket
# --------------------------------------------------------------------------- #
def test_bucket_starts_full_and_exhausts():
    clock = FakeClock()
    b = _TokenBucket(rate_per_min=60, time_fn=clock)  # 1 token/sec
    assert b.available() == 60
    b.consume(60)
    assert b.can_afford(1) is False


def test_bucket_refills_continuously():
    clock = FakeClock()
    b = _TokenBucket(rate_per_min=60, time_fn=clock)  # 1 token/sec
    b.consume(60)
    assert b.can_afford(1) is False
    clock.advance(10)  # +10 tokens
    assert b.available() == pytest.approx(10)
    assert b.can_afford(10) is True
    assert b.can_afford(11) is False


def test_bucket_refill_capped_at_capacity():
    clock = FakeClock()
    b = _TokenBucket(rate_per_min=60, time_fn=clock)
    b.consume(30)
    clock.advance(600)  # would overfill
    assert b.available() == 60  # capped


def test_bucket_zero_rate_is_unlimited():
    clock = FakeClock()
    b = _TokenBucket(rate_per_min=0, time_fn=clock)
    assert b.can_afford(10_000) is True
    b.consume(10_000)
    assert b.can_afford(10_000) is True


def test_bucket_drain_empties():
    clock = FakeClock()
    b = _TokenBucket(rate_per_min=100, time_fn=clock)
    b.drain()
    assert b.can_afford(1) is False


# --------------------------------------------------------------------------- #
# Circuit breaker — all four transitions
# --------------------------------------------------------------------------- #
def test_breaker_trips_after_threshold_failures():
    clock = FakeClock()
    cb = _CircuitBreaker(fail_threshold=3, cooldown_s=60, time_fn=clock)
    assert cb.state == BreakerState.CLOSED
    cb.on_failure()
    cb.on_failure()
    assert cb.state == BreakerState.CLOSED  # not yet
    cb.on_failure()
    assert cb.state == BreakerState.OPEN


def test_breaker_trips_immediately_on_429_or_5xx():
    clock = FakeClock()
    cb = _CircuitBreaker(fail_threshold=3, cooldown_s=60, time_fn=clock)
    cb.trip(retry_after=None)
    assert cb.state == BreakerState.OPEN
    assert cb.allow() is False  # still in cooldown


def test_breaker_open_to_halfopen_after_cooldown():
    clock = FakeClock()
    cb = _CircuitBreaker(fail_threshold=1, cooldown_s=30, time_fn=clock)
    cb.trip()
    assert cb.allow() is False
    clock.advance(30)
    assert cb.allow() is True  # single trial
    assert cb.state == BreakerState.HALF_OPEN
    assert cb.allow() is False  # no second concurrent trial


def test_breaker_halfopen_success_closes():
    clock = FakeClock()
    cb = _CircuitBreaker(fail_threshold=1, cooldown_s=30, time_fn=clock)
    cb.trip()
    clock.advance(30)
    cb.allow()  # -> HALF_OPEN
    cb.on_success()
    assert cb.state == BreakerState.CLOSED
    assert cb.allow() is True


def test_breaker_halfopen_failure_reopens():
    clock = FakeClock()
    cb = _CircuitBreaker(fail_threshold=3, cooldown_s=30, time_fn=clock)
    cb.trip()
    clock.advance(30)
    cb.allow()  # -> HALF_OPEN
    cb.on_failure()  # single failure in HALF_OPEN must reopen regardless of threshold
    assert cb.state == BreakerState.OPEN


def test_breaker_uses_retry_after_over_default():
    clock = FakeClock()
    cb = _CircuitBreaker(fail_threshold=1, cooldown_s=60, time_fn=clock)
    cb.trip(retry_after=5)
    clock.advance(5)
    assert cb.allow() is True  # retry_after (5s) beat the 60s default


# --------------------------------------------------------------------------- #
# Scheduler pick() / record()
# --------------------------------------------------------------------------- #
def _sched(clock, rpm=60, tpm=10_000, fails=3, cooldown=60, keys=("a", "b")):
    limits = [ProviderLimits(k, rpm, tpm) for k in keys]
    cfg = SchedulerConfig(breaker_fails=fails, breaker_cooldown_s=cooldown)
    return ProviderScheduler(limits, cfg, time_fn=clock)


def test_pick_prefers_priority_order():
    clock = FakeClock()
    s = _sched(clock)
    assert s.pick(estimated_tokens=100) == "a"  # primary first


def test_pick_skips_exhausted_provider():
    clock = FakeClock()
    s = _sched(clock, rpm=1)  # each provider affords exactly 1 request
    assert s.pick(100) == "a"
    assert s.pick(100) == "b"  # a is out of RPM, fall to b
    assert s.pick(100) is None  # both exhausted


def test_pick_returns_none_when_all_rate_exhausted():
    clock = FakeClock()
    s = _sched(clock, tpm=100, keys=("a",))
    assert s.pick(100) == "a"
    assert s.pick(1) is None  # token bucket drained


def test_pick_returns_none_when_all_open():
    clock = FakeClock()
    s = _sched(clock, keys=("a", "b"))
    s.record("a", status=429)
    s.record("b", status=500)
    assert s.pick(100) is None
    assert s.state_of("a") == BreakerState.OPEN
    assert s.state_of("b") == BreakerState.OPEN


def test_record_429_drains_bucket_and_opens():
    clock = FakeClock()
    s = _sched(clock, keys=("a",))
    s.pick(100)
    s.record("a", status=429, retry_after=10)
    assert s.state_of("a") == BreakerState.OPEN
    # bucket drained AND breaker open → not pickable until cooldown
    assert s.pick(100) is None
    clock.advance(10)  # cooldown + refill
    assert s.pick(100) == "a"


def test_record_success_resets_breaker():
    clock = FakeClock()
    s = _sched(clock, fails=2, keys=("a",))
    s.record("a", success=False, status=None)  # 1 generic failure
    s.record("a", status=200)  # success resets the counter
    s.record("a", success=False, status=None)  # 1 again, below threshold
    assert s.state_of("a") == BreakerState.CLOSED


def test_record_reconciles_token_underestimate():
    clock = FakeClock()
    s = _sched(clock, tpm=1_000, keys=("a",))
    s.pick(estimated_tokens=100)      # charged 100
    s.record("a", tokens=900, status=200)  # actual 900 → charge extra 800
    # 1000 - 100 - 800 = 100 left
    assert s.pick(estimated_tokens=100) == "a"
    assert s.pick(estimated_tokens=1) is None


# --------------------------------------------------------------------------- #
# Config from env
# --------------------------------------------------------------------------- #
def test_estimate_tokens():
    assert estimate_tokens("x" * 40, reply_reserve=0) == 10
    assert estimate_tokens("", reply_reserve=512) == 512


def test_scheduler_config_from_env(monkeypatch):
    monkeypatch.setenv("LLM_MAX_CONCURRENT_BATCHES", "7")
    monkeypatch.setenv("LLM_BREAKER_FAILS", "5")
    monkeypatch.setenv("LLM_BREAKER_COOLDOWN_S", "12.5")
    cfg = SchedulerConfig.from_env()
    assert cfg.max_concurrent_batches == 7
    assert cfg.breaker_fails == 5
    assert cfg.breaker_cooldown_s == 12.5


def test_from_env_maps_primary_and_fallback_limits(monkeypatch):
    monkeypatch.setenv("LLM_PRIMARY_RPM", "99")
    monkeypatch.setenv("LLM_PRIMARY_TPM", "111")
    monkeypatch.setenv("LLM_FALLBACK_1_RPM", "22")
    monkeypatch.setenv("LLM_FALLBACK_1_TPM", "333")
    clock = FakeClock()
    s = ProviderScheduler.from_env(["groq", "openrouter"], time_fn=clock)
    assert s._states["groq"].limits.rpm == 99
    assert s._states["groq"].limits.tpm == 111
    assert s._states["openrouter"].limits.rpm == 22
    assert s._states["openrouter"].limits.tpm == 333
