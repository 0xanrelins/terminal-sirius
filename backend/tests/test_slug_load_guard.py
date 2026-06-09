"""Tests for Polymarket slug load backoff / FD exhaustion guard."""

import pytest

from adapters.polymarket.slug_load_guard import (
    EMFILE_BACKOFF_MIN_SEC,
    SLUG_LOAD_BACKOFF_INITIAL_SEC,
    SlugLoadGuard,
    is_fd_exhaustion,
)


def test_is_fd_exhaustion_oserror():
    assert is_fd_exhaustion(OSError(24, "Too many open files")) is True
    assert is_fd_exhaustion(OSError(8, "nodename nor servname")) is False


def test_is_fd_exhaustion_message():
    assert is_fd_exhaustion(RuntimeError("errno 24: too many open files")) is True


def test_should_skip_before_retry_after():
    guard = SlugLoadGuard()
    now = 1_000_000_000_000
    guard._retry_after_ns["slug-a"] = now + 5_000_000_000
    assert guard.should_skip("slug-a", now) is True
    assert guard.should_skip("slug-a", now + 6_000_000_000) is False


def test_record_failure_exponential_backoff():
    guard = SlugLoadGuard()
    now = 0
    d1 = guard.record_failure("s", now, LookupError("missing"))
    d2 = guard.record_failure("s", now, LookupError("missing"))
    assert d1 == SLUG_LOAD_BACKOFF_INITIAL_SEC
    assert d2 == SLUG_LOAD_BACKOFF_INITIAL_SEC * 2
    assert guard.should_skip("s", now + 1) is True
    assert guard.should_skip("s", now + int(d2 * 1_000_000_000) + 1) is False


def test_record_failure_emfile_minimum_delay():
    guard = SlugLoadGuard()
    delay = guard.record_failure("s", 0, OSError(24, "Too many open files"))
    assert delay >= EMFILE_BACKOFF_MIN_SEC


def test_record_success_clears_state():
    guard = SlugLoadGuard()
    guard.record_failure("s", 0, LookupError("x"))
    guard.record_success("s")
    assert guard.should_skip("s", 0) is False
    assert guard._failures.get("s") is None
