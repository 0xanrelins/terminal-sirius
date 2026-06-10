"""Build post-liquidation verdict rows from ParquetDataCatalog (research / dashboard)."""

from __future__ import annotations

import bisect
from typing import Any

from catalog import get_catalog
from recorders.data_types import LiquidationTick
from recorders.liq_post_event_service import (
    COIN_TO_NAUTILUS,
    NAUTILUS_TO_COIN,
    _normalize_side,
    parse_symbols_param,
    parse_sides_param,
)
from recorders.second_prices import SecondPrice
from recorders.second_prices import SymbolPriceSeries
from recorders.second_prices import load_event_second_prices
from recorders.second_prices import load_second_prices_by_symbol
from strategies.liquidation_verdict_logic import OpenVerdictEvent
from strategies.liquidation_verdict_logic import VerdictEventIdFactory
from strategies.liquidation_verdict_logic import expire_open_event
from strategies.liquidation_verdict_logic import update_open_event

DEFAULT_LOOKBACK_SEC = 7 * 86400
DEFAULT_LIQ_MOVE_THRESHOLD_PCT = 0.2
DEFAULT_RECOVERY_MOVE_THRESHOLD_PCT = 0.2
DEFAULT_MAX_OBSERVATION_SEC = 450


def _unwrap(row: Any) -> Any:
    return getattr(row, "data", row)


def _price_rows_for_event(
    series: SymbolPriceSeries | None,
    event_ts_ns: int,
    window_sec: int,
) -> list[SecondPrice]:
    if series is None or not series.rows:
        return []
    window_end_ns = event_ts_ns + window_sec * 1_000_000_000
    lo = bisect.bisect_left(series.times_ns, event_ts_ns)
    hi = bisect.bisect_right(series.times_ns, window_end_ns)
    return list(series.rows[lo:hi])


def _anchor_price(series: SymbolPriceSeries | None, event_ts_ns: int, fallback: float) -> float:
    if series is None or not series.rows:
        return fallback
    lo = bisect.bisect_right(series.times_ns, event_ts_ns) - 1
    if lo >= 0:
        return float(series.rows[lo].last_price)
    return float(series.rows[0].last_price)


def _price_series_for_event(
    catalog: Any,
    bulk: SymbolPriceSeries | None,
    *,
    symbol: str,
    event_ts_ns: int,
    window_sec: int,
) -> SymbolPriceSeries:
    if bulk is not None and bulk.rows:
        if _price_rows_for_event(bulk, event_ts_ns, window_sec):
            return bulk
    return load_event_second_prices(catalog, symbol, event_ts_ns, window_sec)


def merge_verdict_rows(
    persisted: list[dict[str, Any]],
    catalog_rows: list[dict[str, Any]],
    *,
    limit: int | None,
) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for row in catalog_rows:
        by_id[str(row["event_id"])] = row
    for row in persisted:
        by_id[str(row["event_id"])] = row
    merged = sorted(by_id.values(), key=lambda r: int(r["event_time"]), reverse=True)
    if limit is not None:
        return merged[: max(1, limit)]
    return merged


def build_verdict_rows(
    *,
    symbols: tuple[str, ...],
    min_notional: float = 0.0,
    sides: frozenset[str] | None = None,
    limit: int | None = 50,
    lookback_sec: int = DEFAULT_LOOKBACK_SEC,
    liq_move_threshold_pct: float = DEFAULT_LIQ_MOVE_THRESHOLD_PCT,
    recovery_move_threshold_pct: float = DEFAULT_RECOVERY_MOVE_THRESHOLD_PCT,
    max_observation_sec: int = DEFAULT_MAX_OBSERVATION_SEC,
    now_sec: int | None = None,
) -> list[dict[str, Any]]:
    import time

    sides = sides or frozenset({"LONG", "SHORT"})
    now = now_sec if now_sec is not None else int(time.time())
    symbol_set = set(symbols)
    catalog = get_catalog()
    start_ns = max(0, (now - lookback_sec) * 1_000_000_000)
    end_ns = now * 1_000_000_000

    events: list[tuple[LiquidationTick, int, float, str]] = []
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
        events.append((tick, int(tick.ts_event), notional, side))

    events.sort(key=lambda x: x[1], reverse=True)
    if limit is not None:
        events = events[: max(1, limit)]

    prices_by_symbol = load_second_prices_by_symbol(
        catalog, symbol_set, start_ns, end_ns
    )

    rows: list[dict[str, Any]] = []
    window_ns = max_observation_sec * 1_000_000_000
    event_ids = VerdictEventIdFactory()
    for tick, event_ts_ns, notional, side in reversed(events):
        symbol = tick.symbol
        bulk = prices_by_symbol.get(symbol)
        series = _price_series_for_event(
            catalog,
            bulk,
            symbol=symbol,
            event_ts_ns=event_ts_ns,
            window_sec=max_observation_sec,
        )
        tick_price = float(tick.price) if float(tick.price) > 0 else 0.0
        anchor = tick_price if tick_price > 0 else _anchor_price(series, event_ts_ns, tick_price)
        if anchor <= 0:
            anchor = _anchor_price(series, event_ts_ns, tick_price)
        order_id = int(getattr(tick, "order_id", 0) or 0)
        open_ev = OpenVerdictEvent(
            event_id=event_ids.make(symbol, side, event_ts_ns, order_id=order_id),
            symbol=symbol,
            liq_side=side,  # type: ignore[arg-type]
            notional=notional,
            event_price=anchor,
            event_ts_ns=event_ts_ns,
        )
        completed = None
        for row in _price_rows_for_event(series, event_ts_ns, max_observation_sec):
            price = float(row.last_price)
            ts_ns = int(row.ts_event)
            completed = update_open_event(
                open_ev,
                price,
                ts_ns,
                liq_move_threshold_pct=liq_move_threshold_pct,
                recovery_move_threshold_pct=recovery_move_threshold_pct,
            )
            if completed is not None:
                break
            if ts_ns - event_ts_ns >= window_ns:
                completed = expire_open_event(open_ev)
                break
        if completed is None:
            completed = expire_open_event(open_ev)

        coin = NAUTILUS_TO_COIN.get(symbol, symbol)
        rows.append(
            {
                "event_id": completed.event_id,
                "symbol": coin,
                "liq_side": completed.liq_side,
                "notional": round(completed.notional, 2),
                "event_price": completed.event_price,
                "winner": completed.winner,
                "liq_move_pct": round(completed.liq_move_pct, 4),
                "recovery_move_pct": round(completed.recovery_move_pct, 4),
                "dominance_ratio": round(completed.dominance_ratio, 2),
                "time_to_dominance_sec": round(completed.time_to_dominance_sec, 2),
                "area_bias": round(completed.area_bias, 4),
                "status": completed.status,
                "completion_reason": completed.completion_reason,
                "event_time": event_ts_ns // 1_000_000_000,
            }
        )
    rows.reverse()
    return rows


def build_verdict_response(
    *,
    symbols: str | None = None,
    min_notional: float = 0.0,
    sides: str | None = None,
    limit: int | None = 50,
    liq_move_threshold_pct: float = DEFAULT_LIQ_MOVE_THRESHOLD_PCT,
    recovery_move_threshold_pct: float = DEFAULT_RECOVERY_MOVE_THRESHOLD_PCT,
    max_observation_sec: int = DEFAULT_MAX_OBSERVATION_SEC,
) -> dict[str, Any]:
    sym_tuple = parse_symbols_param(symbols)
    side_set = parse_sides_param(sides)
    rows = build_verdict_rows(
        symbols=sym_tuple,
        min_notional=max(0.0, min_notional),
        sides=side_set,
        limit=limit,
        liq_move_threshold_pct=liq_move_threshold_pct,
        recovery_move_threshold_pct=recovery_move_threshold_pct,
        max_observation_sec=max_observation_sec,
    )
    return {"verdicts": rows}
