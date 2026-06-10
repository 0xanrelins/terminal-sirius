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
        "paper_snapshot",
        "paper_event",
        "strategy_signal_snapshot",
        "liquidation_verdict",
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
    validate_ws_payload(
        {
            "type": "paper_snapshot",
            "ts": 1,
            "run": {"strategy_on": True, "paper": True},
        }
    )
    validate_ws_payload(
        {
            "type": "paper_event",
            "kind": "fill",
            "ts": 1,
            "instrument_id": "0x123.POLYMARKET",
        }
    )
    validate_ws_payload(
        {
            "type": "strategy_signal_snapshot",
            "ts": 1,
            "symbols": {
                "SOLUSDT-PERP.BINANCE": {
                    "vwap": 142.5,
                    "decision": "HOLD",
                    "vwap_ready": True,
                }
            },
        }
    )
    validate_ws_payload(
        {
            "type": "liquidation_verdict",
            "verdict": {
                "event_id": "v-1",
                "symbol": "BTC",
                "liq_side": "LONG",
                "notional": 100000.0,
                "event_price": 100.0,
                "winner": "recovery",
                "liq_move_pct": 0.1,
                "recovery_move_pct": 0.6,
                "dominance_ratio": 6.0,
                "time_to_dominance_sec": 18.0,
                "area_bias": 0.4,
                "status": "completed",
                "event_time": 1,
            },
            "tape": [],
            "pending": 0,
            "pending_by_symbol": {},
        }
    )


def test_unknown_type_rejected() -> None:
    try:
        validate_ws_payload({"type": "live_bet_open"})
    except ValueError as e:
        assert "unknown" in str(e).lower()
    else:
        raise AssertionError("expected ValueError")
