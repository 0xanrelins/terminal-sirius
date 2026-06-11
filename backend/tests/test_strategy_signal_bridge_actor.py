"""StrategySignalBridgeActor snapshot tests."""

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from adapters.polymarket.messages import ActivePolymarketMarket  # noqa: E402
from nautilus_trader.model.identifiers import InstrumentId  # noqa: E402
from recorders.data_types import LiquidationTick  # noqa: E402
from strategy_signal_bridge_actor import StrategySignalBridgeActor  # noqa: E402
from strategy_signal_bridge_actor import StrategySignalBridgeActorConfig  # noqa: E402

SYMBOL = "BTCUSDT-PERP.BINANCE"
YES = InstrumentId.from_str("0xyes.POLYMARKET")
NO = InstrumentId.from_str("0xno.POLYMARKET")


def _bridge(*, trade_enabled: bool = True) -> StrategySignalBridgeActor:
    return StrategySignalBridgeActor(
        config=StrategySignalBridgeActorConfig(
            component_id="bridge-test",
            instrument_ids=(SYMBOL,),
            strategy_id="fresh_paper",
            trade_enabled=trade_enabled,
            min_seconds_to_expiry_for_entry=200,
        ),
        data_queue=MagicMock(),
    )


def _attach_clock(bridge: StrategySignalBridgeActor, *, timestamp_ns: int) -> None:
    clock = MagicMock()
    clock.timestamp_ns.return_value = timestamp_ns
    type(bridge).clock = property(lambda self: clock)


def test_snapshot_includes_fresh_paper_meta_and_market_state():
    bridge = _bridge()
    _attach_clock(bridge, timestamp_ns=1_780_815_000_000_000_000)
    bridge.subscribe_quote_ticks = MagicMock()
    bridge.handle_data(
        ActivePolymarketMarket(
            instrument_id=YES,
            no_instrument_id=NO,
            series="btc-updown-15m",
            slug="btc-updown-15m-1780814700",
            question="BTC up?",
            ts_event=1,
            ts_init=1,
        )
    )
    bridge._states[SYMBOL].last_price = 100_123.45
    bridge._states[SYMBOL].yes_ask = 0.42
    bridge._states[SYMBOL].no_ask = 0.58

    snap = bridge._build_snapshot()

    assert snap["strategy_id"] == "fresh_paper"
    assert snap["trade_enabled"] is True
    row = snap["symbols"][SYMBOL]
    assert row["market_ready"] is True
    assert row["active_slug"] == "btc-updown-15m-1780814700"
    assert row["last_price"] == 100_123.45
    assert row["yes_ask"] == 0.42
    assert row["entry_allowed"] is True
    assert row["decision"] == "HOLD"


def test_liquidation_tick_sets_long_trigger_when_trade_enabled():
    bridge = _bridge(trade_enabled=True)
    bridge.handle_data(
        LiquidationTick(
            symbol=SYMBOL,
            side="SELL",
            notional=12_000.0,
            price=100.0,
            quantity=1.0,
            order_id=1,
            ts_event=1,
            ts_init=1,
        )
    )
    row = bridge._build_snapshot()["symbols"][SYMBOL]
    assert row["liq_long_trigger"] is True
    assert row["decision"] == "LONG"
