"""Retry transient failures with bounded exponential backoff + jitter.

LLM API calls fail transiently — network blips, `429` rate limits, `5xx` server
errors. A single such failure should never abort a whole workflow, so each
provider's network call is wrapped in `call_with_retry`. Permanent errors (bad
auth `401`, invalid request `400`) are re-raised immediately: retrying them only
burns time and quota.

The policy is data (a `RetryPolicy`) so it is fully configurable per provider,
and `sleep`/`rng` are injectable so the backoff schedule is unit-testable with no
real waiting or randomness.
"""
from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass
from typing import Callable

log = logging.getLogger(__name__)

# HTTP statuses worth retrying: request timeout, conflict, too-early, rate limit,
# and the 5xx server-side failures. Everything else (400/401/403/404/422…) is a
# permanent client error that a retry cannot fix.
RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})


@dataclass
class RetryPolicy:
    """Bounded exponential backoff. Total tries = `max_attempts` (1 initial +
    `max_attempts - 1` retries)."""
    max_attempts: int = 5
    base_delay: float = 0.5     # seconds before the first retry
    max_delay: float = 30.0     # cap on any single backoff
    multiplier: float = 2.0     # exponential growth factor per attempt
    jitter: float = 0.25        # ± this fraction of the delay, uniformly random

    def delay_for(self, attempt: int, rng: Callable[[], float] = random.random) -> float:
        """Backoff before the retry that follows a failed `attempt` (1-based)."""
        raw = self.base_delay * (self.multiplier ** (attempt - 1))
        raw = min(self.max_delay, raw)
        raw *= 1.0 + self.jitter * (2.0 * rng() - 1.0)   # rng()=0.5 -> no jitter
        return max(0.0, raw)


def is_retryable_error(exc: BaseException) -> bool:
    """Best-effort, SDK-agnostic classification of a provider exception.

    We avoid importing the anthropic/openai exception hierarchies (they are lazy
    deps and their class names drift across versions), and instead key off two
    robust signals both SDKs expose: connection/timeout error *type names*, and
    an HTTP *status code* on the exception (or its `.response`).
    """
    name = type(exc).__name__.lower()
    if "connection" in name or "timeout" in name:   # no HTTP response at all
        return True
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(exc, "status", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    return status in RETRYABLE_STATUS


def call_with_retry(fn: Callable[[], object], *,
                    policy: RetryPolicy | None = None,
                    retryable: Callable[[BaseException], bool] = is_retryable_error,
                    sleep: Callable[[float], None] = time.sleep,
                    rng: Callable[[], float] = random.random,
                    what: str = "call") -> object:
    """Call `fn()`; on a `retryable` exception, back off and retry up to
    `policy.max_attempts` times. Non-retryable errors, and the final attempt's
    error, propagate to the caller unchanged."""
    policy = policy or RetryPolicy()
    for attempt in range(1, policy.max_attempts + 1):
        try:
            return fn()
        except BaseException as exc:   # noqa: BLE001 - re-raised unless retryable
            if attempt >= policy.max_attempts or not retryable(exc):
                if attempt > 1:
                    log.warning("%s failed after %d attempt(s): %s",
                                what, attempt, exc)
                raise
            delay = policy.delay_for(attempt, rng)
            log.warning("%s failed (attempt %d/%d): %s — retrying in %.2fs",
                        what, attempt, policy.max_attempts, exc, delay)
            sleep(delay)
