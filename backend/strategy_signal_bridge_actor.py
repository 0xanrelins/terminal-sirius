"""
StrategySignalBridgeActor — live FreshPaperStrategy state for the UI widget.

Subscribes to msgbus custom data (``ActivePolymarketMarket``, ``LiquidationTick``,
optional ``VwapZoneSnapshot`` / ``LiquidationVerdict``), Binance trade ticks, Polymarket
quote ticks, and ``events.order.*``, then flushes ``strategy_signal_snapshot`` to the
FastAPI WS queue on a timer.
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
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.events import OrderSubmitted
from nautilus_trader.model.identifiers import InstrumentId

from adapters.polymarket.messages import ActivePolymarketMarket
from adapters.polymarket.rolling import WINDOW_SEC
from adapters.polymarket.rolling import parse_window_epoch_from_slug
from recorders.data_types import LiquidationTick
from strategies.liquidation_signal_actor import threshold_for_symbol
from strategies.mapping import BINANCE_TO_POLY_SERIES
from strategies.messages import LiquidationVerdict
from strategies.messages import VwapZoneSnapshot
from strategies.subscriptions import subscribe_custom_data
from strategy_signal_tags import parse_entry_signal_tag

if TYPE_CHECKING:
    import multiprocessing


class StrategySignalBridgeActorConfig(ActorConfig, frozen=True):
    instrument_ids: tuple[str, ...]
    strategy_id: str = "fresh_paper"
    trade_enabled: bool = True
    recovery_exit_pct: float = 0.2
    max_entry_token_price: float = 0.5
    min_seconds_to_expiry_for_entry: int = 200
    max_hold_seconds: int = 200
    snapshot_interval_sec: float = 2.0
    use_vwap_input: bool = False
    use_verdict_input: bool = False
    liq_threshold_btc: float = 10_000.0
    liq_threshold_eth: float = 10_000.0
    liq_threshold_sol: float = 5_000.0
    liq_threshold_xrp: float = 5_000.0
    liq_threshold_doge: float = 2_500.0


@dataclass
class _SymbolState:
    last_price: float | None = None
    liq_long_trigger: bool = False
    liq_short_trigger: bool = False
    active_slug: str | None = None
    yes_instrument_id: str | None = None
    no_instrument_id: str | None = None
    yes_ask: float | None = None
    no_ask: float | None = None
    vwap: float | None = None
    slope: float | None = None
    low_zone: float | None = None
    high_zone: float | None = None
    last_verdict_winner: str | None = None


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


def _ask_from_quote(tick: QuoteTick | None) -> float | None:
    if tick is None or tick.ask_price is None:
        return None
    try:
        return float(tick.ask_price)
    except (TypeError, ValueError):
        return None


class StrategySignalBridgeActor(Actor):
    def __init__(
        self,
        config: StrategySignalBridgeActorConfig,
        data_queue: queue.Queue | multiprocessing.queues.Queue,
    ) -> None:
        super().__init__(config)
        self._queue = data_queue
        self._interval_sec = float(config.snapshot_interval_sec)
        self._states: dict[str, _SymbolState] = {
            sym: _SymbolState() for sym in config.instrument_ids
        }
        self._thresholds = {
            sym: threshold_for_symbol(config, sym) for sym in config.instrument_ids
        }
        self._series_to_symbol = {
            series: sym
            for sym, series in BINANCE_TO_POLY_SERIES.items()
            if sym in self._states
        }
        self._subscribed_poly_quotes: set[InstrumentId] = set()

    def _enqueue(self, msg: dict) -> None:
        try:
            self._queue.put_nowait(msg)
        except queue.Full:
            pass

    def on_start(self) -> None:
        subscribe_custom_data(self, ActivePolymarketMarket, backtest=False)
        subscribe_custom_data(self, LiquidationTick, backtest=False)
        if self.config.use_vwap_input:
            subscribe_custom_data(self, VwapZoneSnapshot, backtest=False)
        if self.config.use_verdict_input:
            subscribe_custom_data(self, LiquidationVerdict, backtest=False)
        for symbol in self.config.instrument_ids:
            self.subscribe_trade_ticks(InstrumentId.from_str(symbol))
        self.msgbus.subscribe(topic="events.order.*", handler=self._on_order_event)
        self.clock.set_timer(
            "strategy_signal_snapshot",
            timedelta(seconds=self._interval_sec),
            callback=self._on_snapshot_timer,
        )
        print(
            "[strategy-signals] StrategySignalBridgeActor → strategy_signal_snapshot "
            f"every {self._interval_sec}s ({len(self._states)} symbols, "
            f"strategy_id={self.config.strategy_id})"
        )

    def on_stop(self) -> None:
        try:
            self.clock.cancel_timer("strategy_signal_snapshot")
        except Exception:  # noqa: BLE001
            pass

    def handle_data(self, data: Data) -> None:
        if isinstance(data, ActivePolymarketMarket):
            self._on_active_market(data)
        elif isinstance(data, LiquidationTick):
            self._on_liquidation_tick(data)
        elif isinstance(data, VwapZoneSnapshot):
            self._on_vwap_snapshot(data)
        elif isinstance(data, LiquidationVerdict):
            self._on_liquidation_verdict(data)

    def on_trade_tick(self, tick: TradeTick) -> None:
        symbol = str(tick.instrument_id)
        st = self._states.get(symbol)
        if st is None:
            return
        st.last_price = float(tick.price)

    def on_quote_tick(self, tick: QuoteTick) -> None:
        iid = tick.instrument_id
        for st in self._states.values():
            if st.yes_instrument_id == str(iid):
                st.yes_ask = _ask_from_quote(tick)
                return
            if st.no_instrument_id == str(iid):
                st.no_ask = _ask_from_quote(tick)
                return

    def _on_active_market(self, data: ActivePolymarketMarket) -> None:
        symbol = self._series_to_symbol.get(str(data.series))
        if symbol is None:
            return
        st = self._states.get(symbol)
        if st is None:
            return

        old_ids = {
            InstrumentId.from_str(iid)
            for iid in (st.yes_instrument_id, st.no_instrument_id)
            if iid
        }
        st.yes_instrument_id = str(data.instrument_id)
        st.no_instrument_id = str(data.no_instrument_id)
        st.active_slug = data.slug or None
        st.yes_ask = None
        st.no_ask = None
        new_ids = {data.instrument_id, data.no_instrument_id}

        for old_iid in old_ids - new_ids:
            if old_iid in self._subscribed_poly_quotes:
                self.unsubscribe_quote_ticks(old_iid)
                self._subscribed_poly_quotes.discard(old_iid)
        for new_iid in new_ids - old_ids:
            if new_iid not in self._subscribed_poly_quotes:
                self.subscribe_quote_ticks(new_iid)
                self._subscribed_poly_quotes.add(new_iid)

    def _on_liquidation_tick(self, data: LiquidationTick) -> None:
        if not self.config.trade_enabled:
            return
        symbol = data.symbol
        st = self._states.get(symbol)
        if st is None:
            return
        threshold = self._thresholds.get(symbol, 0.0)
        if float(data.notional) < threshold:
            return
        if data.side == "SELL":
            st.liq_long_trigger = True
        elif data.side == "BUY":
            st.liq_short_trigger = True

    def _on_vwap_snapshot(self, data: VwapZoneSnapshot) -> None:
        symbol = str(data.instrument_id)
        st = self._states.get(symbol)
        if st is None:
            return
        st.vwap = data.vwap
        st.slope = data.slope
        st.low_zone = data.low_zone
        st.high_zone = data.high_zone
        if st.last_price is None:
            st.last_price = data.close

    def _on_liquidation_verdict(self, data: LiquidationVerdict) -> None:
        symbol = str(data.instrument_id)
        st = self._states.get(symbol)
        if st is None:
            return
        st.last_verdict_winner = data.winner or None

    def _on_order_event(self, event) -> None:
        if not isinstance(event, OrderSubmitted):
            return
        order = self.cache.order(event.client_order_id)
        if order is None:
            return
        parsed = parse_entry_signal_tag(order.tags)
        if not parsed:
            return
        symbol = parsed.get("sym") or parsed.get("symbol")
        if not symbol:
            return
        st = self._states.get(symbol)
        if st is None:
            return
        st.liq_long_trigger = False
        st.liq_short_trigger = False

    def _seconds_until_window_end(self, slug: str | None) -> float | None:
        if not slug:
            return None
        window_start = parse_window_epoch_from_slug(slug)
        if window_start is None:
            return None
        now_sec = int(self.clock.timestamp_ns() // 1_000_000_000)
        return float(window_start + WINDOW_SEC - now_sec)

    def _entry_allowed(self, remaining: float | None) -> bool:
        if remaining is None:
            return True
        if remaining <= 0:
            return False
        return remaining >= float(self.config.min_seconds_to_expiry_for_entry)

    def _decision(self, st: _SymbolState) -> str:
        if st.liq_long_trigger:
            return "LONG"
        if st.liq_short_trigger:
            return "SHORT"
        return "HOLD"

    def _on_snapshot_timer(self, _event) -> None:
        try:
            self._enqueue(_json_safe(self._build_snapshot()))
        except Exception as e:  # noqa: BLE001
            self.log.warning(f"strategy_signal_snapshot build failed: {e!r}")

    def _build_snapshot(self) -> dict:
        symbols_out: dict[str, dict] = {}
        for symbol, st in self._states.items():
            remaining = self._seconds_until_window_end(st.active_slug)
            market_ready = bool(st.yes_instrument_id and st.no_instrument_id)
            symbols_out[symbol] = {
                "last_price": st.last_price,
                "liq_threshold": self._thresholds.get(symbol),
                "liq_long_trigger": st.liq_long_trigger,
                "liq_short_trigger": st.liq_short_trigger,
                "active_slug": st.active_slug,
                "yes_instrument_id": st.yes_instrument_id,
                "no_instrument_id": st.no_instrument_id,
                "yes_ask": st.yes_ask,
                "no_ask": st.no_ask,
                "seconds_to_expiry": remaining,
                "entry_allowed": self._entry_allowed(remaining),
                "market_ready": market_ready,
                "decision": self._decision(st),
                "vwap": st.vwap,
                "slope": st.slope,
                "low_zone": st.low_zone,
                "high_zone": st.high_zone,
                "last_verdict_winner": st.last_verdict_winner,
            }
        return {
            "type": "strategy_signal_snapshot",
            "ts": self.clock.timestamp_ns(),
            "strategy_id": self.config.strategy_id,
            "trade_enabled": self.config.trade_enabled,
            "recovery_exit_pct": self.config.recovery_exit_pct,
            "max_entry_token_price": self.config.max_entry_token_price,
            "min_seconds_to_expiry_for_entry": self.config.min_seconds_to_expiry_for_entry,
            "max_hold_seconds": self.config.max_hold_seconds,
            "symbols": symbols_out,
        }
