"""Frozen WS contract — regression without network."""
from ws_contract import ALLOWED_WS_TYPES, validate_ws_payload


def test_allowed_types_match_feed_msg_union() -> None:
    expected = {
        "trade",
        "quote",
        "bar",
        "polymarket",
        "liquidation",
        "simulation_signal",
        "simulation_bet_open",
        "simulation_bet_settle",
        "simulation_cycle_closed",
        "live_signal",
        "live_bet_open",
        "live_bet_settle",
        "live_cycle_closed",
        "live_order_error",
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


def test_validate_sample_live_open() -> None:
    validate_ws_payload(
        {
            "type": "live_bet_open",
            "bet_id": 1,
            "cycle_id": 2,
            "side": "long",
            "asset": "DOGE",
            "leg": 1,
            "poly_slug": "doge-updown-15m-1",
            "candle_open": 100,
            "entry_price": 0.5,
            "shares": 5.0,
            "cost_usd": 2.5,
        }
    )


def test_unknown_type_rejected() -> None:
    try:
        validate_ws_payload({"type": "legacy_polymarket_exec_fill"})
    except ValueError as e:
        assert "unknown" in str(e).lower()
    else:
        raise AssertionError("expected ValueError")
