from fastapi import HTTPException

from knowledge_miner.auth import require_api_key
from knowledge_miner.config import settings
from knowledge_miner.rate_limit import InMemoryRateLimiter
from knowledge_miner.rate_limit import require_rate_limit
from knowledge_miner.retry import retry_call


def test_rate_limiter_blocks_after_limit():
    limiter = InMemoryRateLimiter(limit=3, window_seconds=60)
    assert limiter.check("k", now=100.0)
    assert limiter.check("k", now=101.0)
    assert limiter.check("k", now=102.0)
    assert not limiter.check("k", now=103.0)


def test_rate_limiter_resets_after_window():
    limiter = InMemoryRateLimiter(limit=2, window_seconds=10)
    assert limiter.check("k", now=100.0)
    assert limiter.check("k", now=101.0)
    assert not limiter.check("k", now=102.0)
    assert limiter.check("k", now=111.0)


def test_retry_call_retries_then_succeeds():
    state = {"n": 0}

    def flaky() -> str:
        state["n"] += 1
        if state["n"] < 3:
            raise RuntimeError("temporary")
        return "ok"

    out = retry_call(flaky, attempts=3, delays=(0.0, 0.0, 0.0), sleep=lambda _: None)
    assert out == "ok"
    assert state["n"] == 3


def test_retry_call_respects_non_retryable():
    state = {"n": 0}

    def always_fail() -> str:
        state["n"] += 1
        raise ValueError("bad request")

    try:
        retry_call(
            always_fail,
            attempts=3,
            delays=(0.0, 0.0, 0.0),
            should_retry=lambda exc: not isinstance(exc, ValueError),
            sleep=lambda _: None,
        )
        assert False, "expected ValueError"
    except ValueError:
        pass
    assert state["n"] == 1


def test_require_api_key_allows_when_auth_disabled():
    original_auth = settings.auth_enabled
    try:
        object.__setattr__(settings, "auth_enabled", False)
        assert require_api_key(None) == "auth_disabled"
    finally:
        object.__setattr__(settings, "auth_enabled", original_auth)


def test_require_api_key_validates_when_auth_enabled():
    original_auth = settings.auth_enabled
    original_token = settings.api_token
    try:
        object.__setattr__(settings, "auth_enabled", True)
        object.__setattr__(settings, "api_token", "token-1")
        assert require_api_key("Bearer token-1") == "token-1"
        try:
            require_api_key(None)
            assert False, "expected HTTPException"
        except HTTPException as exc:
            assert exc.status_code == 401
    finally:
        object.__setattr__(settings, "auth_enabled", original_auth)
        object.__setattr__(settings, "api_token", original_token)


def test_require_rate_limit_noop_when_auth_disabled():
    original_auth = settings.auth_enabled
    try:
        object.__setattr__(settings, "auth_enabled", False)
        require_rate_limit("any")
    finally:
        object.__setattr__(settings, "auth_enabled", original_auth)
