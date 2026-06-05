"""
FastAPI WebSocket bridge.

Startup sequence:
  1. Load .env
  2. Init PostgreSQL pool + run schema migration
  3. Start Nautilus node in daemon thread (Binance + Polymarket DataClient)
  4. Start broadcast loop (WS fan-out + bar persistence)

Endpoints:
  GET  /klines                        — historical OHLCV (DB-first)
  GET  /polymarket/markets?q=…        — search Polymarket markets
  POST /polymarket/subscribe          — add a slug to live stream at runtime
  WS   /ws?symbols=…                  — live feed (trade / quote / bar / polymarket)

Liquidation: native ``BinanceFuturesLiquidation`` on the Nautilus node →
``LiquidationUiBridgeActor`` → `liquidation_bars` + optional `liquidation_events`.
"""
import asyncio
import json
import multiprocessing
import os
import sys
from contextlib import asynccontextmanager
from typing import Optional

from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

load_dotenv(override=True)
load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=True)
sys.path.insert(0, ".")

import nautilus_env

nautilus_env.prepare_polymarket_env()

import db
from adapters.polymarket.gamma import get_token_ids, search_markets
from adapters.polymarket.rolling import PRESET_15M_SERIES, series_symbol, slug_for_series
from bar_time import bar_open_time_ns
from klines import fetch_klines
from liquidations import (
    MAJOR_NAUTILUS_SYMBOLS,
    binance_to_nautilus,
    fetch_liquidation_bars,
    fetch_liquidation_events,
)
from node import (
    create_data_queue,
    run_node_in_process,
)

data_queue = create_data_queue()
_node_process: multiprocessing.Process | None = None
_clients: list[tuple[WebSocket, Optional[set[str]]]] = []


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _node_process
    await db.init()

    try:
        _node_process = run_node_in_process(data_queue)
    except ImportError as e:
        print(f"[warn] Nautilus not available, market data disabled: {e}")
        _node_process = None

    if _node_process is not None:
        if _persist_liquidation_events_to_db_enabled():
            print("[liquidations] native BinanceFuturesLiquidation → UI/DB via node bridge")
        else:
            print(
                "[liquidations] native BinanceFuturesLiquidation → liquidation_bars + WS only "
                "(PERSIST_LIQUIDATION_EVENTS_TO_DB=0)"
            )

    asyncio.create_task(_broadcast_loop())
    asyncio.create_task(_liquidation_events_retention_loop())

    yield

    if _node_process is not None and _node_process.is_alive():
        _node_process.terminate()
        _node_process.join(timeout=8)

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
    if not symbol.endswith(".BINANCE"):
        symbol = binance_to_nautilus(symbol)
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
    """Recent major-coin liquidations from `liquidation_watchlist_events`."""
    try:
        if symbols:
            sym_tuple = tuple(s.strip() for s in symbols.split(",") if s.strip())
        else:
            sym_tuple = tuple(MAJOR_NAUTILUS_SYMBOLS)
        return await fetch_liquidation_events(sym_tuple, min(limit, 500))
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/liq-post-event/sessions")
async def liq_post_event_sessions_endpoint(
    symbols: Optional[str] = None,
    interval: str = "30s",
    min_notional: float = 0.0,
    sides: Optional[str] = None,
    limit: Optional[int] = None,
):
    """Post-liquidation 30m % performance sessions from ParquetDataCatalog."""
    if interval not in ("30s", "1s", "5s"):
        raise HTTPException(status_code=400, detail="interval must be 30s")
    try:
        from recorders.liq_post_event_service import build_sessions_response

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            lambda: build_sessions_response(
                symbols=symbols,
                min_notional=min_notional,
                sides=sides,
                interval=interval,
                limit=limit,
            ),
        )
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
    out = []
    for p in PRESET_15M_SERIES:
        series = p["series"]
        slug = slug_for_series(series)
        info = await get_token_ids(slug)
        from adapters.polymarket.quote_registry import get_slug_quotes

        book = get_slug_quotes(slug)
        yes_price = book.yes_mid if book else None
        out.append({
            **p,
            "symbol": series_symbol(series),
            "current_slug": slug,
            "yes_price": yes_price,
            "question": info["question"] if info else None,
        })
    return out


@app.post("/polymarket/subscribe")
async def polymarket_subscribe(body: SubscribeBody):
    try:
        from node import get_polymarket_quote_bridge

        bridge = get_polymarket_quote_bridge()
        if bridge is None:
            raise HTTPException(
                status_code=503,
                detail="Polymarket quote bridge not running (enable POLYMARKET_DATA_ENABLED)",
            )
        if body.series:
            bridge.subscribe_series(body.series)
            slug = slug_for_series(body.series)
            return {
                "status": "queued",
                "series": body.series,
                "symbol": series_symbol(body.series),
                "slug": slug,
            }
        if body.slug:
            bridge.subscribe_slug(body.slug)
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
    """Push messages to all WS clients."""
    dead: list[tuple[WebSocket, Optional[set[str]]]] = []
    for out in messages:
        payload = json.dumps(out)
        symbol = out.get("symbol") or out.get("binance_symbol")
        for ws, symbol_filter in _clients:
            if symbol_filter and symbol and symbol not in symbol_filter:
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

        if msg.get("type") == "bar":
            asyncio.create_task(_persist_bar(msg))
        elif msg.get("type") == "liquidation":
            asyncio.create_task(_persist_liquidation(msg))

        if not _clients:
            continue

        if msg.get("type") == "liquidation":
            outbound = [{k: v for k, v in msg.items() if not k.startswith("_")}]
        else:
            outbound = [msg]
        await _fanout_messages(outbound)


def _persist_liquidation_events_to_db_enabled() -> bool:
    v = os.environ.get("PERSIST_LIQUIDATION_EVENTS_TO_DB", "1").lower()
    return v in ("1", "true", "yes", "on")


async def _persist_liquidation(msg: dict) -> None:
    try:
        from liquidations import record_liquidation

        # Child process updated its own _buckets; replay here so parent-process
        # in-memory state (read by fetch_liquidation_bars) stays current.
        record_liquidation(msg["symbol"], msg["side"], msg["notional"], msg["time"] * 1000)

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
