"""Frozen WS contract — regression without network."""
from ws_contract import ALLOWED_WS_TYPES, validate_ws_payload


def test_allowed_types_match_feed_msg_union() -> None:
    expected = {
        "trade",
        "quote",
        "bar",
        "indicator",
        "polymarket",
        "liquidation",
    }
    assert ALLOWED_WS_TYPES == expected


def test_validate_sample_market_messages() -> None:
    validate_ws_payload(
        {
            "type": "polymarket",
            "symbol": "btc-updown-15m.POLYMARKET",
            "slug": "btc-updown-15m-123",
            "question": "BTC up?",
            "yes_price": 0.52,
            "ts": 1,
        }
    )
    validate_ws_payload(
        {
            "type": "bar",
            "symbol": "BTCUSDT-PERP.BINANCE",
            "interval": "15m",
            "time": 1000,
            "open": "1",
            "high": "2",
            "low": "0.5",
            "close": "1.5",
            "volume": "100",
            "ts": 2,
        }
    )
    validate_ws_payload(
        {
            "type": "indicator",
            "symbol": "BTCUSDT-PERP.BINANCE",
            "interval": "5s",
            "time": 1000,
            "indicator": "ema",
            "period": 20,
            "value": "50000.5",
        }
    )


def test_unknown_type_rejected() -> None:
    try:
        validate_ws_payload({"type": "live_bet_open"})
    except ValueError as e:
        assert "unknown" in str(e).lower()
    else:
        raise AssertionError("expected ValueError")
