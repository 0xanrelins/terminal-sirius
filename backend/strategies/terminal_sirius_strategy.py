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
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.trading.strategy import Strategy

from adapters.polymarket.messages import ActivePolymarketMarket
from adapters.polymarket.rolling import WINDOW_SEC
from adapters.polymarket.rolling import parse_window_epoch_from_slug
from strategies.config import TerminalSiriusStrategyConfig
from strategies.mapping import BINANCE_TO_POLY_SERIES
from strategies.messages import LiquidationTrigger
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

    def on_start(self) -> None:
        bt = self.config.backtest_mode
        subscribe_custom_data(self, VwapZoneSnapshot, backtest=bt)
        subscribe_custom_data(self, LiquidationTrigger, backtest=bt)
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
            symbol = str(data.instrument_id)
            st = self._states.get(symbol)
            if st is None:
                return
            if data.long_triggered:
                st.liq_long_trigger = True
            if data.short_triggered:
                st.liq_short_trigger = True
            self._maybe_execute(symbol, self._recalculate(symbol))
        elif isinstance(data, ActivePolymarketMarket):
            self._on_active_market(data)

    def _on_active_market(self, data: ActivePolymarketMarket) -> None:
        """Adopt the active Polymarket YES + NO instruments announced by the quote bridge."""
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
        self.log.info(
            f"PAPER position OPEN {event.instrument_id} {event.side} "
            f"qty={event.quantity} avg={event.avg_px_open}",
            color=LogColor.YELLOW,
        )

    def on_position_closed(self, event: PositionClosed) -> None:
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
        _st: _LayerState,
        held_iid: InstrumentId,
    ) -> Decision:
        """
        Hold until Polymarket resolution — no discretionary exit.

        Nautilus ``BinaryOption`` matching enters pending resolution at
        ``instrument.expiration_ns`` and settles open positions when an
        ``InstrumentClose`` (or sandbox settlement price) is applied.
        The strategy does not call ``close_all_positions`` mid-window.
        """
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
        """Block entries on expired 15m windows (before sandbox grace / bridge rotation)."""
        now_ns = self.clock.timestamp_ns()
        if now_ns >= instrument.expiration_ns:
            return False
        window_start = parse_window_epoch_from_slug(self._instrument_slug(instrument))
        if window_start is None:
            return True
        now_sec = int(now_ns // 1_000_000_000)
        return now_sec < window_start + WINDOW_SEC

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
            st.liq_long_trigger = False
            st.liq_short_trigger = False
        elif decision == Decision.CLOSE:
            for iid in (self._poly_iid.get(symbol), self._poly_no_iid.get(symbol)):
                if iid is not None:
                    self.close_all_positions(iid)
