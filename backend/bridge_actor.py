import queue
from nautilus_trader.common.actor import Actor
from nautilus_trader.config import ActorConfig
from nautilus_trader.model.data import Bar, BarType, TradeTick, QuoteTick
from nautilus_trader.model.identifiers import InstrumentId


# Bar types to subscribe to per instrument (interval -> Nautilus bar type suffix)
BAR_SPECS = [
    "1-MINUTE-LAST-EXTERNAL",
    "5-MINUTE-LAST-EXTERNAL",
    "15-MINUTE-LAST-EXTERNAL",
    "1-HOUR-LAST-EXTERNAL",
    "4-HOUR-LAST-EXTERNAL",
    "1-DAY-LAST-EXTERNAL",
]


class BridgeActorConfig(ActorConfig, frozen=True):
    instrument_ids: tuple[str, ...]


class BridgeActor(Actor):
    """Subscribes to Nautilus data events and forwards them to a thread-safe Queue."""

    def __init__(self, config: BridgeActorConfig, data_queue: queue.Queue) -> None:
        super().__init__(config)
        self._instrument_ids = [InstrumentId.from_str(i) for i in config.instrument_ids]
        self._queue = data_queue

    def on_start(self) -> None:
        for iid in self._instrument_ids:
            self.subscribe_trade_ticks(iid)
            self.subscribe_quote_ticks(iid)
            for spec in BAR_SPECS:
                bar_type = BarType.from_str(f"{iid}-{spec}")
                self.subscribe_bars(bar_type)

    def on_trade_tick(self, tick: TradeTick) -> None:
        self._queue.put_nowait({
            "type": "trade",
            "symbol": str(tick.instrument_id),
            "price": str(tick.price),
            "size": str(tick.size),
            "side": str(tick.aggressor_side),
            "ts": tick.ts_event,
        })

    def on_quote_tick(self, tick: QuoteTick) -> None:
        self._queue.put_nowait({
            "type": "quote",
            "symbol": str(tick.instrument_id),
            "bid": str(tick.bid_price),
            "ask": str(tick.ask_price),
            "bid_size": str(tick.bid_size),
            "ask_size": str(tick.ask_size),
            "ts": tick.ts_event,
        })

    def on_bar(self, bar: Bar) -> None:
        spec = bar.bar_type.spec
        self._queue.put_nowait({
            "type": "bar",
            "symbol": str(bar.bar_type.instrument_id),
            "interval": _spec_to_interval(spec),
            "open": str(bar.open),
            "high": str(bar.high),
            "low": str(bar.low),
            "close": str(bar.close),
            "volume": str(bar.volume),
            "ts": bar.ts_event,
        })


def _spec_to_interval(spec) -> str:
    """Convert Nautilus BarSpecification to lightweight-charts interval string."""
    step = spec.step
    agg = str(spec.aggregation)
    mapping = {
        "MINUTE": "m",
        "HOUR": "h",
        "DAY": "d",
        "WEEK": "w",
        "MONTH": "M",
    }
    for key, suffix in mapping.items():
        if key in agg:
            return f"{step}{suffix}"
    return f"{step}{agg}"
