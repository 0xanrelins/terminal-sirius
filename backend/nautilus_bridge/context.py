"""Global Nautilus TradingNode + strategy references (same process as FastAPI)."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from nautilus_trader.live.node import TradingNode

    from strategies.liq_poly_strategy import LiqPolyStrategy

_node: TradingNode | None = None
_strategy: LiqPolyStrategy | None = None


def register_trading_node(node: TradingNode | None) -> None:
    global _node
    _node = node


def get_trading_node() -> TradingNode | None:
    return _node


def set_liq_poly_strategy(strategy: LiqPolyStrategy | None) -> None:
    global _strategy
    _strategy = strategy


def get_liq_poly_strategy() -> LiqPolyStrategy | None:
    return _strategy


def exec_client_ready() -> bool:
    node = _node
    if node is None:
        return False
    try:
        clients = node.kernel.exec_engine.get_clients()
        return bool(clients)
    except Exception:
        return False


def get_polymarket_exec_client() -> Any | None:
    node = _node
    if node is None:
        return None
    try:
        for client in node.kernel.exec_engine.get_clients().values():
            name = type(client).__name__
            if "Polymarket" in name:
                return client
    except Exception:
        return None
    return None
