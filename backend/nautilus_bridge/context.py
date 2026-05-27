"""Global Nautilus TradingNode references (same process as FastAPI)."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nautilus_trader.live.node import TradingNode

_node: TradingNode | None = None


def register_trading_node(node: TradingNode | None) -> None:
    global _node
    _node = node


def get_trading_node() -> TradingNode | None:
    return _node


def _registered_exec_client_ids(node: TradingNode) -> list[str]:
    engine = node.kernel.exec_engine
    registered = getattr(engine, "registered_clients", None)
    if registered is not None:
        return [str(client_id) for client_id in registered]
    get_clients = getattr(engine, "get_clients", None)
    if callable(get_clients):
        clients = get_clients()
        if hasattr(clients, "keys"):
            return [str(k) for k in clients.keys()]
        return [str(c) for c in clients]
    return []


def _polymarket_exec_client(node: TradingNode):
    try:
        engine = node.kernel.exec_engine
        clients = getattr(engine, "_clients", None)
        if isinstance(clients, dict):
            for client in clients.values():
                if "Polymarket" in type(client).__name__:
                    return client
        get_clients = getattr(engine, "get_clients", None)
        if callable(get_clients):
            for client in get_clients().values():
                if "Polymarket" in type(client).__name__:
                    return client
    except Exception:
        return None
    return None


def exec_client_ready() -> bool:
    """True when POLYMARKET exec client is registered and connected (if checkable)."""
    node = _node
    if node is None:
        return False
    try:
        if not any("POLYMARKET" in cid.upper() for cid in _registered_exec_client_ids(node)):
            return False
        client = _polymarket_exec_client(node)
        if client is None:
            return True
        is_connected = getattr(client, "is_connected", None)
        if callable(is_connected):
            return bool(is_connected())
        return True
    except Exception:
        return False
