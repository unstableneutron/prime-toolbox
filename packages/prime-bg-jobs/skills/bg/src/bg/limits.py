"""Bounded-wait ceilings for :mod:`bg`.

Every wait in this package is bounded. The kernel that imports :mod:`bg` is
single-threaded and is the agent's only tool surface, so an unbounded wait
would remove the agent's ability to answer anyone for the duration.
"""

from __future__ import annotations

DEFAULT_WAIT_SECONDS = 5.0
MAX_WAIT_SECONDS = 60.0
DEFAULT_ESCALATE_SECONDS = 5.0
MAX_KILL_GRACE_SECONDS = 10.0
DEFAULT_TAIL_CHARS = 8_000
MAX_TAIL_CHARS = 200_000
POLL_INTERVAL_SECONDS = 0.05


def clamp_timeout(
    timeout: float | None,
    *,
    default: float = DEFAULT_WAIT_SECONDS,
    maximum: float = MAX_WAIT_SECONDS,
) -> float:
    """Return a non-negative wait bounded by ``maximum``.

    ``None`` selects ``default``. Larger requests are clamped rather than
    refused so a hopeful ``timeout=3600`` still returns control quickly.
    """
    if timeout is None:
        value = float(default)
    elif isinstance(timeout, bool) or not isinstance(timeout, int | float):
        raise TypeError("timeout must be a number of seconds")
    else:
        value = float(timeout)
    if value != value:
        raise ValueError("timeout must not be NaN")
    if value < 0:
        raise ValueError("timeout must not be negative")
    return min(value, float(maximum))


def clamp_tail_chars(max_chars: int | None) -> int:
    """Return a positive tail window bounded by :data:`MAX_TAIL_CHARS`."""
    if max_chars is None:
        return DEFAULT_TAIL_CHARS
    if isinstance(max_chars, bool) or not isinstance(max_chars, int):
        raise TypeError("max_chars must be an integer")
    if max_chars <= 0:
        raise ValueError("max_chars must be a positive integer")
    return min(max_chars, MAX_TAIL_CHARS)


__all__ = [
    "DEFAULT_ESCALATE_SECONDS",
    "DEFAULT_TAIL_CHARS",
    "DEFAULT_WAIT_SECONDS",
    "MAX_KILL_GRACE_SECONDS",
    "MAX_TAIL_CHARS",
    "MAX_WAIT_SECONDS",
    "POLL_INTERVAL_SECONDS",
    "clamp_tail_chars",
    "clamp_timeout",
]
