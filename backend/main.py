"""
FastAPI WebSocket bridge.

Startup sequence:
  1. Load .env
  2. Init PostgreSQL pool + run schema migration
  3. Start Nautilus node in daemon thread (Binance + Polymarket actors)
  4. Start broadcast loop (WS fan-out + bar persistence)

Endpoints:
  GET  /klines                        — historical OHLCV (DB-first)
  GET  /polymarket/markets?q=…        — search Polymarket markets
  POST /polymarket/subscribe          — add a slug to live stream at runtime
  WS   /ws?symbols=…                  — live feed (trade / quote / bar / polymarket)

Liquidation raw: recorder (NDJSON + Postgres mirror when DATABASE_URL set) fills
`liquidation_events`; uvicorn keeps PERSIST_LIQUIDATION_EVENTS_TO_DB=0 and only updates
`liquidation_bars` + live WS from its stream.
"""
import asyncio
import json
import os
import queue
import sys
from contextlib import asynccontextmanager
from typing import Optional

from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

load_dotenv()
load_dotenv(Path(__file__).resolve().parent.parent / ".env")
sys.path.insert(0, ".")

import db
from adapters.polymarket.gamma import get_market_by_slug, get_token_ids, search_markets
from adapters.polymarket.rolling import PRESET_15M_SERIES, series_symbol, slug_for_series
from bar_time import bar_open_time_ns
from klines import fetch_klines
from liquidations import (
    MAJOR_NAUTILUS_SYMBOLS,
    fetch_liquidation_bars,
    fetch_liquidation_events,
)
from liquidation_stream import run_liquidation_stream
from live.engine import LiveTradingEngine
from simulation.engine import SimulationEngine

data_queue: queue.Queue = queue.Queue(maxsize=10_000)
_clients: list[tuple[WebSocket, Optional[set[str]]]] = []
_simulation: SimulationEngine | None = None
_live: LiveTradingEngine | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _simulation, _live
    # DB
    await db.init()

    _simulation = SimulationEngine()
    await _simulation.load_state()
    startup_sim = await _simulation.sync_bars_from_store()

    _live = LiveTradingEngine()
    await _live.load_state()
    startup_live = await _live.sync_bars_from_store()

    # Nautilus (daemon thread — safe to skip if nautilus_trader not installed yet)
    from node import DEFAULT_INSTRUMENTS, run_node_in_thread

    try:
        run_node_in_thread(data_queue)
    except ImportError as e:
        print(f"[warn] Nautilus not available, market data disabled: {e}")

    asyncio.create_task(run_liquidation_stream(data_queue, DEFAULT_INSTRUMENTS))
    if _persist_liquidation_events_to_db_enabled():
        print("[liquidations] raw events: backend → liquidation_events")
    else:
        print(
            "[liquidations] raw events: off (use record_binance_liquidations.py); "
            "bars + WS from backend stream"
        )

    asyncio.create_task(_broadcast_loop())
    asyncio.create_task(_liquidation_events_retention_loop())
    if startup_sim:
        asyncio.create_task(_fanout_messages(startup_sim))
    if startup_live:
        asyncio.create_task(_fanout_messages(startup_live))

    yield

    await db.close()


app = FastAPI(title="Terminal Sirius — WebSocket Bridge", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── REST ─────────────────────────────────────────────────────────────────────

@app.get("/klines")
async def klines_endpoint(
    symbol: str, interval: str = "1m", limit: int = 500, before: Optional[int] = None
):
    try:
        return await fetch_klines(symbol, interval, min(limit, 1000), before=before)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Upstream error: {e}")


@app.get("/liquidations")
async def liquidations_endpoint(
    symbol: str,
    interval: str = "1m",
    limit: int = 500,
    before: Optional[int] = None,
    from_time: Optional[int] = Query(None, alias="from"),
    to_time: Optional[int] = Query(None, alias="to"),
):
    try:
        return await fetch_liquidation_bars(
            symbol,
            interval,
            min(limit, 10_000),
            before=before,
            from_time=from_time,
            to_time=to_time,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Upstream error: {e}")


@app.get("/liquidation-events")
async def liquidation_events_endpoint(
    symbols: Optional[str] = None,
    limit: int = 200,
):
    """Recent raw forceOrder events (from recorder/import → liquidation_events, not uvicorn)."""
    try:
        if symbols:
            sym_tuple = tuple(s.strip() for s in symbols.split(",") if s.strip())
        else:
            sym_tuple = tuple(MAJOR_NAUTILUS_SYMBOLS)
        return await fetch_liquidation_events(sym_tuple, min(limit, 500))
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


# ── Polymarket ───────────────────────────────────────────────────────────────

@app.get("/polymarket/markets")
async def polymarket_markets(q: str, limit: int = 20):
    try:
        return await search_markets(q, min(limit, 50))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Gamma API error: {e}")


class SubscribeBody(BaseModel):
    slug: str | None = None
    series: str | None = None


@app.get("/polymarket/presets")
async def polymarket_presets():
    """Configured rolling 15m markets (stable series id + current window slug)."""
    import json

    out = []
    for p in PRESET_15M_SERIES:
        series = p["series"]
        slug = slug_for_series(series)
        info = await get_token_ids(slug)
        yes_price = None
        market = await get_market_by_slug(slug)
        if market:
            prices = json.loads(market.get("outcomePrices") or "[]")
            yes_price = float(prices[0]) if prices else None
        out.append({
            **p,
            "symbol": series_symbol(series),
            "current_slug": slug,
            "yes_price": yes_price,
            "question": info["question"] if info else None,
        })
    return out


# ── Simulation ───────────────────────────────────────────────────────────────

@app.get("/simulation/status")
async def simulation_status():
    try:
        from simulation import config as sim_cfg

        status = await db.get_simulation_status()
        status["enabled"] = sim_cfg.is_enabled()
        status["thresholds"] = sim_cfg.thresholds()
        status["min_usd"] = sim_cfg.min_usd()
        status["min_shares"] = sim_cfg.min_shares_default()
        return status
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/simulation/bets")
async def simulation_bets(limit: int = 100):
    try:
        return await db.get_simulation_bets(min(limit, 500))
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/simulation/reconcile")
async def simulation_reconcile():
    """Re-scan 15m liq bars vs thresholds (fixes engine/chart total drift)."""
    if _simulation is None:
        raise HTTPException(status_code=503, detail="Simulation not running")
    try:
        events = await _simulation.reconcile_all_bars()
        events += await _simulation.reconcile_settlements()
        if events:
            asyncio.create_task(_fanout_messages(events))
        return {"events": len(events), "ok": True}
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/simulation/reset")
async def simulation_reset():
    """Wipe paper-sim bet/cycle history and reset in-memory engine state."""
    if _simulation is None:
        raise HTTPException(status_code=503, detail="Simulation not running")
    try:
        counts = await _simulation.reset_history()
        return {"ok": True, **counts}
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


class SimulationConfigBody(BaseModel):
    thresholds: dict[str, float] | None = None
    min_usd: float | None = None
    enabled: bool | None = None


@app.post("/simulation/config")
async def simulation_config(body: SimulationConfigBody):
    import json
    import os

    if body.thresholds is not None:
        os.environ["SIM_THRESHOLDS_JSON"] = json.dumps(body.thresholds)
    if body.min_usd is not None:
        os.environ["SIM_MIN_USD"] = str(body.min_usd)
    if body.enabled is not None:
        os.environ["SIM_ENABLED"] = "true" if body.enabled else "false"
    if _simulation is not None:
        from simulation import config as sim_cfg

        _simulation._thresholds = sim_cfg.thresholds()
        _simulation._min_usd = sim_cfg.min_usd()
    return await simulation_status()


# ── Live trading ─────────────────────────────────────────────────────────────

@app.get("/live/status")
async def live_status():
    try:
        from live import config as live_cfg
        from adapters.polymarket import orders as poly_orders

        status = await db.get_live_status()
        status["enabled"] = live_cfg.is_enabled()
        status["orders_enabled"] = poly_orders.can_place_orders()
        status["credentials_configured"] = poly_orders.credentials_configured()
        status["thresholds"] = live_cfg.thresholds()
        status["assets"] = list(live_cfg.active_assets().keys())
        status["min_usd"] = live_cfg.min_usd()
        status["min_shares"] = live_cfg.min_shares_default()
        return status
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/live/bets")
async def live_bets(limit: int = 100):
    try:
        return await db.get_live_bets(min(limit, 500))
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/live/reconcile")
async def live_reconcile(reset_signaled: bool = False):
    if _live is None:
        raise HTTPException(status_code=503, detail="Live trading not running")
    try:
        if reset_signaled:
            _live.clear_signal_cache()
        events = await _live.reconcile_all_bars()
        events += await _live.reconcile_settlements()
        if events:
            asyncio.create_task(_fanout_messages(events))
        return {
            "events": len(events),
            "ok": True,
            "reset_signaled": reset_signaled,
            "event_types": [e.get("type") for e in events],
        }
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


class LiveConfigBody(BaseModel):
    thresholds: dict[str, float] | None = None
    min_usd: float | None = None
    enabled: bool | None = None


@app.post("/live/config")
async def live_config(body: LiveConfigBody):
    import json
    import os

    if body.thresholds is not None:
        os.environ["LIVE_THRESHOLDS_JSON"] = json.dumps(body.thresholds)
    if body.min_usd is not None:
        os.environ["LIVE_MIN_USD"] = str(body.min_usd)
    if body.enabled is not None:
        os.environ["LIVE_ENABLED"] = "true" if body.enabled else "false"
    if _live is not None:
        from live import config as live_cfg

        _live._assets = live_cfg.active_assets()
        _live._thresholds = live_cfg.thresholds()
        _live._min_usd = live_cfg.min_usd()
    return await live_status()


@app.post("/polymarket/subscribe")
async def polymarket_subscribe(body: SubscribeBody):
    try:
        from node import get_polymarket_actor
        actor = get_polymarket_actor()
        if actor is None:
            raise HTTPException(status_code=503, detail="Polymarket actor not running")
        if body.series:
            actor.subscribe_series(body.series)
            slug = slug_for_series(body.series)
            return {
                "status": "queued",
                "series": body.series,
                "symbol": series_symbol(body.series),
                "slug": slug,
            }
        if body.slug:
            actor.subscribe_slug(body.slug)
            return {"status": "queued", "slug": body.slug}
        raise HTTPException(status_code=400, detail="Provide slug or series")
    except ImportError:
        raise HTTPException(status_code=503, detail="Nautilus not available")


# ── WebSocket ─────────────────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, symbols: Optional[str] = None):
    await websocket.accept()
    symbol_filter = set(symbols.split(",")) if symbols else None
    _clients.append((websocket, symbol_filter))
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        _clients[:] = [(ws, sf) for ws, sf in _clients if ws is not websocket]


# ── Broadcast + persist loop ──────────────────────────────────────────────────

async def _fanout_messages(messages: list[dict]) -> None:
    """Push messages to all WS clients (sim/live events skip symbol filters)."""
    dead: list[tuple[WebSocket, Optional[set[str]]]] = []
    for out in messages:
        payload = json.dumps(out)
        mtype = str(out.get("type", ""))
        is_global = mtype.startswith("simulation") or mtype.startswith("live")
        symbol = out.get("symbol") or out.get("binance_symbol")
        for ws, symbol_filter in _clients:
            if (
                not is_global
                and symbol_filter
                and symbol
                and symbol not in symbol_filter
            ):
                continue
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append((ws, symbol_filter))
    for item in dead:
        try:
            _clients.remove(item)
        except ValueError:
            pass


async def _broadcast_loop() -> None:
    loop = asyncio.get_running_loop()

    while True:
        try:
            msg = await loop.run_in_executor(None, _blocking_get)
        except Exception:
            continue

        # Persist to PostgreSQL
        if msg.get("type") == "bar":
            asyncio.create_task(_persist_bar(msg))
        elif msg.get("type") == "liquidation":
            asyncio.create_task(_persist_liquidation(msg))

        sim_events: list[dict] = []
        if _simulation is not None:
            try:
                sim_events = await _simulation.on_message(msg)
            except Exception as e:
                print(f"[warn] simulation error: {e}")

        live_events: list[dict] = []
        if _live is not None:
            try:
                live_events = await _live.on_message(msg)
            except Exception as e:
                print(f"[warn] live trading error: {e}")

        if not _clients and not sim_events and not live_events:
            continue

        outbound: list[dict] = []
        if msg.get("type") == "liquidation":
            outbound.append({k: v for k, v in msg.items() if not k.startswith("_")})
        else:
            outbound.append(msg)
        outbound.extend(sim_events)
        outbound.extend(live_events)

        await _fanout_messages(outbound)


def _persist_liquidation_events_to_db_enabled() -> bool:
    v = os.environ.get("PERSIST_LIQUIDATION_EVENTS_TO_DB", "0").lower()
    return v in ("1", "true", "yes", "on")


async def _persist_liquidation(msg: dict) -> None:
    try:
        payload = msg.get("_payload")
        if payload is not None and _persist_liquidation_events_to_db_enabled():
            from liquidations import force_order_trade_id

            trade_id = force_order_trade_id(payload)
            if await db.insert_liquidation_event(trade_id, payload):
                await db.maybe_prune_liquidation_events()

        updates = msg.get("_updates") or []
        for u in updates:
            await db.add_liquidation_delta(
                symbol=u["symbol"],
                interval=u["interval"],
                time=u["time"],
                long_delta=u["long_delta"],
                short_delta=u["short_delta"],
            )
    except Exception as e:
        print(f"[warn] liquidation persist failed: {e}")


async def _liquidation_events_retention_loop() -> None:
    while True:
        await asyncio.sleep(3600)
        try:
            await db.prune_liquidation_events()
        except Exception as e:
            print(f"[warn] liquidation_events retention failed: {e}")


async def _persist_bar(msg: dict) -> None:
    try:
        interval = msg["interval"]
        bar_time = msg.get("time")
        if bar_time is None:
            bar_time = bar_open_time_ns(int(msg["ts"]), interval)
        await db.upsert_bar(
            symbol=msg["symbol"],
            interval=interval,
            bar={
                "time": int(bar_time),
                "open": float(msg["open"]),
                "high": float(msg["high"]),
                "low": float(msg["low"]),
                "close": float(msg["close"]),
                "volume": float(msg["volume"]),
            },
        )
    except Exception as e:
        print(f"[warn] bar persist failed: {e}")


def _blocking_get() -> dict:
    return data_queue.get(timeout=1)
