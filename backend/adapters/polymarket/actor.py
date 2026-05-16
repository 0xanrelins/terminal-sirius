"""
PolymarketActor — Nautilus Actor that bridges Polymarket CLOB WebSocket
into the shared data_queue consumed by the FastAPI WebSocket bridge.

Architecture:
  - Inherits nautilus_trader.common.actor.Actor so it participates in the
    Nautilus TradingNode lifecycle (start/stop/dispose).
  - Uses asyncio.create_task() to run a non-blocking WS stream alongside the
    rest of the Nautilus event loop.
  - When a new slug is added at runtime the stream task is cancelled and
    restarted with the updated token_id set.

Symbol convention: "{slug}.POLYMARKET"  (e.g. "will-trump-win-2024.POLYMARKET")
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

CLOB_WS = "wss://ws-subscriptions-clob.polymarket.com/ws/market"


class PolymarketActorConfig(ActorConfig, frozen=True):
    initial_slugs: tuple[str, ...] = ()


class PolymarketActor(Actor):
    def __init__(self, config: PolymarketActorConfig, data_queue: queue.Queue) -> None:
        super().__init__(config)
        self._queue = data_queue
        self._initial_slugs: list[str] = list(config.initial_slugs)

        # slug → {yes: tokenId, no: tokenId, question: str, volume: float}
        self._meta: dict[str, dict] = {}
        # YES tokenId → slug
        self._token_to_slug: dict[str, str] = {}

        self._stream_task: Optional[asyncio.Task] = None

    def _enqueue(self, msg: dict) -> None:
        try:
            self._queue.put_nowait(msg)
        except queue.Full:
            pass

    # ── Nautilus lifecycle ────────────────────────────────────────────────────

    def on_start(self) -> None:
        if self._initial_slugs:
            asyncio.create_task(self._bootstrap(self._initial_slugs))

    def on_stop(self) -> None:
        self._cancel_stream()

    def on_dispose(self) -> None:
        self._cancel_stream()

    # ── Public API (called from FastAPI endpoint) ─────────────────────────────

    def subscribe_slug(self, slug: str) -> None:
        """Dynamically add a market slug at runtime (thread-safe via asyncio)."""
        asyncio.create_task(self._add_slug(slug))

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _bootstrap(self, slugs: list[str]) -> None:
        await self._resolve_slugs(slugs)
        self._restart_stream()

    async def _add_slug(self, slug: str) -> None:
        if slug in self._meta:
            return  # already subscribed
        await self._resolve_slugs([slug])
        self._restart_stream()

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
                        if isinstance(events, list):
                            for ev in events:
                                self._dispatch(ev)
            except asyncio.CancelledError:
                return
            except Exception as e:
                self.log.warning(f"Polymarket WS error ({e!r}), reconnecting in {backoff}s")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    def _dispatch(self, event: dict) -> None:
        asset_id = event.get("asset_id")
        slug = self._token_to_slug.get(asset_id)
        if not slug:
            return

        symbol = _slug_to_symbol(slug)
        question = self._meta[slug]["question"]
        ts_ns = int(time.time() * 1e9)

        etype = event.get("event_type")

        if etype == "price_change":
            self._enqueue({
                "type": "polymarket",
                "symbol": symbol,
                "slug": slug,
                "question": question,
                "yes_price": float(event.get("price", 0)),
                "ts": ts_ns,
            })

        elif etype == "book":
            bids = event.get("bids", [])
            asks = event.get("asks", [])
            best_bid = float(bids[0][0]) if bids else 0.0
            best_ask = float(asks[0][0]) if asks else 0.0
            mid = (best_bid + best_ask) / 2 if best_bid and best_ask else (best_bid or best_ask)
            self._enqueue({
                "type": "polymarket",
                "symbol": symbol,
                "slug": slug,
                "question": question,
                "yes_price": round(mid, 4),
                "bid": best_bid,
                "ask": best_ask,
                "ts": ts_ns,
            })


def _slug_to_symbol(slug: str) -> str:
    return f"{slug}.POLYMARKET"
