"""
FastAPI WebSocket bridge.

Startup sequence:
  1. Load .env
  2. Init PostgreSQL pool + run schema migration
  3. Start Nautilus node in daemon thread (Binance + Polymarket DataClient + strategies)
  4. Start broadcast loop (WS fan-out + bar persistence)

Endpoints:
  GET  /klines                        — historical OHLCV (DB-first)
  GET  /polymarket/markets?q=…        — search Polymarket markets
  POST /polymarket/subscribe          — add a slug to live stream at runtime
  WS   /ws?symbols=…                  — live feed (trade / quote / bar / polymarket)

Liquidation: single writer via `liquidation_stream` (or Nautilus LiquidationActor) →
`liquidation_bars` + optional `liquidation_events` / watchlist when
PERSIST_LIQUIDATION_EVENTS_TO_DB=1 (default). External recorder scripts are not used.
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

load_dotenv(override=True)
load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=True)
sys.path.insert(0, ".")

import nautilus_env

nautilus_env.prepare_polymarket_env()

import db
from adapters.polymarket.gamma import get_market_by_slug, get_token_ids, search_markets
from adapters.polymarket.rolling import PRESET_15M_SERIES, series_symbol, slug_for_series
from bar_time import bar_open_time_ns
from klines import fetch_klines
from liquidations import (
    MAJOR_NAUTILUS_SYMBOLS,
    binance_to_nautilus,
    fetch_liquidation_bars,
    fetch_liquidation_events,
)
from liquidation_stream import run_liquidation_stream
data_queue: queue.Queue = queue.Queue(maxsize=10_000)
strategy_event_queue: queue.Queue = queue.Queue(maxsize=10_000)
_clients: list[tuple[WebSocket, Optional[set[str]]]] = []


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init()

    from engines.poly_sync import load_restore_state
    from nautilus_bridge.strategy_runtime import set_main_loop, set_runtime
    from strategies.liq_poly_config import runtime_from_env

    set_main_loop(asyncio.get_running_loop())

    live_restore = await load_restore_state("live")
    sim_restore = await load_restore_state("sim")
    set_runtime("live", runtime_from_env("live", live_restore))
    set_runtime("sim", runtime_from_env("sim", sim_restore))

    from node import DEFAULT_INSTRUMENTS, run_node_in_thread

    nautilus_ok = False
    try:
        run_node_in_thread(data_queue, strategy_event_queue)
        nautilus_ok = True
    except ImportError as e:
        print(f"[warn] Nautilus not available, market data disabled: {e}")

    if not nautilus_ok:
        asyncio.create_task(run_liquidation_stream(data_queue, DEFAULT_INSTRUMENTS))
        print("[liquidations] fallback: standalone liquidation_stream (no Nautilus)")
    if _persist_liquidation_events_to_db_enabled():
        print(
            "[liquidations] single writer: stream → bars + liquidation_events "
            "(watchlist trigger)"
        )
    else:
        print(
            "[liquidations] single writer: stream → liquidation_bars + WS only "
            "(PERSIST_LIQUIDATION_EVENTS_TO_DB=0)"
        )

    asyncio.create_task(_broadcast_loop())
    asyncio.create_task(_liquidation_events_retention_loop())

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
    # Normalize Binance symbol (BTCUSDT) → Nautilus format (BTCUSDT-PERP.BINANCE)
    # DB stores all liq bars in Nautilus format (live stream uses binance_to_nautilus).
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
    """Recent major-coin liquidations from `liquidation_watchlist_events` (backend stream persist only)."""
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
        status["assets"] = list(sim_cfg.active_assets().keys())
        status["thresholds"] = sim_cfg.thresholds()
        status["min_usd"] = sim_cfg.min_usd()
        status["min_shares"] = sim_cfg.min_shares_default()
        return status
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/simulation/bets")
async def simulation_bets(limit: int = 100, assets: str | None = None):
    try:
        asset_list = (
            [a.strip().upper() for a in assets.split(",") if a.strip()]
            if assets
            else None
        )
        return await db.get_simulation_bets(min(limit, 500), asset_list)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/simulation/reconcile")
async def simulation_reconcile(reset_signaled: bool = False):
    """Re-scan 15m liq bars vs thresholds (LiqPolyStrategy)."""
    try:
        from nautilus_bridge.strategy_runtime import request_strategy_catchup

        events = await asyncio.wait_for(
            request_strategy_catchup("sim", reset_signaled), timeout=60
        )
        if events:
            asyncio.create_task(_fanout_messages(events))
        return {
            "events": len(events),
            "ok": True,
            "reset_signaled": reset_signaled,
        }
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Strategy reconcile timed out")
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/simulation/reset")
async def simulation_reset():
    """Wipe paper-sim bet/cycle history and refresh LiqPoly sim runtime."""
    try:
        from engines.poly_sync import load_restore_state
        from nautilus_bridge.strategy_runtime import set_runtime
        from strategies.liq_poly_config import runtime_from_env

        counts = await db.clear_simulation_history()
        sim_restore = await load_restore_state("sim")
        set_runtime("sim", runtime_from_env("sim", sim_restore))
        print(
            f"[simulation] history cleared "
            f"({counts['bets_deleted']} bets, {counts['cycles_deleted']} cycles)"
        )
        return {"ok": True, **counts}
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


class SimulationConfigBody(BaseModel):
    assets: list[str] | None = None
    thresholds: dict[str, float] | None = None
    min_usd: float | None = None
    enabled: bool | None = None


@app.post("/simulation/config")
async def simulation_config(body: SimulationConfigBody):
    import json
    import os

    if body.assets is not None:
        os.environ["SIM_ASSETS"] = ",".join(a.strip().upper() for a in body.assets if a.strip())
    if body.thresholds is not None:
        os.environ["SIM_THRESHOLDS_JSON"] = json.dumps(body.thresholds)
    if body.min_usd is not None:
        os.environ["SIM_MIN_USD"] = str(body.min_usd)
    if body.enabled is not None:
        os.environ["SIM_ENABLED"] = "true" if body.enabled else "false"
    from nautilus_bridge.strategy_runtime import refresh_runtime_from_env

    refresh_runtime_from_env("sim")
    return await simulation_status()


# ── Live trading ─────────────────────────────────────────────────────────────

@app.get("/live/status")
async def live_status():
    try:
        from live import config as live_cfg
        from adapters.polymarket import orders as poly_orders

        from nautilus_bridge.context import exec_client_ready, get_trading_node

        status = await db.get_live_status()
        status["enabled"] = live_cfg.is_enabled()
        status["orders_enabled"] = poly_orders.can_place_orders()
        status["credentials_configured"] = poly_orders.credentials_configured()
        status["exec_client_ready"] = exec_client_ready()
        status["trading_node_alive"] = get_trading_node() is not None
        status["orders_ready"] = (
            status["orders_enabled"] and status["exec_client_ready"]
        )
        status["thresholds"] = live_cfg.thresholds()
        status["assets"] = list(live_cfg.active_assets().keys())
        status["min_usd"] = live_cfg.min_usd()
        status["min_shares"] = live_cfg.min_shares_default()
        return status
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/live/bets")
async def live_bets(limit: int = 100, assets: str | None = None):
    try:
        asset_list = (
            [a.strip().upper() for a in assets.split(",") if a.strip()]
            if assets
            else None
        )
        return await db.get_live_bets(min(limit, 500), asset_list)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/live/reconcile")
async def live_reconcile(reset_signaled: bool = False):
    try:
        from nautilus_bridge.strategy_runtime import request_strategy_catchup

        events = await asyncio.wait_for(
            request_strategy_catchup("live", reset_signaled), timeout=60
        )
        if events:
            asyncio.create_task(_fanout_messages(events))
        return {
            "events": len(events),
            "ok": True,
            "reset_signaled": reset_signaled,
            "event_types": [e.get("type") for e in events],
        }
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Strategy reconcile timed out")
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


class LiveConfigBody(BaseModel):
    assets: list[str] | None = None
    thresholds: dict[str, float] | None = None
    min_usd: float | None = None
    enabled: bool | None = None


@app.post("/live/config")
async def live_config(body: LiveConfigBody):
    import json
    import os

    if body.assets is not None:
        os.environ["LIVE_ASSETS"] = ",".join(a.strip().upper() for a in body.assets if a.strip())
    if body.thresholds is not None:
        os.environ["LIVE_THRESHOLDS_JSON"] = json.dumps(body.thresholds)
    if body.min_usd is not None:
        os.environ["LIVE_MIN_USD"] = str(body.min_usd)
    if body.enabled is not None:
        os.environ["LIVE_ENABLED"] = "true" if body.enabled else "false"
    from nautilus_bridge.strategy_runtime import refresh_runtime_from_env

    refresh_runtime_from_env("live")
    return await live_status()


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
        strategy_batch = await loop.run_in_executor(None, _drain_strategy_queue)
        if strategy_batch:
            await _fanout_messages(strategy_batch)

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


def _drain_strategy_queue() -> list[dict]:
    out: list[dict] = []
    while True:
        try:
            out.append(strategy_event_queue.get_nowait())
        except queue.Empty:
            break
    return out


def _blocking_get() -> dict:
    return data_queue.get(timeout=1)
