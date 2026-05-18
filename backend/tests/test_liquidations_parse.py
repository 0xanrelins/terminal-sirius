"""Tests for Binance forceOrder parse helpers."""
from liquidations import (
    build_liquidation_message,
    force_order_trade_id,
    parse_force_order,
)


def _sample_item() -> dict:
    return {
        "e": "forceOrder",
        "E": 1710000000123,
        "o": {
            "s": "BTCUSDT",
            "S": "SELL",
            "o": "LIMIT",
            "f": "IOC",
            "q": "0.5",
            "p": "65000",
            "ap": "64980.5",
            "X": "FILLED",
            "l": "0.5",
            "z": "0.5",
            "T": 1710000000100,
            "i": 987654321,
        },
    }


def test_parse_force_order():
    parsed = parse_force_order(_sample_item())
    assert parsed is not None
    assert parsed["symbol"] == "BTCUSDT-PERP.BINANCE"
    assert parsed["side"] == "SELL"
    assert parsed["notional"] == round(64980.5 * 0.5, 2)
    assert parsed["time"] == 1710000000
    assert parsed["trade_id"] == 987654321


def test_force_order_trade_id_uses_order_id():
    assert force_order_trade_id(_sample_item()) == 987654321


def test_build_liquidation_message_includes_payload():
    msg = build_liquidation_message(_sample_item())
    assert msg is not None
    assert msg["type"] == "liquidation"
    assert msg["_payload"] == _sample_item()
    assert isinstance(msg["_updates"], list)
    assert len(msg["_updates"]) > 0


def test_parse_rejects_non_force_order():
    assert parse_force_order({"e": "trade"}) is None
