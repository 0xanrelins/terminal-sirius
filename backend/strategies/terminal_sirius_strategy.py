"""
TerminalSiriusStrategy — 3-layer signal fusion + Polymarket execution.

Uses ``Strategy.subscribe_signal``, ``clock.set_timer``, ``submit_order`` (market).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from enum import Enum

from nautilus_trader.common.enums import LogColor
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.enums import TimeInForce
from nautilus_trader.model.events import OrderFilled
from nautilus_trader.model.events import PositionClosed
from nautilus_trader.model.events import PositionOpened
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Quantity
from nautilus_trader.trading.strategy import Strategy

from adapters.polymarket.rolling import active_rolling_slugs
from strategies.config import TerminalSiriusStrategyConfig
from strategies.mapping import BINANCE_TO_POLY_SERIES
from strategies.signals import signal_name
from strategies.signals import signals


class Decision(str, Enum):
    OPEN = "OPEN"
    CLOSE = "CLOSE"
    HOLD = "HOLD"


@dataclass
class _LayerState:
    slope: float | None = None
    vwap: float | None = None
    low_zone: float | None = None
    high_zone: float | None = None
    last_price: float | None = None
    liq_long_trigger: bool = False
    liq_short_trigger: bool = False
    vwap_ready: bool = False


class TerminalSiriusStrategy(Strategy):
    def __init__(self, config: TerminalSiriusStrategyConfig) -> None:
        super().__init__(config)
        self._slope_eps = float(config.slope_range_threshold)
        self._states: dict[str, _LayerState] = {
            sym: _LayerState() for sym in config.binance_instruments
        }
        self._poly_iid: dict[str, InstrumentId | None] = {
            sym: None for sym in config.binance_instruments
        }
        self._poly_mid: dict[str, float | None] = {sym: None for sym in config.binance_instruments}
        self._rotation_task: asyncio.Task | None = None

    def on_start(self) -> None:
        for sym in self.config.binance_instruments:
            self.subscribe_signal(signal_name(signals.LIQ_LONG_TRIGGER, sym))
            self.subscribe_signal(signal_name(signals.LIQ_SHORT_TRIGGER, sym))
            self.subscribe_signal(signal_name(signals.VWAP_SNAPSHOT, sym))
            self.subscribe_signal(signal_name(signals.SLOPE_SNAPSHOT, sym))
            self.subscribe_signal(signal_name(signals.ZONE_SNAPSHOT, sym))

        self.clock.set_timer(
            "recalc",
            timedelta(seconds=float(self.config.recalc_interval_sec)),
            callback=self._on_recalc_timer,
        )
        self._rotation_task = asyncio.create_task(self._polymarket_rotation_loop())

    def on_stop(self) -> None:
        self.clock.cancel_timer("recalc")
        if self._rotation_task and not self._rotation_task.done():
            self._rotation_task.cancel()

    def _on_recalc_timer(self, _event) -> None:
        for sym in self.config.binance_instruments:
            decision = self._recalculate(sym)
            if decision != Decision.HOLD:
                self.log.info(f"{sym} → {decision.value}", color=LogColor.CYAN)
            self._maybe_execute(sym, decision)

    def on_signal(self, signal) -> None:
        name: str = signal.name
        value = signal.value
        if ":" not in name:
            return
        base, symbol = name.rsplit(":", 1)
        st = self._states.get(symbol)
        if st is None:
            return

        if base == signals.LIQ_LONG_TRIGGER:
            st.liq_long_trigger = bool(value)
            self._maybe_execute(symbol, self._recalculate(symbol))
        elif base == signals.LIQ_SHORT_TRIGGER:
            st.liq_short_trigger = bool(value)
            self._maybe_execute(symbol, self._recalculate(symbol))
        elif base == signals.VWAP_SNAPSHOT:
            st.vwap = float(value)
            st.vwap_ready = True
        elif base == signals.SLOPE_SNAPSHOT:
            st.slope = float(value)
            st.vwap_ready = True
        elif base == signals.ZONE_SNAPSHOT:
            parts = str(value).split(",")
            if len(parts) == 3:
                st.low_zone, st.high_zone, st.last_price = (
                    float(parts[0]),
                    float(parts[1]),
                    float(parts[2]),
                )
                st.vwap_ready = True

    def on_order_filled(self, event: OrderFilled) -> None:
        self.log.info(
            f"PAPER fill {event.instrument_id} {event.order_side.name} "
            f"qty={event.last_qty} px={event.last_px} "
            f"comm={event.commission}",
            color=LogColor.GREEN,
        )

    def on_position_opened(self, event: PositionOpened) -> None:
        pos = event.position
        self.log.info(
            f"PAPER position OPEN {pos.instrument_id} {pos.side.name} "
            f"qty={pos.quantity} avg={pos.avg_px_open}",
            color=LogColor.YELLOW,
        )

    def on_position_closed(self, event: PositionClosed) -> None:
        pos = event.position
        self.log.info(
            f"PAPER position CLOSE {pos.instrument_id} pnl={pos.realized_pnl}",
            color=LogColor.MAGENTA,
        )

    def on_quote_tick(self, tick: QuoteTick) -> None:
        for sym, iid in self._poly_iid.items():
            if iid is not None and tick.instrument_id == iid:
                bid = float(tick.bid_price.as_double())
                ask = float(tick.ask_price.as_double())
                self._poly_mid[sym] = (bid + ask) / 2 if bid > 0 and ask > 0 else max(bid, ask)

    def _recalculate(self, symbol: str) -> Decision:
        st = self._states[symbol]
        if not st.vwap_ready or st.slope is None or st.low_zone is None:
            return Decision.HOLD

        poly_iid = self._poly_iid.get(symbol)
        if poly_iid is not None:
            pos = self.cache.position_for_instrument(poly_iid)
            if pos is not None and not pos.is_flat():
                return self._exit_decision(symbol, st, pos)

        direction = self._entry_direction(st)
        if direction is None:
            return Decision.HOLD
        return Decision.OPEN

    def _entry_direction(self, st: _LayerState) -> str | None:
        slope = st.slope
        price = st.last_price
        if slope is None or price is None or st.low_zone is None or st.high_zone is None:
            return None
        in_range = abs(slope) <= self._slope_eps
        long_zone = price < st.low_zone
        short_zone = price > st.high_zone

        if (slope > self._slope_eps or in_range) and long_zone and st.liq_long_trigger:
            return "LONG"
        if (slope < -self._slope_eps or in_range) and short_zone and st.liq_short_trigger:
            return "SHORT"
        return None

    def _exit_decision(self, symbol: str, st: _LayerState, pos) -> Decision:
        poly_iid = self._poly_iid[symbol]
        if poly_iid is None:
            return Decision.HOLD
        instrument = self.cache.instrument(poly_iid)
        if instrument is None:
            return Decision.HOLD
        mid = self._poly_mid.get(symbol)
        if mid is None or st.vwap is None:
            return Decision.HOLD
        time_left = instrument.expiration_ns - self.clock.timestamp_ns()
        if time_left <= 0:
            return Decision.CLOSE
        # Target: session VWAP mapped to Polymarket mid scale — use relative progress heuristic
        if st.vwap and abs(mid - 0.5) < 0.02:
            return Decision.CLOSE
        if time_left < 60 * 1_000_000_000 and not st.liq_long_trigger and not st.liq_short_trigger:
            return Decision.CLOSE
        return Decision.HOLD

    def _maybe_execute(self, symbol: str, decision: Decision) -> None:
        poly_iid = self._poly_iid.get(symbol)
        if poly_iid is None:
            return
        instrument = self.cache.instrument(poly_iid)
        if instrument is None:
            return

        if decision == Decision.OPEN:
            st = self._states[symbol]
            direction = self._entry_direction(st)
            if direction is None:
                return
            side = OrderSide.BUY if direction == "LONG" else OrderSide.SELL
            qty = Quantity.from_str(str(self.config.trade_size))
            order = self.order_factory.market(
                instrument_id=poly_iid,
                order_side=side,
                quantity=qty,
                time_in_force=TimeInForce.IOC,
            )
            self.log.info(
                f"PAPER submit {direction} {poly_iid} qty={qty}",
                color=LogColor.CYAN,
            )
            self.submit_order(order)
            st.liq_long_trigger = False
            st.liq_short_trigger = False
        elif decision == Decision.CLOSE:
            self.close_all_positions(poly_iid)

    async def _polymarket_rotation_loop(self) -> None:
        await asyncio.sleep(2.0)
        while True:
            try:
                for sym in self.config.binance_instruments:
                    series = BINANCE_TO_POLY_SERIES.get(sym)
                    if series is None:
                        continue
                    await self._ensure_poly_instrument(sym, series)
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.log.warning(f"Polymarket rotation: {e!r}")
            await asyncio.sleep(20.0)

    async def _ensure_poly_instrument(self, binance_sym: str, series: str) -> None:
        from nautilus_trader.adapters.polymarket.loaders import PolymarketDataLoader

        slug, _ = active_rolling_slugs(series)
        try:
            loader = await PolymarketDataLoader.from_market_slug(slug, token_index=0)
            instrument = loader.instrument
        except Exception as e:
            self.log.warning(f"Strategy skip slug={slug!r}: {e!r}")
            return
        iid = instrument.id
        prev = self._poly_iid.get(binance_sym)
        if prev is not None and prev != iid:
            try:
                self.unsubscribe_quote_ticks(prev)
            except Exception:
                pass
        if self.cache.instrument(iid) is None:
            self.cache.add_instrument(instrument)
        self.request_instrument(iid)
        self.subscribe_quote_ticks(iid)
        self._poly_iid[binance_sym] = iid
