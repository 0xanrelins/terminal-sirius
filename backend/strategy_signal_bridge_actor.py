"""
StrategySignalBridgeActor — live strategy signal state for the UI widget.

Subscribes to native msgbus custom data (``VwapZoneSnapshot``, ``LiquidationVolumeSnapshot``,
``LiquidationTrigger``) and ``events.order.*``, mirrors ``TerminalSiriusStrategy`` layer state,
and flushes ``strategy_signal_snapshot`` to the FastAPI WS queue on a timer.
"""
from __future__ import annotations

import math
import queue
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from nautilus_trader.common.actor import Actor
from nautilus_trader.config import ActorConfig
from nautilus_trader.core.data import Data
from nautilus_trader.model.data import DataType
from nautilus_trader.model.events import OrderSubmitted

from strategies.liquidation_signal_actor import threshold_for_symbol
from strategies.messages import LiquidationTrigger
from strategies.messages import LiquidationVolumeSnapshot
from strategies.messages import VwapZoneSnapshot
from strategies.signal_state import SignalInputs
from strategies.signal_state import compute_derived
from strategy_signal_tags import parse_entry_signal_tag

if TYPE_CHECKING:
    import multiprocessing


class StrategySignalBridgeActorConfig(ActorConfig, frozen=True):
    instrument_ids: tuple[str, ...]
    slope_range_threshold: float = 0.05
    snapshot_interval_sec: float = 2.0
    liq_threshold_btc: float = 500_000.0
    liq_threshold_eth: float = 200_000.0
    liq_threshold_sol: float = 100_000.0
    liq_threshold_xrp: float = 50_000.0
    liq_threshold_doge: float = 25_000.0


@dataclass
class _SymbolState:
    slope: float | None = None
    vwap: float | None = None
    low_zone: float | None = None
    high_zone: float | None = None
    last_price: float | None = None
    liq_long_trigger: bool = False
    liq_short_trigger: bool = False
    vwap_ready: bool = False
    long_volume: float = 0.0
    short_volume: float = 0.0
    liq_long_hit: bool = False
    liq_short_hit: bool = False


def _json_safe(obj: Any) -> Any:
    if obj is None or isinstance(obj, (str, bool, int)):
        return obj
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    return str(obj)


class StrategySignalBridgeActor(Actor):
    def __init__(
        self,
        config: StrategySignalBridgeActorConfig,
        data_queue: queue.Queue | multiprocessing.queues.Queue,
    ) -> None:
        super().__init__(config)
        self._queue = data_queue
        self._slope_eps = float(config.slope_range_threshold)
        self._interval_sec = float(config.snapshot_interval_sec)
        self._states: dict[str, _SymbolState] = {
            sym: _SymbolState() for sym in config.instrument_ids
        }
        self._thresholds = {
            sym: threshold_for_symbol(config, sym) for sym in config.instrument_ids
        }

    def _enqueue(self, msg: dict) -> None:
        try:
            self._queue.put_nowait(msg)
        except queue.Full:
            pass

    def on_start(self) -> None:
        for data_cls in (VwapZoneSnapshot, LiquidationVolumeSnapshot, LiquidationTrigger):
            self.msgbus.subscribe(
                topic=f"data.{DataType(data_cls).topic}",
                handler=self.handle_data,
            )
        self.msgbus.subscribe(topic="events.order.*", handler=self._on_order_event)
        self.clock.set_timer(
            "strategy_signal_snapshot",
            timedelta(seconds=self._interval_sec),
            callback=self._on_snapshot_timer,
        )
        print(
            "[strategy-signals] StrategySignalBridgeActor → strategy_signal_snapshot "
            f"every {self._interval_sec}s ({len(self._states)} symbols)"
        )

    def on_stop(self) -> None:
        try:
            self.clock.cancel_timer("strategy_signal_snapshot")
        except Exception:  # noqa: BLE001
            pass

    def handle_data(self, data: Data) -> None:
        if isinstance(data, VwapZoneSnapshot):
            symbol = str(data.instrument_id)
            st = self._states.get(symbol)
            if st is None:
                return
            st.vwap = data.vwap
            st.slope = data.slope
            st.low_zone = data.low_zone
            st.high_zone = data.high_zone
            st.last_price = data.close
            st.vwap_ready = True
        elif isinstance(data, LiquidationVolumeSnapshot):
            symbol = str(data.instrument_id)
            st = self._states.get(symbol)
            if st is None:
                return
            st.long_volume = data.long_volume
            st.short_volume = data.short_volume
            st.liq_long_hit = data.long_hit
            st.liq_short_hit = data.short_hit
        elif isinstance(data, LiquidationTrigger):
            symbol = str(data.instrument_id)
            st = self._states.get(symbol)
            if st is None:
                return
            if data.long_triggered:
                st.liq_long_trigger = True
            if data.short_triggered:
                st.liq_short_trigger = True

    def _on_order_event(self, event) -> None:
        if not isinstance(event, OrderSubmitted):
            return
        order = self.cache.order(event.client_order_id)
        if order is None:
            return
        parsed = parse_entry_signal_tag(order.tags)
        if not parsed:
            return
        symbol = parsed.get("sym")
        if not symbol:
            return
        st = self._states.get(symbol)
        if st is None:
            return
        st.liq_long_trigger = False
        st.liq_short_trigger = False

    def _on_snapshot_timer(self, _event) -> None:
        try:
            self._enqueue(_json_safe(self._build_snapshot()))
        except Exception as e:  # noqa: BLE001
            self.log.warning(f"strategy_signal_snapshot build failed: {e!r}")

    def _build_snapshot(self) -> dict:
        symbols_out: dict[str, dict] = {}
        for symbol, st in self._states.items():
            inputs = SignalInputs(
                vwap=st.vwap,
                slope=st.slope,
                low_zone=st.low_zone,
                high_zone=st.high_zone,
                last_price=st.last_price,
                liq_long_trigger=st.liq_long_trigger,
                liq_short_trigger=st.liq_short_trigger,
                slope_eps=self._slope_eps,
                vwap_ready=st.vwap_ready,
            )
            derived = compute_derived(inputs)
            symbols_out[symbol] = {
                "vwap": st.vwap,
                "slope": st.slope,
                "low_zone": st.low_zone,
                "high_zone": st.high_zone,
                "close": st.last_price,
                "vwap_ready": st.vwap_ready,
                "long_volume": st.long_volume,
                "short_volume": st.short_volume,
                "liq_threshold": self._thresholds.get(symbol),
                "liq_long_hit": st.liq_long_hit,
                "liq_short_hit": st.liq_short_hit,
                "liq_long_trigger": st.liq_long_trigger,
                "liq_short_trigger": st.liq_short_trigger,
                "in_range": derived.in_range,
                "long_zone": derived.long_zone,
                "short_zone": derived.short_zone,
                "decision": derived.decision,
            }
        return {
            "type": "strategy_signal_snapshot",
            "ts": self.clock.timestamp_ns(),
            "symbols": symbols_out,
        }
