"""
Frozen WebSocket contract (Faz 0).

Source of truth for TypeScript shapes: frontend/src/types.ts (FeedMsg).
Backend must only emit `type` values listed here. Breaking changes require
updating this module, types.ts, and docs/ws-api-contract.md together.
"""
from __future__ import annotations

# Market data (Nautilus actors → data_queue)
MARKET_DATA_TYPES = frozenset({"trade", "quote", "bar", "indicator", "polymarket", "liquidation"})

# Account-level paper-trade monitoring (PaperTradeMonitorActor → data_queue).
# These are not market data and carry no `symbol` (broadcast to all WS clients).
PAPER_TRADE_TYPES = frozenset({"paper_snapshot", "paper_event"})
STRATEGY_SIGNAL_TYPES = frozenset({"strategy_signal_snapshot"})
LIQUIDATION_VERDICT_TYPES = frozenset({"liquidation_verdict"})

# Mirrors FeedMsg in frontend/src/types.ts
FEED_MSG_TYPES = (
    MARKET_DATA_TYPES
    | PAPER_TRADE_TYPES
    | STRATEGY_SIGNAL_TYPES
    | LIQUIDATION_VERDICT_TYPES
)

ALLOWED_WS_TYPES = FEED_MSG_TYPES

# Minimum keys clients rely on (not exhaustive — see docs/ws-api-contract.md)
REQUIRED_KEYS: dict[str, frozenset[str]] = {
    "trade": frozenset({"type", "symbol", "price", "size", "side", "ts"}),
    "quote": frozenset({"type", "symbol", "bid", "ask", "bid_size", "ask_size", "ts"}),
    "bar": frozenset({"type", "symbol", "interval", "time", "open", "high", "low", "close", "volume", "ts"}),
    "indicator": frozenset({"type", "symbol", "interval", "time", "indicator", "period"}),
    "polymarket": frozenset({"type", "symbol", "slug", "question", "yes_price", "ts"}),
    "liquidation": frozenset({"type", "symbol", "side", "notional", "time"}),
    "paper_snapshot": frozenset({"type", "ts", "run"}),
    "paper_event": frozenset({"type", "kind", "ts", "instrument_id"}),
    "strategy_signal_snapshot": frozenset({"type", "ts", "symbols"}),
    "liquidation_verdict": frozenset({"type", "verdict", "tape", "pending", "pending_by_symbol"}),
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
