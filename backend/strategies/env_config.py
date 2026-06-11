"""Load strategy / actor config from environment (paper trade tuning)."""

from __future__ import annotations

import os
from decimal import Decimal

from strategies.config import (
    FreshPaperStrategyConfig,
    LiquidationSignalActorConfig,
    LiquidationVerdictActorConfig,
    TerminalSiriusStrategyConfig,
    VwapSignalActorConfig,
)


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    return float(raw)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    return int(raw)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def _env_decimal(name: str, default: str) -> Decimal:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return Decimal(default)
    return Decimal(raw)


def build_liquidation_verdict_config(
    *,
    component_id: str,
    instrument_ids: tuple[str, ...],
    backtest_mode: bool = False,
) -> LiquidationVerdictActorConfig:
    return LiquidationVerdictActorConfig(
        component_id=component_id,
        instrument_ids=instrument_ids,
        backtest_mode=backtest_mode,
        max_observation_sec=_env_int("VERDICT_MAX_OBSERVATION_SEC", 450),
        liq_move_threshold_pct=_env_float("VERDICT_LIQ_MOVE_THRESHOLD_PCT", 0.2),
        recovery_move_threshold_pct=_env_float("VERDICT_RECOVERY_MOVE_THRESHOLD_PCT", 0.2),
        min_notional=_env_float("VERDICT_MIN_NOTIONAL", 0.0),
        min_notional_btc=_env_float("VERDICT_MIN_NOTIONAL_BTC", 10_000.0),
        min_notional_eth=_env_float("VERDICT_MIN_NOTIONAL_ETH", 10_000.0),
        min_notional_sol=_env_float("VERDICT_MIN_NOTIONAL_SOL", 5_000.0),
        min_notional_xrp=_env_float("VERDICT_MIN_NOTIONAL_XRP", 5_000.0),
        min_notional_doge=_env_float("VERDICT_MIN_NOTIONAL_DOGE", 2_500.0),
    )


def build_liquidation_signal_config(
    *,
    component_id: str,
    instrument_ids: tuple[str, ...],
) -> LiquidationSignalActorConfig:
    return LiquidationSignalActorConfig(
        component_id=component_id,
        instrument_ids=instrument_ids,
        liq_threshold_btc=_env_float("LIQ_THRESHOLD_BTC", 10_000.0),
        liq_threshold_eth=_env_float("LIQ_THRESHOLD_ETH", 10_000.0),
        liq_threshold_sol=_env_float("LIQ_THRESHOLD_SOL", 5_000.0),
        liq_threshold_xrp=_env_float("LIQ_THRESHOLD_XRP", 5_000.0),
        liq_threshold_doge=_env_float("LIQ_THRESHOLD_DOGE", 2_500.0),
    )


def build_vwap_signal_config(
    *,
    component_id: str,
    instrument_ids: tuple[str, ...],
) -> VwapSignalActorConfig:
    return VwapSignalActorConfig(
        component_id=component_id,
        instrument_ids=instrument_ids,
        bar_period=_env_int("STRATEGY_BAR_PERIOD", 900),
        zone_pct=_env_float("STRATEGY_ZONE_PCT", 0.15),
        slope_range_threshold=_env_float("STRATEGY_SLOPE_RANGE_THRESHOLD", 0.05),
    )


def build_terminal_sirius_config(
    *,
    binance_instruments: tuple[str, ...],
    polymarket_series: tuple[str, ...],
) -> TerminalSiriusStrategyConfig:
    return TerminalSiriusStrategyConfig(
        binance_instruments=binance_instruments,
        polymarket_series=polymarket_series,
        bar_period=_env_int("STRATEGY_BAR_PERIOD", 900),
        slope_range_threshold=_env_float("STRATEGY_SLOPE_RANGE_THRESHOLD", 0.05),
        liq_threshold_btc=_env_float("LIQ_THRESHOLD_BTC", 10_000.0),
        liq_threshold_eth=_env_float("LIQ_THRESHOLD_ETH", 10_000.0),
        liq_threshold_sol=_env_float("LIQ_THRESHOLD_SOL", 5_000.0),
        liq_threshold_xrp=_env_float("LIQ_THRESHOLD_XRP", 5_000.0),
        liq_threshold_doge=_env_float("LIQ_THRESHOLD_DOGE", 2_500.0),
        pos_multiplier_small=_env_float("STRATEGY_POS_MULT_SMALL", 1.5),
        pos_multiplier_large=_env_float("STRATEGY_POS_MULT_LARGE", 3.0),
        trade_size=_env_decimal("STRATEGY_TRADE_SIZE", "10"),
        recalc_interval_sec=_env_float("STRATEGY_RECALC_INTERVAL_SEC", 1.0),
        recovery_exit_pct=_env_float("STRATEGY_RECOVERY_EXIT_PCT", 0.2),
        max_entry_token_price=_env_float("STRATEGY_MAX_ENTRY_TOKEN_PRICE", 0.5),
        min_seconds_to_expiry_for_entry=_env_int(
            "STRATEGY_MIN_SECONDS_TO_EXPIRY_FOR_ENTRY",
            250,
        ),
        use_verdict_triggers=_env_bool("STRATEGY_USE_VERDICT_TRIGGERS", False),
        use_rolling_liq_triggers=_env_bool("STRATEGY_USE_ROLLING_LIQ_TRIGGERS", True),
        verdict_min_recovery_move_pct=_env_float("VERDICT_RECOVERY_MOVE_THRESHOLD_PCT", 0.2),
        verdict_max_time_sec=_env_float("VERDICT_MAX_TIME_SEC", 450.0),
        verdict_min_area_bias=_env_float("VERDICT_MIN_AREA_BIAS", 0.0),
    )


def build_fresh_paper_strategy_config(
    *,
    binance_instruments: tuple[str, ...],
    polymarket_series: tuple[str, ...],
) -> FreshPaperStrategyConfig:
    return FreshPaperStrategyConfig(
        binance_instruments=binance_instruments,
        polymarket_series=polymarket_series,
        strategy_id=os.environ.get("PAPER_STRATEGY_ID", "fresh_paper").strip() or "fresh_paper",
        recalc_interval_sec=_env_float("PAPER_STRATEGY_RECALC_INTERVAL_SEC", 1.0),
        trade_enabled=_env_bool("PAPER_STRATEGY_TRADE_ENABLED", True),
        trade_size=_env_decimal("PAPER_STRATEGY_TRADE_SIZE", "10"),
        max_entry_token_price=_env_float("PAPER_STRATEGY_MAX_ENTRY_TOKEN_PRICE", 0.5),
        recovery_exit_pct=_env_float("PAPER_STRATEGY_RECOVERY_EXIT_PCT", 0.2),
        min_seconds_to_expiry_for_entry=_env_int(
            "PAPER_STRATEGY_MIN_SECONDS_TO_EXPIRY_FOR_ENTRY", 200
        ),
        max_hold_seconds=_env_int("PAPER_STRATEGY_MAX_HOLD_SECONDS", 200),
        liq_threshold_btc=_env_float("LIQ_THRESHOLD_BTC", 10_000.0),
        liq_threshold_eth=_env_float("LIQ_THRESHOLD_ETH", 10_000.0),
        liq_threshold_sol=_env_float("LIQ_THRESHOLD_SOL", 5_000.0),
        liq_threshold_xrp=_env_float("LIQ_THRESHOLD_XRP", 5_000.0),
        liq_threshold_doge=_env_float("LIQ_THRESHOLD_DOGE", 2_500.0),
        use_vwap_input=_env_bool("PAPER_STRATEGY_USE_VWAP_INPUT", False),
        use_liquidation_input=_env_bool("PAPER_STRATEGY_USE_LIQUIDATION_INPUT", True),
        use_verdict_input=_env_bool("PAPER_STRATEGY_USE_VERDICT_INPUT", False),
    )


def build_strategy_signal_bridge_config(
    *,
    component_id: str,
    instrument_ids: tuple[str, ...],
) -> "StrategySignalBridgeActorConfig":
    from strategy_signal_bridge_actor import StrategySignalBridgeActorConfig

    return StrategySignalBridgeActorConfig(
        component_id=component_id,
        instrument_ids=instrument_ids,
        strategy_id=os.environ.get("PAPER_STRATEGY_ID", "fresh_paper").strip() or "fresh_paper",
        trade_enabled=_env_bool("PAPER_STRATEGY_TRADE_ENABLED", True),
        recovery_exit_pct=_env_float("PAPER_STRATEGY_RECOVERY_EXIT_PCT", 0.2),
        max_entry_token_price=_env_float("PAPER_STRATEGY_MAX_ENTRY_TOKEN_PRICE", 0.5),
        min_seconds_to_expiry_for_entry=_env_int(
            "PAPER_STRATEGY_MIN_SECONDS_TO_EXPIRY_FOR_ENTRY", 200
        ),
        max_hold_seconds=_env_int("PAPER_STRATEGY_MAX_HOLD_SECONDS", 200),
        snapshot_interval_sec=_env_float(
            "STRATEGY_SIGNAL_SNAPSHOT_INTERVAL_SEC",
            _env_float("PAPER_SNAPSHOT_INTERVAL_SEC", 2.0),
        ),
        use_vwap_input=_env_bool("PAPER_STRATEGY_USE_VWAP_INPUT", False),
        use_verdict_input=_env_bool("PAPER_STRATEGY_USE_VERDICT_INPUT", False),
        liq_threshold_btc=_env_float("LIQ_THRESHOLD_BTC", 10_000.0),
        liq_threshold_eth=_env_float("LIQ_THRESHOLD_ETH", 10_000.0),
        liq_threshold_sol=_env_float("LIQ_THRESHOLD_SOL", 5_000.0),
        liq_threshold_xrp=_env_float("LIQ_THRESHOLD_XRP", 5_000.0),
        liq_threshold_doge=_env_float("LIQ_THRESHOLD_DOGE", 2_500.0),
    )


def log_strategy_env_summary() -> None:
    """Print active thresholds at startup (no secrets)."""
    print(
        "[strategy] liq thresholds ($, single-event): "
        f"BTC={_env_float('LIQ_THRESHOLD_BTC', 10_000):,.0f} "
        f"ETH={_env_float('LIQ_THRESHOLD_ETH', 10_000):,.0f} "
        f"SOL={_env_float('LIQ_THRESHOLD_SOL', 5_000):,.0f} "
        f"XRP={_env_float('LIQ_THRESHOLD_XRP', 5_000):,.0f} "
        f"DOGE={_env_float('LIQ_THRESHOLD_DOGE', 2_500):,.0f}"
    )
    print(
        f"[strategy] zone_pct={_env_float('STRATEGY_ZONE_PCT', 0.15):.4f}% "
        f"recovery_exit_pct={_env_float('STRATEGY_RECOVERY_EXIT_PCT', 0.2):.4f}% "
        f"max_entry_token_price={_env_float('STRATEGY_MAX_ENTRY_TOKEN_PRICE', 0.5):.4f} "
        f"min_tte_entry={_env_int('STRATEGY_MIN_SECONDS_TO_EXPIRY_FOR_ENTRY', 250)}s "
        f"trade_size={_env_decimal('STRATEGY_TRADE_SIZE', '10')} "
        f"warm-up≈{_env_int('STRATEGY_BAR_PERIOD', 900)}s"
    )
    print(
        "[strategy] active=fresh_paper "
        f"paper_strategy_id={os.environ.get('PAPER_STRATEGY_ID', 'fresh_paper').strip() or 'fresh_paper'} "
        f"trade_enabled={_env_bool('PAPER_STRATEGY_TRADE_ENABLED', True)} "
        f"fresh_trade_size={_env_decimal('PAPER_STRATEGY_TRADE_SIZE', '10')} shares "
        f"fresh_max_entry={_env_float('PAPER_STRATEGY_MAX_ENTRY_TOKEN_PRICE', 0.5):.4f} "
        f"fresh_recovery_exit={_env_float('PAPER_STRATEGY_RECOVERY_EXIT_PCT', 0.2):.4f}%"
    )
