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
"""
import asyncio
import json
import os
import queue
import sys
from contextlib import asynccontextmanager
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

load_dotenv()
sys.path.insert(0, ".")

import db
from adapters.polymarket.gamma import get_market_by_slug, get_token_ids, search_markets
from adapters.polymarket.rolling import PRESET_15M_SERIES, series_symbol, slug_for_series
from bar_time import bar_open_time_ns
from klines import fetch_klines
from liquidations import fetch_liquidation_bars
from liquidation_stream import run_liquidation_stream
from simulation.engine import SimulationEngine

data_queue: queue.Queue = queue.Queue(maxsize=10_000)
_clients: list[tuple[WebSocket, Optional[set[str]]]] = []
_simulation: SimulationEngine | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _simulation
    # DB
    await db.init()

    _simulation = SimulationEngine()
    await _simulation.load_state()
    startup_sim = await _simulation.sync_bars_from_store()

    # Nautilus (daemon thread — safe to skip if nautilus_trader not installed yet)
    from node import DEFAULT_INSTRUMENTS, run_node_in_thread

    try:
        run_node_in_thread(data_queue)
    except ImportError as e:
        print(f"[warn] Nautilus not available, market data disabled: {e}")

    asyncio.create_task(run_liquidation_stream(data_queue, DEFAULT_INSTRUMENTS))

    asyncio.create_task(_broadcast_loop())
    if startup_sim:
        asyncio.create_task(_fanout_messages(startup_sim))

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
    symbol: str, interval: str = "1m", limit: int = 500, before: Optional[int] = None
):
    try:
        return await fetch_liquidation_bars(symbol, interval, min(limit, 1000), before=before)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Upstream error: {e}")


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
    """Push messages to all WS clients (simulation events skip symbol filters)."""
    dead: list[tuple[WebSocket, Optional[set[str]]]] = []
    for out in messages:
        payload = json.dumps(out)
        is_sim = str(out.get("type", "")).startswith("simulation")
        symbol = out.get("symbol") or out.get("binance_symbol")
        for ws, symbol_filter in _clients:
            if (
                not is_sim
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

        if not _clients and not sim_events:
            continue

        outbound: list[dict] = []
        if msg.get("type") == "liquidation":
            outbound.append({k: v for k, v in msg.items() if not k.startswith("_")})
        else:
            outbound.append(msg)
        outbound.extend(sim_events)

        await _fanout_messages(outbound)


async def _persist_liquidation(msg: dict) -> None:
    try:
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
