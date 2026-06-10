"""Unit tests for Polymarket UP mid bucketing (no Nautilus node)."""
from unittest.mock import MagicMock
from unittest.mock import PropertyMock
from unittest.mock import patch

from nautilus_trader.model.identifiers import InstrumentId

from adapters.polymarket.messages import ActivePolymarketMarket
from adapters.polymarket.quote_bridge_actor import should_broadcast_quote
from polymarket_realtime_bucket_actor import PolymarketRealtimeBucketActor
from polymarket_realtime_bucket_actor import PolymarketRealtimeBucketActorConfig
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


def test_on_active_market_subscribes_when_instrument_in_cache():
    actor = PolymarketRealtimeBucketActor(
        PolymarketRealtimeBucketActorConfig(series=("btc-updown-15m",)),
        data_queue=MagicMock(),
    )
    actor.subscribe_quote_ticks = MagicMock()
    iid = InstrumentId.from_str("0xabc-YES.POLYMARKET")
    inst = MagicMock()
    cache = MagicMock()
    with patch.object(type(actor), "cache", new_callable=PropertyMock, return_value=cache):
        cache.instrument.return_value = inst
        actor._on_active_market(
            ActivePolymarketMarket(
                instrument_id=iid,
                no_instrument_id=iid,
                series="btc-updown-15m",
                slug="btc-updown-15m-123",
                question="q",
                ts_event=1,
                ts_init=1,
            ),
        )
    actor.subscribe_quote_ticks.assert_called_once_with(iid)
    assert actor._series_slugs["btc-updown-15m"] == "btc-updown-15m-123"


def test_on_active_market_defers_without_cache_instrument():
    actor = PolymarketRealtimeBucketActor(
        PolymarketRealtimeBucketActorConfig(series=("btc-updown-15m",)),
        data_queue=MagicMock(),
    )
    actor.subscribe_quote_ticks = MagicMock()
    iid = InstrumentId.from_str("0xabc-YES.POLYMARKET")
    cache = MagicMock()
    with patch.object(type(actor), "cache", new_callable=PropertyMock, return_value=cache):
        cache.instrument.return_value = None
        actor._on_active_market(
            ActivePolymarketMarket(
                instrument_id=iid,
                no_instrument_id=iid,
                series="btc-updown-15m",
                slug="btc-updown-15m-123",
                question="q",
                ts_event=1,
                ts_init=1,
            ),
        )
    actor.subscribe_quote_ticks.assert_not_called()
