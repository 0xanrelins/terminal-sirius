"""Tests for post-liquidation catalog session builder."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import AggressorSide
from nautilus_trader.model.identifiers import InstrumentId, TradeId
from nautilus_trader.model.objects import Price, Quantity

from catalog import get_catalog
from recorders.data_types import LiquidationTick
from recorders.liq_post_event_service import (
    _price_rows_for_event,
    build_points_for_event,
    build_sessions,
    display_points,
    parse_sides_param,
    parse_symbols_param,
    PostEventPoint,
)
from recorders.second_prices import SecondPrice, SymbolPriceSeries


@pytest.fixture
def temp_catalog(monkeypatch: pytest.MonkeyPatch):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "catalog"
        monkeypatch.setenv("CATALOG_PATH", str(path))
        yield path


def _write_liq(catalog, *, ts_sec: int, symbol: str, side: str, price: float, qty: float):
    ts = ts_sec * 1_000_000_000
    notional = price * qty
    catalog.write_data(
        [
            LiquidationTick(
                ts_event=ts,
                ts_init=ts,
                symbol=symbol,
                side=side,
                notional=notional,
                price=price,
                quantity=qty,
            )
        ]
    )


def _write_trade(catalog, *, ts_sec: int, symbol: str, last_price: float):
    ts = ts_sec * 1_000_000_000
    iid = InstrumentId.from_str(symbol)
    catalog.write_data(
        [
            TradeTick(
                instrument_id=iid,
                price=Price.from_str(str(last_price)),
                size=Quantity.from_str("1.0"),
                aggressor_side=AggressorSide.BUYER,
                trade_id=TradeId(str(ts_sec)),
                ts_event=ts,
                ts_init=ts,
            )
        ]
    )


def test_parse_symbols_and_sides():
    assert parse_symbols_param("BTC,ETH") == (
        "BTCUSDT-PERP.BINANCE",
        "ETHUSDT-PERP.BINANCE",
    )
    assert parse_sides_param("LONG") == frozenset({"LONG"})
    assert parse_sides_param("") == frozenset({"LONG", "SHORT"})


def test_build_points_pct_and_status():
    symbol = "BTCUSDT-PERP.BINANCE"
    event_time = 1_700_000_000
    anchor = 100.0
    rows = [
        SecondPrice(
            ts_event=event_time * 1_000_000_000,
            symbol=symbol,
            last_price=100.0,
        ),
        SecondPrice(
            ts_event=(event_time + 60) * 1_000_000_000,
            symbol=symbol,
            last_price=101.0,
        ),
    ]
    points, status = build_points_for_event(
        symbol=symbol,
        event_time_sec=event_time,
        anchor_price=anchor,
        now_sec=event_time + 120,
        price_rows=rows,
    )
    assert status == "active"
    assert points[0] == PostEventPoint(0, 0.0)
    assert any(p.elapsed_sec == 60 and abs(p.pct - 1.0) < 0.01 for p in points)


def test_build_points_completed_at_30m():
    symbol = "BTCUSDT-PERP.BINANCE"
    event_time = 1_700_000_000
    _, status = build_points_for_event(
        symbol=symbol,
        event_time_sec=event_time,
        anchor_price=100.0,
        now_sec=event_time + 1800,
        price_rows=[],
    )
    assert status == "completed"


def test_display_points_30s():
    points = [PostEventPoint(i, float(i)) for i in range(0, 95, 1)]
    out = display_points(points)
    elapsed = [p.elapsed_sec for p in out]
    assert 0 in elapsed
    assert 30 in elapsed
    assert 60 in elapsed
    assert 90 in elapsed
    assert len(out) < len(points)


def test_display_points_active_same_coarse():
    points = [PostEventPoint(i, float(i)) for i in range(0, 120, 1)]
    out = display_points(points)
    assert 30 in [p.elapsed_sec for p in out]
    assert len(out) < len(points)


def test_build_sessions_filters_notional_and_side(temp_catalog: Path):
    catalog = get_catalog()
    symbol = "BTCUSDT-PERP.BINANCE"
    t0 = 1_700_000_000

    _write_liq(catalog, ts_sec=t0, symbol=symbol, side="LONG", price=100.0, qty=500.0)
    _write_liq(catalog, ts_sec=t0 + 10, symbol=symbol, side="SHORT", price=100.0, qty=100.0)
    _write_trade(catalog, ts_sec=t0, symbol=symbol, last_price=100.0)
    _write_trade(catalog, ts_sec=t0 + 5, symbol=symbol, last_price=100.5)

    sessions = build_sessions(
        symbols=(symbol,),
        min_notional=40_000.0,
        sides=frozenset({"LONG"}),
        limit=10,
        now_sec=t0 + 300,
    )
    assert len(sessions) == 1
    assert sessions[0].side == "LONG"
    assert sessions[0].notional == 50_000.0
    assert sessions[0].status == "active"
    assert len(sessions[0].points) >= 2


def test_build_sessions_unlimited_returns_all_matching(temp_catalog: Path):
    catalog = get_catalog()
    symbol = "BTCUSDT-PERP.BINANCE"
    t0 = 1_700_000_000

    _write_liq(catalog, ts_sec=t0, symbol=symbol, side="LONG", price=100.0, qty=500.0)
    _write_liq(catalog, ts_sec=t0 + 10, symbol=symbol, side="SHORT", price=100.0, qty=500.0)
    _write_trade(catalog, ts_sec=t0, symbol=symbol, last_price=100.0)
    _write_trade(catalog, ts_sec=t0 + 10, symbol=symbol, last_price=100.0)

    sessions = build_sessions(
        symbols=(symbol,),
        min_notional=40_000.0,
        limit=None,
        now_sec=t0 + 300,
    )
    assert len(sessions) == 2


def test_price_rows_for_event_slices_sorted_series():
    symbol = "BTCUSDT-PERP.BINANCE"
    rows = [
        SecondPrice(
            ts_event=(1_700_000_000 + i) * 1_000_000_000,
            symbol=symbol,
            last_price=100.0 + i,
        )
        for i in range(0, 120)
    ]
    series = SymbolPriceSeries(
        rows=tuple(rows),
        times_ns=tuple(int(r.ts_event) for r in rows),
    )
    sliced = _price_rows_for_event(series, 1_700_000_000, rows[0].ts_event)
    assert len(sliced) == 120
    assert sliced[0].last_price == 100.0
    assert sliced[-1].last_price == 219.0