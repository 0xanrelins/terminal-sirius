"""Build LiqPolyStrategyConfig from env + DB restore payload."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from simulation.config import ASSETS, bet_window_open

Mode = Literal["live", "sim", "backtest"]


@dataclass
class RestoreBet:
    bet_id: int
    binance_symbol: str
    side: str
    leg: int
    asset: str
    cycle_id: int
    candle_open: int
    poly_series: str
    entry_price: float
    shares: float
    cost_usd: float
    order_id: str | None = None


@dataclass
class RestoreState:
    signaled: list[tuple[str, int, str]] = field(default_factory=list)
    active_cycles: dict[tuple[str, str], int] = field(default_factory=dict)
    open_bets: list[RestoreBet] = field(default_factory=list)


@dataclass(frozen=True)
class LiqPolyRuntimeConfig:
    mode: Mode
    assets: dict[str, dict[str, str]]
    thresholds: dict[str, float]
    min_usd: float
    min_shares: float
    orders_enabled: bool
    restore: RestoreState


def runtime_for_backtest() -> LiqPolyRuntimeConfig:
    """Isolated sim config for BacktestEngine (no PostgreSQL restore)."""
    from simulation import config as cfg

    return LiqPolyRuntimeConfig(
        mode="sim",
        assets=cfg.active_assets(),
        thresholds=cfg.thresholds(),
        min_usd=cfg.min_usd(),
        min_shares=cfg.min_shares_default(),
        orders_enabled=False,
        restore=RestoreState(),
    )


def runtime_from_env(mode: Mode, restore: RestoreState | None = None) -> LiqPolyRuntimeConfig:
    if mode == "backtest":
        return runtime_for_backtest()
    if mode == "live":
        from live import config as cfg

        return LiqPolyRuntimeConfig(
            mode="live",
            assets=cfg.active_assets(),
            thresholds=cfg.thresholds(),
            min_usd=cfg.min_usd(),
            min_shares=cfg.min_shares_default(),
            orders_enabled=cfg.is_enabled(),
            restore=restore or RestoreState(),
        )
    from simulation import config as cfg

    return LiqPolyRuntimeConfig(
        mode="sim",
        assets=cfg.active_assets(),
        thresholds=cfg.thresholds(),
        min_usd=cfg.min_usd(),
        min_shares=cfg.min_shares_default(),
        orders_enabled=cfg.is_enabled(),
        restore=restore or RestoreState(),
    )


__all__ = [
    "RestoreBet",
    "RestoreState",
    "LiqPolyRuntimeConfig",
    "runtime_from_env",
    "runtime_for_backtest",
    "bet_window_open",
    "ASSETS",
]
