"""
FreshPaperStrategy — clean-slate active paper strategy skeleton.

This strategy intentionally does not copy Terminal Sirius entry/exit logic. It
subscribes to optional native actor data and tracks the Polymarket instruments
needed for future paper rules; order submission stays disabled until explicit
rules are added and ``PAPER_STRATEGY_TRADE_ENABLED`` is enabled.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from nautilus_trader.common.enums import LogColor
from nautilus_trader.core.data import Data
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.enums import TimeInForce
from nautilus_trader.model.events import OrderFilled
from nautilus_trader.model.events import PositionClosed
from nautilus_trader.model.events import PositionOpened
from nautilus_trader.model.identifiers import PositionId
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.trading.strategy import Strategy

from adapters.polymarket.messages import ActivePolymarketMarket
from adapters.polymarket.rolling import WINDOW_SEC
from adapters.polymarket.rolling import parse_window_epoch_from_slug
from recorders.data_types import LiquidationTick
from strategies.config import FreshPaperStrategyConfig
from strategies.mapping import BINANCE_TO_POLY_SERIES
from strategies.messages import LiquidationVerdict
from strategies.messages import VwapZoneSnapshot
from strategies.subscriptions import subscribe_custom_data
from strategy_signal_tags import build_paper_entry_signal_tags
from strategy_signal_tags import build_paper_exit_reason_tags
from strategy_signal_tags import liquidation_exit_reason
from strategy_signal_tags import recovery_exit_reason
from strategy_signal_tags import time_stop_reason


@dataclass
class _FreshSymbolState:
    yes_instrument_id: InstrumentId | None = None
    no_instrument_id: InstrumentId | None = None
    vwap: float | None = None
    slope: float | None = None
    low_zone: float | None = None
    high_zone: float | None = None
    last_price: float | None = None
    liq_long_trigger: bool = False
    liq_short_trigger: bool = False
    last_verdict_id: str | None = None
    last_verdict_winner: str | None = None
    active_slug: str | None = None


@dataclass
class _RecoveryTrade:
    symbol: str
    direction: str
    instrument_id: InstrumentId
    anchor_price: float
    entry_order_id: str
    position_id: PositionId | None = None
    entry_ts_ns: int | None = None
    active: bool = False
    exit_submitted: bool = False


class FreshPaperStrategy(Strategy):
    """Nautilus-native empty strategy shell for the next paper strategy."""

    def __init__(self, config: FreshPaperStrategyConfig) -> None:
        super().__init__(config)
        self._states: dict[str, _FreshSymbolState] = {
            sym: _FreshSymbolState() for sym in config.binance_instruments
        }
        self._series_to_symbol: dict[str, str] = {
            BINANCE_TO_POLY_SERIES[sym]: sym
            for sym in config.binance_instruments
            if sym in BINANCE_TO_POLY_SERIES
        }
        self._recoveries: list[_RecoveryTrade] = []
        self._recoveries_by_entry_order: dict[str, _RecoveryTrade] = {}
        self._recovery_factor = float(config.recovery_exit_pct) / 100.0
        self._recovery_exit_reason = recovery_exit_reason(float(config.recovery_exit_pct))
        self._liquidation_exit_reason = liquidation_exit_reason(float(config.recovery_exit_pct))
        self._time_stop_reason = time_stop_reason(int(config.max_hold_seconds))
        self._min_entry_seconds = int(config.min_seconds_to_expiry_for_entry)
        self._max_hold_seconds = int(config.max_hold_seconds)

    def on_start(self) -> None:
        bt = self.config.backtest_mode
        subscribe_custom_data(self, ActivePolymarketMarket, backtest=bt)
        for symbol in self.config.binance_instruments:
            self.subscribe_trade_ticks(InstrumentId.from_str(symbol))
        if self.config.use_vwap_input:
            subscribe_custom_data(self, VwapZoneSnapshot, backtest=bt)
        if self.config.use_liquidation_input:
            subscribe_custom_data(self, LiquidationTick, backtest=bt)
        if self.config.use_verdict_input:
            subscribe_custom_data(self, LiquidationVerdict, backtest=bt)

        self.clock.set_timer(
            "fresh_paper_recalc",
            timedelta(seconds=float(self.config.recalc_interval_sec)),
            callback=self._on_recalc_timer,
        )
        self.log.info(
            f"{self.config.strategy_id} started "
            f"(trade_enabled={self.config.trade_enabled})",
            color=LogColor.CYAN,
        )

    def on_stop(self) -> None:
        self.clock.cancel_timer("fresh_paper_recalc")

    def on_data(self, data: Data) -> None:
        if isinstance(data, ActivePolymarketMarket):
            self._on_active_market(data)
        elif isinstance(data, VwapZoneSnapshot):
            self._on_vwap_snapshot(data)
        elif isinstance(data, LiquidationTick):
            self._on_liquidation_tick(data)
        elif isinstance(data, LiquidationVerdict):
            self._on_liquidation_verdict(data)

    def on_instrument(self, instrument: Instrument) -> None:
        if instrument.id in self._tracked_poly_iids():
            self._evaluate_for_instrument(instrument.id)

    def on_quote_tick(self, tick: QuoteTick) -> None:
        if tick.instrument_id in self._tracked_poly_iids():
            self._evaluate_for_instrument(tick.instrument_id)

    def on_trade_tick(self, tick: TradeTick) -> None:
        symbol = str(tick.instrument_id)
        state = self._states.get(symbol)
        if state is None:
            return
        state.last_price = float(tick.price)
        self._check_recovery_exits(symbol)

    def on_order_filled(self, event: OrderFilled) -> None:
        entry = self._recoveries_by_entry_order.get(str(event.client_order_id))
        if entry is not None and event.order_side == OrderSide.BUY:
            entry.active = True
            entry.position_id = event.position_id
            if entry.entry_ts_ns is None:
                entry.entry_ts_ns = self.clock.timestamp_ns()
        self.log.info(
            f"{self.config.strategy_id} fill {event.instrument_id} "
            f"{event.order_side.name} qty={event.last_qty} px={event.last_px}",
            color=LogColor.GREEN,
        )

    def on_position_opened(self, event: PositionOpened) -> None:
        for recovery in self._recoveries:
            if recovery.entry_order_id != str(event.opening_order_id):
                continue
            recovery.position_id = event.position_id
            recovery.active = True
            if recovery.entry_ts_ns is None:
                recovery.entry_ts_ns = self.clock.timestamp_ns()
            return

    def on_position_closed(self, event: PositionClosed) -> None:
        closed_id = event.position_id
        self._recoveries = [
            recovery
            for recovery in self._recoveries
            if recovery.position_id != closed_id
        ]
        self._recoveries_by_entry_order = {
            order_id: recovery
            for order_id, recovery in self._recoveries_by_entry_order.items()
            if recovery in self._recoveries
        }

    def _on_recalc_timer(self, _event) -> None:
        for symbol in self.config.binance_instruments:
            self._evaluate_symbol(symbol)

    def _on_active_market(self, data: ActivePolymarketMarket) -> None:
        symbol = self._series_to_symbol.get(str(data.series))
        if symbol is None:
            return
        state = self._states.get(symbol)
        if state is None:
            return

        old_ids = {state.yes_instrument_id, state.no_instrument_id} - {None}
        state.yes_instrument_id = data.instrument_id
        state.no_instrument_id = data.no_instrument_id
        state.active_slug = data.slug or None
        new_ids = {data.instrument_id, data.no_instrument_id}

        if not self.config.backtest_mode:
            for old_iid in old_ids - new_ids:
                self.unsubscribe_quote_ticks(old_iid)
            for new_iid in new_ids - old_ids:
                self.subscribe_quote_ticks(new_iid)

        self.log.info(
            f"{self.config.strategy_id} active market {symbol}: "
            f"YES={data.instrument_id} NO={data.no_instrument_id}",
            color=LogColor.BLUE,
        )
        self._evaluate_symbol(symbol)

    def _on_vwap_snapshot(self, data: VwapZoneSnapshot) -> None:
        state = self._states.get(str(data.instrument_id))
        if state is None:
            return
        state.vwap = data.vwap
        state.slope = data.slope
        state.low_zone = data.low_zone
        state.high_zone = data.high_zone
        state.last_price = data.close

    def _on_liquidation_tick(self, data: LiquidationTick) -> None:
        if not self.config.trade_enabled:
            return
        symbol = data.symbol
        state = self._states.get(symbol)
        if state is None:
            return
        threshold = self._threshold_for_symbol(symbol)
        if float(data.notional) < threshold:
            return

        if data.side == "SELL":
            state.liq_long_trigger = True
            self._submit_recovery_entry(
                symbol=symbol,
                direction="LONG",
                anchor_price=float(data.price),
                notional=float(data.notional),
                threshold=threshold,
                liquidation_side=data.side,
            )
        elif data.side == "BUY":
            state.liq_short_trigger = True
            self._submit_recovery_entry(
                symbol=symbol,
                direction="SHORT",
                anchor_price=float(data.price),
                notional=float(data.notional),
                threshold=threshold,
                liquidation_side=data.side,
            )

    def _on_liquidation_verdict(self, data: LiquidationVerdict) -> None:
        state = self._states.get(str(data.instrument_id))
        if state is None:
            return
        state.last_verdict_id = data.event_id
        state.last_verdict_winner = data.winner

    def _evaluate_for_instrument(self, instrument_id: InstrumentId) -> None:
        for symbol, state in self._states.items():
            if instrument_id in (state.yes_instrument_id, state.no_instrument_id):
                self._evaluate_symbol(symbol)
                return

    def _evaluate_symbol(self, symbol: str) -> None:
        if not self.config.trade_enabled:
            return
        self._check_recovery_exits(symbol)

    def _threshold_for_symbol(self, symbol: str) -> float:
        if "BTC" in symbol:
            return float(self.config.liq_threshold_btc)
        if "ETH" in symbol:
            return float(self.config.liq_threshold_eth)
        if "SOL" in symbol:
            return float(self.config.liq_threshold_sol)
        if "XRP" in symbol:
            return float(self.config.liq_threshold_xrp)
        if "DOGE" in symbol:
            return float(self.config.liq_threshold_doge)
        return float(self.config.liq_threshold_doge)

    def _target_instrument_id(self, symbol: str, direction: str) -> InstrumentId | None:
        state = self._states.get(symbol)
        if state is None:
            return None
        if direction == "LONG":
            return state.yes_instrument_id
        if direction == "SHORT":
            return state.no_instrument_id
        return None

    def _quote_ask_price(self, instrument_id: InstrumentId) -> float | None:
        tick = self.cache.quote_tick(instrument_id)
        if tick is None or tick.ask_price is None:
            return None
        try:
            return float(tick.ask_price)
        except (TypeError, ValueError):
            return None

    def _submit_recovery_entry(
        self,
        *,
        symbol: str,
        direction: str,
        anchor_price: float,
        notional: float,
        threshold: float,
        liquidation_side: str,
    ) -> bool:
        target_iid = self._target_instrument_id(symbol, direction)
        if target_iid is None:
            self.log.info(
                f"skip OPEN {symbol}: no active Polymarket {direction} instrument",
                color=LogColor.YELLOW,
            )
            return False

        instrument = self.cache.instrument(target_iid)
        if instrument is None:
            return False
        if not self._entry_allowed(symbol):
            self.log.info(
                f"skip OPEN {symbol}: Polymarket window ended or <{self._min_entry_seconds}s to close",
                color=LogColor.YELLOW,
            )
            return False

        ready, reason = self._execution_ready(target_iid)
        if not ready:
            self.log.info(f"skip OPEN {symbol}: {reason} for {target_iid}", color=LogColor.YELLOW)
            return False

        max_price = float(self.config.max_entry_token_price)
        ask = self._quote_ask_price(target_iid)
        if ask is None:
            self.log.info(f"skip OPEN {symbol}: no ask price for {target_iid}", color=LogColor.YELLOW)
            return False
        if ask > max_price:
            self.log.info(
                f"skip OPEN {symbol}: ask {ask:.4f} > {max_price:.4f}",
                color=LogColor.YELLOW,
            )
            return False

        qty = instrument.make_qty(self.config.trade_size)
        order = self.order_factory.limit(
            instrument_id=target_iid,
            order_side=OrderSide.BUY,
            quantity=qty,
            price=instrument.make_price(max_price),
            time_in_force=TimeInForce.IOC,
            tags=self._entry_tags(
                symbol=symbol,
                direction=direction,
                reason="liq_recovery_entry",
                context={
                    "anchor": anchor_price,
                    "liq_side": liquidation_side,
                    "notional": notional,
                    "threshold": threshold,
                    "max_px": max_price,
                },
            ),
        )
        entry_order_id = str(order.client_order_id)
        recovery = _RecoveryTrade(
            symbol=symbol,
            direction=direction,
            instrument_id=target_iid,
            anchor_price=anchor_price,
            entry_order_id=entry_order_id,
        )
        self._recoveries.append(recovery)
        self._recoveries_by_entry_order[entry_order_id] = recovery
        self.submit_order(order)
        self.log.info(
            f"{self.config.strategy_id} OPEN {direction} {symbol} "
            f"qty={qty} limit={max_price:.4f} anchor={anchor_price:.4f}",
            color=LogColor.CYAN,
        )
        return True

    def _check_recovery_exits(self, symbol: str) -> None:
        state = self._states.get(symbol)
        if state is None or state.last_price is None:
            return
        current = float(state.last_price)
        exiting_position_ids = {
            recovery.position_id
            for recovery in self._recoveries
            if recovery.exit_submitted and recovery.position_id is not None
        }
        for recovery in list(self._recoveries):
            if recovery.symbol != symbol or not recovery.active or recovery.exit_submitted:
                continue
            if recovery.position_id is None:
                continue
            if recovery.position_id in exiting_position_ids:
                continue
            exit_reason = self._exit_reason_for_price(recovery, current)
            if exit_reason is None and self._time_stop_due(recovery):
                exit_reason = self._time_stop_reason
            if exit_reason is None:
                continue
            if self._submit_market_exit(recovery, current_price=current, reason=exit_reason):
                recovery.exit_submitted = True
                exiting_position_ids.add(recovery.position_id)

    def _exit_reason_for_price(self, recovery: _RecoveryTrade, current_price: float) -> str | None:
        if self._recovery_exit_hit(recovery, current_price):
            return self._recovery_exit_reason
        if self._liquidation_exit_hit(recovery, current_price):
            return self._liquidation_exit_reason
        return None

    def _recovery_exit_hit(self, recovery: _RecoveryTrade, current_price: float) -> bool:
        if recovery.direction == "LONG":
            return current_price >= recovery.anchor_price * (1.0 + self._recovery_factor)
        if recovery.direction == "SHORT":
            return current_price <= recovery.anchor_price * (1.0 - self._recovery_factor)
        return False

    def _liquidation_exit_hit(self, recovery: _RecoveryTrade, current_price: float) -> bool:
        if recovery.direction == "LONG":
            return current_price <= recovery.anchor_price * (1.0 - self._recovery_factor)
        if recovery.direction == "SHORT":
            return current_price >= recovery.anchor_price * (1.0 + self._recovery_factor)
        return False

    def _hold_seconds_elapsed(self, recovery: _RecoveryTrade) -> float:
        if recovery.entry_ts_ns is None:
            return 0.0
        return (self.clock.timestamp_ns() - recovery.entry_ts_ns) / 1_000_000_000.0

    def _time_stop_due(self, recovery: _RecoveryTrade) -> bool:
        return self._hold_seconds_elapsed(recovery) >= float(self._max_hold_seconds)

    def _seconds_until_window_end(self, symbol: str) -> float | None:
        state = self._states.get(symbol)
        if state is None or not state.active_slug:
            return None
        window_start = parse_window_epoch_from_slug(state.active_slug)
        if window_start is None:
            return None
        now_sec = int(self.clock.timestamp_ns() // 1_000_000_000)
        return float(window_start + WINDOW_SEC - now_sec)

    def _entry_allowed(self, symbol: str) -> bool:
        remaining = self._seconds_until_window_end(symbol)
        if remaining is None:
            return True
        if remaining <= 0:
            return False
        return remaining >= float(self._min_entry_seconds)

    def _open_position_for_recovery(self, recovery: _RecoveryTrade):
        if recovery.position_id is None:
            return None
        position = self.cache.position(recovery.position_id)
        if position is None:
            return None
        if getattr(position, "is_open", True) is False:
            return None
        ts_closed = getattr(position, "ts_closed", None)
        if ts_closed not in (None, 0):
            return None
        return position

    def _submit_market_exit(
        self,
        recovery: _RecoveryTrade,
        *,
        current_price: float,
        reason: str,
    ) -> bool:
        position = self._open_position_for_recovery(recovery)
        if position is None:
            return False
        ready, readiness_reason = self._execution_ready(recovery.instrument_id)
        if not ready:
            self.log.info(
                f"skip CLOSE {recovery.symbol}: {readiness_reason} for {recovery.instrument_id}",
                color=LogColor.YELLOW,
            )
            return False
        order = self.order_factory.market(
            instrument_id=recovery.instrument_id,
            order_side=OrderSide.SELL,
            quantity=position.quantity,
            time_in_force=TimeInForce.IOC,
            reduce_only=True,
            tags=self._exit_tags(
                reason=reason,
                symbol=recovery.symbol,
                direction=recovery.direction,
            ),
        )
        self.submit_order(order, position_id=recovery.position_id)
        self.log.info(
            f"{self.config.strategy_id} CLOSE {recovery.direction} {recovery.symbol} "
            f"position={recovery.position_id} anchor={recovery.anchor_price:.4f} "
            f"current={current_price:.4f}",
            color=LogColor.MAGENTA,
        )
        return True

    def _tracked_poly_iids(self) -> set[InstrumentId]:
        out: set[InstrumentId] = set()
        for state in self._states.values():
            if state.yes_instrument_id is not None:
                out.add(state.yes_instrument_id)
            if state.no_instrument_id is not None:
                out.add(state.no_instrument_id)
        return out

    def _quote_precision_ok(self, instrument: Instrument, tick: QuoteTick) -> bool:
        expected = instrument.price_precision
        has_price = False
        for px in (tick.bid_price, tick.ask_price):
            if px is None:
                continue
            has_price = True
            if px.precision != expected:
                return False
        return has_price

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

    def _entry_tags(
        self,
        *,
        symbol: str,
        direction: str,
        reason: str | None = None,
        context: dict[str, object] | None = None,
    ) -> list[str]:
        return build_paper_entry_signal_tags(
            strategy_id=self.config.strategy_id,
            symbol=symbol,
            direction=direction,
            reason=reason,
            context=context,
        )

    def _exit_tags(
        self,
        *,
        reason: str,
        symbol: str | None = None,
        direction: str | None = None,
    ) -> list[str]:
        return build_paper_exit_reason_tags(
            strategy_id=self.config.strategy_id,
            reason=reason,
            symbol=symbol,
            direction=direction,
        )
