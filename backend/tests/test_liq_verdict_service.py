"""Liquidation verdict catalog merge / anchor helpers."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from recorders.liq_verdict_service import _anchor_price  # noqa: E402
from recorders.liq_verdict_service import merge_verdict_rows  # noqa: E402
from recorders.second_prices import SecondPrice  # noqa: E402
from recorders.second_prices import SymbolPriceSeries  # noqa: E402


def test_anchor_price_uses_last_price_at_or_before_event():
    series = SymbolPriceSeries(
        rows=(
            SecondPrice(ts_event=10_000_000_000, symbol="BTCUSDT-PERP.BINANCE", last_price=100.0),
            SecondPrice(ts_event=20_000_000_000, symbol="BTCUSDT-PERP.BINANCE", last_price=101.0),
            SecondPrice(ts_event=30_000_000_000, symbol="BTCUSDT-PERP.BINANCE", last_price=102.0),
        ),
        times_ns=(10_000_000_000, 20_000_000_000, 30_000_000_000),
    )
    assert _anchor_price(series, 25_000_000_000, 0.0) == 101.0


def test_merge_verdict_rows_prefers_persisted():
    catalog = [
        {
            "event_id": "a",
            "symbol": "BTC",
            "event_time": 100,
            "winner": "neutral",
            "liq_move_pct": 0.0,
        },
        {
            "event_id": "b",
            "symbol": "BTC",
            "event_time": 90,
            "winner": "neutral",
            "liq_move_pct": 0.0,
        },
    ]
    persisted = [
        {
            "event_id": "a",
            "symbol": "BTC",
            "event_time": 100,
            "winner": "recovery",
            "liq_move_pct": 0.1,
            "recovery_move_pct": 0.4,
        }
    ]
    merged = merge_verdict_rows(persisted, catalog, limit=10)
    assert [row["event_id"] for row in merged] == ["a", "b"]
    assert merged[0]["winner"] == "recovery"
    assert merged[0]["recovery_move_pct"] == 0.4
