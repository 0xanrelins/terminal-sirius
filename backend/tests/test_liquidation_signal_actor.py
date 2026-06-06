"""LiquidationSignalActor publish_data tests."""

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from strategies.config import LiquidationSignalActorConfig  # noqa: E402
from strategies.liquidation_signal_actor import LiquidationSignalActor  # noqa: E402
from strategies.messages import LiquidationVolumeSnapshot  # noqa: E402
from recorders.data_types import LiquidationTick  # noqa: E402


def test_publishes_volume_snapshot_on_liq_tick():
    cfg = LiquidationSignalActorConfig(
        component_id="LiqSignalActor-TEST",
        instrument_ids=("SOLUSDT-PERP.BINANCE",),
        liq_threshold_sol=100.0,
    )
    actor = LiquidationSignalActor(cfg)
    published: list = []
    actor.publish_data = lambda _dt, payload: published.append(payload)

    tick = LiquidationTick(
        symbol="SOLUSDT-PERP.BINANCE",
        side="SELL",
        notional=150.0,
        ts_event=1_000_000_000,
        ts_init=1_000_000_000,
    )
    actor.on_data(tick)

    volume_snaps = [p for p in published if isinstance(p, LiquidationVolumeSnapshot)]
    assert len(volume_snaps) == 1
    snap = volume_snaps[0]
    assert snap.long_volume == 150.0
    assert snap.long_hit is True
    assert snap.short_hit is False
