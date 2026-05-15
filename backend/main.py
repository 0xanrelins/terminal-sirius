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
from adapters.polymarket.gamma import search_markets
from klines import fetch_klines

data_queue: queue.Queue = queue.Queue(maxsize=10_000)
_clients: list[tuple[WebSocket, Optional[set[str]]]] = []


@asynccontextmanager
async def lifespan(app: FastAPI):
    # DB
    await db.init()

    # Nautilus (daemon thread — safe to skip if nautilus_trader not installed yet)
    try:
        from node import run_node_in_thread
        run_node_in_thread(data_queue)
    except ImportError as e:
        print(f"[warn] Nautilus not available, market data disabled: {e}")

    asyncio.create_task(_broadcast_loop())

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
async def klines_endpoint(symbol: str, interval: str = "1m", limit: int = 500):
    try:
        return await fetch_klines(symbol, interval, min(limit, 1000))
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
    slug: str


@app.post("/polymarket/subscribe")
async def polymarket_subscribe(body: SubscribeBody):
    try:
        from node import get_polymarket_actor
        actor = get_polymarket_actor()
        if actor is None:
            raise HTTPException(status_code=503, detail="Polymarket actor not running")
        actor.subscribe_slug(body.slug)
        return {"status": "queued", "slug": body.slug}
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

async def _broadcast_loop() -> None:
    loop = asyncio.get_running_loop()
    dead: list[tuple[WebSocket, Optional[set[str]]]] = []

    while True:
        try:
            msg = await loop.run_in_executor(None, _blocking_get)
        except Exception:
            continue

        # Persist completed bars to PostgreSQL
        if msg.get("type") == "bar":
            asyncio.create_task(_persist_bar(msg))

        if not _clients:
            continue

        payload = json.dumps(msg)
        symbol = msg.get("symbol")

        for ws, symbol_filter in _clients:
            if symbol_filter and symbol not in symbol_filter:
                continue
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append((ws, symbol_filter))

        if dead:
            for item in dead:
                try:
                    _clients.remove(item)
                except ValueError:
                    pass
            dead.clear()


async def _persist_bar(msg: dict) -> None:
    try:
        await db.upsert_bar(
            symbol=msg["symbol"],
            interval=msg["interval"],
            bar={
                "time": msg["ts"] // 1_000_000_000,   # nanoseconds → seconds
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
