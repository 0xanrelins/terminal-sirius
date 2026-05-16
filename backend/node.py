"""
Nautilus TradingNode factory.

Binance Futures (USDT-M perp) public feed — no API keys needed for market data.
Polymarket CLOB feed via PolymarketActor.

Environment variables:
  DATABASE_URL       — PostgreSQL DSN for Nautilus CacheDatabase
  POLYMARKET_SLUGS        — comma-separated static market slugs on boot
  POLYMARKET_15M_SERIES   — comma-separated rolling 15m series (default: btc,eth,sol,doge,xrp updown)
"""
import os
import queue
import signal
import threading

# Nautilus TradingNode registers signal handlers during __init__ via both
# signal.signal() and loop.add_signal_handler(). Both require the main thread.
# We run the node in a daemon thread (so FastAPI owns the main thread), so we
# patch both to be no-ops when called from a non-main thread.

_orig_signal = signal.signal

def _safe_signal(sig, handler):
    if threading.current_thread() is threading.main_thread():
        return _orig_signal(sig, handler)

signal.signal = _safe_signal  # type: ignore[assignment]

try:
    import uvloop as _uvloop
    _orig_add_sig = _uvloop.Loop.add_signal_handler

    def _safe_add_signal_handler(self, sig, callback, *args):
        if threading.current_thread() is threading.main_thread():
            return _orig_add_sig(self, sig, callback, *args)

    _uvloop.Loop.add_signal_handler = _safe_add_signal_handler  # type: ignore[method-assign]
except ImportError:
    pass

from nautilus_trader.adapters.binance.config import BinanceAccountType, BinanceDataClientConfig
from nautilus_trader.adapters.binance.factories import BinanceLiveDataClientFactory
from nautilus_trader.cache.config import CacheConfig
from nautilus_trader.config import LiveDataEngineConfig, TradingNodeConfig
from nautilus_trader.live.node import TradingNode

from adapters.polymarket.actor import PolymarketActor, PolymarketActorConfig
from bridge_actor import BridgeActor, BridgeActorConfig

DEFAULT_INSTRUMENTS = (
    "BTCUSDT-PERP.BINANCE",
    "ETHUSDT-PERP.BINANCE",
    "SOLUSDT-PERP.BINANCE",
    "XRPUSDT-PERP.BINANCE",
    "DOGEUSDT-PERP.BINANCE",
    "HYPEUSDT-PERP.BINANCE",
)

_polymarket_actor: PolymarketActor | None = None


def get_polymarket_actor() -> PolymarketActor | None:
    return _polymarket_actor


def _cache_config() -> CacheConfig:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        return CacheConfig()
    try:
        from nautilus_trader.cache.postgres.config import PostgresCacheConfig
        return PostgresCacheConfig(database=dsn)
    except ImportError:
        return CacheConfig()


def _polymarket_slugs() -> tuple[str, ...]:
    raw = os.environ.get("POLYMARKET_SLUGS", "")
    return tuple(s.strip() for s in raw.split(",") if s.strip())


def _polymarket_series() -> tuple[str, ...]:
    raw = os.environ.get(
        "POLYMARKET_15M_SERIES",
        "btc-updown-15m,eth-updown-15m,sol-updown-15m,doge-updown-15m,xrp-updown-15m",
    )
    return tuple(s.strip() for s in raw.split(",") if s.strip())


def build_node(data_queue: queue.Queue, instruments: tuple[str, ...] = DEFAULT_INSTRUMENTS) -> TradingNode:
    global _polymarket_actor

    bridge_cfg = BridgeActorConfig(
        component_id="BridgeActor-001",
        instrument_ids=instruments,
    )
    pm_cfg = PolymarketActorConfig(
        component_id="PolymarketActor-001",
        initial_slugs=_polymarket_slugs(),
        initial_series=_polymarket_series(),
    )

    config = TradingNodeConfig(
        trader_id="TERMINAL-SIRIUS-001",
        cache=_cache_config(),
        data_clients={
            "BINANCE": BinanceDataClientConfig(
                account_type=BinanceAccountType.USDT_FUTURES,
                api_key=None,
                api_secret=None,
            )
        },
        data_engine=LiveDataEngineConfig(
            time_bars_build_with_no_updates=False,
        ),
    )

    node = TradingNode(config=config)
    node.add_data_client_factory("BINANCE", BinanceLiveDataClientFactory)

    bridge = BridgeActor(config=bridge_cfg, data_queue=data_queue)
    node.trader.add_actor(bridge)

    pm_actor = PolymarketActor(config=pm_cfg, data_queue=data_queue)
    node.trader.add_actor(pm_actor)
    _polymarket_actor = pm_actor

    node.build()
    return node


def run_node_in_thread(data_queue: queue.Queue) -> threading.Thread:
    def _run() -> None:
        node = build_node(data_queue)
        try:
            node.run()
        finally:
            node.dispose()

    thread = threading.Thread(target=_run, daemon=True, name="nautilus-node")
    thread.start()
    return thread
