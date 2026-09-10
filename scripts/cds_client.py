#!/usr/bin/env python3
"""Shared bounded retry settings for Copernicus CDS clients.

The legacy ``cdsapi`` client defaults to 500 retries with a two-minute delay
for transient HTTP failures.  That is appropriate for an interactive request
that can wait indefinitely, but it can leave a scheduled GitHub job stalled
for hours.  Keep the retry policy in one small, dependency-free module so all
CDS-backed workers use the same bounded behavior.
"""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Callable


DEFAULT_RETRY_MAX = 5
DEFAULT_SLEEP_MAX = 30
QUEUE_RETRY_MAX = 4
QUEUE_RETRY_SLEEP_SECONDS = 60
QUEUE_RETRY_SLEEP_MAX = 300


def _bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return min(maximum, max(minimum, value))


def client_options() -> dict[str, int]:
    """Return bounded ``cdsapi.Client`` retry options.

    The environment overrides are useful for a deliberate manual retry, while
    the defaults fail a transiently unhealthy request in a bounded interval so
    the next scheduled reconciliation can try again.
    """

    return {
        "retry_max": _bounded_int("CDS_RETRY_MAX", DEFAULT_RETRY_MAX, 1, 20),
        "sleep_max": _bounded_int("CDS_RETRY_SLEEP_MAX", DEFAULT_SLEEP_MAX, 1, 120),
    }


def _queue_limit(exc: BaseException) -> bool:
    """Return whether CDS rejected a request because its account queue is full."""

    message = str(exc).lower()
    return (
        "number queued requests" in message
        or "temporarily limited" in message
        or "too many requests" in message
    )


def retrieve_with_queue_retry(
    operation: Callable[[], object],
    *,
    label: str,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> object:
    """Run a CDS retrieval, waiting between account-queue rejections.

    ``cdsapi`` retries transport failures, but a submitted job can still be
    rejected by the service with HTTP 400 when the account queue is full.  A
    short serialized back-off lets scheduled workers recover without
    resubmitting every product or treating a transient quota response as a
    missing-data failure.
    """

    retry_max = _bounded_int("CDS_QUEUE_RETRY_MAX", QUEUE_RETRY_MAX, 0, 10)
    base_sleep = _bounded_int(
        "CDS_QUEUE_RETRY_SLEEP_SECONDS",
        QUEUE_RETRY_SLEEP_SECONDS,
        15,
        QUEUE_RETRY_SLEEP_MAX,
    )
    for attempt in range(retry_max + 1):
        try:
            return operation()
        except Exception as exc:
            if not _queue_limit(exc) or attempt >= retry_max:
                raise
            delay = min(QUEUE_RETRY_SLEEP_MAX, base_sleep * (attempt + 1))
            print(
                f"CDS queue is full for {label}; retrying in {delay} seconds "
                f"(attempt {attempt + 1}/{retry_max})",
                file=sys.stderr,
            )
            sleep_fn(delay)
    raise AssertionError("unreachable")
