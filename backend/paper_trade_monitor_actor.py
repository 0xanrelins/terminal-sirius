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
from nautilus_trader.model.data import DataType
from nautilus_trader.model.identifiers import InstrumentId, Venue

from adapters.polymarket.messages import ActivePolymarketMarket
from adapters.polymarket.rolling import WINDOW_SEC
from strategy_signal_tags import entry_signal_from_order_tags, parse_entry_signal_tag

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


def _extract_question_window(question: str) -> str:
    """``Solana Up or Down - June 4, 11:45PM-12:00AM ET`` → date/time suffix."""
    if not question:
        return ""
    sep = " - "
    if sep in question:
        return question.split(sep, 1)[1].strip()
    return ""


def _fmt_polymarket_time(dt) -> str:
    hour = dt.hour % 12 or 12
    minute = dt.minute
    ampm = "AM" if dt.hour < 12 else "PM"
    if minute == 0:
        return f"{hour}{ampm}"
    return f"{hour}:{minute:02d}{ampm}"


def _window_from_rolling_slug(slug: str) -> str:
    """Fallback when Gamma question is unavailable — derive ET window from slug epoch."""
    if not slug:
        return ""
    tail = slug.rsplit("-", 1)[-1]
    if not tail.isdigit() or len(tail) < 10:
        return ""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    start = int(tail)
    end = start + WINDOW_SEC
    et = ZoneInfo("America/New_York")
    s = datetime.fromtimestamp(start, tz=et)
    e = datetime.fromtimestamp(end, tz=et)
    date_part = f"{s.strftime('%B')} {s.day}"
    return f"{date_part}, {_fmt_polymarket_time(s)}-{_fmt_polymarket_time(e)} ET"


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
        # instrument_id str → market metadata (from ActivePolymarketMarket bus)
        self._iid_meta: dict[str, dict[str, str]] = {}

    def _enqueue(self, msg: dict) -> None:
        try:
            self._queue.put_nowait(msg)
        except queue.Full:
            pass

    # -- LIFECYCLE ----------------------------------------------------------

    def on_start(self) -> None:
        self._started_ns = self.clock.timestamp_ns()
        self.subscribe_data(DataType(ActivePolymarketMarket))
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

    def on_data(self, data) -> None:
        if isinstance(data, ActivePolymarketMarket):
            self._on_active_polymarket_market(data)

    def _on_active_polymarket_market(self, data: ActivePolymarketMarket) -> None:
        slug = str(data.slug or "")
        question = str(data.question or "")
        series = str(data.series or "")
        base = {
            "market_slug": slug,
            "market_series": series,
            "market_question": question,
        }
        yes_iid = str(data.instrument_id)
        no_iid = str(data.no_instrument_id)
        self._iid_meta[yes_iid] = {**base, "market_outcome": "YES"}
        self._iid_meta[no_iid] = {**base, "market_outcome": "NO"}

    def _resolve_market_window(self, meta: dict[str, str]) -> str:
        window = meta.get("market_window") or ""
        if window:
            return window
        question = meta.get("market_question") or ""
        window = _extract_question_window(question)
        if not window:
            window = _window_from_rolling_slug(meta.get("market_slug") or "")
        return window

    def _format_market_label(self, meta: dict[str, str]) -> str:
        series = meta.get("market_series") or ""
        outcome = meta.get("market_outcome") or ""
        window = self._resolve_market_window(meta)
        if series:
            label = series.replace("-", " ").upper()
        else:
            q = meta.get("market_question") or meta.get("market_slug") or ""
            if " - " in q:
                label = q.split(" - ", 1)[0].strip()
            else:
                label = (q[:48] + "…") if len(q) > 48 else q
        parts = [p for p in (label, outcome, window) if p]
        return " · ".join(parts) if parts else "—"

    def _market_context(
        self,
        instrument_id: InstrumentId | str,
        *,
        order_tags: Any = None,
    ) -> dict[str, str]:
        iid = str(instrument_id)
        meta = dict(self._iid_meta.get(iid, {}))
        inst = self.cache.instrument(InstrumentId.from_str(iid) if isinstance(instrument_id, str) else instrument_id)
        if inst is not None:
            desc = getattr(inst, "description", None)
            if desc and not meta.get("market_question"):
                meta["market_question"] = str(desc)
        parsed = parse_entry_signal_tag(order_tags)
        if parsed and parsed.get("sym"):
            meta["underlying"] = parsed["sym"]
        window = self._resolve_market_window(meta)
        if window:
            meta["market_window"] = window
        meta["market_label"] = self._format_market_label(meta)
        return meta

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
            snapshot["closed_positions"] = []
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
        closed = list(self.cache.positions_closed(venue=self._venue))
        snapshot["positions"] = self._open_positions(now_ns)
        snapshot["closed_positions"] = self._closed_positions(closed)
        snapshot["orders"] = self._open_orders()
        snapshot["stats"] = self._analyzer_stats(account, currency, unrealized)

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
            # Keep this list strictly "open" even if cache emits transitional entries.
            ts_closed = getattr(pos, "ts_closed", None)
            side_name = getattr(getattr(pos, "side", None), "name", "")
            qty = _num(getattr(pos, "quantity", None))
            if ts_closed not in (None, 0):
                continue
            if side_name == "FLAT":
                continue
            if qty is None or qty <= 0:
                continue
            ts_opened = int(getattr(pos, "ts_opened", 0) or 0)
            unrealized = self.portfolio.unrealized_pnl(pos.instrument_id)
            out.append(
                {
                    "instrument_id": str(pos.instrument_id),
                    "side": pos.side.name,
                    "quantity": qty,
                    "avg_px_open": _num(pos.avg_px_open),
                    "unrealized_pnl": _num(unrealized),
                    "realized_pnl": _num(pos.realized_pnl),
                    "opened_ts": ts_opened,
                    "duration_s": max(0.0, (now_ns - ts_opened) / 1e9),
                    **self._market_context(pos.instrument_id),
                }
            )
        return out

    def _closed_positions(self, closed: list | None = None) -> list[dict]:
        out: list[dict] = []
        positions = closed if closed is not None else list(self.cache.positions_closed(venue=self._venue))
        positions_sorted = sorted(positions, key=lambda p: int(getattr(p, "ts_closed", 0) or 0), reverse=True)
        for pos in positions_sorted[:200]:
            ts_closed = int(getattr(pos, "ts_closed", 0) or 0)
            ts_opened = int(getattr(pos, "ts_opened", 0) or 0)
            duration_ns = int(getattr(pos, "duration_ns", 0) or 0)
            out.append(
                {
                    "instrument_id": str(pos.instrument_id),
                    "side": getattr(getattr(pos, "side", None), "name", ""),
                    "quantity": _num(pos.peak_qty) or _num(pos.quantity),
                    "avg_px_open": _num(pos.avg_px_open),
                    "avg_px_close": _num(pos.avg_px_close),
                    "unrealized_pnl": 0.0,
                    "realized_pnl": _num(pos.realized_pnl),
                    "opened_ts": ts_opened,
                    "closed_ts": ts_closed,
                    "duration_s": max(0.0, duration_ns / 1e9),
                    **self._market_context(pos.instrument_id),
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
                    **self._market_context(order.instrument_id, order_tags=order.tags),
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
            order = self.cache.order(event.client_order_id)
            tags = order.tags if order is not None else None
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
                **self._market_context(event.instrument_id, order_tags=tags),
            }
        if isinstance(event, OrderRejected):
            order = self.cache.order(event.client_order_id)
            return {
                "type": "paper_event",
                "kind": "order_rejected",
                "ts": int(event.ts_event),
                "instrument_id": str(event.instrument_id),
                "client_order_id": str(event.client_order_id),
                "reason": str(event.reason),
                **self._market_context(
                    event.instrument_id,
                    order_tags=order.tags if order is not None else None,
                ),
            }
        if isinstance(event, OrderDenied):
            order = self.cache.order(event.client_order_id)
            return {
                "type": "paper_event",
                "kind": "order_denied",
                "ts": int(event.ts_event),
                "instrument_id": str(event.instrument_id),
                "client_order_id": str(event.client_order_id),
                "reason": str(event.reason),
                **self._market_context(
                    event.instrument_id,
                    order_tags=order.tags if order is not None else None,
                ),
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
                **self._market_context(event.instrument_id),
            }
        if isinstance(event, PositionClosed):
            duration_ns = int(getattr(event, "duration_ns", 0) or 0)
            return {
                "type": "paper_event",
                "kind": "position_close",
                "ts": int(event.ts_event),
                "instrument_id": str(event.instrument_id),
                "quantity": _num(event.peak_qty),
                "price": _num(event.avg_px_close),
                "realized_pnl": _num(event.realized_pnl),
                "duration_s": max(0.0, duration_ns / 1e9),
                "opened_ts": int(getattr(event, "ts_opened", 0) or 0),
                "closed_ts": int(getattr(event, "ts_closed", 0) or 0),
                **self._market_context(event.instrument_id),
            }
        if isinstance(event, PositionChanged):
            return {
                "type": "paper_event",
                "kind": "position_change",
                "ts": int(event.ts_event),
                "instrument_id": str(event.instrument_id),
                "quantity": _num(event.quantity),
                "realized_pnl": _num(event.realized_pnl),
                **self._market_context(event.instrument_id),
            }
        return None

    def _entry_signal_for_order(self, client_order_id) -> tuple[str, str]:
        order = self.cache.order(client_order_id)
        tags = order.tags if order is not None else None
        return entry_signal_from_order_tags(tags)
