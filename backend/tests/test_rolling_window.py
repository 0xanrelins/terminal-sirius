"""Rolling 15m window helpers."""
from adapters.polymarket.rolling import (
    WINDOW_SEC,
    seconds_until_window_end,
    slug_for_series,
    window_start,
)


def test_seconds_until_window_end_within_window() -> None:
    t = window_start(1_700_000_100) + 100
    assert seconds_until_window_end(t) == WINDOW_SEC - 100


def test_seconds_until_window_end_at_boundary() -> None:
    t = window_start(1_700_000_000) + WINDOW_SEC
    assert seconds_until_window_end(t) == float(WINDOW_SEC)


def test_slug_for_series_uses_window_start() -> None:
    t = window_start(1_700_000_123)
    assert slug_for_series("btc-updown-15m", ts=t) == f"btc-updown-15m-{t}"
