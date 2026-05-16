"""
PolymarketActor — Nautilus Actor that bridges Polymarket CLOB WebSocket
into the shared data_queue consumed by the FastAPI WebSocket bridge.

Architecture:
  - Inherits nautilus_trader.common.actor.Actor so it participates in the
    Nautilus TradingNode lifecycle (start/stop/dispose).
  - Uses asyncio.create_task() to run a non-blocking WS stream alongside the
    rest of the Nautilus event loop.
  - Rolling 15m series (e.g. btc-updown-15m) auto-rotate to the current window slug.

Symbol convention:
  - Static slug:   "{slug}.POLYMARKET"
  - Rolling series: "{series}.POLYMARKET"  (stable; backend maps to current slug)
"""
import asyncio
import json
import queue
import time
from typing import Optional

import websockets

from nautilus_trader.common.actor import Actor
from nautilus_trader.config import ActorConfig

from adapters.polymarket.gamma import get_token_ids
from adapters.polymarket.rolling import series_symbol, slug_for_series

CLOB_WS = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
ROTATION_POLL_SEC = 20


class PolymarketActorConfig(ActorConfig, frozen=True):
    initial_slugs: tuple[str, ...] = ()
    initial_series: tuple[str, ...] = ()


class PolymarketActor(Actor):
    def __init__(self, config: PolymarketActorConfig, data_queue: queue.Queue) -> None:
        super().__init__(config)
        self._queue = data_queue
        self._initial_slugs: list[str] = list(config.initial_slugs)
        self._initial_series: list[str] = list(config.initial_series)

        # slug → {yes: tokenId, no: tokenId, question: str, volume: float}
        self._meta: dict[str, dict] = {}
        # YES/Up tokenId → slug
        self._token_to_slug: dict[str, str] = {}
        # series → current window slug
        self._series_slugs: dict[str, str] = {}
        # slug subscribed only via a series (eligible for cleanup on rotate)
        self._slug_series: dict[str, str] = {}

        self._stream_task: Optional[asyncio.Task] = None
        self._rotation_task: Optional[asyncio.Task] = None

    def _enqueue(self, msg: dict) -> None:
        try:
            self._queue.put_nowait(msg)
        except queue.Full:
            pass

    # ── Nautilus lifecycle ────────────────────────────────────────────────────

    def on_start(self) -> None:
        if self._initial_slugs or self._initial_series:
            asyncio.create_task(self._bootstrap())

    def on_stop(self) -> None:
        self._cancel_stream()
        if self._rotation_task and not self._rotation_task.done():
            self._rotation_task.cancel()

    def on_dispose(self) -> None:
        self.on_stop()

    # ── Public API (called from FastAPI endpoint) ─────────────────────────────

    def subscribe_slug(self, slug: str) -> None:
        """Dynamically add a market slug at runtime (thread-safe via asyncio)."""
        asyncio.create_task(self._add_slug(slug))

    def subscribe_series(self, series: str) -> None:
        """Subscribe to a rolling 15m series; rotates slug every window."""
        asyncio.create_task(self._add_series(series))

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _bootstrap(self) -> None:
        for series in self._initial_series:
            await self._add_series(series)
        await self._resolve_slugs(self._initial_slugs)
        self._restart_stream()
        self._ensure_rotation_loop()

    async def _add_slug(self, slug: str) -> None:
        if slug in self._meta:
            return
        await self._resolve_slugs([slug])
        self._restart_stream()

    async def _add_series(self, series: str) -> None:
        if series in self._series_slugs:
            return
        slug = slug_for_series(series)
        self._series_slugs[series] = slug
        await self._resolve_slugs([slug])
        self._slug_series[slug] = series
        self._restart_stream()
        self._ensure_rotation_loop()

    def _ensure_rotation_loop(self) -> None:
        if self._rotation_task is None or self._rotation_task.done():
            self._rotation_task = asyncio.create_task(self._rotation_loop())

    async def _rotation_loop(self) -> None:
        while self._series_slugs:
            try:
                await self._check_series_rotation()
            except Exception as e:
                self.log.warning(f"Polymarket rotation error: {e!r}")
            await asyncio.sleep(ROTATION_POLL_SEC)

    async def _check_series_rotation(self) -> None:
        changed = False
        for series, old_slug in list(self._series_slugs.items()):
            new_slug = slug_for_series(series)
            if new_slug == old_slug:
                continue
            self.log.info(f"Polymarket: rotating {series!r} {old_slug!r} → {new_slug!r}")
            self._drop_slug(old_slug)
            self._series_slugs[series] = new_slug
            await self._resolve_slugs([new_slug])
            self._slug_series[new_slug] = series
            changed = True
        if changed:
            self._restart_stream()

    def _drop_slug(self, slug: str) -> None:
        info = self._meta.pop(slug, None)
        if info:
            self._token_to_slug.pop(info["yes"], None)
        self._slug_series.pop(slug, None)

    async def _resolve_slugs(self, slugs: list[str]) -> None:
        for slug in slugs:
            if slug in self._meta:
                continue
            try:
                info = await get_token_ids(slug)
                if info:
                    self._meta[slug] = info
                    self._token_to_slug[info["yes"]] = slug
                    self.log.info(f"Polymarket: resolved {slug!r} → token {info['yes'][:8]}…")
                else:
                    self.log.warning(f"Polymarket: no token found for slug {slug!r}")
            except Exception as e:
                self.log.error(f"Polymarket: resolve error for {slug!r}: {e}")

    def _restart_stream(self) -> None:
        self._cancel_stream()
        token_ids = [v["yes"] for v in self._meta.values()]
        if token_ids:
            self._stream_task = asyncio.create_task(self._stream(token_ids))

    def _cancel_stream(self) -> None:
        if self._stream_task and not self._stream_task.done():
            self._stream_task.cancel()

    def _symbol_for_slug(self, slug: str) -> str:
        series = self._slug_series.get(slug)
        if series:
            return series_symbol(series)
        return _slug_to_symbol(slug)

    async def _stream(self, token_ids: list[str]) -> None:
        backoff = 1.0
        while True:
            try:
                async with websockets.connect(CLOB_WS, ping_interval=20) as ws:
                    backoff = 1.0
                    await ws.send(json.dumps({"assets_ids": token_ids, "type": "market"}))
                    self.log.info(f"Polymarket: streaming {len(token_ids)} token(s)")
                    async for raw in ws:
                        events = json.loads(raw)
                        batch = events if isinstance(events, list) else [events]
                        for ev in batch:
                            if isinstance(ev, dict):
                                self._dispatch(ev)
            except asyncio.CancelledError:
                return
            except Exception as e:
                self.log.warning(f"Polymarket WS error ({e!r}), reconnecting in {backoff}s")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    def _dispatch(self, event: dict) -> None:
        etype = event.get("event_type")
        try:
            if etype == "price_change":
                for change in event.get("price_changes") or []:
                    self._emit_from_levels(change.get("asset_id"), change)
            elif etype == "book":
                self._emit_from_book(event.get("asset_id"), event)
            elif etype == "last_trade_price":
                self._emit_from_trade(event)
        except Exception as e:
            self.log.warning(f"Polymarket: dispatch error ({etype}): {e!r}")

    @staticmethod
    def _level_price(level) -> float:
        if isinstance(level, dict):
            return float(level.get("price", 0) or 0)
        if isinstance(level, (list, tuple)) and level:
            return float(level[0])
        return 0.0

    def _slug_for_asset(self, asset_id) -> str | None:
        if asset_id is None:
            return None
        return self._token_to_slug.get(str(asset_id))

    def _emit_from_levels(self, asset_id, levels: dict) -> None:
        slug = self._slug_for_asset(asset_id)
        if not slug:
            return
        bid = float(levels.get("best_bid") or 0)
        ask = float(levels.get("best_ask") or 0)
        if bid and ask:
            mid = (bid + ask) / 2
        else:
            mid = float(levels.get("price") or 0)
        self._emit_quote(slug, mid, bid or None, ask or None)

    def _emit_from_book(self, asset_id, event: dict) -> None:
        slug = self._slug_for_asset(asset_id)
        if not slug:
            return
        bids = event.get("bids") or []
        asks = event.get("asks") or []
        best_bid = self._level_price(bids[0]) if bids else 0.0
        best_ask = self._level_price(asks[0]) if asks else 0.0
        mid = (best_bid + best_ask) / 2 if best_bid and best_ask else (best_bid or best_ask)
        self._emit_quote(slug, mid, best_bid or None, best_ask or None)

    def _emit_from_trade(self, event: dict) -> None:
        slug = self._slug_for_asset(event.get("asset_id"))
        if not slug:
            return
        price = float(event.get("price") or 0)
        self._emit_quote(slug, price)

    def _emit_quote(
        self,
        slug: str,
        yes_price: float,
        bid: float | None = None,
        ask: float | None = None,
    ) -> None:
        if yes_price <= 0:
            return
        symbol = self._symbol_for_slug(slug)
        msg: dict = {
            "type": "polymarket",
            "symbol": symbol,
            "slug": slug,
            "series": self._slug_series.get(slug),
            "question": self._meta[slug]["question"],
            "yes_price": round(yes_price, 4),
            "ts": int(time.time() * 1e9),
        }
        if bid:
            msg["bid"] = bid
        if ask:
            msg["ask"] = ask
        self._enqueue(msg)


def _slug_to_symbol(slug: str) -> str:
    return f"{slug}.POLYMARKET"
