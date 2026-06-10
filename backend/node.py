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
  CATALOG_STREAMING_ENABLED — default true; native StreamingConfig on TradingNode
  CATALOG_PATH              — Parquet root (default backend/catalog)
  NAUTILUS_LOG_LEVEL        — Nautilus stdout log level (default WARNING)
  STRATEGY_ENABLED          — register TerminalSirius strategy + signal actors
  STRATEGY_PAPER_TRADE      — use SandboxExecutionClient for POLYMARKET (with STRATEGY_ENABLED)
"""
from __future__ import annotations

import multiprocessing
import os
import queue
from typing import TYPE_CHECKING

from nautilus_trader.adapters.binance.config import (
    BinanceAccountType,
    BinanceDataClientConfig,
    BinanceInstrumentProviderConfig,
)
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
from nautilus_trader.adapters.sandbox.factory import SandboxLiveExecClientFactory
from nautilus_trader.cache.config import CacheConfig
from nautilus_trader.config import (
    LiveDataEngineConfig,
    LiveExecEngineConfig,
    LoggingConfig,
    TradingNodeConfig,
)
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.identifiers import InstrumentId

import nautilus_env
from adapters.polymarket.orders import credentials_configured
from adapters.polymarket.quote_bridge_actor import (
    PolymarketQuoteBridgeActor,
    PolymarketQuoteBridgeActorConfig,
)
from bridge_actor import BridgeActor, BridgeActorConfig
from liquidation_feed_actor import (
    LiquidationFeedActor,
    LiquidationFeedActorConfig,
)
from liquidation_ui_bridge_actor import (
    LiquidationUiBridgeActor,
    LiquidationUiBridgeActorConfig,
)
from paper_trade_monitor_actor import (
    PaperTradeMonitorActor,
    PaperTradeMonitorActorConfig,
)
from polymarket_realtime_bucket_actor import (
    PolymarketRealtimeBucketActor,
    PolymarketRealtimeBucketActorConfig,
)
from realtime_bucket_actor import RealtimeBucketActor, RealtimeBucketActorConfig

if TYPE_CHECKING:
    from multiprocessing.context import BaseContext

DEFAULT_INSTRUMENTS = (
    "BTCUSDT-PERP.BINANCE",
    "ETHUSDT-PERP.BINANCE",
    "SOLUSDT-PERP.BINANCE",
    "XRPUSDT-PERP.BINANCE",
    "DOGEUSDT-PERP.BINANCE",
    "HYPEUSDT-PERP.BINANCE",
)

_polymarket_quote_bridge: PolymarketQuoteBridgeActor | None = None
_trading_node: TradingNode | None = None
_mp_ctx: BaseContext = multiprocessing.get_context("spawn")


def get_trading_node() -> TradingNode | None:
    return _trading_node


def strategy_enabled() -> bool:
    return os.environ.get("STRATEGY_ENABLED", "false").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _strategy_enabled() -> bool:
    return strategy_enabled()


def _strategy_paper_trade() -> bool:
    return os.environ.get("STRATEGY_PAPER_TRADE", "true").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _polymarket_exec_enabled() -> bool:
    raw = os.environ.get(
        "POLYMARKET_EXEC_ENABLED",
        os.environ.get("LIVE_ENABLED", "false"),
    )
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _nautilus_logging_config() -> LoggingConfig:
    """Keep TradingNode logs quiet in uvicorn.log (WARNING default, no log file)."""
    return LoggingConfig(
        log_level=os.environ.get("NAUTILUS_LOG_LEVEL", "WARNING"),
        log_level_file=os.environ.get("NAUTILUS_LOG_LEVEL_FILE", "OFF"),
    )


def cache_uses_postgres() -> bool:
    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        return False
    try:
        from nautilus_trader.cache.postgres.config import PostgresCacheConfig  # noqa: F401

        return True
    except ImportError:
        return False


def _cache_config() -> CacheConfig:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        return CacheConfig()
    try:
        from nautilus_trader.cache.postgres.config import PostgresCacheConfig

        return PostgresCacheConfig(database=dsn)
    except ImportError:
        return CacheConfig()


def _log_cache_startup(node: TradingNode) -> None:
    """Log whether Nautilus restored orders/positions from Cache (Postgres or memory)."""
    kind = "PostgresCache" if cache_uses_postgres() else "in-memory Cache"
    try:
        cache = node.trader.cache
        if cache is None:
            print(f"[nautilus] {kind}: trader cache not ready yet")
            return
        orders_n = len(cache.orders())
        positions_n = len(cache.positions())
        snapshots_n = len(cache.position_snapshots())
        print(
            f"[nautilus] {kind} at node startup: {orders_n} orders, "
            f"{positions_n} positions, {snapshots_n} position snapshots "
            "(restarted node should reload these when Postgres cache is enabled)"
        )
    except Exception as e:  # noqa: BLE001
        print(f"[nautilus] {kind} startup stats unavailable: {e!r}")


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
    resolve_poll_enabled = os.environ.get("POLYMARKET_RESOLVE_POLL_ENABLED", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    return PolymarketDataClientConfig(
        **_polymarket_client_fields(wallet),
        auto_load_missing_instruments=True,
        resolve_poll_enabled=resolve_poll_enabled,
        resolve_poll_interval_secs=int(os.environ.get("POLYMARKET_RESOLVE_POLL_INTERVAL_SECS", "5")),
        resolve_poll_grace_secs=int(os.environ.get("POLYMARKET_RESOLVE_POLL_GRACE_SECS", "2")),
        resolve_poll_max_wait_secs=int(os.environ.get("POLYMARKET_RESOLVE_POLL_MAX_WAIT_SECS", "3600")),
        instrument_config=PolymarketInstrumentProviderConfig(
            use_gamma_markets=True,
        ),
    )


def build_node(
    data_queue: queue.Queue | multiprocessing.queues.Queue,
    instruments: tuple[str, ...] = DEFAULT_INSTRUMENTS,
) -> TradingNode:
    global _polymarket_quote_bridge, _trading_node

    wallet = nautilus_env.polymarket_wallet_config()
    data_cfg = _polymarket_data_config(wallet)
    strategy_on = _strategy_enabled()
    paper_trade = _strategy_paper_trade()

    bridge_cfg = BridgeActorConfig(
        component_id="BridgeActor-001",
        instrument_ids=instruments,
    )
    exec_cfg = _polymarket_exec_config(wallet)
    if strategy_on and paper_trade:
        from nautilus_trader.adapters.sandbox.config import SandboxExecutionClientConfig
        from nautilus_trader.config import InstrumentProviderConfig

        exec_cfg = SandboxExecutionClientConfig(
            venue="POLYMARKET",
            starting_balances=[os.environ.get("STRATEGY_STARTING_BALANCE", "10_000 pUSD")],
            instrument_provider=InstrumentProviderConfig(load_all=True),
            account_type="CASH",
            oms_type="HEDGING",
        )
        print("[nautilus] Polymarket Sandbox execution (paper trade, binary settlement)")
    elif strategy_on and exec_cfg is None:
        print(
            "[warn] STRATEGY_ENABLED but no execution client — set STRATEGY_PAPER_TRADE=true "
            "or POLYMARKET_EXEC_ENABLED with creds"
        )
    if data_cfg is None:
        print("[warn] Polymarket DataClient disabled — no Polymarket ticker stream")

    exec_clients: dict = {}
    binance_load_ids = frozenset(InstrumentId.from_str(sym) for sym in instruments)
    data_clients: dict = {
        "BINANCE": BinanceDataClientConfig(
            account_type=BinanceAccountType.USDT_FUTURES,
            api_key=None,
            api_secret=None,
            instrument_provider=BinanceInstrumentProviderConfig(
                load_ids=binance_load_ids,
            ),
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
        if strategy_on:
            print("[nautilus] Polymarket execution enabled for TerminalSiriusStrategy")
        else:
            print("[nautilus] Polymarket ExecutionClient enabled (idle, no strategies)")

    from recorders.config import streaming_config, streaming_enabled

    streaming = streaming_config() if streaming_enabled() else None
    if streaming is not None:
        print(f"[nautilus] Catalog streaming → {streaming.catalog_path}")

    config = TradingNodeConfig(
        trader_id="TERMINAL-SIRIUS-001",
        logging=_nautilus_logging_config(),
        cache=_cache_config(),
        data_clients=data_clients,
        exec_clients=exec_clients,
        data_engine=LiveDataEngineConfig(
            time_bars_build_with_no_updates=False,
        ),
        exec_engine=exec_engine,
        streaming=streaming,
    )

    node = TradingNode(config=config)
    node.add_data_client_factory("BINANCE", BinanceLiveDataClientFactory)
    if data_cfg is not None:
        node.add_data_client_factory("POLYMARKET", PolymarketLiveDataClientFactory)
    if exec_cfg is not None:
        if strategy_on and paper_trade:
            node.add_exec_client_factory("POLYMARKET", SandboxLiveExecClientFactory)
        else:
            node.add_exec_client_factory("POLYMARKET", PolymarketLiveExecClientFactory)

    bridge = BridgeActor(config=bridge_cfg, data_queue=data_queue)
    node.trader.add_actor(bridge)

    from recorders.config import (
        catalog_path_from_env,
        flush_interval_ms_from_env,
        max_batch_rows_from_env,
        streaming_enabled,
    )

    liq_catalog_path = str(catalog_path_from_env()) if streaming_enabled() else None
    feed_cfg = LiquidationFeedActorConfig(
        component_id="LiquidationFeed-001",
        instrument_ids=instruments,
        catalog_path=liq_catalog_path,
        catalog_flush_interval_sec=flush_interval_ms_from_env() / 1000.0,
        catalog_max_batch=max_batch_rows_from_env(),
    )
    node.trader.add_actor(LiquidationFeedActor(config=feed_cfg))
    if liq_catalog_path:
        print(
            "[liquidations] LiquidationFeedActor → LiquidationTick bus + "
            f"catalog.write_data → {liq_catalog_path}"
        )
    else:
        print("[liquidations] LiquidationFeedActor → LiquidationTick bus (catalog flush off)")

    liq_ui_cfg = LiquidationUiBridgeActorConfig(component_id="LiquidationUiBridge-001")
    node.trader.add_actor(LiquidationUiBridgeActor(config=liq_ui_cfg, data_queue=data_queue))

    from liquidation_verdict_bridge_actor import (
        LiquidationVerdictBridgeActor,
        LiquidationVerdictBridgeActorConfig,
    )
    from strategies.env_config import build_liquidation_verdict_config
    from strategies.liquidation_verdict_actor import LiquidationVerdictActor

    node.trader.add_actor(
        LiquidationVerdictActor(
            config=build_liquidation_verdict_config(
                component_id="LiqVerdictActor-001",
                instrument_ids=instruments,
            ),
        ),
    )
    node.trader.add_actor(
        LiquidationVerdictBridgeActor(
            config=LiquidationVerdictBridgeActorConfig(
                component_id="LiqVerdictBridge-001",
            ),
            data_queue=data_queue,
        ),
    )
    print("[nautilus] LiquidationVerdictActor + bridge enabled (liquidation_verdict → WS queue)")

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

    if strategy_on:
        if data_cfg is None:
            print(
                "[warn] STRATEGY_ENABLED but POLYMARKET_DATA_ENABLED=false — no quote bridge, "
                "so the strategy receives no ActivePolymarketMarket and cannot trade Polymarket"
            )
        from strategies.env_config import (
            build_liquidation_signal_config,
            build_strategy_signal_bridge_config,
            build_terminal_sirius_config,
            build_vwap_signal_config,
            log_strategy_env_summary,
        )
        from strategy_signal_bridge_actor import StrategySignalBridgeActor
        from strategies.liquidation_signal_actor import LiquidationSignalActor
        from strategies.mapping import BINANCE_TO_POLY_SERIES, STRATEGY_BINANCE_INSTRUMENTS
        from strategies.terminal_sirius_strategy import TerminalSiriusStrategy
        from strategies.vwap_signal_actor import VwapSignalActor

        strategy_symbols = tuple(
            s for s in instruments if s in STRATEGY_BINANCE_INSTRUMENTS
        ) or STRATEGY_BINANCE_INSTRUMENTS
        poly_series = tuple(
            BINANCE_TO_POLY_SERIES[s]
            for s in strategy_symbols
            if s in BINANCE_TO_POLY_SERIES
        )

        node.trader.add_actor(
            LiquidationSignalActor(
                config=build_liquidation_signal_config(
                    component_id="LiqSignalActor-001",
                    instrument_ids=strategy_symbols,
                ),
            ),
        )
        node.trader.add_actor(
            VwapSignalActor(
                config=build_vwap_signal_config(
                    component_id="VwapSignalActor-001",
                    instrument_ids=strategy_symbols,
                ),
            ),
        )
        node.trader.add_strategy(
            TerminalSiriusStrategy(
                config=build_terminal_sirius_config(
                    binance_instruments=strategy_symbols,
                    polymarket_series=poly_series,
                ),
            ),
        )
        node.trader.add_actor(
            PaperTradeMonitorActor(
                config=PaperTradeMonitorActorConfig(
                    component_id="PaperTradeMonitor-001",
                    venue="POLYMARKET",
                    snapshot_interval_sec=float(
                        os.environ.get("PAPER_SNAPSHOT_INTERVAL_SEC", "2.0")
                    ),
                    paper_trade=paper_trade,
                ),
                data_queue=data_queue,
            ),
        )
        if paper_trade:
            from polymarket_settlement_actor import PolymarketSettlementActor
            from polymarket_settlement_actor import PolymarketSettlementActorConfig

            node.trader.add_actor(
                PolymarketSettlementActor(
                    config=PolymarketSettlementActorConfig(
                        component_id="PolySettlement-001",
                        binance_instruments=strategy_symbols,
                        venue="POLYMARKET",
                    ),
                ),
            )
            print(
                "[nautilus] PolymarketSettlementActor enabled "
                "(Binance 15m → InstrumentClose settlement)"
            )
        node.trader.add_actor(
            StrategySignalBridgeActor(
                config=build_strategy_signal_bridge_config(
                    component_id="StrategySignalBridge-001",
                    instrument_ids=strategy_symbols,
                ),
                data_queue=data_queue,
            ),
        )
        log_strategy_env_summary()
        mode = "paper (Sandbox)" if paper_trade else "live exec"
        print(f"[nautilus] TerminalSiriusStrategy + signal actors enabled — {mode}")
        print("[nautilus] PaperTradeMonitorActor enabled (paper_snapshot/paper_event → WS queue)")
        print("[nautilus] StrategySignalBridgeActor enabled (strategy_signal_snapshot → WS queue)")

    node.build()
    _log_cache_startup(node)
    _trading_node = node
    return node


def _node_process_main(
    data_queue: queue.Queue | multiprocessing.queues.Queue,
) -> None:
    """Child process entry — main thread owns signal handlers (no monkey-patch)."""
    global _polymarket_quote_bridge, _trading_node
    node = build_node(data_queue)
    try:
        node.run()
    finally:
        _trading_node = None
        try:
            node.stop()
        except Exception:
            pass
        node.dispose()
        _polymarket_quote_bridge = None


def create_data_queue() -> multiprocessing.queues.Queue:
    """Process-safe queue for Nautilus child → FastAPI parent."""
    return _mp_ctx.Queue(maxsize=10_000)


def run_node_in_process(
    data_queue: queue.Queue | multiprocessing.queues.Queue,
) -> multiprocessing.Process:
    """Start TradingNode in a child process (Nautilus-native signal handling)."""
    proc = _mp_ctx.Process(
        target=_node_process_main,
        args=(data_queue,),
        name="nautilus-node",
        daemon=True,
    )
    proc.start()
    return proc


# Backward-compatible alias for scripts/tests.
run_node_in_thread = run_node_in_process
