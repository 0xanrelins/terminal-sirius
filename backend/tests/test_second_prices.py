"""Tests for TradeTick → second price aggregation."""
from __future__ import annotations

from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import AggressorSide
from nautilus_trader.model.identifiers import InstrumentId, TradeId
from nautilus_trader.model.objects import Price, Quantity

from recorders.second_prices import ticks_to_second_prices


def test_ticks_to_second_prices_collapses_same_second():
    symbol = "BTCUSDT-PERP.BINANCE"
    iid = InstrumentId.from_str(symbol)
    sec = 1_700_000_000
    ticks = [
        TradeTick(
            instrument_id=iid,
            price=Price.from_str("100.0"),
            size=Quantity.from_str("1"),
            aggressor_side=AggressorSide.BUYER,
            trade_id=TradeId("a"),
            ts_event=sec * 1_000_000_000 + 100_000_000,
            ts_init=0,
        ),
        TradeTick(
            instrument_id=iid,
            price=Price.from_str("101.0"),
            size=Quantity.from_str("1"),
            aggressor_side=AggressorSide.BUYER,
            trade_id=TradeId("b"),
            ts_event=sec * 1_000_000_000 + 900_000_000,
            ts_init=0,
        ),
    ]
    out = ticks_to_second_prices(ticks, symbol=symbol)
    assert len(out) == 1
    assert out[0].last_price == 101.0
