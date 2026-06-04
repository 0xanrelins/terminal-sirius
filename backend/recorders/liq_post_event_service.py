"""Build post-liquidation % performance sessions from ParquetDataCatalog."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import bisect

from catalog import get_catalog
from recorders.binance_liquidation import (
    instrument_symbol,
    liquidation_anchor_price,
    liquidation_notional_usd,
    liquidation_side_str,
)
from recorders.data_types import LiquidationTick
from recorders.second_prices import SecondPrice
from recorders.second_prices import SymbolPriceSeries
from recorders.second_prices import load_second_prices_by_symbol

WINDOW_SEC = 1800
DEFAULT_LOOKBACK_SEC = 7 * 86400
DISPLAY_STEP_SEC = 30

POST_EVENT_COINS = ("BTC", "ETH", "SOL", "XRP", "DOGE")
COIN_TO_NAUTILUS: dict[str, str] = {
    "BTC": "BTCUSDT-PERP.BINANCE",
    "ETH": "ETHUSDT-PERP.BINANCE",
    "SOL": "SOLUSDT-PERP.BINANCE",
    "XRP": "XRPUSDT-PERP.BINANCE",
    "DOGE": "DOGEUSDT-PERP.BINANCE",
}
NAUTILUS_TO_COIN: dict[str, str] = {v: k for k, v in COIN_TO_NAUTILUS.items()}

LiqSide = Literal["LONG", "SHORT"]
SessionStatus = Literal["active", "completed"]


@dataclass(frozen=True)
class PostEventPoint:
    elapsed_sec: int
    pct: float


@dataclass(frozen=True)
class PostEventSession:
    session_id: str
    symbol: str
    side: LiqSide
    notional: float
    anchor_price: float
    event_time: int
    status: SessionStatus
    points: tuple[PostEventPoint, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "symbol": self.symbol,
            "side": self.side,
            "notional": round(self.notional, 2),
            "anchor_price": self.anchor_price,
            "event_time": self.event_time,
            "status": self.status,
            "points": [
                {"elapsed_sec": p.elapsed_sec, "pct": round(p.pct, 4)}
                for p in self.points
            ],
        }


def _unwrap(row: Any) -> Any:
    return getattr(row, "data", row)


def normalize_coin_symbol(raw: str) -> str | None:
    s = raw.strip().upper()
    if s in COIN_TO_NAUTILUS:
        return COIN_TO_NAUTILUS[s]
    if s.endswith(".BINANCE"):
        return s if s in NAUTILUS_TO_COIN else None
    return None


def parse_symbols_param(symbols: str | None) -> tuple[str, ...]:
    if not symbols:
        return tuple(COIN_TO_NAUTILUS[c] for c in POST_EVENT_COINS)
    out: list[str] = []
    for part in symbols.split(","):
        sym = normalize_coin_symbol(part)
        if sym and sym not in out:
            out.append(sym)
    return tuple(out) if out else tuple(COIN_TO_NAUTILUS[c] for c in POST_EVENT_COINS)


def parse_sides_param(sides: str | None) -> frozenset[LiqSide]:
    if not sides or not sides.strip():
        return frozenset({"LONG", "SHORT"})
    out: set[LiqSide] = set()
    for part in sides.split(","):
        side = part.strip().upper()
        if side in ("LONG", "SHORT"):
            out.add(side)  # type: ignore[arg-type]
    return frozenset(out) if out else frozenset({"LONG", "SHORT"})


def _normalize_side(raw: str) -> LiqSide | None:
    s = raw.strip().upper()
    if s in ("LONG", "SHORT"):
        return s  # type: ignore[return-value]
    if s == "SELL":
        return "LONG"
    if s == "BUY":
        return "SHORT"
    return None


def _session_id(symbol: str, side: LiqSide, event_time: int) -> str:
    coin = NAUTILUS_TO_COIN.get(symbol, symbol)
    return f"liq-{event_time}-{coin}-{side}"


def display_points(points: list[PostEventPoint]) -> list[PostEventPoint]:
    """Downsample all sessions to 30s steps for lighter payloads."""
    if not points:
        return points
    step = DISPLAY_STEP_SEC
    out = [p for p in points if p.elapsed_sec == 0 or p.elapsed_sec % step == 0]
    if out[-1].elapsed_sec != points[-1].elapsed_sec:
        out.append(points[-1])
    return out


def build_points_for_event(
    *,
    symbol: str,
    event_time_sec: int,
    anchor_price: float,
    now_sec: int,
    price_rows: list[SecondPrice],
) -> tuple[list[PostEventPoint], SessionStatus]:
    if anchor_price <= 0:
        return ([PostEventPoint(0, 0.0)], "completed")

    end_sec = min(event_time_sec + WINDOW_SEC, now_sec)
    by_elapsed: dict[int, float] = {0: 0.0}

    for row in price_rows:
        if row.symbol != symbol:
            continue
        price_sec = int(row.ts_event) // 1_000_000_000
        elapsed = price_sec - event_time_sec
        if elapsed < 0 or elapsed > WINDOW_SEC:
            continue
        if price_sec > end_sec:
            continue
        by_elapsed[elapsed] = ((float(row.last_price) / anchor_price) - 1.0) * 100.0

    points = [PostEventPoint(e, by_elapsed[e]) for e in sorted(by_elapsed)]
    status: SessionStatus = (
        "completed" if now_sec >= event_time_sec + WINDOW_SEC else "active"
    )
    return points, status


def _price_rows_for_event(
    series: SymbolPriceSeries | None,
    event_time_sec: int,
    event_ts_ns: int,
) -> list[SecondPrice]:
    if series is None or not series.rows:
        return []
    window_end_ns = (event_time_sec + WINDOW_SEC) * 1_000_000_000
    lo = bisect.bisect_left(series.times_ns, event_ts_ns)
    hi = bisect.bisect_right(series.times_ns, window_end_ns)
    return list(series.rows[lo:hi])


def build_sessions(
    *,
    symbols: tuple[str, ...],
    min_notional: float = 0.0,
    sides: frozenset[LiqSide] | None = None,
    limit: int | None = None,
    now_sec: int | None = None,
    lookback_sec: int = DEFAULT_LOOKBACK_SEC,
) -> list[PostEventSession]:
    """Query catalog and compute post-liq % lines for each qualifying event."""
    import time

    sides = sides or frozenset({"LONG", "SHORT"})
    now = now_sec if now_sec is not None else int(time.time())
    symbol_set = set(symbols)

    catalog = get_catalog()
    start_ns = max(0, (now - lookback_sec) * 1_000_000_000)
    end_ns = now * 1_000_000_000

    events: list[tuple[object, int, float, LiqSide]] = []

    try:
        from nautilus_trader.adapters.binance import BinanceFuturesLiquidation
    except ImportError:
        BinanceFuturesLiquidation = None  # type: ignore[misc, assignment]

    if BinanceFuturesLiquidation is not None:
        for raw in catalog.query(
            data_cls=BinanceFuturesLiquidation,
            start=start_ns,
            end=end_ns,
        ):
            liq = _unwrap(raw)
            if not isinstance(liq, BinanceFuturesLiquidation):
                continue
            symbol = instrument_symbol(liq)
            if symbol not in symbol_set:
                continue
            side = _normalize_side(liquidation_side_str(liq))
            if side is None or side not in sides:
                continue
            notional = liquidation_notional_usd(liq)
            if notional < min_notional:
                continue
            event_time = int(liq.ts_event) // 1_000_000_000
            events.append((liq, event_time, notional, side))

    for raw in catalog.query(
        data_cls=LiquidationTick,
        start=start_ns,
        end=end_ns,
    ):
        tick = _unwrap(raw)
        if not isinstance(tick, LiquidationTick):
            continue
        if tick.symbol not in symbol_set:
            continue
        side = _normalize_side(str(tick.side))
        if side is None or side not in sides:
            continue
        notional = float(tick.notional) if tick.notional else float(tick.price) * float(
            tick.quantity
        )
        if notional < min_notional:
            continue
        event_time = int(tick.ts_event) // 1_000_000_000
        events.append((tick, event_time, notional, side))

    events.sort(key=lambda x: x[1], reverse=True)
    if limit is not None:
        events = events[: max(1, limit)]

    prices_by_symbol = load_second_prices_by_symbol(
        catalog, symbol_set, start_ns, end_ns
    )

    sessions: list[PostEventSession] = []
    for liq, event_time, notional, side in events:
        if BinanceFuturesLiquidation is not None and isinstance(liq, BinanceFuturesLiquidation):
            symbol = instrument_symbol(liq)
            anchor = liquidation_anchor_price(liq)
        else:
            symbol = liq.symbol
            anchor = float(liq.price)
        price_rows = _price_rows_for_event(
            prices_by_symbol.get(symbol),
            event_time,
            int(liq.ts_event),
        )

        points, status = build_points_for_event(
            symbol=symbol,
            event_time_sec=event_time,
            anchor_price=anchor,
            now_sec=now,
            price_rows=price_rows,
        )
        points = display_points(points)

        coin = NAUTILUS_TO_COIN.get(symbol, symbol)
        sessions.append(
            PostEventSession(
                session_id=_session_id(symbol, side, event_time),
                symbol=coin,
                side=side,
                notional=notional,
                anchor_price=anchor,
                event_time=event_time,
                status=status,
                points=tuple(points),
            )
        )

    return sessions


def build_sessions_response(
    *,
    symbols: str | None = None,
    min_notional: float = 0.0,
    sides: str | None = None,
    interval: str = "30s",
    limit: int | None = None,
    now_sec: int | None = None,
) -> dict[str, Any]:
    sym_tuple = parse_symbols_param(symbols)
    side_set = parse_sides_param(sides)
    sessions = build_sessions(
        symbols=sym_tuple,
        min_notional=max(0.0, min_notional),
        sides=side_set,
        limit=limit,
        now_sec=now_sec,
    )
    return {"sessions": [s.to_dict() for s in sessions]}
