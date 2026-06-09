"""Serialize Polymarket slug Gamma/loader work and backoff on FD pressure."""

from __future__ import annotations

import asyncio

# One slug load at a time per process — avoids rotate bursts exhausting FDs.
SLUG_LOAD_SEM = asyncio.Semaphore(1)

SLUG_LOAD_BACKOFF_INITIAL_SEC = 5.0
SLUG_LOAD_BACKOFF_MAX_SEC = 120.0
EMFILE_BACKOFF_MIN_SEC = 30.0


def is_fd_exhaustion(exc: BaseException) -> bool:
    if isinstance(exc, OSError) and exc.errno == 24:
        return True
    msg = str(exc).lower()
    return "too many open files" in msg or "errno 24" in msg


class SlugLoadGuard:
    """Per-actor slug retry state; share SLUG_LOAD_SEM across actors."""

    def __init__(self) -> None:
        self._retry_after_ns: dict[str, int] = {}
        self._failures: dict[str, int] = {}

    def should_skip(self, slug: str, now_ns: int) -> bool:
        return now_ns < self._retry_after_ns.get(slug, 0)

    def record_failure(self, slug: str, now_ns: int, exc: BaseException) -> float:
        count = self._failures.get(slug, 0) + 1
        self._failures[slug] = count
        delay = min(
            SLUG_LOAD_BACKOFF_INITIAL_SEC * (2 ** (count - 1)),
            SLUG_LOAD_BACKOFF_MAX_SEC,
        )
        if is_fd_exhaustion(exc):
            delay = max(delay, EMFILE_BACKOFF_MIN_SEC)
        self._retry_after_ns[slug] = now_ns + int(delay * 1_000_000_000)
        return delay

    def record_success(self, slug: str) -> None:
        self._failures.pop(slug, None)
        self._retry_after_ns.pop(slug, None)
