"""
Liq → Polymarket strategy (Nautilus TradingNode).

Owns signal, sizing, live submit_order, and sim paper bets.
UI/DB persist runs on the FastAPI asyncio loop via strategy_runtime bridge.
"""
from __future__ import annotations

import asyncio
import time
from datetime import timedelta
from typing import Any

from nautilus_trader.adapters.polymarket.common.constants import POLYMARKET_CLIENT_ID
from nautilus_trader.adapters.polymarket.loaders import PolymarketDataLoader
from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar, BarType, DataType
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.identifiers import ClientOrderId, InstrumentId
from nautilus_trader.model.objects import Quantity
from nautilus_trader.trading.strategy import Strategy

from adapters.polymarket.rolling import WINDOW_SEC
from bridge_actor import BAR_SPECS
from engines.liq_poly_runner import LiqPolyRunner
from engines.poly_sync import quote_for_bet
from simulation.backtest_pricing import backtest_entry_for_side
from engines.strategy_persist import handle_strategy_events
from nautilus_bridge.context import exec_client_ready
from nautilus_bridge.strategy_runtime import (
    drain_catchup_request,
    get_event_queue,
    get_main_loop,
    get_runtime,
)
from strategies.liq_poly_data import LiqBar15mUpdate


class LiqPolyStrategyConfig(StrategyConfig, frozen=True):
    mode: str = "live"  # live | sim | backtest


class LiqPolyStrategy(Strategy):
    def __init__(self, config: LiqPolyStrategyConfig) -> None:
        super().__init__(config)
        self._mode = config.mode
        self._runner: LiqPolyRunner | None = None
        self._pending: dict[str, dict[str, Any]] = {}
        self._defer_open: list[dict] = []

    def on_start(self) -> None:
        cfg = get_runtime("sim" if self._mode == "backtest" else self._mode)
        self._runner = LiqPolyRunner(cfg)
        self.subscribe_data(DataType(LiqBar15mUpdate))
        for asset, meta in cfg.assets.items():
            iid = meta["binance_symbol"]
            for spec in BAR_SPECS:
                if spec != "15-MINUTE-LAST-EXTERNAL":
                    continue
                bar_type = BarType.from_str(f"{iid}-{spec}")
                self.subscribe_bars(bar_type)
        self.clock.set_timer(
            "exec_deferred_poll",
            timedelta(milliseconds=500),
            callback=self._on_exec_deferred_poll,
        )
        self.log.info(f"LiqPolyStrategy started mode={self._mode}")
        if self._mode != "backtest":
            self._startup_strategy_catchup()

    def on_stop(self) -> None:
        self.log.info(f"LiqPolyStrategy stopped mode={self._mode}")

    def on_data(self, data) -> None:
        self._drain_strategy_catchup_requests()
        if not isinstance(data, LiqBar15mUpdate) or self._runner is None:
            return
        for cmd in self._runner.on_liq_bar(
            symbol=data.symbol,
            bar_open=int(data.bar_open),
            long_total=float(data.long_total),
            short_total=float(data.short_total),
            signal_ts=int(data.signal_ts),
        ):
            self._dispatch(cmd)

    def on_bar(self, bar: Bar) -> None:
        self._drain_strategy_catchup_requests()
        if self._runner is None:
            return
        spec = str(bar.bar_type.spec)
        if "15-MINUTE" not in spec:
            return
        symbol = str(bar.bar_type.instrument_id)
        from bar_time import bar_open_time_ns

        bar_open = bar_open_time_ns(bar.ts_event, "15m")
        for cmd in self._runner.on_bar_close(
            symbol=symbol,
            bar_open=bar_open,
            open_p=float(bar.open),
            close_p=float(bar.close),
        ):
            self._dispatch(cmd)

    def on_order_filled(self, event) -> None:
        order = self.cache.order(event.client_order_id)
        if order is None:
            return
        ctx = self._pending.pop(str(event.client_order_id), None)
        if ctx is None or self._runner is None:
            return
        fill_qty = (
            event.last_qty.as_double()
            if hasattr(event.last_qty, "as_double")
            else float(event.last_qty)
        )
        fill_px = (
            event.last_px.as_double()
            if hasattr(event.last_px, "as_double")
            else float(ctx["entry_price"])
        )
        cost = fill_px * fill_qty if fill_qty > 0 else float(ctx["cost_usd"])
        self._finalize_open(
            ctx,
            order_id=str(event.venue_order_id),
            shares=fill_qty,
            cost_usd=cost,
        )

    def on_order_submitted(self, event) -> None:
        """Reserve liq bar+side once Nautilus accepts the order (async exec boundary)."""
        ctx = self._pending.get(str(event.client_order_id))
        if ctx is None or ctx.get("mode") != "live":
            return
        self._mark_live_signal_reserved(ctx)

    def on_order_rejected(self, event) -> None:
        ctx = self._pending.pop(str(event.client_order_id), None)
        if ctx is None:
            return
        self._emit_live_order_error(ctx, event, default_reason="rejected")
        self._release_live_signal_reserved(ctx)

    def on_order_denied(self, event) -> None:
        ctx = self._pending.pop(str(event.client_order_id), None)
        if ctx is None:
            return
        self._emit_live_order_error(ctx, event, default_reason="denied")
        self._release_live_signal_reserved(ctx)

    def _mark_live_signal_reserved(self, ctx: dict) -> None:
        if self._runner is None:
            return
        sk = ctx.get("signal_key")
        if not sk:
            return
        sym, bar_open, side = sk
        self._runner.mark_signaled(sym, bar_open, side)

    def _release_live_signal_reserved(self, ctx: dict) -> None:
        if self._runner is None:
            return
        sk = ctx.get("signal_key")
        if not sk:
            return
        sym, bar_open, side = sk
        self._runner.unmark_signaled(sym, bar_open, side)

    def _emit_live_order_error(self, ctx: dict, event, *, default_reason: str) -> None:
        reason = getattr(event, "reason", None) or default_reason
        self._emit(
            {
                "type": "live_order_error",
                "mode": "live",
                "asset": ctx["asset"],
                "side": ctx["side"],
                "leg": ctx["leg"],
                "poly_slug": ctx.get("slug") or ctx.get("poly_slug"),
                "error": str(reason),
            }
        )

    def _dispatch(self, cmd: dict) -> list[dict]:
        name = cmd.get("cmd")
        if name == "open_bet":
            return self._handle_open(cmd)
        if name == "settle":
            return self._handle_settle(cmd) or []
        if name == "close_cycle":
            ev = {
                "type": (
                    "live_cycle_closed"
                    if cmd["mode"] == "live"
                    else "simulation_cycle_closed"
                ),
                "mode": cmd["mode"],
                "cycle_id": cmd["cycle_id"],
                "asset": cmd["asset"],
                "side": cmd["side"],
            }
            return self._persist([ev])
        return []

    def _handle_open(self, cmd: dict) -> list[dict]:
        if self._runner is None:
            return []
        cfg = get_runtime("sim" if self._mode == "backtest" else self._mode)
        asset = cmd["asset"]
        meta = cfg.assets[asset]
        bt_entry = (
            backtest_entry_for_side(cmd["side"])
            if self._mode == "backtest"
            else None
        )
        quote = quote_for_bet(
            poly_series=meta["poly_series"],
            candle_open=int(cmd["candle_open"]),
            side=cmd["side"],
            min_shares_default=cfg.min_shares,
            min_usd=cfg.min_usd,
            for_live=cmd["mode"] == "live",
            backtest_entry=bt_entry,
        )
        if quote is None:
            self.log.warning(f"skip {asset} {cmd['side']} leg{cmd['leg']}: no quote")
            return []
        if cmd["mode"] == "live":
            if not cfg.orders_enabled:
                dry = {
                    "type": "live_signal",
                    "mode": "live",
                    "side": cmd["side"],
                    "asset": asset,
                    "dry_run": True,
                    "signal_time": cmd["signal_time"],
                    "threshold": cmd["threshold"],
                    "liq_bar_open": cmd["liq_bar_open"],
                    "target_candle_open": cmd["candle_open"],
                }
                self._emit(dry)
                return [{k: v for k, v in dry.items() if k != "mode"}]
            if not exec_client_ready():
                self._defer_open_cmd(cmd)
                return []
            self._submit_live(cmd, meta, quote)
            return []
        ctx = {
            **cmd,
            "poly_series": meta["poly_series"],
            "poly_slug": quote.slug,
            "entry_price": quote.entry_price,
            "yes_price": quote.yes_price,
            "price_source": quote.price_source,
            "shares": quote.shares,
            "cost_usd": quote.cost_usd,
            "opened_at": int(time.time()),
            "include_signal": cmd["leg"] == 1,
        }
        return self._finalize_open(ctx)

    def _submit_live(self, cmd: dict, meta: dict, quote) -> None:
        token_index = 0 if cmd["side"] == "long" else 1
        loop = get_main_loop()
        try:
            fut = asyncio.run_coroutine_threadsafe(
                PolymarketDataLoader.from_market_slug(quote.slug, token_index=token_index),
                loop,
            )
            loader = fut.result(timeout=30)
        except Exception as e:
            self.log.error(f"instrument load failed {quote.slug}: {e}")
            self._emit(
                {
                    "type": "live_order_error",
                    "mode": "live",
                    "asset": cmd["asset"],
                    "side": cmd["side"],
                    "leg": cmd["leg"],
                    "poly_slug": quote.slug,
                    "error": str(e),
                }
            )
            return
        instrument = loader.instrument
        if self.cache.instrument(instrument.id) is None:
            self.cache.add_instrument(instrument)
        order = self.order_factory.market(
            instrument_id=instrument.id,
            order_side=OrderSide.BUY,
            quantity=Quantity.from_str(f"{quote.cost_usd:.2f}"),
            time_in_force=TimeInForce.FOK,
            quote_quantity=True,
        )
        ctx = {
            **cmd,
            "poly_series": meta["poly_series"],
            "slug": quote.slug,
            "entry_price": quote.entry_price,
            "yes_price": quote.yes_price,
            "price_source": quote.price_source,
            "shares": quote.shares,
            "cost_usd": quote.cost_usd,
            "include_signal": cmd["leg"] == 1,
        }
        client_order_id = str(order.client_order_id)
        sk = cmd.get("signal_key")
        if sk and any(c.get("signal_key") == sk for c in self._pending.values()):
            self.log.warning(
                f"skip duplicate submit {cmd['asset']} {cmd['side']} "
                f"leg{cmd['leg']}: order already pending"
            )
            return
        self._pending[client_order_id] = ctx
        self.submit_order(order, client_id=POLYMARKET_CLIENT_ID)

    def _finalize_open(
        self,
        ctx: dict,
        *,
        order_id: str | None = None,
        shares: float | None = None,
        cost_usd: float | None = None,
    ) -> list[dict]:
        if self._runner is None:
            return []
        shares_v = float(shares if shares is not None else ctx["shares"])
        cost_v = float(cost_usd if cost_usd is not None else ctx["cost_usd"])
        open_ev = {
            "type": (
                "live_bet_open" if ctx["mode"] == "live" else "simulation_bet_open"
            ),
            "mode": ctx["mode"],
            "side": ctx["side"],
            "asset": ctx["asset"],
            "leg": ctx["leg"],
            "binance_symbol": ctx["binance_symbol"],
            "poly_series": ctx["poly_series"],
            "poly_slug": ctx.get("poly_slug") or ctx.get("slug"),
            "candle_open": ctx["candle_open"],
            "entry_price": ctx["entry_price"],
            "yes_price": ctx.get("yes_price"),
            "price_source": ctx.get("price_source"),
            "shares": shares_v,
            "cost_usd": cost_v,
            "opened_at": ctx.get("opened_at", int(time.time())),
            "signal_time": ctx.get("signal_time"),
            "signal_notional": ctx.get("signal_notional"),
            "threshold": ctx.get("threshold"),
            "liq_bar_open": ctx.get("liq_bar_open"),
            "cycle_id": ctx.get("cycle_id"),
            "order_id": order_id,
            "clob_status": "matched" if order_id else None,
            "include_signal": ctx.get("include_signal", False),
        }
        ws = self._persist([open_ev])
        if not ws:
            return []
        bet_ev = next(e for e in ws if e["type"].endswith("_bet_open"))
        cycle_id = int(bet_ev["cycle_id"])
        bet_id = int(bet_ev["bet_id"])
        self._mark_live_signal_reserved(ctx)
        self._runner.attach_open_bet(
            bet_id=bet_id,
            binance_symbol=ctx["binance_symbol"],
            side=ctx["side"],
            candle_open=int(ctx["candle_open"]),
            cycle_id=cycle_id,
            leg=int(ctx["leg"]),
            asset=ctx["asset"],
            poly_series=ctx["poly_series"],
            entry_price=float(ctx["entry_price"]),
            shares=shares_v,
            cost_usd=cost_v,
            order_id=order_id,
        )
        return ws

    def _handle_settle(self, cmd: dict) -> list[dict]:
        bet_id = cmd.get("bet_id")
        if bet_id is None:
            return []
        settle_ev = {
            "type": (
                "live_bet_settle" if cmd["mode"] == "live" else "simulation_bet_settle"
            ),
            "mode": cmd["mode"],
            "bet_id": bet_id,
            "cycle_id": cmd.get("cycle_id"),
            "side": cmd.get("side"),
            "asset": cmd.get("asset"),
            "leg": cmd.get("leg"),
            "candle_open": cmd.get("candle_open"),
            "won": cmd.get("won"),
            "bar_open": cmd.get("bar_open"),
            "bar_close": cmd.get("bar_close"),
            "shares": cmd.get("shares"),
            "cost_usd": cmd.get("cost_usd"),
            "order_id": cmd.get("order_id"),
        }
        return self._persist([settle_ev])

    def _persist(self, events: list[dict]) -> list[dict]:
        if self._mode == "backtest":
            return self._persist_backtest(events)
        loop = get_main_loop()
        fut = asyncio.run_coroutine_threadsafe(handle_strategy_events(events), loop)
        try:
            ws = fut.result(timeout=30)
        except Exception as e:
            self.log.error(f"persist failed: {e}")
            return []
        for ev in ws:
            self._emit(ev)
        return ws

    def _persist_backtest(self, events: list[dict]) -> list[dict]:
        """Assign synthetic ids for BacktestEngine (no PostgreSQL)."""
        out: list[dict] = []
        _bet_id = [0]
        _cycle_id = [0]

        def next_bet_id() -> int:
            _bet_id[0] += 1
            return _bet_id[0]

        def next_cycle_id() -> int:
            _cycle_id[0] += 1
            return _cycle_id[0]

        for ev in events:
            t = ev.get("type", "")
            if t == "simulation_bet_open":
                out.append(
                    {
                        **ev,
                        "bet_id": ev.get("bet_id") or next_bet_id(),
                        "cycle_id": ev.get("cycle_id") or next_cycle_id(),
                    }
                )
            else:
                out.append(dict(ev))
        return out

    def _emit(self, ev: dict) -> None:
        if self._mode == "backtest":
            return
        q = get_event_queue()
        try:
            q.put_nowait(ev)
        except Exception:
            pass

    def _defer_open_cmd(self, cmd: dict) -> None:
        now = int(time.time())
        target = int(cmd["candle_open"])
        if now >= target + WINDOW_SEC:
            self.log.warning(
                f"drop deferred {cmd['asset']} {cmd['side']} leg{cmd['leg']}: "
                "Polymarket window closed"
            )
            return
        sk = cmd.get("signal_key")
        for pending in self._defer_open:
            if pending.get("signal_key") == sk and pending.get("candle_open") == cmd.get(
                "candle_open"
            ):
                return
        self._defer_open.append(cmd)
        self.log.warning(
            f"defer {cmd['asset']} {cmd['side']} leg{cmd['leg']}: "
            "Polymarket exec client not ready"
        )

    def _flush_deferred_opens(self) -> None:
        if not self._defer_open or not exec_client_ready():
            return
        pending = self._defer_open
        self._defer_open = []
        self.log.info(f"retrying {len(pending)} deferred live open(s)")
        for cmd in pending:
            self._handle_open(cmd)

    def _on_exec_deferred_poll(self, _event) -> None:
        self._flush_deferred_opens()
        self._drain_strategy_catchup_requests()

    def _startup_strategy_catchup(self) -> None:
        if self._runner is None:
            return
        cfg = get_runtime(self._mode)
        loop = get_main_loop()
        from engines.strategy_catchup import catchup_bar_cmds, catchup_settlement_cmds

        try:
            settle_fut = asyncio.run_coroutine_threadsafe(
                catchup_settlement_cmds(self._runner, cfg), loop
            )
            bar_fut = asyncio.run_coroutine_threadsafe(
                catchup_bar_cmds(self._runner, cfg), loop
            )
            for cmd in settle_fut.result(timeout=60):
                self._dispatch(cmd)
            for cmd in bar_fut.result(timeout=60):
                self._dispatch(cmd)
            self.log.info(f"startup strategy catch-up done mode={self._mode}")
        except Exception as e:
            self.log.error(f"startup strategy catch-up failed: {e}")

    def _drain_strategy_catchup_requests(self) -> None:
        if self._mode == "backtest":
            return
        req = drain_catchup_request(self._mode)
        if req is None or self._runner is None:
            return
        reset, fut = req
        loop = get_main_loop()
        cfg = get_runtime(self._mode)
        from engines.strategy_catchup import catchup_bar_cmds, catchup_settlement_cmds

        ws_events: list[dict] = []
        try:
            if reset:
                self._runner.clear_signal_cache()
            for coro in (catchup_settlement_cmds, catchup_bar_cmds):
                cmds = asyncio.run_coroutine_threadsafe(
                    coro(self._runner, cfg), loop
                ).result(timeout=60)
                for cmd in cmds:
                    ws_events.extend(self._dispatch(cmd) or [])
            if not fut.done():
                loop.call_soon_threadsafe(fut.set_result, ws_events)
        except Exception as e:
            if not fut.done():
                loop.call_soon_threadsafe(fut.set_exception, e)
