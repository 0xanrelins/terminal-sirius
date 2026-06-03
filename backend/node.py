"""
Nautilus TradingNode factory.

Binance Futures (USDT-M perp) public feed — no API keys needed for market data.
Polymarket market data via Nautilus PolymarketDataClient + PolymarketQuoteBridgeActor.
Polymarket execution client registers when creds + POLYMARKET_EXEC_ENABLED (no strategies).

Environment variables:
  DATABASE_URL            — PostgreSQL DSN for Nautilus CacheDatabase
  POLYMARKET_SLUGS        — comma-separated static market slugs on boot
  POLYMARKET_15M_SERIES   — comma-separated rolling 15m series
  POLYMARKET_EXEC_ENABLED — register Polymarket ExecutionClient when true + creds
  POLYMARKET_DATA_ENABLED — default true; PolymarketDataClient + quote bridge for UI
  MARKET_RECORDER_ENABLED   — default false; ParquetDataCatalog via MarketRecorderActor
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
from nautilus_trader.adapters.polymarket.config import (
    PolymarketDataClientConfig,
    PolymarketExecClientConfig,
    PolymarketInstrumentProviderConfig,
)
from nautilus_trader.adapters.polymarket.factories import (
    PolymarketLiveDataClientFactory,
    PolymarketLiveExecClientFactory,
)
from nautilus_trader.cache.config import CacheConfig
from nautilus_trader.config import LiveDataEngineConfig, LiveExecEngineConfig, TradingNodeConfig
from nautilus_trader.live.node import TradingNode

import nautilus_env
from adapters.polymarket.orders import credentials_configured
from adapters.polymarket.quote_bridge_actor import (
    PolymarketQuoteBridgeActor,
    PolymarketQuoteBridgeActorConfig,
)
from bridge_actor import BridgeActor, BridgeActorConfig
from polymarket_realtime_bucket_actor import (
    PolymarketRealtimeBucketActor,
    PolymarketRealtimeBucketActorConfig,
)
from realtime_bucket_actor import RealtimeBucketActor, RealtimeBucketActorConfig
from liquidation_actor import LiquidationActor, LiquidationActorConfig

DEFAULT_INSTRUMENTS = (
    "BTCUSDT-PERP.BINANCE",
    "ETHUSDT-PERP.BINANCE",
    "SOLUSDT-PERP.BINANCE",
    "XRPUSDT-PERP.BINANCE",
    "DOGEUSDT-PERP.BINANCE",
    "HYPEUSDT-PERP.BINANCE",
)

_polymarket_quote_bridge: PolymarketQuoteBridgeActor | None = None
_catalog_writer = None
_trading_node: TradingNode | None = None


def get_polymarket_quote_bridge() -> PolymarketQuoteBridgeActor | None:
    return _polymarket_quote_bridge


def get_trading_node() -> TradingNode | None:
    return _trading_node


def _polymarket_exec_enabled() -> bool:
    raw = os.environ.get(
        "POLYMARKET_EXEC_ENABLED",
        os.environ.get("LIVE_ENABLED", "false"),
    )
    return raw.strip().lower() in ("1", "true", "yes", "on")


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
        "btc-updown-15m,eth-updown-15m,sol-updown-15m,xrp-updown-15m,doge-updown-15m,hype-updown-15m",
    )
    return tuple(s.strip() for s in raw.split(",") if s.strip())


def _polymarket_client_fields(
    wallet: nautilus_env.PolymarketWalletConfig,
) -> dict:
    return {
        "private_key": wallet.private_key,
        "signature_type": wallet.signature_type,
        "funder": wallet.funder,
        "api_key": wallet.api_key,
        "api_secret": wallet.api_secret,
        "passphrase": wallet.passphrase,
    }


def _polymarket_exec_config(
    wallet: nautilus_env.PolymarketWalletConfig,
) -> PolymarketExecClientConfig | None:
    if not _polymarket_exec_enabled() or not credentials_configured():
        return None
    if not wallet.has_l2_api:
        print(
            "[warn] Polymarket ExecutionClient skipped: set POLYMARKET_API_KEY "
            "or ensure POLYMARKET_PRIVATE_KEY can derive L2 creds at startup"
        )
        return None
    return PolymarketExecClientConfig(
        **_polymarket_client_fields(wallet),
        max_retries=int(os.environ.get("POLYMARKET_MAX_RETRIES", "3")),
        retry_delay_initial_ms=int(
            float(os.environ.get("POLYMARKET_RETRY_DELAY_SEC", "1.0")) * 1000
        ),
    )


def _polymarket_data_config(
    wallet: nautilus_env.PolymarketWalletConfig,
) -> PolymarketDataClientConfig | None:
    enabled = os.environ.get("POLYMARKET_DATA_ENABLED", "true").strip().lower()
    if enabled in {"0", "false", "no", "off"}:
        return None
    return PolymarketDataClientConfig(
        **_polymarket_client_fields(wallet),
        auto_load_missing_instruments=True,
        instrument_config=PolymarketInstrumentProviderConfig(
            use_gamma_markets=False,
        ),
    )


def build_node(
    data_queue: queue.Queue,
    instruments: tuple[str, ...] = DEFAULT_INSTRUMENTS,
) -> TradingNode:
    global _polymarket_quote_bridge, _catalog_writer, _trading_node

    bridge_cfg = BridgeActorConfig(
        component_id="BridgeActor-001",
        instrument_ids=instruments,
    )
    liq_cfg = LiquidationActorConfig(component_id="LiquidationActor-001")

    wallet = nautilus_env.polymarket_wallet_config()
    data_cfg = _polymarket_data_config(wallet)
    exec_cfg = _polymarket_exec_config(wallet)
    if data_cfg is None:
        print("[warn] Polymarket DataClient disabled — no Polymarket ticker stream")

    exec_clients: dict = {}
    data_clients: dict = {
        "BINANCE": BinanceDataClientConfig(
            account_type=BinanceAccountType.USDT_FUTURES,
            api_key=None,
            api_secret=None,
        )
    }
    if data_cfg is not None:
        data_clients["POLYMARKET"] = data_cfg
        print("[nautilus] Polymarket DataClient enabled")
    open_check_secs = float(os.environ.get("POLYMARKET_OPEN_CHECK_INTERVAL_SEC", "30"))
    exec_engine = LiveExecEngineConfig(
        open_check_interval_secs=open_check_secs if exec_cfg else None,
    )
    if exec_cfg is not None:
        exec_clients["POLYMARKET"] = exec_cfg
        print("[nautilus] Polymarket ExecutionClient enabled (idle, no strategies)")

    config = TradingNodeConfig(
        trader_id="TERMINAL-SIRIUS-001",
        cache=_cache_config(),
        data_clients=data_clients,
        exec_clients=exec_clients,
        data_engine=LiveDataEngineConfig(
            time_bars_build_with_no_updates=False,
        ),
        exec_engine=exec_engine,
    )

    node = TradingNode(config=config)
    node.add_data_client_factory("BINANCE", BinanceLiveDataClientFactory)
    if data_cfg is not None:
        node.add_data_client_factory("POLYMARKET", PolymarketLiveDataClientFactory)
    if exec_cfg is not None:
        node.add_exec_client_factory("POLYMARKET", PolymarketLiveExecClientFactory)

    bridge = BridgeActor(config=bridge_cfg, data_queue=data_queue)
    node.trader.add_actor(bridge)

    rt_cfg = RealtimeBucketActorConfig(
        component_id="RealtimeBucketActor-001",
        instrument_ids=instruments,
    )
    rt_actor = RealtimeBucketActor(config=rt_cfg, data_queue=data_queue)
    node.trader.add_actor(rt_actor)
    print("[nautilus] RealtimeBucketActor enabled (1s/5s bars + indicators → WS queue)")

    _polymarket_quote_bridge = None
    if data_cfg is not None:
        qb_cfg = PolymarketQuoteBridgeActorConfig(
            component_id="PolymarketQuoteBridge-001",
            initial_slugs=_polymarket_slugs(),
            initial_series=_polymarket_series(),
        )
        qb_actor = PolymarketQuoteBridgeActor(config=qb_cfg, data_queue=data_queue)
        node.trader.add_actor(qb_actor)
        _polymarket_quote_bridge = qb_actor
        print("[nautilus] PolymarketQuoteBridgeActor enabled (Nautilus quotes → WS queue)")

        pm_rt_cfg = PolymarketRealtimeBucketActorConfig(
            component_id="PolymarketRealtimeBucket-001",
            series=_polymarket_series(),
        )
        node.trader.add_actor(
            PolymarketRealtimeBucketActor(config=pm_rt_cfg, data_queue=data_queue)
        )
        print("[nautilus] PolymarketRealtimeBucketActor enabled (1s/5s UP bars → WS queue)")

    liq_actor = LiquidationActor(config=liq_cfg, data_queue=data_queue)
    node.trader.add_actor(liq_actor)

    from recorders.config import (
        binance_instruments_from_env,
        flush_interval_ms_from_env,
        is_enabled as market_recorder_enabled,
        max_batch_rows_from_env,
        polymarket_series_from_env,
    )
    from recorders.catalog_writer import CatalogWriter
    from recorders.market_recorder_actor import MarketRecorderActor, MarketRecorderActorConfig

    if market_recorder_enabled():
        _catalog_writer = CatalogWriter(
            flush_interval_ms=flush_interval_ms_from_env(),
            max_batch_rows=max_batch_rows_from_env(),
        )
        _catalog_writer.start()
        recorder_cfg = MarketRecorderActorConfig(
            component_id="MarketRecorder-001",
            binance_instruments=binance_instruments_from_env(),
            polymarket_series=polymarket_series_from_env() if data_cfg is not None else (),
        )
        node.trader.add_actor(MarketRecorderActor(config=recorder_cfg, writer=_catalog_writer))
        print("[nautilus] MarketRecorderActor enabled → ParquetDataCatalog")

    node.build()
    _trading_node = node
    return node


def run_node_in_thread(data_queue: queue.Queue) -> threading.Thread:
    def _run() -> None:
        global _polymarket_quote_bridge, _catalog_writer, _trading_node
        node = build_node(data_queue)
        try:
            node.run()
        finally:
            _trading_node = None
            node.dispose()
            _polymarket_quote_bridge = None
            if _catalog_writer is not None:
                _catalog_writer.stop()
                _catalog_writer = None

    thread = threading.Thread(target=_run, daemon=True, name="nautilus-node")
    thread.start()
    return thread
