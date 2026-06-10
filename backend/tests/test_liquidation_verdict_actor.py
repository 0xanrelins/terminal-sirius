"""LiquidationVerdictActor publish_data tests."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nautilus_trader.model.identifiers import InstrumentId, TradeId  # noqa: E402
from nautilus_trader.model.objects import Price  # noqa: E402
from nautilus_trader.model.objects import Quantity  # noqa: E402
from nautilus_trader.model.enums import AggressorSide  # noqa: E402
from nautilus_trader.model.data import TradeTick  # noqa: E402

from recorders.data_types import LiquidationTick  # noqa: E402
from strategies.config import LiquidationVerdictActorConfig  # noqa: E402
from strategies.liquidation_verdict_actor import LiquidationVerdictActor  # noqa: E402
from strategies.messages import LiquidationVerdict  # noqa: E402


def _trade(symbol: str, price: float, ts_ns: int) -> TradeTick:
    iid = InstrumentId.from_str(symbol)
    return TradeTick(
        instrument_id=iid,
        price=Price.from_str(str(price)),
        size=Quantity.from_str("1"),
        aggressor_side=AggressorSide.BUYER,
        trade_id=TradeId("1"),
        ts_event=ts_ns,
        ts_init=ts_ns,
    )


def test_publishes_verdict_after_recovery_threshold():
    cfg = LiquidationVerdictActorConfig(
        component_id="LiqVerdictActor-TEST",
        instrument_ids=("BTCUSDT-PERP.BINANCE",),
        liq_move_threshold_pct=0.2,
        recovery_move_threshold_pct=0.2,
        max_observation_sec=450,
        min_notional_btc=1.0,
    )
    actor = LiquidationVerdictActor(cfg)
    published: list = []
    actor.publish_data = lambda _dt, payload: published.append(payload)

    actor._on_trade_tick(_trade("BTCUSDT-PERP.BINANCE", 100.0, 0))
    actor._on_liquidation_tick(
        LiquidationTick(
            symbol="BTCUSDT-PERP.BINANCE",
            side="SELL",
            notional=50_000.0,
            price=100.0,
            quantity=500.0,
            ts_event=1_000_000_000,
            ts_init=1_000_000_000,
        )
    )
    actor._on_trade_tick(_trade("BTCUSDT-PERP.BINANCE", 100.21, 2_000_000_000))

    verdicts = [p for p in published if isinstance(p, LiquidationVerdict)]
    assert len(verdicts) >= 1
    last = verdicts[-1]
    assert last.liq_side == "LONG"
    assert last.winner == "recovery"
    assert last.completion_reason == "recovery_threshold"


def test_same_ms_ticks_keep_all_open_events():
    cfg = LiquidationVerdictActorConfig(
        component_id="LiqVerdictActor-TEST",
        instrument_ids=("BTCUSDT-PERP.BINANCE",),
        liq_move_threshold_pct=0.2,
        recovery_move_threshold_pct=0.2,
        max_observation_sec=450,
        min_notional_btc=1.0,
    )
    actor = LiquidationVerdictActor(cfg)
    actor.publish_data = lambda *_a, **_k: None

    actor._on_trade_tick(_trade("BTCUSDT-PERP.BINANCE", 100.0, 0))
    for order_id in (101, 102, 103):
        actor._on_liquidation_tick(
            LiquidationTick(
                symbol="BTCUSDT-PERP.BINANCE",
                side="SELL",
                notional=50_000.0,
                price=100.0,
                quantity=500.0,
                order_id=order_id,
                ts_event=1_000_000_000,
                ts_init=1_000_000_000,
            )
        )

    assert actor._trackers["BTCUSDT-PERP.BINANCE"].open_count == 3
