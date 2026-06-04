"""Strategy and actor configuration (``StrategyConfig`` / ``ActorConfig``)."""

from __future__ import annotations

from decimal import Decimal

from nautilus_trader.config import ActorConfig
from nautilus_trader.config import PositiveFloat
from nautilus_trader.config import PositiveInt
from nautilus_trader.config import StrategyConfig


class LiquidationSignalActorConfig(ActorConfig, frozen=True):
    instrument_ids: tuple[str, ...]
    backtest_mode: bool = False
    window_sec: PositiveInt = 900
    liq_threshold_btc: PositiveFloat = 500_000.0
    liq_threshold_eth: PositiveFloat = 200_000.0
    liq_threshold_sol: PositiveFloat = 100_000.0
    liq_threshold_xrp: PositiveFloat = 50_000.0
    liq_threshold_doge: PositiveFloat = 25_000.0


class VwapSignalActorConfig(ActorConfig, frozen=True):
    instrument_ids: tuple[str, ...]
    bar_period: PositiveInt = 900
    atr_multiplier: PositiveFloat = 1.5
    slope_range_threshold: PositiveFloat = 0.05


class TerminalSiriusStrategyConfig(StrategyConfig, frozen=True):
    binance_instruments: tuple[str, ...]
    polymarket_series: tuple[str, ...]
    backtest_mode: bool = False
    bar_period: PositiveInt = 900
    atr_multiplier: PositiveFloat = 1.5
    slope_range_threshold: PositiveFloat = 0.05
    liq_threshold_btc: PositiveFloat = 500_000.0
    liq_threshold_eth: PositiveFloat = 200_000.0
    liq_threshold_sol: PositiveFloat = 100_000.0
    liq_threshold_xrp: PositiveFloat = 50_000.0
    liq_threshold_doge: PositiveFloat = 25_000.0
    pos_multiplier_small: PositiveFloat = 1.5
    pos_multiplier_large: PositiveFloat = 3.0
    trade_size: Decimal = Decimal("10")
    recalc_interval_sec: PositiveFloat = 1.0
