"""
LiquidationSignalActor — rolling 900s liq volume → ``publish_data`` LiquidationTrigger.

Data: native ``BinanceFuturesLiquidation`` via ``subscribe_data`` on the Binance
data client (all-market ``!forceOrder@arr`` when Rust adapter is active).
"""

from __future__ import annotations

from nautilus_trader.adapters.binance import BinanceFuturesLiquidation
from nautilus_trader.common.actor import Actor
from nautilus_trader.core.data import Data
from nautilus_trader.model.data import DataType
from nautilus_trader.model.identifiers import ClientId
from nautilus_trader.model.identifiers import InstrumentId

from recorders.binance_liquidation import (
    instrument_symbol,
    liquidation_notional_usd,
    liquidation_side_str,
)
from strategies.config import LiquidationSignalActorConfig
from strategies.indicators.rolling_liquidation_volume import RollingLiquidationVolume
from strategies.messages import LiquidationTrigger


def _threshold_for_symbol(config: LiquidationSignalActorConfig, symbol: str) -> float:
    if "BTC" in symbol:
        return float(config.liq_threshold_btc)
    if "ETH" in symbol:
        return float(config.liq_threshold_eth)
    if "SOL" in symbol:
        return float(config.liq_threshold_sol)
    if "XRP" in symbol:
        return float(config.liq_threshold_xrp)
    if "DOGE" in symbol:
        return float(config.liq_threshold_doge)
    return float(config.liq_threshold_doge)


class LiquidationSignalActor(Actor):
    def __init__(self, config: LiquidationSignalActorConfig) -> None:
        super().__init__(config)
        self._symbols = tuple(config.instrument_ids)
        self._indicators: dict[str, RollingLiquidationVolume] = {
            sym: RollingLiquidationVolume(int(config.window_sec)) for sym in self._symbols
        }
        self._thresholds = {sym: _threshold_for_symbol(config, sym) for sym in self._symbols}
        self._last_long_trigger: dict[str, bool] = {sym: False for sym in self._symbols}
        self._last_short_trigger: dict[str, bool] = {sym: False for sym in self._symbols}

    def on_start(self) -> None:
        self.subscribe_data(
            DataType(BinanceFuturesLiquidation),
            client_id=ClientId("BINANCE"),
        )

    def on_data(self, data: Data) -> None:
        if not isinstance(data, BinanceFuturesLiquidation):
            return
        symbol = instrument_symbol(data)
        side = liquidation_side_str(data)
        notional = liquidation_notional_usd(data)
        ts_event = int(data.ts_event)
        if symbol not in self._indicators:
            return
        ind = self._indicators[symbol]
        if side == "SELL":
            ind.update_long_liquidation(ts_event=ts_event, notional=notional)
        elif side == "BUY":
            ind.update_short_liquidation(ts_event=ts_event, notional=notional)
        else:
            return
        self._maybe_publish_triggers(symbol, ind, ts_event)

    def _maybe_publish_triggers(
        self,
        symbol: str,
        ind: RollingLiquidationVolume,
        ts_event: int,
    ) -> None:
        if not ind.initialized:
            return
        threshold = self._thresholds[symbol]
        long_hit = ind.long_volume >= threshold
        short_hit = ind.short_volume >= threshold
        long_edge = long_hit and not self._last_long_trigger[symbol]
        short_edge = short_hit and not self._last_short_trigger[symbol]
        if long_edge or short_edge:
            self.publish_data(
                DataType(LiquidationTrigger),
                LiquidationTrigger(
                    instrument_id=InstrumentId.from_str(symbol),
                    long_triggered=long_edge,
                    short_triggered=short_edge,
                    ts_event=ts_event,
                    ts_init=ts_event,
                ),
            )
        self._last_long_trigger[symbol] = long_hit
        self._last_short_trigger[symbol] = short_hit
