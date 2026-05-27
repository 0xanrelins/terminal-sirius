"""
BacktestEngine — replays catalog LiquidationTick + Bar data through LiqPolyStrategy.

  DataCatalog (LiquidationTick, Bar)
    → BacktestEngine
    → LiqAggActor → LiqBar15mUpdate
    → LiqPolyStrategy (mode=backtest, paper sim, no PostgreSQL)

Usage:
  cd backend && python backtest.py
  cd backend && python backtest.py --start 2026-04-01 --end 2026-05-01
"""
from __future__ import annotations

import argparse
import asyncio
import queue
import sys
from datetime import datetime, timezone
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent
sys.path.insert(0, str(_BACKEND))

from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.config import BacktestEngineConfig, LoggingConfig
from nautilus_trader.model.currencies import USDT
from nautilus_trader.model.data import Bar, BarType, DataType
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.identifiers import InstrumentId, Venue
from nautilus_trader.model.instruments import CryptoPerpetual
from nautilus_trader.model.objects import Money, Price, Quantity
from nautilus_trader.trading.actor import Actor, ActorConfig

from catalog import get_catalog
from liquidations import bucket_time, binance_to_nautilus
from nautilus_bridge.strategy_runtime import set_event_queue, set_main_loop, set_runtime
from strategies.liq_poly_config import runtime_for_backtest
from strategies.liq_poly_data import LiqBar15mUpdate, LiquidationTick
from strategies.liq_poly_strategy import LiqPolyStrategy, LiqPolyStrategyConfig

BINANCE = Venue("BINANCE")
DEFAULT_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT", "XRPUSDT")

_PRICE_PREC: dict[str, int] = {
    "BTCUSDT": 1, "ETHUSDT": 2, "SOLUSDT": 3,
    "DOGEUSDT": 5, "XRPUSDT": 4,
}
_SIZE_PREC: dict[str, int] = {
    "BTCUSDT": 3, "ETHUSDT": 3, "SOLUSDT": 1,
    "DOGEUSDT": 0, "XRPUSDT": 1,
}


def _make_instrument(sym: str) -> CryptoPerpetual:
    base = sym.replace("USDT", "")
    pp = _PRICE_PREC.get(sym, 2)
    sp = _SIZE_PREC.get(sym, 3)
    iid = InstrumentId.from_str(f"{sym}-PERP.BINANCE")
    from nautilus_trader.model.identifiers import Symbol
    from nautilus_trader.model.currencies import Currency

    return CryptoPerpetual(
        instrument_id=iid,
        raw_symbol=Symbol(sym),
        base_currency=Currency.from_str(base),
        quote_currency=USDT,
        settlement_currency=USDT,
        is_inverse=False,
        price_precision=pp,
        size_precision=sp,
        price_increment=Price(10 ** -pp, pp),
        size_increment=Quantity(10 ** -sp, sp),
        max_quantity=None,
        min_quantity=None,
        max_notional=None,
        min_notional=None,
        max_price=None,
        min_price=None,
        margin_init=None,
        margin_maint=None,
        maker_fee=None,
        taker_fee=None,
        ts_event=0,
        ts_init=0,
    )


class LiqAggActorConfig(ActorConfig, frozen=True):
    bar_seconds: int = 900


class LiqAggActor(Actor):
    def __init__(self, config: LiqAggActorConfig) -> None:
        super().__init__(config)
        self._buckets: dict[tuple[str, int], dict[str, float]] = {}

    def on_start(self) -> None:
        self.subscribe_data(DataType(LiquidationTick))

    def on_data(self, data: LiquidationTick) -> None:  # type: ignore[override]
        if not isinstance(data, LiquidationTick):
            return
        ts_ms = data.ts_event // 1_000_000
        bar_open = bucket_time(ts_ms, "15m")
        key = (data.symbol, bar_open)
        bucket = self._buckets.setdefault(key, {"long": 0.0, "short": 0.0})
        if data.side == "SELL":
            bucket["long"] += data.usd_value
        elif data.side == "BUY":
            bucket["short"] += data.usd_value
        self.publish_data(
            data_type=DataType(LiqBar15mUpdate),
            data=LiqBar15mUpdate(
                symbol=data.symbol,
                bar_open=bar_open,
                long_total=round(bucket["long"], 2),
                short_total=round(bucket["short"], 2),
                signal_ts=int(data.ts_event // 1_000_000_000),
                ts_event=data.ts_event,
                ts_init=data.ts_init,
            ),
        )


def _wire_backtest_runtime() -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    set_main_loop(loop)
    set_event_queue(queue.Queue(maxsize=10_000))
    set_runtime("sim", runtime_for_backtest())


def build_engine(
    symbols: tuple[str, ...] = DEFAULT_SYMBOLS,
    start: datetime | None = None,
    end: datetime | None = None,
    catalog_path: Path | None = None,
) -> BacktestEngine:
    _wire_backtest_runtime()
    catalog = get_catalog(catalog_path)

    engine = BacktestEngine(
        config=BacktestEngineConfig(
            logging=LoggingConfig(log_level="INFO", bypass_logging=False),
        )
    )

    engine.add_venue(
        venue=BINANCE,
        oms_type=OmsType.NETTING,
        account_type=AccountType.MARGIN,
        base_currency=USDT,
        starting_balances=[Money(100_000, USDT)],
    )

    for sym in symbols:
        engine.add_instrument(_make_instrument(sym))

    bar_types = [
        BarType.from_str(f"{binance_to_nautilus(s)}-1-MINUTE-LAST-EXTERNAL")
        for s in symbols
    ]
    all_bars: list[Bar] = []
    for bt in bar_types:
        try:
            chunk = catalog.bars([bt], start=start, end=end)
            all_bars.extend(chunk)
        except Exception as e:
            print(f"[warn] bars not found for {bt}: {e}")
    if all_bars:
        all_bars.sort(key=lambda b: b.ts_init)
        engine.add_data(all_bars)
        print(f"Loaded {len(all_bars)} bars from catalog")

    all_ticks: list[LiquidationTick] = []
    try:
        raw = catalog.custom_data(
            data_cls=LiquidationTick,
            start=start,
            end=end,
        )
        all_ticks = sorted(raw, key=lambda t: t.ts_init)
    except Exception as e:
        print(f"[warn] LiquidationTick not found in catalog: {e}")
    if all_ticks:
        engine.add_data(all_ticks)
        print(f"Loaded {len(all_ticks)} LiquidationTick objects from catalog")

    engine.add_actor(LiqAggActor(LiqAggActorConfig(component_id="LiqAggActor-001")))
    engine.add_strategy(
        LiqPolyStrategy(config=LiqPolyStrategyConfig(strategy_id="LiqPoly-Backtest", mode="backtest"))
    )

    return engine


def run(
    symbols: tuple[str, ...] = DEFAULT_SYMBOLS,
    start: datetime | None = None,
    end: datetime | None = None,
    catalog_path: Path | None = None,
) -> BacktestEngine:
    engine = build_engine(symbols=symbols, start=start, end=end, catalog_path=catalog_path)
    engine.run()
    return engine


def _parse_dt(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run LiqPolyStrategy backtest (BacktestEngine)")
    p.add_argument("--start", type=str, help="ISO8601 start (e.g. 2026-04-01)")
    p.add_argument("--end", type=str, help="ISO8601 end (e.g. 2026-05-01)")
    p.add_argument("--catalog", type=Path, default=None)
    p.add_argument(
        "--symbols", nargs="+", default=list(DEFAULT_SYMBOLS),
        help="Binance symbols to backtest",
    )
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    engine = run(
        symbols=tuple(s.upper() for s in args.symbols),
        start=_parse_dt(args.start) if args.start else None,
        end=_parse_dt(args.end) if args.end else None,
        catalog_path=args.catalog,
    )
    print("\n--- Account Report ---")
    print(engine.trader.generate_account_report(BINANCE))
