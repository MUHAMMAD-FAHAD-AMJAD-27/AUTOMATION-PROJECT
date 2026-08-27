"""
crawler/provider_scheduler.py — capacity-aware provider selection (Step 1)
==========================================================================
STANDALONE, INERT primitives for the multi-LLM distribution rollout.

This module contains ONLY pure logic — a dual token bucket (RPM + TPM) and a
circuit-breaker state machine — plus the config that drives them. It makes no
API calls, touches no database, and is imported by nothing in the production
path yet. Wiring into ``verifier.extract_batch()`` and the pipeline batch loop
is a later, separately-approved step.

Design notes (see Phase 16 plan):
  * State is in-process only. Valid because the deployment runs a single
    always-on scheduler process (Procfile: ``scheduler: python scheduler.py``).
  * Rate limits are configurable with conservative defaults, because most free
    tiers do not publish exact RPM/TPM. The buckets are a *soft* governor on
    top of the reactive 429/Retry-After handling that already lives in
    verifier.py — never the sole guard.
  * ``time_fn`` is injectable so the buckets and breaker are unit-testable
    without real sleeps.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable

# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
DEFAULT_PRIMARY_RPM = 30      # Groq free tier (official)
DEFAULT_PRIMARY_TPM = 8_000   # Groq free tier (official)
DEFAULT_FALLBACK_RPM = 20     # OpenRouter free = 20 RPM; conservative for others
DEFAULT_FALLBACK_TPM = 6_000  # conservative: most free-tier TPMs are unpublished


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    try:
        return float(raw) if raw else default
    except ValueError:
        return default


@dataclass(frozen=True)
class SchedulerConfig:
    """Global knobs. ``max_concurrent_batches`` is stored for the later
    concurrency step; it has no effect on the primitives in this module."""
    max_concurrent_batches: int = 3
    breaker_fails: int = 3
    breaker_cooldown_s: float = 60.0

    @classmethod
    def from_env(cls) -> "SchedulerConfig":
        return cls(
            max_concurrent_batches=_int_env("LLM_MAX_CONCURRENT_BATCHES", 3),
            breaker_fails=_int_env("LLM_BREAKER_FAILS", 3),
            breaker_cooldown_s=_float_env("LLM_BREAKER_COOLDOWN_S", 60.0),
        )


@dataclass(frozen=True)
class ProviderLimits:
    """Per-provider rate ceilings. ``rpm``/``tpm`` <= 0 means 'unlimited'."""
    key: str
    rpm: int
    tpm: int


def estimate_tokens(text: str, reply_reserve: int = 512) -> int:
    """chars/4 request estimate + a fixed reply reserve.

    Deliberately approximate — see module docstring. Exactness is not needed
    because a 429 with Retry-After overrides any estimate drift downstream.
    """
    return len(text) // 4 + reply_reserve


# --------------------------------------------------------------------------- #
# Token bucket (continuous refill)
# --------------------------------------------------------------------------- #
class _TokenBucket:
    """One-minute burst capacity, refilled continuously at rate/60 per second."""

    def __init__(self, rate_per_min: float, time_fn: Callable[[], float]) -> None:
        self.unlimited = rate_per_min <= 0
        self.capacity = float(rate_per_min)
        self.tokens = float(rate_per_min)
        self.refill_per_sec = rate_per_min / 60.0
        self._time = time_fn
        self._last = time_fn()

    def _refill(self) -> None:
        if self.unlimited:
            return
        now = self._time()
        elapsed = now - self._last
        if elapsed > 0:
            self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_per_sec)
            self._last = now

    def available(self) -> float:
        self._refill()
        return float("inf") if self.unlimited else self.tokens

    def can_afford(self, amount: float) -> bool:
        return self.unlimited or self.available() >= amount

    def consume(self, amount: float) -> None:
        if self.unlimited:
            return
        self._refill()
        self.tokens -= amount  # may dip negative under estimate drift; refill recovers

    def drain(self) -> None:
        """Empty the bucket — used when a provider signals 429/overload."""
        if self.unlimited:
            return
        self._refill()
        self.tokens = 0.0


# --------------------------------------------------------------------------- #
# Circuit breaker
# --------------------------------------------------------------------------- #
class BreakerState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class _CircuitBreaker:
    """closed → open → half_open → closed.

    * CLOSED→OPEN: ``breaker_fails`` consecutive failures, or an immediate
      ``trip()`` (used for 429/5xx).
    * OPEN: rejects until cooldown elapses, then promotes to HALF_OPEN and
      permits exactly one trial request.
    * HALF_OPEN: success → CLOSED; failure → OPEN (cooldown restarts).
    """

    def __init__(self, fail_threshold: int, cooldown_s: float,
                 time_fn: Callable[[], float]) -> None:
        self.fail_threshold = max(1, fail_threshold)
        self.cooldown_s = cooldown_s
        self._time = time_fn
        self.state = BreakerState.CLOSED
        self.consecutive_failures = 0
        self.cooldown_until = 0.0
        self._trial_in_flight = False

    def allow(self) -> bool:
        if self.state == BreakerState.CLOSED:
            return True
        if self.state == BreakerState.OPEN:
            if self._time() >= self.cooldown_until:
                self.state = BreakerState.HALF_OPEN
                self._trial_in_flight = True
                return True  # single trial
            return False
        # HALF_OPEN: only the one outstanding trial is allowed
        return False

    def on_success(self) -> None:
        self.state = BreakerState.CLOSED
        self.consecutive_failures = 0
        self._trial_in_flight = False

    def on_failure(self, retry_after: float | None = None) -> None:
        self.consecutive_failures += 1
        if self.state == BreakerState.HALF_OPEN or self.consecutive_failures >= self.fail_threshold:
            self._open(retry_after)

    def trip(self, retry_after: float | None = None) -> None:
        """Force OPEN immediately (429/5xx), regardless of failure count."""
        self.consecutive_failures += 1
        self._open(retry_after)

    def _open(self, retry_after: float | None) -> None:
        self.state = BreakerState.OPEN
        cooldown = retry_after if retry_after is not None else self.cooldown_s
        self.cooldown_until = self._time() + cooldown
        self._trial_in_flight = False


# --------------------------------------------------------------------------- #
# Per-provider state + scheduler
# --------------------------------------------------------------------------- #
class _ProviderState:
    def __init__(self, limits: ProviderLimits, config: SchedulerConfig,
                 time_fn: Callable[[], float]) -> None:
        self.limits = limits
        self.requests = _TokenBucket(limits.rpm, time_fn)
        self.tokens_bucket = _TokenBucket(limits.tpm, time_fn)
        self.breaker = _CircuitBreaker(config.breaker_fails, config.breaker_cooldown_s, time_fn)
        self.last_estimated: int | None = None


class ProviderScheduler:
    """Capacity-aware provider selection over an ordered provider list.

    ``pick()`` returns the highest-priority provider key that is both
    breaker-allowed and within its RPM+TPM budget, tentatively charging its
    buckets; ``record()`` feeds the outcome back. Provider *priority order*
    (primary first, then fallbacks) is preserved as the tie-break, so behavior
    stays close to today's ordered chain while skipping throttled/dead slots.
    """

    def __init__(self, limits: list[ProviderLimits], config: SchedulerConfig,
                 time_fn: Callable[[], float] = time.monotonic) -> None:
        self._time = time_fn
        self.config = config
        self._states: dict[str, _ProviderState] = {}
        self._order: list[str] = []
        for lim in limits:
            self._states[lim.key] = _ProviderState(lim, config, time_fn)
            self._order.append(lim.key)

    @classmethod
    def from_env(cls, provider_keys: list[str],
                 time_fn: Callable[[], float] = time.monotonic) -> "ProviderScheduler":
        """Build limits from the LLM_PRIMARY_* / LLM_FALLBACK_N_* env vars.

        ``provider_keys`` must be in priority order (index 0 = primary).
        """
        limits: list[ProviderLimits] = []
        for i, key in enumerate(provider_keys):
            if i == 0:
                rpm = _int_env("LLM_PRIMARY_RPM", DEFAULT_PRIMARY_RPM)
                tpm = _int_env("LLM_PRIMARY_TPM", DEFAULT_PRIMARY_TPM)
            else:
                rpm = _int_env(f"LLM_FALLBACK_{i}_RPM", DEFAULT_FALLBACK_RPM)
                tpm = _int_env(f"LLM_FALLBACK_{i}_TPM", DEFAULT_FALLBACK_TPM)
            limits.append(ProviderLimits(key, rpm, tpm))
        return cls(limits, SchedulerConfig.from_env(), time_fn)

    def pick(self, estimated_tokens: int) -> str | None:
        """Return the best eligible provider key, or None if all are exhausted
        or circuit-open. Tentatively charges the chosen provider's buckets."""
        for key in self._order:
            st = self._states[key]
            if (st.breaker.allow()
                    and st.requests.can_afford(1)
                    and st.tokens_bucket.can_afford(estimated_tokens)):
                st.requests.consume(1)
                st.tokens_bucket.consume(estimated_tokens)
                st.last_estimated = estimated_tokens
                return key
        return None

    def record(self, key: str, *, tokens: int | None = None,
               status: int | None = None, retry_after: float | None = None,
               success: bool | None = None) -> None:
        """Feed a request outcome back into the buckets and breaker.

        * success (2xx or ``success=True``): breaker closes.
        * 429 / 5xx: breaker trips immediately and both buckets drain.
        * any other failure: breaker counts toward its threshold.
        ``tokens`` (actual usage) reconciles any under-estimate from pick().
        """
        st = self._states.get(key)
        if st is None:
            return
        if tokens is not None and st.last_estimated is not None:
            delta = tokens - st.last_estimated
            if delta > 0:
                st.tokens_bucket.consume(delta)
        st.last_estimated = None

        is_success = success is True or (status is not None and 200 <= status < 300)
        is_rate_or_server = status is not None and (status == 429 or status >= 500)

        if is_success:
            st.breaker.on_success()
        elif is_rate_or_server:
            st.breaker.trip(retry_after)
            st.requests.drain()
            st.tokens_bucket.drain()
        else:
            st.breaker.on_failure(retry_after)

    def state_of(self, key: str) -> BreakerState:
        """Breaker state for a provider — introspection aid for logs/tests."""
        return self._states[key].breaker.state
