"""Binance perp ↔ Polymarket 15m rolling series (strategy-build §5.1)."""

from __future__ import annotations

STRATEGY_BINANCE_INSTRUMENTS: tuple[str, ...] = (
    "BTCUSDT-PERP.BINANCE",
    "ETHUSDT-PERP.BINANCE",
    "SOLUSDT-PERP.BINANCE",
    "XRPUSDT-PERP.BINANCE",
    "DOGEUSDT-PERP.BINANCE",
)

BINANCE_TO_POLY_SERIES: dict[str, str] = {
    "BTCUSDT-PERP.BINANCE": "btc-updown-15m",
    "ETHUSDT-PERP.BINANCE": "eth-updown-15m",
    "SOLUSDT-PERP.BINANCE": "sol-updown-15m",
    "XRPUSDT-PERP.BINANCE": "xrp-updown-15m",
    "DOGEUSDT-PERP.BINANCE": "doge-updown-15m",
}
