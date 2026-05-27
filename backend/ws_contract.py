"""
Frozen WebSocket + strategy event contract (Faz 0).

Source of truth for TypeScript shapes: frontend/src/types.ts (FeedMsg).
Backend must only emit `type` values listed here. Breaking changes require
updating this module, types.ts, and docs/ws-api-contract.md together.
"""
from __future__ import annotations

# Market data (Nautilus actors → data_queue)
MARKET_DATA_TYPES = frozenset({"trade", "quote", "bar", "polymarket", "liquidation"})

# Strategy / sim / live (strategy_event_queue → persist → WS)
SIMULATION_TYPES = frozenset(
    {
        "simulation_signal",
        "simulation_bet_open",
        "simulation_bet_settle",
        "simulation_cycle_closed",
    }
)
LIVE_TYPES = frozenset(
    {
        "live_signal",
        "live_bet_open",
        "live_bet_settle",
        "live_cycle_closed",
        "live_order_error",
    }
)

# Mirrors FeedMsg in frontend/src/types.ts
FEED_MSG_TYPES = MARKET_DATA_TYPES | SIMULATION_TYPES | LIVE_TYPES

ALLOWED_WS_TYPES = FEED_MSG_TYPES

# Minimum keys clients rely on (not exhaustive — see docs/ws-api-contract.md)
REQUIRED_KEYS: dict[str, frozenset[str]] = {
    "trade": frozenset({"type", "symbol", "price", "size", "side", "ts"}),
    "quote": frozenset({"type", "symbol", "bid", "ask", "bid_size", "ask_size", "ts"}),
    "bar": frozenset({"type", "symbol", "interval", "time", "open", "high", "low", "close", "volume", "ts"}),
    "polymarket": frozenset({"type", "symbol", "slug", "question", "yes_price", "ts"}),
    "liquidation": frozenset({"type", "symbol", "side", "notional", "time"}),
    "simulation_signal": frozenset(
        {"type", "side", "asset", "binance_symbol", "poly_series", "signal_time", "threshold", "target_candle_open"}
    ),
    "simulation_bet_open": frozenset(
        {"type", "bet_id", "cycle_id", "side", "asset", "leg", "poly_slug", "candle_open", "entry_price", "shares", "cost_usd"}
    ),
    "simulation_bet_settle": frozenset(
        {"type", "bet_id", "cycle_id", "side", "asset", "leg", "candle_open", "outcome", "pnl_usd", "won", "settled_at"}
    ),
    "live_signal": frozenset(
        {"type", "side", "asset", "binance_symbol", "poly_series", "signal_time", "threshold", "target_candle_open"}
    ),
    "live_bet_open": frozenset(
        {"type", "bet_id", "cycle_id", "side", "asset", "leg", "poly_slug", "candle_open", "entry_price", "shares", "cost_usd"}
    ),
    "live_bet_settle": frozenset(
        {"type", "bet_id", "cycle_id", "side", "asset", "leg", "candle_open", "outcome", "pnl_usd", "won", "settled_at"}
    ),
    "live_order_error": frozenset({"type", "asset", "side", "leg", "poly_slug", "error"}),
    "simulation_cycle_closed": frozenset({"type", "cycle_id", "asset", "side"}),
    "live_cycle_closed": frozenset({"type", "cycle_id", "asset", "side"}),
}


def validate_ws_payload(msg: dict) -> None:
    """Raise ValueError if payload violates frozen contract."""
    t = msg.get("type")
    if t not in ALLOWED_WS_TYPES:
        raise ValueError(f"unknown ws type: {t!r}")
    required = REQUIRED_KEYS.get(t)
    if required is None:
        return
    missing = required - set(msg.keys())
    if missing:
        raise ValueError(f"type={t!r} missing keys: {sorted(missing)}")
