"""Strategy indicator unit tests."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from strategies.indicators.rolling_liquidation_volume import RollingLiquidationVolume  # noqa: E402


def test_rolling_liquidation_prunes_old_events():
    ind = RollingLiquidationVolume(10)
    ind.update_long_liquidation(ts_event=0, notional=100.0)
    ind.update_long_liquidation(ts_event=5_000_000_000, notional=50.0)
    ind.update_long_liquidation(ts_event=11_000_000_000, notional=1.0)
    assert ind.long_volume == 51.0
