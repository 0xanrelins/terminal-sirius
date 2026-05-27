"""Persist Nautilus strategy events to PostgreSQL + UI WS shape."""
from __future__ import annotations

import time

import db
from adapters.polymarket.rolling import slug_for_series
from simulation.sizing import pnl_for_outcome


async def handle_strategy_events(events: list[dict]) -> list[dict]:
    """Apply strategy-emitted events; return WS fan-out payloads."""
    out: list[dict] = []
    for ev in events:
        et = ev.get("type")
        mode = ev.get("mode", "live")
        if et == "live_signal" or et == "simulation_signal":
            out.append({k: v for k, v in ev.items() if k != "mode"})
        elif et == "live_bet_open" or et == "simulation_bet_open":
            out.extend(await _persist_open(ev))
        elif et == "live_bet_settle" or et == "simulation_bet_settle":
            out.extend(await _persist_settle(ev))
        elif et == "live_cycle_closed" or et == "simulation_cycle_closed":
            if mode == "live":
                await db.close_live_cycle(int(ev["cycle_id"]))
            else:
                await db.close_simulation_cycle(int(ev["cycle_id"]))
            out.append({k: v for k, v in ev.items() if k != "mode"})
        elif et == "live_order_error":
            out.append({k: v for k, v in ev.items() if k != "mode"})
    return out


async def _persist_open(ev: dict) -> list[dict]:
    mode = ev.get("mode", "live")
    leg = int(ev["leg"])
    side = ev.get("side") or "long"
    asset = ev["asset"]
    binance_symbol = ev["binance_symbol"]
    poly_series = ev["poly_series"]
    candle_open = int(ev["candle_open"])
    opened_at = int(ev.get("opened_at") or time.time())
    signal_time = ev.get("signal_time") or opened_at
    liq_bar_open = ev.get("liq_bar_open")
    threshold = float(ev.get("threshold") or 0)
    signal_notional = float(ev.get("signal_notional") or 0)
    cycle_id = ev.get("cycle_id")

    if mode == "live":
        if cycle_id is None:
            cycle_id = await db.create_live_cycle(
                asset=asset,
                binance_symbol=binance_symbol,
                poly_series=poly_series,
                signal_time=int(signal_time),
                side=side,
                signal_notional=signal_notional,
                threshold=threshold,
                liq_bar_open=liq_bar_open,
            )
        bet_id = await db.insert_live_bet(
            cycle_id=int(cycle_id),
            leg=leg,
            side=side,
            candle_open=candle_open,
            poly_slug=ev.get("poly_slug") or slug_for_series(poly_series, ts=candle_open),
            poly_series=poly_series,
            entry_price=float(ev["entry_price"]),
            shares=float(ev["shares"]),
            cost_usd=float(ev["cost_usd"]),
            opened_at=opened_at,
            order_id=ev.get("order_id"),
            clob_status=ev.get("clob_status"),
            fill_price=ev.get("fill_price"),
        )
        prefix = "live"
    else:
        if cycle_id is None:
            cycle_id = await db.create_simulation_cycle(
                asset=asset,
                binance_symbol=binance_symbol,
                poly_series=poly_series,
                signal_time=int(signal_time),
                side=side,
                signal_notional=signal_notional,
                threshold=threshold,
                liq_bar_open=liq_bar_open,
            )
        bet_id = await db.insert_simulation_bet(
            cycle_id=int(cycle_id),
            leg=leg,
            side=side,
            candle_open=candle_open,
            poly_slug=ev.get("poly_slug") or slug_for_series(poly_series, ts=candle_open),
            poly_series=poly_series,
            entry_price=float(ev["entry_price"]),
            shares=float(ev["shares"]),
            cost_usd=float(ev["cost_usd"]),
            opened_at=opened_at,
        )
        prefix = "simulation"

    ws: list[dict] = []
    if leg == 1 and ev.get("include_signal"):
        sig_type = f"{prefix}_signal" if prefix == "live" else "simulation_signal"
        sig: dict = {
            "type": sig_type,
            "side": side,
            "asset": asset,
            "cycle_id": int(cycle_id),
            "binance_symbol": binance_symbol,
            "poly_series": poly_series,
            "signal_time": signal_time,
            "liq_bar_open": liq_bar_open,
            "threshold": threshold,
            "target_candle_open": candle_open,
            "dry_run": mode != "live",
        }
        if side == "long":
            sig["signal_long_notional"] = signal_notional
        else:
            sig["signal_short_notional"] = signal_notional
        ws.append(sig)

    open_type = f"{prefix}_bet_open"
    ws.append(
        {
            "type": open_type,
            "bet_id": bet_id,
            "cycle_id": int(cycle_id),
            "side": side,
            "asset": asset,
            "leg": leg,
            "binance_symbol": binance_symbol,
            "poly_series": poly_series,
            "poly_slug": ev.get("poly_slug") or slug_for_series(poly_series, ts=candle_open),
            "candle_open": candle_open,
            "entry_price": float(ev["entry_price"]),
            "yes_price": ev.get("yes_price"),
            "price_source": ev.get("price_source"),
            "shares": float(ev["shares"]),
            "cost_usd": float(ev["cost_usd"]),
            "opened_at": opened_at,
            "signal_time": signal_time,
            "liq_bar_open": liq_bar_open,
            "threshold": threshold,
            "order_id": ev.get("order_id"),
            "clob_status": ev.get("clob_status"),
        }
    )
    return ws


async def _persist_settle(ev: dict) -> list[dict]:
    mode = ev.get("mode", "live")
    won = bool(ev["won"])
    shares = float(ev["shares"])
    cost = float(ev["cost_usd"])
    pnl = pnl_for_outcome(shares, cost, won)
    outcome = "win" if won else "loss"
    settled_at = int(time.time())
    bet_id = int(ev["bet_id"])

    if mode == "live":
        await db.settle_live_bet(bet_id, outcome, pnl, settled_at)
        prefix = "live"
    else:
        await db.settle_simulation_bet(bet_id, outcome, pnl, settled_at)
        prefix = "simulation"

    return [
        {
            "type": f"{prefix}_bet_settle",
            "bet_id": bet_id,
            "cycle_id": ev.get("cycle_id"),
            "side": ev.get("side"),
            "asset": ev.get("asset"),
            "leg": ev.get("leg"),
            "candle_open": ev.get("candle_open"),
            "outcome": outcome,
            "pnl_usd": pnl,
            "won": won,
            "bar_open": ev.get("bar_open"),
            "bar_close": ev.get("bar_close"),
            "candle_green": float(ev.get("bar_close", 0)) >= float(ev.get("bar_open", 0)),
            "settled_at": settled_at,
            "order_id": ev.get("order_id"),
        }
    ]
