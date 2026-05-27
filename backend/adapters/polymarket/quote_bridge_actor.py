"""
Forward Polymarket quote ticks from Nautilus DataClient into the shared FastAPI data_queue.

Forwards Nautilus PolymarketDataClient quote ticks to the UI queue (`type: polymarket`).
"""
from __future__ import annotations

import asyncio
import queue
from typing import Optional

from nautilus_trader.common.actor import Actor
from nautilus_trader.config import ActorConfig
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.identifiers import InstrumentId

from adapters.polymarket.gamma import get_token_ids
from adapters.polymarket.rolling import series_symbol, slug_for_series

ROTATION_POLL_SEC = 20


def _slug_to_symbol(slug: str) -> str:
    return f"{slug}.POLYMARKET"


def _price_as_float(p) -> float:
    if hasattr(p, "as_double"):
        return float(p.as_double())
    return float(p)


class PolymarketQuoteBridgeActorConfig(ActorConfig, frozen=True):
    initial_slugs: tuple[str, ...] = ()
    initial_series: tuple[str, ...] = ()


class PolymarketQuoteBridgeActor(Actor):
    def __init__(self, config: PolymarketQuoteBridgeActorConfig, data_queue: queue.Queue) -> None:
        super().__init__(config)
        self._queue = data_queue
        self._initial_slugs: list[str] = list(config.initial_slugs)
        self._initial_series: list[str] = list(config.initial_series)

        # series → current window slug
        self._series_slugs: dict[str, str] = {}
        # slug subscribed via a series (for rotation cleanup)
        self._slug_series: dict[str, str] = {}
        # slug → all InstrumentIds for that market (YES + NO; WS sends both)
        self._slug_to_iids: dict[str, list[InstrumentId]] = {}
        # quote subscription target (YES/Up)
        self._slug_quote_iid: dict[str, InstrumentId] = {}
        # str(InstrumentId) → display meta (quote stream only)
        self._meta_by_iid: dict[str, dict] = {}

        self._rotation_task: Optional[asyncio.Task] = None

    def _enqueue(self, msg: dict) -> None:
        try:
            self._queue.put_nowait(msg)
        except queue.Full:
            pass

    def on_start(self) -> None:
        if self._initial_slugs or self._initial_series:
            asyncio.create_task(self._bootstrap_delayed())

    def on_stop(self) -> None:
        if self._rotation_task and not self._rotation_task.done():
            self._rotation_task.cancel()

    def on_dispose(self) -> None:
        self.on_stop()

    def subscribe_slug(self, slug: str) -> None:
        asyncio.create_task(self._ensure_slug(slug, series=None))

    def subscribe_series(self, series: str) -> None:
        asyncio.create_task(self._add_series(series))

    def on_quote_tick(self, tick: QuoteTick) -> None:
        key = str(tick.instrument_id)
        meta = self._meta_by_iid.get(key)
        if not meta:
            return
        bid = _price_as_float(tick.bid_price)
        ask = _price_as_float(tick.ask_price)
        if bid > 0 and ask > 0:
            mid = (bid + ask) / 2
        else:
            mid = max(bid, ask)
        if mid <= 0:
            return
        msg: dict = {
            "type": "polymarket",
            "symbol": meta["symbol"],
            "slug": meta["slug"],
            "series": meta.get("series"),
            "question": meta["question"],
            "yes_price": round(mid, 4),
            "ts": int(tick.ts_event),
        }
        if bid > 0:
            msg["bid"] = bid
        if ask > 0:
            msg["ask"] = ask
        self._enqueue(msg)

    def _ensure_rotation_loop(self) -> None:
        if self._rotation_task is None or self._rotation_task.done():
            self._rotation_task = asyncio.create_task(self._rotation_loop())

    async def _bootstrap_delayed(self) -> None:
        # Let Polymarket DataClient finish its initial WS connection window first.
        await asyncio.sleep(5.0)
        for series in self._initial_series:
            await self._add_series(series)
        for slug in self._initial_slugs:
            await self._ensure_slug(slug, series=None)
        self._ensure_rotation_loop()

    async def _add_series(self, series: str) -> None:
        if series in self._series_slugs:
            return
        slug = slug_for_series(series)
        self._series_slugs[series] = slug
        self._slug_series[slug] = series
        await self._ensure_slug(slug, series=series)
        self._ensure_rotation_loop()

    async def _rotation_loop(self) -> None:
        while self._series_slugs:
            try:
                await self._check_series_rotation()
            except Exception as e:
                self.log.warning(f"Polymarket quote bridge rotation error: {e!r}")
            await asyncio.sleep(ROTATION_POLL_SEC)

    async def _check_series_rotation(self) -> None:
        for series, old_slug in list(self._series_slugs.items()):
            new_slug = slug_for_series(series)
            if new_slug == old_slug:
                continue
            self.log.info(f"Polymarket bridge: rotating {series!r} {old_slug!r} → {new_slug!r}")
            await self._drop_slug(old_slug)
            self._series_slugs[series] = new_slug
            self._slug_series[new_slug] = series
            await self._ensure_slug(new_slug, series=series)

    async def _drop_slug(self, slug: str) -> None:
        quote_iid = self._slug_quote_iid.pop(slug, None)
        all_iids = self._slug_to_iids.pop(slug, [])
        self._slug_series.pop(slug, None)
        if quote_iid is not None:
            self._meta_by_iid.pop(str(quote_iid), None)
        for iid in all_iids:
            try:
                self.unsubscribe_quote_ticks(iid)
            except Exception as e:
                self.log.warning(f"Polymarket bridge: unsubscribe failed for {slug!r}: {e!r}")

    def _register_instrument(self, instrument) -> InstrumentId:
        """Cache + data-client request so Polymarket WS can resolve sibling tokens."""
        iid = instrument.id
        if self.cache.instrument(iid) is None:
            self.cache.add_instrument(instrument)
        try:
            self.request_instrument(iid)
        except Exception as e:
            self.log.warning(f"Polymarket bridge: request_instrument failed for {iid}: {e!r}")
        return iid

    async def _ensure_slug(self, slug: str, *, series: str | None) -> None:
        if slug in self._slug_quote_iid:
            return
        from nautilus_trader.adapters.polymarket.loaders import PolymarketDataLoader

        try:
            info = await get_token_ids(slug)
        except Exception as e:
            self.log.error(f"Polymarket bridge: gamma error for {slug!r}: {e}")
            return
        question = (info or {}).get("question") or slug

        loaded: list[InstrumentId] = []
        quote_iid: InstrumentId | None = None
        for token_index in (0, 1):
            try:
                loader = await PolymarketDataLoader.from_market_slug(
                    slug, token_index=token_index
                )
            except Exception as e:
                if token_index == 0:
                    self.log.error(
                        f"Polymarket bridge: instrument load failed for {slug!r}: {e}"
                    )
                    return
                break
            iid = self._register_instrument(loader.instrument)
            loaded.append(iid)
            if token_index == 0:
                quote_iid = iid

        if quote_iid is None:
            return

        self._slug_to_iids[slug] = loaded
        self._slug_quote_iid[slug] = quote_iid
        sym = series_symbol(series) if series else _slug_to_symbol(slug)
        self._meta_by_iid[str(quote_iid)] = {
            "slug": slug,
            "symbol": sym,
            "series": series,
            "question": question,
        }
        self.subscribe_quote_ticks(quote_iid)
        self.log.info(
            f"Polymarket bridge: subscribed quotes for {slug!r} → {quote_iid} "
            f"({len(loaded)} instrument(s) in cache)"
        )
