"""Unit tests for the LLM retry/backoff layer (no network, no real sleeping)."""
import pytest

from saage.retry import (RetryPolicy, call_with_retry, is_retryable_error,
                       RETRYABLE_STATUS)


# --- fake provider-style exceptions (mimic the anthropic/openai shapes) ----- #
class FakeStatusError(Exception):
    def __init__(self, status_code):
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


class FakeConnectionError(Exception):
    pass


class FakeTimeoutError(Exception):
    pass


class _Resp:
    def __init__(self, status_code):
        self.status_code = status_code


class FakeRespError(Exception):
    """Status lives on a nested `.response`, like some SDK errors."""
    def __init__(self, status_code):
        super().__init__("boom")
        self.response = _Resp(status_code)


# --------------------------------------------------------------------------- #
# classification
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("status", sorted(RETRYABLE_STATUS))
def test_retryable_statuses(status):
    assert is_retryable_error(FakeStatusError(status)) is True


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_permanent_statuses_not_retried(status):
    assert is_retryable_error(FakeStatusError(status)) is False


def test_connection_and_timeout_are_retryable():
    assert is_retryable_error(FakeConnectionError()) is True
    assert is_retryable_error(FakeTimeoutError()) is True


def test_status_on_nested_response():
    assert is_retryable_error(FakeRespError(503)) is True
    assert is_retryable_error(FakeRespError(404)) is False


def test_unknown_error_is_not_retryable():
    assert is_retryable_error(ValueError("nope")) is False


# --------------------------------------------------------------------------- #
# call_with_retry
# --------------------------------------------------------------------------- #
def _recorder():
    slept: list[float] = []
    return slept, (lambda d: slept.append(d))


def test_succeeds_first_try_no_sleep():
    slept, sleep = _recorder()
    calls = []
    out = call_with_retry(lambda: calls.append(1) or "ok", sleep=sleep)
    assert out == "ok"
    assert calls == [1] and slept == []          # no retries, no backoff


def test_retries_then_succeeds():
    slept, sleep = _recorder()
    attempts = {"n": 0}

    def fn():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise FakeStatusError(503)
        return "recovered"

    out = call_with_retry(fn, policy=RetryPolicy(max_attempts=5),
                          sleep=sleep, rng=lambda: 0.5)
    assert out == "recovered"
    assert attempts["n"] == 3
    assert len(slept) == 2                        # backed off before each retry


def test_gives_up_after_max_attempts_and_reraises():
    slept, sleep = _recorder()
    attempts = {"n": 0}

    def fn():
        attempts["n"] += 1
        raise FakeStatusError(500)

    with pytest.raises(FakeStatusError):
        call_with_retry(fn, policy=RetryPolicy(max_attempts=4),
                        sleep=sleep, rng=lambda: 0.5)
    assert attempts["n"] == 4                      # exactly max_attempts tries
    assert len(slept) == 3                         # slept between the 4 tries


def test_non_retryable_raises_immediately():
    slept, sleep = _recorder()
    attempts = {"n": 0}

    def fn():
        attempts["n"] += 1
        raise FakeStatusError(400)                 # permanent client error

    with pytest.raises(FakeStatusError):
        call_with_retry(fn, sleep=sleep)
    assert attempts["n"] == 1                      # no retry on a 400
    assert slept == []


# --------------------------------------------------------------------------- #
# backoff schedule
# --------------------------------------------------------------------------- #
def test_exponential_schedule_without_jitter():
    # rng()=0.5 -> the jitter term is exactly 0, so delays are deterministic.
    p = RetryPolicy(base_delay=1.0, multiplier=2.0, max_delay=30.0, jitter=0.25)
    delays = [p.delay_for(a, rng=lambda: 0.5) for a in range(1, 6)]
    assert delays == [1.0, 2.0, 4.0, 8.0, 16.0]


def test_delay_is_capped_at_max():
    p = RetryPolicy(base_delay=10.0, multiplier=10.0, max_delay=15.0, jitter=0.0)
    assert p.delay_for(3, rng=lambda: 0.5) == 15.0   # 10*10^2 capped to 15


def test_jitter_bounds():
    p = RetryPolicy(base_delay=4.0, multiplier=1.0, max_delay=100.0, jitter=0.25)
    lo = p.delay_for(1, rng=lambda: 0.0)             # -25%
    hi = p.delay_for(1, rng=lambda: 1.0)             # +25%
    assert lo == pytest.approx(3.0) and hi == pytest.approx(5.0)


def test_jitter_never_exceeds_max_delay():
    # jitter is applied BEFORE the cap, so even max positive jitter on a delay that
    # already blew past max_delay must not exceed the cap.
    p = RetryPolicy(base_delay=100.0, multiplier=2.0, max_delay=30.0, jitter=0.25)
    assert p.delay_for(5, rng=lambda: 1.0) == 30.0   # 100*16*1.25 -> clamped to 30


# --------------------------------------------------------------------------- #
# max_attempts edge: never silently skip the call
# --------------------------------------------------------------------------- #
def test_max_attempts_below_one_still_calls_once():
    slept, sleep = _recorder()
    calls = []
    out = call_with_retry(lambda: calls.append(1) or "ok",
                          policy=RetryPolicy(max_attempts=0), sleep=sleep)
    assert out == "ok" and calls == [1] and slept == []   # one try, no retry, no None


def test_max_attempts_below_one_failing_reraises_after_one_try():
    attempts = {"n": 0}

    def fn():
        attempts["n"] += 1
        raise FakeStatusError(500)

    with pytest.raises(FakeStatusError):
        call_with_retry(fn, policy=RetryPolicy(max_attempts=0), sleep=lambda d: None)
    assert attempts["n"] == 1


def test_malformed_response_body_is_retryable():
    """A garbage body behind a 200 (proxy page, truncated stream) must retry,
    not kill the run — seen live: OpenRouter returned non-JSON once and the
    JSONDecodeError aborted an 18-experiment hill-climb."""
    import json as _json

    from saage.retry import call_with_retry, is_retryable_error

    exc = _json.JSONDecodeError("Expecting value", "<html>bad gateway</html>", 0)
    assert is_retryable_error(exc)

    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise exc
        return "ok"

    assert call_with_retry(flaky, sleep=lambda s: None) == "ok"
    assert calls["n"] == 2
