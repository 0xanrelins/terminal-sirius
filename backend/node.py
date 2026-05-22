"""
Nautilus TradingNode factory.

Binance Futures (USDT-M perp) public feed — no API keys needed for market data.
Polymarket CLOB feed via PolymarketActor.
Live Polymarket execution via PolymarketExecutionClient + LiqPolyStrategy when creds set.

Environment variables:
  DATABASE_URL       — PostgreSQL DSN for Nautilus CacheDatabase
  POLYMARKET_SLUGS        — comma-separated static market slugs on boot
  POLYMARKET_15M_SERIES   — comma-separated rolling 15m series
  LIVE_ENABLED            — register Polymarket exec client when true + creds
"""
from __future__ import annotations

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
from nautilus_trader.adapters.polymarket.config import PolymarketExecClientConfig
from nautilus_trader.adapters.polymarket.factories import PolymarketLiveExecClientFactory
from nautilus_trader.cache.config import CacheConfig
from nautilus_trader.config import LiveDataEngineConfig, LiveExecEngineConfig, TradingNodeConfig
from nautilus_trader.live.node import TradingNode

from adapters.polymarket.actor import PolymarketActor, PolymarketActorConfig
from adapters.polymarket.orders import credentials_configured
from bridge_actor import BridgeActor, BridgeActorConfig
from liquidation_actor import LiquidationActor, LiquidationActorConfig
from live import config as live_cfg
from nautilus_bridge.context import register_trading_node
from strategies.liq_poly_strategy import LiqPolyStrategy, LiqPolyStrategyConfig

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


def _polymarket_exec_config() -> PolymarketExecClientConfig | None:
    if not live_cfg.is_enabled() or not credentials_configured():
        return None
    if not os.environ.get("POLYMARKET_API_KEY", "").strip():
        print(
            "[warn] Polymarket ExecutionClient skipped: set POLYMARKET_API_KEY "
            "or ensure POLYMARKET_PRIVATE_KEY can derive L2 creds at startup"
        )
        return None
    sig_raw = os.environ.get("POLYMARKET_SIGNATURE_TYPE", "").strip()
    signature_type = int(sig_raw) if sig_raw.isdigit() else 0
    funder = os.environ.get("POLYMARKET_FUNDER_ADDRESS", "").strip() or None
    return PolymarketExecClientConfig(
        private_key=os.environ.get("POLYMARKET_PRIVATE_KEY", "").strip() or None,
        signature_type=signature_type,
        funder=funder,
        api_key=os.environ.get("POLYMARKET_API_KEY", "").strip() or None,
        api_secret=os.environ.get("POLYMARKET_API_SECRET", "").strip() or None,
        passphrase=os.environ.get("POLYMARKET_API_PASSPHRASE", "").strip() or None,
        max_retries=int(os.environ.get("POLYMARKET_MAX_RETRIES", "3")),
        retry_delay_initial_ms=int(
            float(os.environ.get("POLYMARKET_RETRY_DELAY_SEC", "1.0")) * 1000
        ),
    )


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
    liq_cfg = LiquidationActorConfig(component_id="LiquidationActor-001")

    exec_cfg = _polymarket_exec_config()
    exec_clients: dict = {}
    exec_engine = LiveExecEngineConfig()
    if exec_cfg is not None:
        exec_clients["POLYMARKET"] = exec_cfg
        print("[nautilus] Polymarket ExecutionClient enabled (live orders via Nautilus)")

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
        exec_clients=exec_clients,
        data_engine=LiveDataEngineConfig(
            time_bars_build_with_no_updates=False,
        ),
        exec_engine=exec_engine,
    )

    node = TradingNode(config=config)
    node.add_data_client_factory("BINANCE", BinanceLiveDataClientFactory)
    if exec_cfg is not None:
        node.add_exec_client_factory("POLYMARKET", PolymarketLiveExecClientFactory)

    bridge = BridgeActor(config=bridge_cfg, data_queue=data_queue)
    node.trader.add_actor(bridge)

    pm_actor = PolymarketActor(config=pm_cfg, data_queue=data_queue)
    node.trader.add_actor(pm_actor)
    _polymarket_actor = pm_actor

    liq_actor = LiquidationActor(config=liq_cfg, data_queue=data_queue)
    node.trader.add_actor(liq_actor)

    if exec_cfg is not None:
        strat_cfg = LiqPolyStrategyConfig()
        node.trader.add_strategy(LiqPolyStrategy(config=strat_cfg))

    node.build()
    register_trading_node(node)
    return node


def run_node_in_thread(data_queue: queue.Queue) -> threading.Thread:
    def _run() -> None:
        node = build_node(data_queue)
        try:
            node.run()
        finally:
            register_trading_node(None)  # type: ignore[arg-type]
            node.dispose()

    thread = threading.Thread(target=_run, daemon=True, name="nautilus-node")
    thread.start()
    return thread
