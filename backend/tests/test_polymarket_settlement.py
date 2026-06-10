"""Polymarket paper settlement helpers."""

from unittest.mock import MagicMock
from unittest.mock import PropertyMock
from unittest.mock import patch

from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Price

from adapters.polymarket.rolling import parse_window_epoch_from_slug
from polymarket_settlement_actor import PolymarketSettlementActor
from polymarket_settlement_actor import PolymarketSettlementActorConfig
from polymarket_settlement_actor import instrument_close_topic
from polymarket_settlement_actor import position_won
from polymarket_settlement_actor import quote_precision_ok
from polymarket_settlement_actor import settlement_price_str
from polymarket_settlement_actor import up_outcome_from_bar


def _bar(open_: str, close: str):
    bar = MagicMock()
    bar.open.as_double.return_value = float(open_)
    bar.close.as_double.return_value = float(close)
    return bar


def test_up_outcome_green_candle():
    assert up_outcome_from_bar(_bar("100", "101")) is True


def test_up_outcome_red_candle():
    assert up_outcome_from_bar(_bar("101", "100")) is False


def test_up_outcome_doji_counts_up():
    assert up_outcome_from_bar(_bar("100", "100")) is True


def test_position_won_yes_no():
    assert position_won(outcome="YES", up_won=True) is True
    assert position_won(outcome="YES", up_won=False) is False
    assert position_won(outcome="NO", up_won=False) is True
    assert position_won(outcome="NO", up_won=True) is False


def test_settlement_price_precision():
    assert settlement_price_str(True, 3) == "1.000"
    assert settlement_price_str(False, 3) == "0.000"
    assert settlement_price_str(True, 0) == "1"


def test_parse_window_epoch_from_slug():
    assert parse_window_epoch_from_slug("btc-updown-15m-1778931900") == 1_778_931_900
    assert parse_window_epoch_from_slug("btc-updown-15m") is None


def test_instrument_close_topic_matches_sandbox_subscription():
    iid = InstrumentId.from_str("0xabc-YES.POLYMARKET")
    topic = instrument_close_topic(iid)
    assert topic == "data.close.POLYMARKET.0xabc-YES"
    # Python SandboxExecutionClient subscribes to data.*.{venue}.*
    assert topic.startswith("data.")
    assert ".POLYMARKET." in f"{topic}."


def test_settlement_suppressed_when_flat_but_not_when_reopened():
    actor = PolymarketSettlementActor(
        PolymarketSettlementActorConfig(binance_instruments=("BTCUSDT-PERP.BINANCE",)),
    )
    iid = InstrumentId.from_str("0xabc-YES.POLYMARKET")
    iid_s = str(iid)
    actor._settled.add(iid_s)
    cache = MagicMock()
    with patch.object(type(actor), "cache", new_callable=PropertyMock, return_value=cache):
        cache.positions_open.return_value = False
        assert actor._settlement_suppressed(iid_s, iid) is True
        cache.positions_open.return_value = True
        assert actor._settlement_suppressed(iid_s, iid) is False


def test_bar_open_sec_not_double_scaled():
    from bar_time import bar_open_time

    ts_ns = 1_749_123_450_000_000_000  # ~2025-06
    assert bar_open_time(ts_ns // 1_000_000_000, "15m") > 1_000_000


def _px(precision: int):
    px = MagicMock()
    px.precision = precision
    return px


def test_quote_precision_ok_matches_instrument():
    inst = MagicMock()
    inst.price_precision = 3
    tick = MagicMock()
    tick.bid_price = _px(3)
    tick.ask_price = _px(3)
    assert quote_precision_ok(inst, tick) is True


def test_quote_precision_ok_rejects_stale_quote():
    inst = MagicMock()
    inst.price_precision = 3
    tick = MagicMock()
    tick.bid_price = _px(2)
    tick.ask_price = _px(3)
    assert quote_precision_ok(inst, tick) is False


def test_apply_settlement_defers_without_ready_precision():
    actor = PolymarketSettlementActor(
        PolymarketSettlementActorConfig(binance_instruments=("BTCUSDT-PERP.BINANCE",)),
    )
    iid = InstrumentId.from_str("0xabc-YES.POLYMARKET")
    inst = MagicMock()
    inst.price_precision = 3
    inst.description = "Up"
    tick = MagicMock()
    tick.bid_price = _px(2)
    tick.ask_price = _px(2)
    cache = MagicMock()
    bus = MagicMock()
    with (
        patch.object(type(actor), "cache", new_callable=PropertyMock, return_value=cache),
        patch.object(type(actor), "msgbus", new_callable=PropertyMock, return_value=bus),
    ):
        cache.positions_open.return_value = True
        cache.instrument.return_value = inst
        cache.quote_tick.return_value = tick
        assert actor._apply_settlement(iid, True, slug="btc-updown-15m-1") is False
    bus.publish.assert_not_called()


def test_apply_settlement_publishes_when_ready():
    actor = PolymarketSettlementActor(
        PolymarketSettlementActorConfig(binance_instruments=("BTCUSDT-PERP.BINANCE",)),
    )
    iid = InstrumentId.from_str("0xabc-YES.POLYMARKET")
    inst = MagicMock()
    inst.price_precision = 3
    inst.description = "Up"
    inst.make_price.return_value = Price.from_str("1.000")
    tick = MagicMock()
    tick.bid_price = _px(3)
    tick.ask_price = _px(3)
    actor._settlement_ready_iids.add(iid)
    cache = MagicMock()
    clock = MagicMock()
    clock.timestamp_ns.return_value = 1_000
    bus = MagicMock()
    with (
        patch.object(type(actor), "cache", new_callable=PropertyMock, return_value=cache),
        patch.object(type(actor), "clock", new_callable=PropertyMock, return_value=clock),
        patch.object(type(actor), "msgbus", new_callable=PropertyMock, return_value=bus),
    ):
        cache.positions_open.return_value = True
        cache.instrument.return_value = inst
        cache.quote_tick.return_value = tick
        assert actor._apply_settlement(iid, True, slug="btc-updown-15m-1") is True
    bus.publish.assert_called_once()


def test_history_request_plan_expands_for_old_open_window():
    actor = PolymarketSettlementActor(
        PolymarketSettlementActorConfig(binance_instruments=("BTCUSDT-PERP.BINANCE",)),
    )
    clock = MagicMock()
    now_sec = 1_800_000_000
    clock.timestamp_ns.return_value = now_sec * 1_000_000_000
    cache = MagicMock()
    pos = MagicMock()
    pos.instrument_id = InstrumentId.from_str("0xabc-YES.POLYMARKET")
    inst = MagicMock()
    inst.info = {"market_slug": "btc-updown-15m-1799964000"}  # 10h before `now_sec`
    actor._bar_types["BTCUSDT-PERP.BINANCE"] = MagicMock()

    with (
        patch.object(type(actor), "clock", new_callable=PropertyMock, return_value=clock),
        patch.object(type(actor), "cache", new_callable=PropertyMock, return_value=cache),
    ):
        cache.positions_open.return_value = [pos]
        cache.instrument.return_value = inst
        plan = actor._history_request_plan()

    start_dt, limit = plan["BTCUSDT-PERP.BINANCE"]
    assert int(start_dt.timestamp()) <= 1_799_963_100  # window_start - 15m
    assert limit > 32


def test_request_window_history_dedupes_native_request_bars():
    actor = PolymarketSettlementActor(
        PolymarketSettlementActorConfig(binance_instruments=("BTCUSDT-PERP.BINANCE",)),
    )
    actor._bar_types["BTCUSDT-PERP.BINANCE"] = MagicMock()
    actor.request_bars = MagicMock()

    actor._request_window_history("BTCUSDT-PERP.BINANCE", 1_780_000_000)
    actor._request_window_history("BTCUSDT-PERP.BINANCE", 1_780_000_000)

    actor.request_bars.assert_called_once()
