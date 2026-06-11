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
        cleared = await db.clear_paper_run_history()
        if cleared["events"] or cleared["equity_points"]:
            print(
                "[paper] cleared prior run history: "
                f"{cleared['events']} events, {cleared['equity_points']} equity points"
            )
    except Exception as e:
        print(f"[warn] paper run history clear failed: {e}")

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
    asyncio.create_task(_liquidation_verdict_retention_loop())

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


def _liq_verdict_filters(
    symbols: Optional[str],
    sides: Optional[str],
) -> tuple[tuple[str, ...], list[str], frozenset[str]]:
    from recorders.liq_post_event_service import NAUTILUS_TO_COIN
    from recorders.liq_post_event_service import parse_sides_param
    from recorders.liq_post_event_service import parse_symbols_param

    sym_tuple = parse_symbols_param(symbols)
    coins = [NAUTILUS_TO_COIN.get(sym, sym.split("USDT")[0]) for sym in sym_tuple]
    return sym_tuple, coins, parse_sides_param(sides)


@app.get("/liq-verdict/stats")
async def liq_verdict_stats_endpoint(
    symbols: Optional[str] = None,
    min_notional: float = 0.0,
    sides: Optional[str] = None,
):
    """Cumulative verdict aggregates over all persisted rows (not list window)."""
    try:
        _sym_tuple, coins, side_set = _liq_verdict_filters(symbols, sides)
        if not _persist_liquidation_verdicts_to_db_enabled():
            return {
                "count": 0,
                "completed": 0,
                "expired": 0,
                "recovery_rate": 0.0,
                "avg_dominance": 0.0,
                "avg_time": 0.0,
                "avg_area": 0.0,
            }
        return await db.get_liquidation_verdict_stats(
            coins=coins,
            min_notional=min_notional,
            sides=side_set,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/liq-verdict/recent")
async def liq_verdict_recent_endpoint(
    symbols: Optional[str] = None,
    min_notional: float = 0.0,
    sides: Optional[str] = None,
    limit: Optional[int] = 0,
    liq_move_threshold_pct: float = 0.2,
    recovery_move_threshold_pct: float = 0.2,
    max_observation_sec: int = 450,
):
    """Post-liquidation verdict rows (DB-persisted live + catalog backfill)."""
    try:
        from recorders.liq_verdict_service import build_verdict_response
        from recorders.liq_verdict_service import merge_verdict_rows

        sym_tuple, coins, side_set = _liq_verdict_filters(symbols, sides)
        row_limit = None if limit is None or int(limit) <= 0 else max(1, int(limit))
        catalog_limit = row_limit if row_limit is not None else 500

        if _persist_liquidation_verdicts_to_db_enabled():
            persisted = await db.get_liquidation_verdict_events(
                coins=coins,
                min_notional=min_notional,
                sides=side_set,
                limit=row_limit,
            )
            return {"verdicts": persisted}

        loop = asyncio.get_running_loop()
        catalog_resp = await loop.run_in_executor(
            None,
            lambda: build_verdict_response(
                symbols=symbols,
                min_notional=min_notional,
                sides=sides,
                limit=catalog_limit,
                liq_move_threshold_pct=liq_move_threshold_pct,
                recovery_move_threshold_pct=recovery_move_threshold_pct,
                max_observation_sec=max_observation_sec,
            ),
        )
        return {
            "verdicts": merge_verdict_rows(
                [],
                catalog_resp.get("verdicts") or [],
                limit=row_limit,
            )
        }
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


# ── Paper-trade monitoring ────────────────────────────────────────────────────

@app.get("/paper/equity")
async def paper_equity_endpoint(
    since: Optional[int] = None,
    limit: int = 5000,
):
    """Equity/PnL curve points (ascending) for the paper-trade dashboard."""
    try:
        return await db.get_paper_equity(since, min(limit, 20_000))
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/paper/events")
async def paper_events_endpoint(
    limit: int = 200,
    run_started_ts: Optional[int] = None,
):
    """Recent paper-trade order/position events (newest first)."""
    try:
        return await db.get_paper_events(min(limit, 1000), run_started_ts=run_started_ts)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


# ── Polymarket ───────────────────────────────────────────────────────────────

@app.get("/polymarket/markets")
async def polymarket_markets(q: str, limit: int = 20):
    try:
        return await search_markets(q, min(limit, 50))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Gamma API error: {e}")


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
        elif msg.get("type") == "paper_snapshot":
            _maybe_persist_paper_snapshot(msg)
        elif msg.get("type") == "paper_event":
            asyncio.create_task(_persist_paper_event(msg))
        elif msg.get("type") == "liquidation_verdict":
            verdict = msg.get("verdict") or {}
            if verdict.get("event_id"):
                asyncio.create_task(_persist_liquidation_verdict(verdict))

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


def _persist_liquidation_verdicts_to_db_enabled() -> bool:
    v = os.environ.get("PERSIST_LIQUIDATION_VERDICTS_TO_DB", "1").lower()
    return v in ("1", "true", "yes", "on")


async def _persist_liquidation(msg: dict) -> None:
    try:
        from liquidations import liquidation_db_trade_id_and_payload, record_liquidation

        updates = msg.get("_updates")
        if updates is None:
            # Child process updated its own _buckets; replay here so parent-process
            # in-memory state (read by fetch_liquidation_bars) stays current.
            updates = record_liquidation(
                msg["symbol"],
                msg["side"],
                msg["notional"],
                msg["time"] * 1000,
            )

        if _persist_liquidation_events_to_db_enabled():
            resolved = liquidation_db_trade_id_and_payload(msg)
            if resolved is not None:
                trade_id, payload = resolved
                if await db.insert_liquidation_event(trade_id, payload):
                    await db.maybe_prune_liquidation_events()
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


async def _persist_liquidation_verdict(verdict: dict) -> None:
    if not _persist_liquidation_verdicts_to_db_enabled():
        return
    try:
        await db.insert_liquidation_verdict(verdict)
    except Exception as e:
        print(f"[warn] liquidation verdict persist failed: {e}")


async def _liquidation_verdict_retention_loop() -> None:
    from db import _prune_liquidation_verdicts_enabled

    if not _prune_liquidation_verdicts_enabled():
        return
    while True:
        await asyncio.sleep(3600)
        try:
            await db.prune_liquidation_verdicts()
        except Exception as e:
            print(f"[warn] liquidation_verdict_events retention failed: {e}")


# Downsample equity-curve persistence (snapshots stream every ~2s; keep the DB
# curve coarser). WS broadcast is unaffected — clients still see every snapshot.
_PAPER_EQUITY_PERSIST_INTERVAL_NS = int(
    float(os.environ.get("PAPER_EQUITY_PERSIST_INTERVAL_SEC", "10")) * 1e9
)
_last_paper_equity_persist_ns = 0


def _maybe_persist_paper_snapshot(msg: dict) -> None:
    global _last_paper_equity_persist_ns
    if msg.get("account") is None:
        return  # account not ready — nothing to chart yet
    ts = int(msg.get("ts") or 0)
    if ts - _last_paper_equity_persist_ns < _PAPER_EQUITY_PERSIST_INTERVAL_NS:
        return
    _last_paper_equity_persist_ns = ts
    asyncio.create_task(_persist_paper_snapshot(msg))


async def _persist_paper_snapshot(msg: dict) -> None:
    try:
        await db.insert_paper_snapshot(msg)
    except Exception as e:
        print(f"[warn] paper_snapshot persist failed: {e}")


async def _persist_paper_event(msg: dict) -> None:
    try:
        await db.insert_paper_event(msg)
    except Exception as e:
        print(f"[warn] paper_event persist failed: {e}")


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
