"""Tests for Polymarket quote bridge UI broadcast filtering."""
from adapters.polymarket.quote_bridge_actor import should_broadcast_quote


def test_broadcast_yes_token_direct_slug() -> None:
    meta = {
        "slug": "btc-updown-15m-123",
        "token": "yes",
        "series": None,
    }
    assert should_broadcast_quote(meta, {}) is True


def test_skip_no_token() -> None:
    meta = {
        "slug": "btc-updown-15m-123",
        "token": "no",
        "series": None,
    }
    assert should_broadcast_quote(meta, {}) is False


def test_broadcast_current_series_slug() -> None:
    series = "btc-updown-15m"
    current = f"{series}-123"
    meta = {
        "slug": current,
        "token": "yes",
        "series": series,
    }
    assert should_broadcast_quote(meta, {series: current}) is True


def test_skip_next_window_prefetch_slug() -> None:
    series = "btc-updown-15m"
    current = f"{series}-123"
    nxt = f"{series}-456"
    meta = {
        "slug": nxt,
        "token": "yes",
        "series": series,
    }
    assert should_broadcast_quote(meta, {series: current}) is False


def test_skip_no_token_even_for_current_series_slug() -> None:
    series = "btc-updown-15m"
    current = f"{series}-123"
    meta = {
        "slug": current,
        "token": "no",
        "series": series,
    }
    assert should_broadcast_quote(meta, {series: current}) is False
