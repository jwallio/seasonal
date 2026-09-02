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


DEFAULT_RETRY_MAX = 5
DEFAULT_SLEEP_MAX = 30


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
