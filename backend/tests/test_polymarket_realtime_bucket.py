"""Unit tests for Polymarket UP mid bucketing (no Nautilus node)."""
from adapters.polymarket.quote_bridge_actor import should_broadcast_quote
from polymarket_realtime_bucket_actor import _BucketOhlcv
from bar_time import bar_open_time_ns


def test_bar_open_time_ns_1s_and_5s():
    ts = 1_700_000_000_500_000_000  # .5s
    assert bar_open_time_ns(ts, "1s") == 1_700_000_000
    assert bar_open_time_ns(ts, "5s") == 1_700_000_000


def test_bucket_ohlcv_volume_zero():
    b = _BucketOhlcv(time=100, open=0.5, high=0.6, low=0.4, close=0.55)
    assert b.volume == 0.0


def test_bucket_chart_uses_current_slug_only():
    series = "btc-updown-15m"
    current = f"{series}-123"
    nxt = f"{series}-456"
    series_slugs = {series: current}
    assert (
        should_broadcast_quote(
            {"slug": current, "token": "yes", "series": series},
            series_slugs,
        )
        is True
    )
    assert (
        should_broadcast_quote(
            {"slug": nxt, "token": "yes", "series": series},
            series_slugs,
        )
        is False
    )
