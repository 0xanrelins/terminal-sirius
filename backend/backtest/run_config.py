"""
Build ``BacktestRunConfig`` for Terminal Sirius from ParquetDataCatalog.

Uses native ``BacktestDataConfig``, ``BacktestVenueConfig``, ``ImportableActorConfig``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from nautilus_trader.adapters.polymarket import POLYMARKET_VENUE
from nautilus_trader.backtest.config import BacktestDataConfig, BacktestEngineConfig, BacktestRunConfig
from nautilus_trader.backtest.config import BacktestVenueConfig
from nautilus_trader.common.config import ImportableActorConfig
from nautilus_trader.config import DataCatalogConfig, LoggingConfig
from nautilus_trader.model.data import QuoteTick, TradeTick
from nautilus_trader.model.identifiers import TraderId
from nautilus_trader.trading.config import ImportableStrategyConfig

from adapters.polymarket.messages import ActivePolymarketMarket
from recorders.data_types import LiquidationTick
from strategies.mapping import BINANCE_TO_POLY_SERIES, STRATEGY_BINANCE_INSTRUMENTS


def _iso_to_ns(value: str | int | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1_000_000_000)


def build_terminal_sirius_run_config(
    *,
    catalog_path: str | Path,
    start_time: str | int | None = None,
    end_time: str | int | None = None,
    binance_instruments: tuple[str, ...] = STRATEGY_BINANCE_INSTRUMENTS,
    include_strategy: bool = True,
    include_liquidations: bool = True,
    include_polymarket_quotes: bool = True,
    log_level: str = "INFO",
) -> BacktestRunConfig:
    """Assemble a single ``BacktestRunConfig`` for catalog replay."""
    path = str(Path(catalog_path).expanduser().resolve())
    start_ns = _iso_to_ns(start_time)
    end_ns = _iso_to_ns(end_time)

    data_configs: list[BacktestDataConfig] = []
    for instrument_id in binance_instruments:
        common = dict(
            catalog_path=path,
            start_time=start_ns,
            end_time=end_ns,
            instrument_id=instrument_id,
        )
        data_configs.append(BacktestDataConfig(data_cls=TradeTick, **common))

    if include_liquidations:
        data_configs.append(
            BacktestDataConfig(
                catalog_path=path,
                data_cls=LiquidationTick,
                client_id="BACKTEST",
                start_time=start_ns,
                end_time=end_ns,
            ),
        )

    if include_polymarket_quotes:
        data_configs.append(
            BacktestDataConfig(
                catalog_path=path,
                data_cls=QuoteTick,
                start_time=start_ns,
                end_time=end_ns,
            ),
        )

    if include_strategy:
        data_configs.append(
            BacktestDataConfig(
                catalog_path=path,
                data_cls=ActivePolymarketMarket,
                client_id="BACKTEST",
                start_time=start_ns,
                end_time=end_ns,
            ),
        )

    actors = [
        ImportableActorConfig(
            actor_path="strategies.liquidation_verdict_actor:LiquidationVerdictActor",
            config_path="strategies.config:LiquidationVerdictActorConfig",
            config={
                "component_id": "LiqVerdictActor-BT",
                "instrument_ids": list(binance_instruments),
                "backtest_mode": True,
            },
        ),
        ImportableActorConfig(
            actor_path="strategies.liquidation_signal_actor:LiquidationSignalActor",
            config_path="strategies.config:LiquidationSignalActorConfig",
            config={
                "component_id": "LiqSignalActor-BT",
                "instrument_ids": list(binance_instruments),
                "backtest_mode": True,
            },
        ),
        ImportableActorConfig(
            actor_path="strategies.vwap_signal_actor:VwapSignalActor",
            config_path="strategies.config:VwapSignalActorConfig",
            config={
                "component_id": "VwapSignalActor-BT",
                "instrument_ids": list(binance_instruments),
            },
        ),
    ]

    strategies: list[ImportableStrategyConfig] = []
    if include_strategy:
        strategies.append(
            ImportableStrategyConfig(
                strategy_path="strategies.terminal_sirius_strategy:TerminalSiriusStrategy",
                config_path="strategies.config:TerminalSiriusStrategyConfig",
                config={
                    "binance_instruments": list(binance_instruments),
                    "polymarket_series": list(BINANCE_TO_POLY_SERIES.values()),
                    "backtest_mode": True,
                    "use_verdict_triggers": False,
                    "use_rolling_liq_triggers": True,
                },
            ),
        )

    engine = BacktestEngineConfig(
        trader_id=TraderId("TERMINAL-SIRIUS-BT-001"),
        logging=LoggingConfig(log_level=log_level),
        catalogs=[DataCatalogConfig(path=path)],
        actors=actors,
        strategies=strategies,
    )

    venues = [
        # Binance is data-only here (strategy trades on Polymarket); the venue must exist
        # because TradeTick/instruments reference it.
        BacktestVenueConfig(
            name="BINANCE",
            oms_type="NETTING",
            account_type="MARGIN",
            starting_balances=["1_000_000 USDT"],
        ),
        BacktestVenueConfig(
            name=str(POLYMARKET_VENUE),
            oms_type="NETTING",
            account_type="CASH",
            starting_balances=["10_000 pUSD"],
        ),
    ]

    return BacktestRunConfig(
        engine=engine,
        venues=venues,
        data=data_configs,
        chunk_size=50_000,
        start=start_time,
        end=end_time,
    )
