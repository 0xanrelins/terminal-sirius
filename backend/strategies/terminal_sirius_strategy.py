"""
TerminalSiriusStrategy — 3-layer signal fusion + Polymarket execution.

Uses ``Strategy.subscribe_data`` (typed custom data), ``clock.set_timer``,
``submit_order`` (market).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from enum import Enum

from nautilus_trader.common.enums import LogColor
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.enums import TimeInForce
from nautilus_trader.model.events import OrderFilled
from nautilus_trader.model.events import PositionClosed
from nautilus_trader.model.events import PositionOpened
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.trading.strategy import Strategy

from adapters.polymarket.messages import ActivePolymarketMarket
from adapters.polymarket.rolling import WINDOW_SEC
from adapters.polymarket.rolling import parse_window_epoch_from_slug
from strategies.config import TerminalSiriusStrategyConfig
from strategies.mapping import BINANCE_TO_POLY_SERIES
from strategies.liquidation_verdict_logic import verdict_passes_gates
from strategies.liquidation_verdict_logic import CompletedVerdict
from strategies.messages import LiquidationTrigger
from strategies.messages import LiquidationVerdict
from strategies.messages import VwapZoneSnapshot
from strategies.signal_state import SignalInputs
from strategies.signal_state import entry_direction
from strategies.subscriptions import subscribe_custom_data
from strategy_signal_tags import build_entry_signal_tags


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
        self._recovery_exit_pct = float(config.recovery_exit_pct) / 100.0
        self._max_entry_token_price = float(config.max_entry_token_price)
        self._min_entry_seconds = int(config.min_seconds_to_expiry_for_entry)
        self._states: dict[str, _LayerState] = {
            sym: _LayerState() for sym in config.binance_instruments
        }
        self._poly_iid: dict[str, InstrumentId | None] = {  # YES/Up token
            sym: None for sym in config.binance_instruments
        }
        self._poly_no_iid: dict[str, InstrumentId | None] = {  # NO/Down token
            sym: None for sym in config.binance_instruments
        }
        # series → binance instrument, to route ActivePolymarketMarket announcements
        self._series_to_binance: dict[str, str] = {
            BINANCE_TO_POLY_SERIES[sym]: sym
            for sym in config.binance_instruments
            if sym in BINANCE_TO_POLY_SERIES
        }
        self._entry_anchor_price: dict[str, float | None] = {
            sym: None for sym in config.binance_instruments
        }
        self._entry_side: dict[str, str | None] = {
            sym: None for sym in config.binance_instruments
        }

    def on_start(self) -> None:
        bt = self.config.backtest_mode
        subscribe_custom_data(self, VwapZoneSnapshot, backtest=bt)
        if self.config.use_rolling_liq_triggers:
            subscribe_custom_data(self, LiquidationTrigger, backtest=bt)
        if self.config.use_verdict_triggers:
            subscribe_custom_data(self, LiquidationVerdict, backtest=bt)
        subscribe_custom_data(self, ActivePolymarketMarket, backtest=bt)

        self.clock.set_timer(
            "recalc",
            timedelta(seconds=float(self.config.recalc_interval_sec)),
            callback=self._on_recalc_timer,
        )

    def on_stop(self) -> None:
        self.clock.cancel_timer("recalc")

    def _on_recalc_timer(self, _event) -> None:
        for sym in self.config.binance_instruments:
            decision = self._recalculate(sym)
            if decision != Decision.HOLD:
                self.log.info(f"{sym} → {decision.value}", color=LogColor.CYAN)
            self._maybe_execute(sym, decision)

    def on_data(self, data) -> None:
        if isinstance(data, VwapZoneSnapshot):
            st = self._states.get(str(data.instrument_id))
            if st is None:
                return
            st.vwap = data.vwap
            st.slope = data.slope
            st.low_zone = data.low_zone
            st.high_zone = data.high_zone
            st.last_price = data.close
            st.vwap_ready = True
        elif isinstance(data, LiquidationTrigger):
            if not self.config.use_rolling_liq_triggers:
                return
            symbol = str(data.instrument_id)
            st = self._states.get(symbol)
            if st is None:
                return
            if data.long_triggered:
                st.liq_long_trigger = True
            if data.short_triggered:
                st.liq_short_trigger = True
            self._maybe_execute(symbol, self._recalculate(symbol))
        elif isinstance(data, LiquidationVerdict):
            if not self.config.use_verdict_triggers:
                return
            self._on_liquidation_verdict(data)
        elif isinstance(data, ActivePolymarketMarket):
            self._on_active_market(data)

    def on_instrument(self, instrument: Instrument) -> None:
        """Retry pending entries after bridge rotation loads a new ``BinaryOption``."""
        if instrument.id not in self._tracked_poly_iids():
            return
        self._retry_execute_for(instrument.id)

    def on_quote_tick(self, tick: QuoteTick) -> None:
        """Retry pending entries when quote precision catches up after tick-size change."""
        if tick.instrument_id not in self._tracked_poly_iids():
            return
        self._retry_execute_for(tick.instrument_id)

    def _tracked_poly_iids(self) -> set[InstrumentId]:
        out: set[InstrumentId] = set()
        for iid in (*self._poly_iid.values(), *self._poly_no_iid.values()):
            if iid is not None:
                out.add(iid)
        return out

    def _quote_precision_ok(self, instrument: Instrument, tick: QuoteTick) -> bool:
        """Quote prices must match ``instrument.price_precision`` (Polymarket tick epochs)."""
        expected = instrument.price_precision
        has_price = False
        for px in (tick.bid_price, tick.ask_price):
            if px is None:
                continue
            has_price = True
            if px.precision != expected:
                return False
        return has_price

    def _retry_execute_for(self, instrument_id: InstrumentId) -> None:
        for sym in self.config.binance_instruments:
            if instrument_id not in (
                self._poly_iid.get(sym),
                self._poly_no_iid.get(sym),
            ):
                continue
            decision = self._recalculate(sym)
            if decision != Decision.HOLD:
                self._maybe_execute(sym, decision)

    def _execution_ready(self, instrument_id: InstrumentId) -> tuple[bool, str]:
        instrument = self.cache.instrument(instrument_id)
        if instrument is None:
            return False, "awaiting instrument in cache"
        tick = self.cache.quote_tick(instrument_id)
        if tick is None:
            return False, "no usable quote"
        if not self._quote_precision_ok(instrument, tick):
            return False, "quote precision stale (tick size change)"
        return True, ""

    def _quote_mid_price(self, instrument_id: InstrumentId) -> float | None:
        tick = self.cache.quote_tick(instrument_id)
        if tick is None:
            return None
        bid = float(tick.bid_price) if tick.bid_price is not None else None
        ask = float(tick.ask_price) if tick.ask_price is not None else None
        if bid is not None and ask is not None and bid > 0 and ask > 0:
            return (bid + ask) / 2.0
        if bid is not None and bid > 0:
            return bid
        if ask is not None and ask > 0:
            return ask
        return None

    def _on_active_market(self, data: ActivePolymarketMarket) -> None:
        """Adopt active YES/NO instruments from ``PolymarketQuoteBridgeActor`` rotation."""
        binance = self._series_to_binance.get(data.series)
        if binance is None or binance not in self._poly_iid:
            return
        yes_iid = data.instrument_id
        no_iid = data.no_instrument_id
        if self._poly_iid.get(binance) == yes_iid and self._poly_no_iid.get(binance) == no_iid:
            return
        for prev in (self._poly_iid.get(binance), self._poly_no_iid.get(binance)):
            if prev is not None and prev not in (yes_iid, no_iid):
                try:
                    self.unsubscribe_quote_ticks(prev)
                except Exception:  # noqa: BLE001 — best-effort cleanup on rotation
                    pass
        self._poly_iid[binance] = yes_iid
        self._poly_no_iid[binance] = no_iid
        self.subscribe_quote_ticks(yes_iid)
        self.subscribe_quote_ticks(no_iid)
        self.log.info(
            f"Polymarket market for {binance}: YES={yes_iid} NO={no_iid} (series={data.series})",
            color=LogColor.BLUE,
        )

    def on_order_filled(self, event: OrderFilled) -> None:
        self.log.info(
            f"PAPER fill {event.instrument_id} {event.order_side.name} "
            f"qty={event.last_qty} px={event.last_px} "
            f"comm={event.commission}",
            color=LogColor.GREEN,
        )

    def on_position_opened(self, event: PositionOpened) -> None:
        # PositionOpened is a PositionEvent — fields live on the event, not event.position.
        symbol = self._symbol_for_instrument_id(event.instrument_id)
        if symbol is not None:
            st = self._states.get(symbol)
            if st is not None and st.last_price is not None:
                self._entry_anchor_price[symbol] = float(st.last_price)
            self._entry_side[symbol] = self._direction_for_instrument(symbol, event.instrument_id)
        self.log.info(
            f"PAPER position OPEN {event.instrument_id} {event.side} "
            f"qty={event.quantity} avg={event.avg_px_open}",
            color=LogColor.YELLOW,
        )

    def on_position_closed(self, event: PositionClosed) -> None:
        symbol = self._symbol_for_instrument_id(event.instrument_id)
        if symbol is not None:
            self._entry_anchor_price[symbol] = None
            self._entry_side[symbol] = None
        self.log.info(
            f"PAPER position CLOSE {event.instrument_id} pnl={event.realized_pnl}",
            color=LogColor.MAGENTA,
        )

    def _recalculate(self, symbol: str) -> Decision:
        st = self._states[symbol]
        if not st.vwap_ready or st.slope is None or st.low_zone is None:
            return Decision.HOLD

        # Open position can be on the YES (long) or NO (short) token.
        for held_iid in (self._poly_iid.get(symbol), self._poly_no_iid.get(symbol)):
            if held_iid is not None and self.cache.positions_open(instrument_id=held_iid):
                return self._exit_decision(symbol, st, held_iid)

        direction = self._entry_direction(st)
        if direction is None:
            return Decision.HOLD
        return Decision.OPEN

    def _on_liquidation_verdict(self, data: LiquidationVerdict) -> None:
        symbol = str(data.instrument_id)
        st = self._states.get(symbol)
        if st is None:
            return
        completed = CompletedVerdict(
            event_id=data.event_id,
            symbol=symbol,
            liq_side=data.liq_side,  # type: ignore[arg-type]
            notional=float(data.notional),
            event_price=float(data.event_price),
            event_ts_ns=int(data.ts_event),
            winner=data.winner,  # type: ignore[arg-type]
            liq_move_pct=float(data.liq_move_pct),
            recovery_move_pct=float(data.recovery_move_pct),
            dominance_ratio=float(data.dominance_ratio),
            time_to_dominance_sec=float(data.time_to_dominance_sec),
            area_bias=float(data.area_bias),
            status=data.status,  # type: ignore[arg-type]
        )
        if not verdict_passes_gates(
            completed,
            min_recovery_move_pct=float(self.config.verdict_min_recovery_move_pct),
            max_time_to_completion_sec=float(self.config.verdict_max_time_sec),
            min_area_bias=float(self.config.verdict_min_area_bias),
            required_winner="recovery",
        ):
            return
        if data.liq_side == "LONG":
            st.liq_long_trigger = True
        elif data.liq_side == "SHORT":
            st.liq_short_trigger = True
        self._maybe_execute(symbol, self._recalculate(symbol))

    def _signal_inputs(self, st: _LayerState) -> SignalInputs:
        return SignalInputs(
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

    def _entry_direction(self, st: _LayerState) -> str | None:
        return entry_direction(self._signal_inputs(st))

    def _exit_decision(
        self,
        symbol: str,
        st: _LayerState,
        held_iid: InstrumentId,
    ) -> Decision:
        """
        Recovery exit before binary settlement.

        Entry anchor is the Binance underlying price at ``PositionOpened``. For LONG
        direction, close when price recovers ``+recovery_exit_pct`` from anchor.
        For SHORT direction, close when price reaches ``-recovery_exit_pct``.
        """
        anchor = self._entry_anchor_price.get(symbol)
        direction = self._entry_side.get(symbol)
        price = st.last_price
        if anchor is not None and price is not None and direction in ("LONG", "SHORT"):
            if direction == "LONG":
                target = anchor * (1.0 + self._recovery_exit_pct)
                if price >= target:
                    self.log.info(
                        f"{symbol} recovery exit LONG hit: px={price:.6f} target={target:.6f}",
                        color=LogColor.CYAN,
                    )
                    return Decision.CLOSE
            else:
                target = anchor * (1.0 - self._recovery_exit_pct)
                if price <= target:
                    self.log.info(
                        f"{symbol} recovery exit SHORT hit: px={price:.6f} target={target:.6f}",
                        color=LogColor.CYAN,
                    )
                    return Decision.CLOSE

        instrument = self.cache.instrument(held_iid)
        if instrument is None:
            return Decision.HOLD
        time_left_ns = instrument.expiration_ns - self.clock.timestamp_ns()
        if time_left_ns <= 0 and self.cache.positions_open(instrument_id=held_iid):
            self.log.info(
                f"{symbol} past expiry — waiting for venue resolution on {held_iid} "
                f"(no strategy-initiated close)",
                color=LogColor.MAGENTA,
            )
        return Decision.HOLD

    def _instrument_slug(self, instrument) -> str:
        info = getattr(instrument, "info", {}) or {}
        if isinstance(info, dict):
            for key in ("market_slug", "slug"):
                raw = info.get(key)
                if raw:
                    return str(raw)
        return ""

    def _entry_allowed(self, instrument) -> bool:
        """Block entries when market is too close to 15m expiry."""
        now_ns = self.clock.timestamp_ns()
        if now_ns >= instrument.expiration_ns:
            return False
        window_start = parse_window_epoch_from_slug(self._instrument_slug(instrument))
        if window_start is None:
            return True
        now_sec = int(now_ns // 1_000_000_000)
        window_end = window_start + WINDOW_SEC
        if now_sec >= window_end:
            return False
        return (window_end - now_sec) >= self._min_entry_seconds

    def _symbol_for_instrument_id(self, instrument_id: InstrumentId) -> str | None:
        for sym in self.config.binance_instruments:
            if instrument_id in (self._poly_iid.get(sym), self._poly_no_iid.get(sym)):
                return sym
        return None

    def _direction_for_instrument(self, symbol: str, instrument_id: InstrumentId) -> str | None:
        if instrument_id == self._poly_iid.get(symbol):
            return "LONG"
        if instrument_id == self._poly_no_iid.get(symbol):
            return "SHORT"
        return None

    def _submit_market_exit(self, symbol: str, instrument_id: InstrumentId) -> bool:
        positions = list(self.cache.positions_open(instrument_id=instrument_id))
        if not positions:
            return False
        position = positions[0]
        instrument = self.cache.instrument(instrument_id)
        if instrument is None:
            return False
        ready, reason = self._execution_ready(instrument_id)
        if not ready:
            self.log.info(
                f"skip CLOSE {symbol}: {reason} for {instrument_id}",
                color=LogColor.YELLOW,
            )
            return False
        order = self.order_factory.market(
            instrument_id=instrument_id,
            order_side=OrderSide.SELL,
            quantity=position.quantity,
            time_in_force=TimeInForce.IOC,
        )
        self.log.info(
            f"PAPER submit CLOSE (SELL {instrument_id}) qty={position.quantity}",
            color=LogColor.CYAN,
        )
        self.submit_order(order)
        return True

    def _maybe_execute(self, symbol: str, decision: Decision) -> None:
        if decision == Decision.OPEN:
            st = self._states[symbol]
            direction = self._entry_direction(st)
            if direction is None:
                return
            # Polymarket: long = BUY the YES/Up token, short = BUY the NO/Down token
            # (no short-selling in a CASH account — you buy the outcome you want).
            target_iid = (
                self._poly_iid.get(symbol)
                if direction == "LONG"
                else self._poly_no_iid.get(symbol)
            )
            instrument = self.cache.instrument(target_iid) if target_iid is not None else None
            if instrument is None:
                return
            if not self._entry_allowed(instrument):
                self.log.info(
                    f"skip OPEN {symbol}: Polymarket window ended or expired for {target_iid}",
                    color=LogColor.YELLOW,
                )
                return
            ready, reason = self._execution_ready(target_iid)
            if not ready:
                self.log.info(
                    f"skip OPEN {symbol}: {reason} for {target_iid}",
                    color=LogColor.YELLOW,
                )
                return
            mid = self._quote_mid_price(target_iid)
            if mid is None:
                self.log.info(
                    f"skip OPEN {symbol}: no mid price for {target_iid}",
                    color=LogColor.YELLOW,
                )
                return
            if mid > self._max_entry_token_price:
                self.log.info(
                    f"skip OPEN {symbol}: token price {mid:.4f} > {self._max_entry_token_price:.4f}",
                    color=LogColor.YELLOW,
                )
                return
            # Quantity must carry the instrument's size precision (Polymarket=6); a bare
            # Quantity.from_str("10") has precision 0 and the exec engine rejects it.
            qty = instrument.make_qty(self.config.trade_size)
            signal_tags = build_entry_signal_tags(
                symbol=symbol,
                direction=direction,
                vwap=st.vwap,
                slope=st.slope,
                low_zone=st.low_zone,
                high_zone=st.high_zone,
                last_price=st.last_price,
                liq_long=st.liq_long_trigger,
                liq_short=st.liq_short_trigger,
            )
            order = self.order_factory.market(
                instrument_id=target_iid,
                order_side=OrderSide.BUY,
                quantity=qty,
                time_in_force=TimeInForce.IOC,
                tags=signal_tags,
            )
            self.log.info(
                f"PAPER submit {direction} (BUY {target_iid}) qty={qty}",
                color=LogColor.CYAN,
            )
            self.submit_order(order)
            if st.last_price is not None:
                self._entry_anchor_price[symbol] = float(st.last_price)
            self._entry_side[symbol] = direction
            st.liq_long_trigger = False
            st.liq_short_trigger = False
        elif decision == Decision.CLOSE:
            for iid in (self._poly_iid.get(symbol), self._poly_no_iid.get(symbol)):
                if iid is None:
                    continue
                if self._submit_market_exit(symbol, iid):
                    break
