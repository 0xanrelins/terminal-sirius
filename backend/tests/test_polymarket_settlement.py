"""Polymarket paper settlement helpers."""

from unittest.mock import MagicMock

from nautilus_trader.model.identifiers import InstrumentId

from adapters.polymarket.rolling import parse_window_epoch_from_slug
from polymarket_settlement_actor import instrument_close_topic
from polymarket_settlement_actor import position_won
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


def test_bar_open_sec_not_double_scaled():
    from bar_time import bar_open_time

    ts_ns = 1_749_123_450_000_000_000  # ~2025-06
    assert bar_open_time(ts_ns // 1_000_000_000, "15m") > 1_000_000
