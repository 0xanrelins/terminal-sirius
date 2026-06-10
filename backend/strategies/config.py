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
    liq_threshold_btc: PositiveFloat = 10_000.0
    liq_threshold_eth: PositiveFloat = 10_000.0
    liq_threshold_sol: PositiveFloat = 5_000.0
    liq_threshold_xrp: PositiveFloat = 5_000.0
    liq_threshold_doge: PositiveFloat = 2_500.0


class LiquidationVerdictActorConfig(ActorConfig, frozen=True):
    instrument_ids: tuple[str, ...]
    backtest_mode: bool = False
    max_observation_sec: PositiveInt = 450
    liq_move_threshold_pct: PositiveFloat = 0.2
    recovery_move_threshold_pct: PositiveFloat = 0.2
    min_notional: PositiveFloat = 0.0
    min_notional_btc: PositiveFloat = 10_000.0
    min_notional_eth: PositiveFloat = 10_000.0
    min_notional_sol: PositiveFloat = 5_000.0
    min_notional_xrp: PositiveFloat = 5_000.0
    min_notional_doge: PositiveFloat = 2_500.0


class VwapSignalActorConfig(ActorConfig, frozen=True):
    instrument_ids: tuple[str, ...]
    bar_period: PositiveInt = 900
    zone_pct: PositiveFloat = 0.15
    slope_range_threshold: PositiveFloat = 0.05


class TerminalSiriusStrategyConfig(StrategyConfig, frozen=True):
    binance_instruments: tuple[str, ...]
    polymarket_series: tuple[str, ...]
    backtest_mode: bool = False
    bar_period: PositiveInt = 900
    slope_range_threshold: PositiveFloat = 0.05
    liq_threshold_btc: PositiveFloat = 10_000.0
    liq_threshold_eth: PositiveFloat = 10_000.0
    liq_threshold_sol: PositiveFloat = 5_000.0
    liq_threshold_xrp: PositiveFloat = 5_000.0
    liq_threshold_doge: PositiveFloat = 2_500.0
    pos_multiplier_small: PositiveFloat = 1.5
    pos_multiplier_large: PositiveFloat = 3.0
    trade_size: Decimal = Decimal("10")
    recalc_interval_sec: PositiveFloat = 1.0
    recovery_exit_pct: PositiveFloat = 0.2
    max_entry_token_price: PositiveFloat = 0.5
    min_seconds_to_expiry_for_entry: PositiveInt = 250
    use_verdict_triggers: bool = False
    use_rolling_liq_triggers: bool = True
    verdict_min_recovery_move_pct: PositiveFloat = 0.2
    verdict_max_time_sec: PositiveFloat = 450.0
    verdict_min_area_bias: float = 0.0
