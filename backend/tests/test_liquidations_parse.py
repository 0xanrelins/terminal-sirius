"""Tests for Binance forceOrder parse helpers."""
from liquidations import (
    build_liquidation_message,
    force_order_trade_id,
    liquidation_db_trade_id_and_payload,
    liquidation_message_from_tick,
    parse_force_order,
)
from recorders.data_types import LiquidationTick


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
    assert isinstance(msg["bars"], list)
    assert len(msg["bars"]) == len(msg["_updates"])
    snap_15m = next(b for b in msg["bars"] if b["interval"] == "15m")
    assert snap_15m["long"] == round(64980.5 * 0.5, 2)
    assert snap_15m["short"] == 0.0
    snap_5s = next(b for b in msg["bars"] if b["interval"] == "5s")
    assert snap_5s["long"] == round(64980.5 * 0.5, 2)
    assert snap_5s["short"] == 0.0


def test_parse_rejects_non_force_order():
    assert parse_force_order({"e": "trade"}) is None


def test_liquidation_message_from_tick_includes_trade_id_and_prices():
    tick = LiquidationTick(
        symbol="BTCUSDT-PERP.BINANCE",
        side="SELL",
        notional=100_000.0,
        price=50_000.0,
        quantity=2.0,
        ts_event=1_710_000_000_100_000_000,
        ts_init=1_710_000_000_100_000_000,
    )
    msg = liquidation_message_from_tick(tick)
    assert msg is not None
    assert msg["trade_id"] is not None
    assert msg["price"] == 50_000.0
    assert msg["quantity"] == 2.0
    assert msg["_payload"] is None
    assert msg["_updates"]


def test_liquidation_db_trade_id_and_payload_from_tick_message():
    msg = {
        "type": "liquidation",
        "trade_id": 12345,
        "symbol": "BTCUSDT-PERP.BINANCE",
        "side": "SELL",
        "notional": 100_000.0,
        "price": 50_000.0,
        "quantity": 2.0,
        "time": 1_710_000_000,
        "_payload": None,
    }
    resolved = liquidation_db_trade_id_and_payload(msg)
    assert resolved is not None
    trade_id, payload = resolved
    assert trade_id == 12345
    assert payload["e"] == "forceOrder"
    assert payload["o"]["s"] == "BTCUSDT"
    assert payload["o"]["S"] == "SELL"
