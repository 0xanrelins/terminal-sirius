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
from nautilus_trader.model.data import DataType
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.identifiers import InstrumentId

from adapters.polymarket.gamma import get_token_ids
from adapters.polymarket.slug_load_guard import SLUG_LOAD_SEM
from adapters.polymarket.slug_load_guard import SlugLoadGuard
from adapters.polymarket.instrument_expiry import align_binary_option_expiration
from adapters.polymarket.messages import ActivePolymarketMarket
from adapters.polymarket.quote_registry import register_slug_instruments, update_slug_quote
from adapters.polymarket.rolling import (
    active_rolling_slugs,
    seconds_until_window_end,
    series_symbol,
    slug_for_series,
)

ROTATION_POLL_SEC = 20


def _slug_to_symbol(slug: str) -> str:
    return f"{slug}.POLYMARKET"


def _price_as_float(p) -> float:
    if hasattr(p, "as_double"):
        return float(p.as_double())
    return float(p)


def should_broadcast_quote(meta: dict, series_slugs: dict[str, str]) -> bool:
    """True when a quote tick should be forwarded to the UI WebSocket queue."""
    token = meta.get("token") or "yes"
    if token != "yes":
        return False
    series = meta.get("series")
    if series is None:
        return True
    return meta.get("slug") == series_slugs.get(series)


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
        self._slug_load_guard = SlugLoadGuard()

    def _enqueue(self, msg: dict) -> None:
        try:
            self._queue.put_nowait(msg)
        except queue.Full:
            pass

    def _publish_active_market(self, series: str, slug: str) -> None:
        """Announce active YES + NO instruments (native data) for in-engine consumers (strategy)."""
        yes_iid = self._slug_quote_iid.get(slug)
        if yes_iid is None:
            return
        all_iids = self._slug_to_iids.get(slug, [])
        no_iid = all_iids[1] if len(all_iids) > 1 else yes_iid
        ts = self.clock.timestamp_ns()
        meta = self._meta_by_iid.get(str(yes_iid), {})
        self.publish_data(
            DataType(ActivePolymarketMarket),
            ActivePolymarketMarket(
                instrument_id=yes_iid,
                no_instrument_id=no_iid,
                series=series,
                slug=slug,
                question=str(meta.get("question") or slug),
                ts_event=ts,
                ts_init=ts,
            ),
        )

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
        token = meta.get("token") or "yes"
        update_slug_quote(
            meta["slug"],
            token=token,
            bid=bid,
            ask=ask,
            ts_ms=int(tick.ts_event // 1_000_000),
        )
        if not should_broadcast_quote(meta, self._series_slugs):
            return
        # Always derive symbol from series to avoid stale slug-format symbols
        # that can persist when a slug is re-subscribed with a different series context.
        _series = meta.get("series")
        _sym = series_symbol(_series) if _series else meta["symbol"]
        msg: dict = {
            "type": "polymarket",
            "symbol": _sym,
            "slug": meta["slug"],
            "series": _series,
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
        if series not in self._series_slugs:
            current, _ = active_rolling_slugs(series)
            self._series_slugs[series] = current
            self._slug_series[current] = series
        await self._sync_series_slugs(series)
        self._ensure_rotation_loop()

    async def _rotation_loop(self) -> None:
        while self._series_slugs:
            try:
                await self._check_series_rotation()
            except Exception as e:
                self.log.warning(f"Polymarket quote bridge rotation error: {e!r}")
            until_boundary = seconds_until_window_end()
            if until_boundary <= 1.0:
                await asyncio.sleep(max(0.05, until_boundary + 0.05))
            else:
                await asyncio.sleep(min(ROTATION_POLL_SEC, until_boundary))

    async def _sync_series_slugs(self, series: str) -> None:
        """Subscribe current 15m UP instrument only (UI price widget)."""
        current, _ = active_rolling_slugs(series)
        tracked = self._series_slugs.get(series)
        if tracked is not None and tracked != current:
            self.log.info(
                f"Polymarket bridge: rotating {series!r} {tracked!r} → {current!r}"
            )
            await self._drop_slug(tracked)
            self._series_slugs[series] = current
            self._slug_series[current] = series
        elif series not in self._series_slugs:
            self._series_slugs[series] = current
            self._slug_series[current] = series
        await self._ensure_slug(current, series=series)
        if self._slug_quote_iid.get(current) is not None:
            # Re-announce each sync so a late-starting strategy still learns the active market.
            self._publish_active_market(series, current)
        for slug in list(self._slug_series):
            if self._slug_series.get(slug) == series and slug != current:
                await self._drop_slug(slug)

    async def _check_series_rotation(self) -> None:
        for series in list(self._series_slugs):
            await self._sync_series_slugs(series)
            await asyncio.sleep(0.25)

    async def _drop_slug(self, slug: str) -> None:
        self._slug_quote_iid.pop(slug, None)
        all_iids = self._slug_to_iids.pop(slug, [])
        self._slug_series.pop(slug, None)
        for iid in all_iids:
            self._meta_by_iid.pop(str(iid), None)
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
            # Already subscribed — but if we now have series context, fix any stale
            # slug-format symbol that was set when series was None (e.g. via subscribe_slug).
            if series is not None:
                correct_sym = series_symbol(series)
                for meta in self._meta_by_iid.values():
                    if meta.get("slug") == slug and meta.get("symbol") != correct_sym:
                        self.log.info(
                            f"Polymarket bridge: fixing symbol for {slug!r} "
                            f"{meta['symbol']!r} → {correct_sym!r}"
                        )
                        meta["symbol"] = correct_sym
                        meta["series"] = series
            return

        now_ns = self.clock.timestamp_ns()
        if self._slug_load_guard.should_skip(slug, now_ns):
            return

        async with SLUG_LOAD_SEM:
            if slug in self._slug_quote_iid:
                return
            await self._load_slug_locked(slug, series=series)

    async def _load_slug_locked(self, slug: str, *, series: str | None) -> None:
        from nautilus_trader.adapters.polymarket.loaders import PolymarketDataLoader

        now_ns = self.clock.timestamp_ns()
        try:
            info = await get_token_ids(slug)
            if not info:
                raise LookupError(f"gamma returned no market for {slug!r}")
        except Exception as e:
            delay = self._slug_load_guard.record_failure(slug, now_ns, e)
            self.log.error(
                f"Polymarket bridge: gamma error for {slug!r}: {e} "
                f"(retry in {delay:.0f}s)",
            )
            return

        question = info.get("question") or slug
        loaded: list[InstrumentId] = []
        quote_iid: InstrumentId | None = None
        try:
            for token_index in (0, 1):
                loader = await PolymarketDataLoader.from_market_slug(
                    slug, token_index=token_index
                )
                iid = self._register_instrument(
                    align_binary_option_expiration(loader.instrument, slug),
                )
                loaded.append(iid)
                if token_index == 0:
                    quote_iid = iid
        except Exception as e:
            delay = self._slug_load_guard.record_failure(slug, now_ns, e)
            self.log.error(
                f"Polymarket bridge: instrument load failed for {slug!r}: {e} "
                f"(retry in {delay:.0f}s)",
            )
            return

        if quote_iid is None:
            return

        self._slug_load_guard.record_success(slug)
        self._slug_to_iids[slug] = loaded
        self._slug_quote_iid[slug] = quote_iid
        sym = series_symbol(series) if series else _slug_to_symbol(slug)
        no_iid = loaded[1] if len(loaded) > 1 else None
        register_slug_instruments(
            slug,
            yes_iid=str(quote_iid),
            no_iid=str(no_iid) if no_iid is not None else None,
        )
        for iid in loaded:
            token = "yes" if iid == quote_iid else "no"
            self._meta_by_iid[str(iid)] = {
                "slug": slug,
                "symbol": sym,
                "series": series,
                "question": question,
                "token": token,
            }
            self.subscribe_quote_ticks(iid)
        self.log.info(
            f"Polymarket bridge: subscribed quotes for {slug!r} → {quote_iid} "
            f"({len(loaded)} instrument(s) in cache)"
        )
