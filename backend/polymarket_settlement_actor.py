"""
PolymarketSettlementActor — paper 15m window settlement (native InstrumentClose).

Rule: each open position belongs to a Polymarket slug ``{series}-{window_start}``.
When that 15m window ends, the Binance 15m candle for ``window_start`` decides UP/DOWN
(``close >= open`` → UP). Publish ``InstrumentClose`` at 1/0 so Sandbox closes the bet.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from datetime import timedelta
from datetime import timezone

from nautilus_trader.common.actor import Actor
from nautilus_trader.config import ActorConfig
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import BarType
from nautilus_trader.model.data import DataType
from nautilus_trader.model.data import InstrumentClose
from nautilus_trader.model.enums import BarAggregation
from nautilus_trader.model.enums import InstrumentCloseType
from nautilus_trader.model.events import PositionOpened
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import Venue

from adapters.polymarket.messages import ActivePolymarketMarket
from adapters.polymarket.quote_registry import slug_for_instrument
from adapters.polymarket.rolling import WINDOW_SEC
from adapters.polymarket.rolling import parse_series_from_slug
from adapters.polymarket.rolling import parse_window_epoch_from_slug
from adapters.polymarket.rolling import seconds_until_window_end
from bar_time import bar_open_time
from strategies.mapping import BINANCE_TO_POLY_SERIES

POLY_SERIES_TO_BINANCE: dict[str, str] = {v: k for k, v in BINANCE_TO_POLY_SERIES.items()}
BAR_INTERVAL = "15m"
SETTLE_GRACE_SEC = 5.0
CATCHUP_DELAY_SEC = 25.0
HISTORY_LOOKBACK_H = 6


class PolymarketSettlementActorConfig(ActorConfig, frozen=True):
    binance_instruments: tuple[str, ...]
    venue: str = "POLYMARKET"


def up_outcome_from_bar(bar: Bar) -> bool:
    """``True`` when the 15m candle is green (UP wins on Polymarket up/down markets)."""
    return bar.close.as_double() >= bar.open.as_double()


def settlement_price_str(won: bool, price_precision: int) -> str:
    """Settlement px as string matching instrument ``price_precision`` (e.g. 3 → ``1.000``)."""
    if price_precision <= 0:
        return "1" if won else "0"
    value = 1.0 if won else 0.0
    return f"{value:.{price_precision}f}"


def instrument_close_topic(instrument_id: InstrumentId) -> str:
    """Sandbox/msgbus topic for ``InstrumentClose`` (Nautilus switchboard)."""
    return f"data.close.{instrument_id.venue}.{instrument_id.symbol}"


def position_won(*, outcome: str, up_won: bool) -> bool:
    if outcome == "YES":
        return up_won
    if outcome == "NO":
        return not up_won
    raise ValueError(f"unknown market outcome: {outcome!r}")


class PolymarketSettlementActor(Actor):
    def __init__(self, config: PolymarketSettlementActorConfig) -> None:
        super().__init__(config)
        self._venue = Venue(config.venue)
        self._binance_instruments = tuple(config.binance_instruments)
        self._bar_types: dict[str, BarType] = {}
        # instrument_id str → {"slug", "series", "market_outcome": YES|NO}
        self._iid_meta: dict[str, dict[str, str]] = {}
        # slug frozen at position open (survives rolling slug rotation)
        self._locked_slugs: dict[str, str] = {}
        # (binance_sym, window_open_sec) → bar
        self._bar_by_window: dict[tuple[str, int], Bar] = {}
        self._settled: set[str] = set()
        self._hist_bar_count: int = 0
        self._boundary_task: asyncio.Task | None = None
        self._catchup_task: asyncio.Task | None = None

    def on_start(self) -> None:
        self.msgbus.subscribe(
            topic=f"data.{DataType(ActivePolymarketMarket).topic}",
            handler=self.handle_data,
        )
        self.msgbus.subscribe(topic="events.position.*", handler=self._on_position_event)
        for sym in self._binance_instruments:
            iid = InstrumentId.from_str(sym)
            bar_type = BarType.from_str(f"{iid}-15-MINUTE-LAST-EXTERNAL")
            self._bar_types[sym] = bar_type
            self.subscribe_bars(bar_type)
        self._ensure_boundary_loop()
        self._catchup_task = asyncio.create_task(self._startup_catch_up())
        print(
            "[paper] PolymarketSettlementActor → slug window + Binance 15m → InstrumentClose",
            flush=True,
        )

    def on_stop(self) -> None:
        for task in (self._boundary_task, self._catchup_task):
            if task is not None and not task.done():
                task.cancel()

    def on_data(self, data) -> None:
        if isinstance(data, ActivePolymarketMarket):
            self._on_active_market(data)

    def _on_position_event(self, event) -> None:
        if isinstance(event, PositionOpened):
            self._lock_slug(str(event.instrument_id))

    def on_historical_data(self, data) -> None:
        if isinstance(data, Bar) and self._is_15m_bar(data):
            self._index_bar(data)
            self._hist_bar_count += 1

    def on_bar(self, bar: Bar) -> None:
        if not self._is_15m_bar(bar):
            return
        self._index_bar(bar)
        binance_sym = str(bar.bar_type.instrument_id)
        if binance_sym not in BINANCE_TO_POLY_SERIES:
            return
        window_start_sec = bar_open_time(int(bar.ts_event) // 1_000_000_000, BAR_INTERVAL)
        up_won = up_outcome_from_bar(bar)
        self.log.info(
            f"15m bar closed {binance_sym} window={window_start_sec} "
            f"{'UP' if up_won else 'DOWN'}",
        )
        self._settle_window(binance_sym, window_start_sec, up_won)

    def _on_active_market(self, data: ActivePolymarketMarket) -> None:
        base = {
            "slug": str(data.slug or ""),
            "series": str(data.series or ""),
        }
        yes_iid = str(data.instrument_id)
        no_iid = str(data.no_instrument_id)
        self._iid_meta[yes_iid] = {**base, "market_outcome": "YES"}
        self._iid_meta[no_iid] = {**base, "market_outcome": "NO"}

    def _is_15m_bar(self, bar: Bar) -> bool:
        spec = bar.bar_type.spec
        if spec.aggregation == BarAggregation.MINUTE and spec.step == 15:
            return True
        return "15-MINUTE" in str(bar.bar_type)

    def _index_bar(self, bar: Bar) -> None:
        if not self._is_15m_bar(bar):
            return
        sym = str(bar.bar_type.instrument_id)
        window_open = bar_open_time(int(bar.ts_event) // 1_000_000_000, BAR_INTERVAL)
        self._bar_by_window[(sym, window_open)] = bar

    def _ensure_boundary_loop(self) -> None:
        if self._boundary_task is None or self._boundary_task.done():
            self._boundary_task = asyncio.create_task(self._boundary_loop())

    def _index_bars_from_cache(self, binance_sym: str) -> int:
        bar_type = self._bar_types.get(binance_sym)
        if bar_type is None:
            return 0
        bars = self.cache.bars(bar_type) or []
        for bar in bars:
            self._index_bar(bar)
        return len(bars)

    async def _startup_catch_up(self) -> None:
        try:
            await asyncio.sleep(CATCHUP_DELAY_SEC)
            start = datetime.now(timezone.utc) - timedelta(hours=HISTORY_LOOKBACK_H)
            pending = len(self._bar_types)
            done = 0

            def _on_history_done(sym: str, _request_id) -> None:
                nonlocal done
                cached = self._index_bars_from_cache(sym)
                self.log.info(f"history bars cached for {sym}: {cached}")
                done += 1

            for sym, bar_type in self._bar_types.items():
                self.request_bars(
                    bar_type=bar_type,
                    start=start,
                    limit=32,
                    callback=lambda rid, s=sym: _on_history_done(s, rid),
                )

            for _ in range(60):
                if done >= pending:
                    break
                await asyncio.sleep(0.5)

            for pos in self.cache.positions_open(venue=self._venue):
                self._lock_slug(str(pos.instrument_id))

            settled, skipped = self._settle_all_expired()
            self.log.info(
                f"startup catch-up: settled={settled} skipped={skipped} "
                f"bars={len(self._bar_by_window)} locked_slugs={len(self._locked_slugs)}",
            )
        except asyncio.CancelledError:
            pass
        except Exception as e:  # noqa: BLE001
            self.log.warning(f"settlement startup catch-up failed: {e!r}")

    async def _boundary_loop(self) -> None:
        while True:
            try:
                await self._sleep_until_boundary()
                await asyncio.sleep(SETTLE_GRACE_SEC)
                settled, skipped = self._settle_all_expired()
                if settled or skipped:
                    self.log.info(f"boundary sweep: settled={settled} skipped={skipped}")
            except asyncio.CancelledError:
                break
            except Exception as e:  # noqa: BLE001
                self.log.warning(f"settlement boundary loop error: {e!r}")

    async def _sleep_until_boundary(self) -> None:
        until = seconds_until_window_end()
        if until <= 1.0:
            await asyncio.sleep(max(0.05, until + 0.05))
        else:
            await asyncio.sleep(min(20.0, until))

    def _lock_slug(self, iid_s: str) -> None:
        if iid_s in self._locked_slugs:
            return
        inst = self.cache.instrument(InstrumentId.from_str(iid_s))
        slug = self._resolve_slug(iid_s, inst)
        if slug:
            self._locked_slugs[iid_s] = slug

    def _resolve_slug(self, iid_s: str, instrument) -> str | None:
        meta = self._iid_meta.get(iid_s, {})
        slug = meta.get("slug")
        if slug:
            return slug
        if instrument is not None:
            info = getattr(instrument, "info", {}) or {}
            if isinstance(info, dict):
                for key in ("market_slug", "slug"):
                    raw = info.get(key)
                    if raw:
                        return str(raw)
        return slug_for_instrument(iid_s)

    def _slug_for_iid(self, iid_s: str, instrument) -> str | None:
        locked = self._locked_slugs.get(iid_s)
        if locked:
            return locked
        return self._resolve_slug(iid_s, instrument)

    def _window_for_position(self, iid_s: str, instrument) -> tuple[int, int] | None:
        """(window_start_sec, window_end_sec) from slug suffix."""
        slug = self._slug_for_iid(iid_s, instrument)
        if not slug:
            return None
        ws = parse_window_epoch_from_slug(slug)
        if ws is None:
            return None
        return ws, ws + WINDOW_SEC

    def _binance_for_iid(self, iid_s: str, instrument) -> str | None:
        meta = self._iid_meta.get(iid_s, {})
        series = meta.get("series")
        if not series:
            slug = self._slug_for_iid(iid_s, instrument)
            if slug:
                series = parse_series_from_slug(slug)
        if series:
            return POLY_SERIES_TO_BINANCE.get(series)
        return None

    def _up_outcome_for_window(self, binance_sym: str, window_start_sec: int) -> bool | None:
        bar = self._bar_by_window.get((binance_sym, window_start_sec))
        if bar is not None:
            return up_outcome_from_bar(bar)
        bar_type = self._bar_types.get(binance_sym)
        if bar_type is None:
            return None
        for cached in self.cache.bars(bar_type) or []:
            bar_open = bar_open_time(int(cached.ts_event) // 1_000_000_000, BAR_INTERVAL)
            if bar_open == window_start_sec:
                return up_outcome_from_bar(cached)
        return None

    def _settlement_suppressed(self, iid_s: str, instrument_id: InstrumentId) -> bool:
        """Skip only when a prior ``InstrumentClose`` closed the position."""
        if iid_s not in self._settled:
            return False
        return not self.cache.positions_open(instrument_id=instrument_id)

    def _settle_all_expired(self) -> tuple[int, int]:
        now_sec = int(self.clock.timestamp_ns() // 1_000_000_000)
        settled = 0
        skipped = 0
        for pos in list(self.cache.positions_open(venue=self._venue)):
            iid = pos.instrument_id
            iid_s = str(iid)
            if self._settlement_suppressed(iid_s, iid):
                continue
            inst = self.cache.instrument(iid)
            window = self._window_for_position(iid_s, inst)
            if window is None:
                skipped += 1
                self.log.warning(f"skip settle {iid_s}: no slug window")
                continue
            window_start_sec, window_end_sec = window
            if now_sec < window_end_sec:
                continue
            binance_sym = self._binance_for_iid(iid_s, inst)
            if binance_sym is None:
                skipped += 1
                continue
            up_won = self._up_outcome_for_window(binance_sym, window_start_sec)
            if up_won is None:
                skipped += 1
                self.log.info(
                    f"defer settle {iid_s}: no bar {binance_sym} window={window_start_sec}",
                )
                continue
            slug = self._slug_for_iid(iid_s, inst)
            if self._apply_settlement(iid, up_won, slug=slug):
                settled += 1
        return settled, skipped

    def _settle_window(self, binance_sym: str, window_start_sec: int, up_won: bool) -> None:
        for pos in list(self.cache.positions_open(venue=self._venue)):
            iid = pos.instrument_id
            iid_s = str(iid)
            if self._settlement_suppressed(iid_s, iid):
                continue
            inst = self.cache.instrument(iid)
            window = self._window_for_position(iid_s, inst)
            if window is None:
                continue
            pos_window_start, _ = window
            if pos_window_start != window_start_sec:
                continue
            if self._binance_for_iid(iid_s, inst) != binance_sym:
                continue
            slug = self._slug_for_iid(iid_s, inst)
            self._apply_settlement(iid, up_won, slug=slug)

    def _apply_settlement(
        self,
        instrument_id: InstrumentId,
        up_won: bool,
        *,
        slug: str | None,
    ) -> bool:
        iid_s = str(instrument_id)
        if iid_s in self._settled:
            if self.cache.positions_open(instrument_id=instrument_id):
                # Prior publish did not close the position (e.g. topic mismatch) — retry.
                self._settled.discard(iid_s)
                self.log.warning(f"retry settle {iid_s}: prior InstrumentClose did not close")
            else:
                return False
        if not self.cache.positions_open(instrument_id=instrument_id):
            return False
        instrument = self.cache.instrument(instrument_id)
        if instrument is None:
            return False
        outcome = self._iid_meta.get(iid_s, {}).get("market_outcome")
        if not outcome:
            desc = str(getattr(instrument, "description", "") or "").lower()
            outcome = "NO" if "down" in desc or " no" in desc else "YES"
        won = position_won(outcome=outcome, up_won=up_won)
        settle_px = settlement_price_str(won, instrument.price_precision)
        now_ns = self.clock.timestamp_ns()
        close = InstrumentClose(
            instrument_id,
            instrument.make_price(settle_px),
            InstrumentCloseType.CONTRACT_EXPIRED,
            now_ns,
            now_ns,
        )
        topic = instrument_close_topic(instrument_id)
        self.msgbus.publish(topic=topic, msg=close)
        self._settled.add(iid_s)
        self.log.info(
            f"InstrumentClose {instrument_id} topic={topic} slug={slug or '?'} "
            f"outcome={outcome} {'WON' if won else 'LOST'} px={settle_px}",
        )
        return True
