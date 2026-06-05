"""
PaperTradeMonitorActor — native paper-trade dashboard data source.

Reads ``self.portfolio`` (PortfolioFacade), ``self.cache`` and
``self.portfolio.analyzer`` (PortfolioAnalyzer) — all assigned natively to every
Nautilus ``Actor`` — and forwards two message shapes to the FastAPI WS queue:

  - ``paper_snapshot`` : periodic full account/position/order/PnL/analytics state
    (``clock.set_timer``), the live panel + equity curve source.
  - ``paper_event``    : discrete order/position lifecycle events (fills, opens,
    closes, rejections) for the activity feed, via msgbus ``events.*`` topics.

No strategy changes and no parent-process cache access — same bridge pattern as
``LiquidationUiBridgeActor`` / ``BridgeActor``.
"""
from __future__ import annotations

import math
import queue
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from nautilus_trader.common.actor import Actor
from nautilus_trader.config import ActorConfig
from nautilus_trader.model.events import (
    OrderDenied,
    OrderFilled,
    OrderRejected,
    PositionChanged,
    PositionClosed,
    PositionOpened,
)
from nautilus_trader.model.identifiers import Venue

from strategy_signal_tags import entry_signal_from_order_tags

if TYPE_CHECKING:
    import multiprocessing


class PaperTradeMonitorActorConfig(ActorConfig, frozen=True):
    venue: str = "POLYMARKET"
    snapshot_interval_sec: float = 2.0
    paper_trade: bool = True


def _num(value: Any) -> float | None:
    """Best-effort numeric coercion for Nautilus Money/Price/Quantity/scalars."""
    if value is None:
        return None
    try:
        return value.as_double()
    except AttributeError:
        pass
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _json_safe(obj: Any) -> Any:
    """Recursively coerce to JSON-spec-safe values.

    Critically replaces ``NaN``/``Infinity`` with ``None``: Python's ``json.dumps``
    emits those as bare tokens that the browser's ``JSON.parse`` rejects (the whole
    frame is then dropped by the frontend). Also unwraps numpy scalars.
    """
    if obj is None or isinstance(obj, (str, bool, int)):
        return obj
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    item = getattr(obj, "item", None)
    if callable(item):  # numpy / pandas scalar
        try:
            return _json_safe(item())
        except Exception:  # noqa: BLE001
            return str(obj)
    return str(obj)


def _money_dict(d: dict | None) -> dict[str, float]:
    """Convert a ``dict[Currency, Money]`` to ``{currency_code: amount}``."""
    if not d:
        return {}
    out: dict[str, float] = {}
    for ccy, money in d.items():
        amount = _num(money)
        if amount is not None:
            out[str(ccy)] = amount
    return out


class PaperTradeMonitorActor(Actor):
    """Periodic snapshot + event stream for the paper-trade dashboard widget."""

    def __init__(
        self,
        config: PaperTradeMonitorActorConfig,
        data_queue: queue.Queue | multiprocessing.queues.Queue,
    ) -> None:
        super().__init__(config)
        self._queue = data_queue
        self._venue = Venue(config.venue)
        self._interval_sec = float(config.snapshot_interval_sec)
        self._paper_trade = bool(config.paper_trade)
        self._started_ns: int = 0
        self._fills_count: int = 0

    def _enqueue(self, msg: dict) -> None:
        try:
            self._queue.put_nowait(msg)
        except queue.Full:
            pass

    # -- LIFECYCLE ----------------------------------------------------------

    def on_start(self) -> None:
        self._started_ns = self.clock.timestamp_ns()
        self.msgbus.subscribe(topic="events.order.*", handler=self._on_order_event)
        self.msgbus.subscribe(topic="events.position.*", handler=self._on_position_event)
        self.clock.set_timer(
            "paper_snapshot",
            timedelta(seconds=self._interval_sec),
            callback=self._on_snapshot_timer,
        )
        print(
            "[paper] PaperTradeMonitorActor → paper_snapshot/paper_event → WS queue "
            f"(venue={self._venue}, every {self._interval_sec}s)"
        )

    def on_stop(self) -> None:
        try:
            self.clock.cancel_timer("paper_snapshot")
        except Exception:  # noqa: BLE001 — best-effort on shutdown
            pass

    # -- SNAPSHOT -----------------------------------------------------------

    def _on_snapshot_timer(self, _event) -> None:
        try:
            self._enqueue(_json_safe(self._build_snapshot()))
        except Exception as e:  # noqa: BLE001 — never let monitoring break the node
            self.log.warning(f"paper_snapshot build failed: {e!r}")

    def _build_snapshot(self) -> dict:
        now_ns = self.clock.timestamp_ns()
        snapshot: dict = {
            "type": "paper_snapshot",
            "ts": now_ns,
            "run": {
                "strategy_on": True,
                "paper": self._paper_trade,
                "trader_id": str(self.trader_id),
                "venue": str(self._venue),
                "started_ts": self._started_ns,
                "uptime_s": max(0.0, (now_ns - self._started_ns) / 1e9),
            },
        }

        account = self.portfolio.account(self._venue)
        if account is None:
            snapshot["account"] = None
            snapshot["pnl"] = {}
            snapshot["exposure"] = {}
            snapshot["positions"] = []
            snapshot["orders"] = []
            snapshot["stats"] = {}
            snapshot["counts"] = {"open_positions": 0, "open_orders": 0, "closed_trades": 0}
            return snapshot

        balances = _money_dict(account.balances_total())
        equity = _money_dict(self.portfolio.equity(self._venue))
        locked = _money_dict(self.portfolio.balances_locked(self._venue))
        realized = _money_dict(self.portfolio.realized_pnls(self._venue))
        unrealized = _money_dict(self.portfolio.unrealized_pnls(self._venue))
        total = _money_dict(self.portfolio.total_pnls(self._venue))
        net_exposure = _money_dict(self.portfolio.net_exposures(self._venue))

        currency = self._primary_currency(equity, balances)

        snapshot["account"] = {
            "currency": currency,
            "balance": balances.get(currency) if currency else None,
            "balances": balances,
            "locked": locked,
            "equity": equity.get(currency) if currency else None,
            "equity_all": equity,
        }
        snapshot["pnl"] = {
            "currency": currency,
            "realized": realized.get(currency) if currency else None,
            "unrealized": unrealized.get(currency) if currency else None,
            "total": total.get(currency) if currency else None,
            "realized_all": realized,
            "unrealized_all": unrealized,
            "total_all": total,
        }
        snapshot["exposure"] = {
            "net": net_exposure.get(currency) if currency else None,
            "net_all": net_exposure,
        }
        snapshot["positions"] = self._open_positions(now_ns)
        snapshot["orders"] = self._open_orders()
        snapshot["stats"] = self._analyzer_stats(account, currency, unrealized)

        closed = self.cache.positions_closed(venue=self._venue)
        snapshot["counts"] = {
            "open_positions": len(snapshot["positions"]),
            "open_orders": len(snapshot["orders"]),
            "closed_trades": len(closed),
            "fills": self._fills_count,
        }
        return snapshot

    def _primary_currency(self, equity: dict, balances: dict) -> str | None:
        for source in (equity, balances):
            if source:
                return next(iter(source.keys()))
        return None

    def _open_positions(self, now_ns: int) -> list[dict]:
        out: list[dict] = []
        for pos in self.cache.positions_open(venue=self._venue):
            unrealized = self.portfolio.unrealized_pnl(pos.instrument_id)
            out.append(
                {
                    "instrument_id": str(pos.instrument_id),
                    "side": pos.side.name,
                    "quantity": _num(pos.quantity),
                    "avg_px_open": _num(pos.avg_px_open),
                    "unrealized_pnl": _num(unrealized),
                    "realized_pnl": _num(pos.realized_pnl),
                    "opened_ts": int(pos.ts_opened),
                    "duration_s": max(0.0, (now_ns - int(pos.ts_opened)) / 1e9),
                }
            )
        return out

    def _open_orders(self) -> list[dict]:
        out: list[dict] = []
        for order in self.cache.orders_open(venue=self._venue):
            display, tooltip = entry_signal_from_order_tags(order.tags)
            out.append(
                {
                    "client_order_id": str(order.client_order_id),
                    "instrument_id": str(order.instrument_id),
                    "side": order.side.name,
                    "order_type": order.order_type.name,
                    "quantity": _num(order.quantity),
                    "filled_qty": _num(order.filled_qty),
                    "status": order.status_string(),
                    "ts": int(order.ts_init),
                    "entry_signal": display,
                    "entry_signal_tooltip": tooltip,
                }
            )
        return out

    def _analyzer_stats(self, account, currency: str | None, unrealized: dict) -> dict:
        try:
            analyzer = self.portfolio.analyzer
            positions = self.cache.positions(venue=self._venue)
            analyzer.calculate_statistics(account, positions)

            ccy_obj = None
            unrealized_money = None
            for pos_ccy, money in (
                self.portfolio.unrealized_pnls(self._venue).items()
                if currency
                else {}.items()
            ):
                if str(pos_ccy) == currency:
                    ccy_obj = money.currency
                    unrealized_money = money
                    break

            stats: dict[str, Any] = {}
            stats.update(analyzer.get_performance_stats_pnls(ccy_obj, unrealized_money))
            stats.update(analyzer.get_performance_stats_returns())
            return {str(k): v for k, v in stats.items()}
        except Exception as e:  # noqa: BLE001 — analytics are best-effort
            self.log.debug(f"analyzer stats unavailable: {e!r}")
            return {}

    # -- EVENTS -------------------------------------------------------------

    def _on_order_event(self, event) -> None:
        try:
            msg = self._order_event_msg(event)
        except Exception as e:  # noqa: BLE001
            self.log.warning(f"paper_event (order) failed: {e!r}")
            return
        if msg is not None:
            self._enqueue(_json_safe(msg))

    def _on_position_event(self, event) -> None:
        try:
            msg = self._position_event_msg(event)
        except Exception as e:  # noqa: BLE001
            self.log.warning(f"paper_event (position) failed: {e!r}")
            return
        if msg is not None:
            self._enqueue(_json_safe(msg))

    def _order_event_msg(self, event) -> dict | None:
        if isinstance(event, OrderFilled):
            self._fills_count += 1
            display, tooltip = self._entry_signal_for_order(event.client_order_id)
            return {
                "type": "paper_event",
                "kind": "fill",
                "ts": int(event.ts_event),
                "instrument_id": str(event.instrument_id),
                "side": event.order_side.name,
                "quantity": _num(event.last_qty),
                "price": _num(event.last_px),
                "commission": _num(event.commission),
                "client_order_id": str(event.client_order_id),
                "entry_signal": display,
                "entry_signal_tooltip": tooltip,
            }
        if isinstance(event, OrderRejected):
            return {
                "type": "paper_event",
                "kind": "order_rejected",
                "ts": int(event.ts_event),
                "instrument_id": str(event.instrument_id),
                "client_order_id": str(event.client_order_id),
                "reason": str(event.reason),
            }
        if isinstance(event, OrderDenied):
            return {
                "type": "paper_event",
                "kind": "order_denied",
                "ts": int(event.ts_event),
                "instrument_id": str(event.instrument_id),
                "client_order_id": str(event.client_order_id),
                "reason": str(event.reason),
            }
        return None

    def _position_event_msg(self, event) -> dict | None:
        if isinstance(event, PositionOpened):
            return {
                "type": "paper_event",
                "kind": "position_open",
                "ts": int(event.ts_event),
                "instrument_id": str(event.instrument_id),
                "side": event.side.name,
                "quantity": _num(event.quantity),
                "price": _num(event.avg_px_open),
            }
        if isinstance(event, PositionClosed):
            return {
                "type": "paper_event",
                "kind": "position_close",
                "ts": int(event.ts_event),
                "instrument_id": str(event.instrument_id),
                "quantity": _num(event.peak_qty),
                "price": _num(event.avg_px_close),
                "realized_pnl": _num(event.realized_pnl),
            }
        if isinstance(event, PositionChanged):
            return {
                "type": "paper_event",
                "kind": "position_change",
                "ts": int(event.ts_event),
                "instrument_id": str(event.instrument_id),
                "quantity": _num(event.quantity),
                "realized_pnl": _num(event.realized_pnl),
            }
        return None

    def _entry_signal_for_order(self, client_order_id) -> tuple[str, str]:
        order = self.cache.order(client_order_id)
        tags = order.tags if order is not None else None
        return entry_signal_from_order_tags(tags)
