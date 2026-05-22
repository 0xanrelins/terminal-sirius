"""
Nautilus Strategy for Polymarket live order execution (Terminal Sirius).

Signal logic remains in SimulationEngine / LiveTradingEngine on the FastAPI loop;
this strategy owns live CLOB submission via the node's PolymarketExecutionClient
(RetryManager, order cache) when registered.
"""
from __future__ import annotations

from nautilus_trader.config import StrategyConfig
from nautilus_trader.trading.strategy import Strategy


class LiqPolyStrategyConfig(StrategyConfig, frozen=True):
    """Minimal config — liq/poly rules live in FastAPI engines for now."""


class LiqPolyStrategy(Strategy):
    """Registers with nautilus_bridge; Polymarket exec runs on the TradingNode."""

    def __init__(self, config: LiqPolyStrategyConfig) -> None:
        super().__init__(config)

    def on_start(self) -> None:
        from nautilus_bridge.context import set_liq_poly_strategy

        set_liq_poly_strategy(self)
        self.log.info("LiqPolyStrategy started (Polymarket execution via TradingNode)")

    def on_stop(self) -> None:
        from nautilus_bridge.context import set_liq_poly_strategy

        set_liq_poly_strategy(None)
        self.log.info("LiqPolyStrategy stopped")
