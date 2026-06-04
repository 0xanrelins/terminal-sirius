"""Helpers for native ``BinanceFuturesLiquidation`` (Nautilus ≥1.228)."""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nautilus_trader.model.enums import OrderSide

if TYPE_CHECKING:
    from nautilus_trader.adapters.binance import BinanceFuturesLiquidation


def _native_cls():
    from nautilus_trader.adapters.binance import BinanceFuturesLiquidation

    return BinanceFuturesLiquidation


def _price_as_float(price: Any) -> float:
    if hasattr(price, "as_double"):
        return float(price.as_double())
    return float(price)


def _qty_as_float(qty: Any) -> float:
    if hasattr(qty, "as_double"):
        return float(qty.as_double())
    return float(qty)


def instrument_symbol(liq: BinanceFuturesLiquidation) -> str:
    return str(liq.instrument_id)


def liquidation_side_str(liq: BinanceFuturesLiquidation) -> str:
    """Binance force-order side: SELL = long liq, BUY = short liq."""
    if liq.side == OrderSide.SELL:
        return "SELL"
    if liq.side == OrderSide.BUY:
        return "BUY"
    return str(liq.side)


def liquidation_notional_usd(liq: BinanceFuturesLiquidation) -> float:
    return _price_as_float(liq.average_price) * _qty_as_float(liq.accumulated_qty)


def liquidation_trade_ms(liq: BinanceFuturesLiquidation) -> int:
    return int(liq.ts_event) // 1_000_000


def liquidation_trade_id(liq: BinanceFuturesLiquidation) -> int:
    sym = instrument_symbol(liq)
    side = liquidation_side_str(liq)
    trade_ms = liquidation_trade_ms(liq)
    sym_tag = sum(ord(c) for c in sym) % 10_000
    side_tag = 1 if side == "SELL" else 2
    return trade_ms * 10_000 + sym_tag * 10 + side_tag


def liquidation_anchor_price(liq: BinanceFuturesLiquidation) -> float:
    return _price_as_float(liq.average_price)
