"""
Nautilus TradingNode factory.

Binance Futures (USDT-M perp) public feed — no API keys needed for market data.
Polymarket CLOB feed via PolymarketActor.

Environment variables:
  DATABASE_URL       — PostgreSQL DSN for Nautilus CacheDatabase
  POLYMARKET_SLUGS   — comma-separated list of market slugs to subscribe on boot
                       e.g. "will-trump-win-2024,will-fed-cut-rates-2025"
"""
import os
import queue
import threading

from nautilus_trader.adapters.binance.futures.config import BinanceFuturesDataClientConfig
from nautilus_trader.adapters.binance.futures.factories import BinanceFuturesLiveDataClientFactory
from nautilus_trader.cache.config import CacheConfig
from nautilus_trader.config import LiveDataEngineConfig, TradingNodeConfig
from nautilus_trader.live.node import TradingNode

from adapters.polymarket.actor import PolymarketActor, PolymarketActorConfig
from bridge_actor import BridgeActor, BridgeActorConfig

DEFAULT_INSTRUMENTS = ("BTCUSDT-PERP.BINANCE",)

# Global reference so FastAPI endpoints can call actor.subscribe_slug() at runtime
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


def build_node(data_queue: queue.Queue, instruments: tuple[str, ...] = DEFAULT_INSTRUMENTS) -> TradingNode:
    global _polymarket_actor

    bridge_cfg = BridgeActorConfig(
        component_id="BridgeActor-001",
        instrument_ids=instruments,
    )
    pm_cfg = PolymarketActorConfig(
        component_id="PolymarketActor-001",
        initial_slugs=_polymarket_slugs(),
    )

    config = TradingNodeConfig(
        trader_id="TERMINAL-SIRIUS-001",
        cache=_cache_config(),
        data_clients={
            "BINANCE": BinanceFuturesDataClientConfig(
                api_key=None,
                api_secret=None,
                is_testnet=False,
                us=False,
            )
        },
        data_engine=LiveDataEngineConfig(
            time_bars_build_with_no_updates=False,
        ),
    )

    node = TradingNode(config=config)
    node.add_data_client_factory("BINANCE", BinanceFuturesLiveDataClientFactory)

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
