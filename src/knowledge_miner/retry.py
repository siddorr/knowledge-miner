from __future__ import annotations

import time
from typing import Callable, TypeVar


T = TypeVar("T")


def retry_call(
    fn: Callable[[], T],
    *,
    attempts: int = 3,
    delays: tuple[float, ...] = (1.0, 2.0, 4.0),
    should_retry: Callable[[Exception], bool] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    if attempts < 1:
        raise ValueError("attempts must be >= 1")
    last_error: Exception | None = None
    for idx in range(attempts):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            retryable = should_retry(exc) if should_retry else True
            if not retryable or idx == attempts - 1:
                raise
            delay = delays[idx] if idx < len(delays) else delays[-1]
            sleep(delay)
    assert last_error is not None
    raise last_error

