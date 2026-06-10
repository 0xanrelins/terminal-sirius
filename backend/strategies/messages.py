"""
Typed Actor → Strategy messages (native Nautilus custom data).

Replaces the previous ``publish_signal`` string-packing (``"low,high,close"`` and
``base:symbol`` name encoding). Follows the official pattern in
``examples/backtest/example_10_messaging_with_actor_data``: a ``Data`` subclass
flows over the msgbus via ``Actor.publish_data`` / ``Strategy.subscribe_data`` and
arrives in ``on_data`` — typed, per-instrument, and backtest-compatible.

``@customdataclass`` adds ``ts_event``/``ts_init`` and serialization; all fields use
the supported types (``InstrumentId``, ``float``, ``bool``). Note: no
``from __future__ import annotations`` — ``@customdataclass`` introspects real type
objects to build its Arrow schema, which PEP 563 stringized annotations break.
"""

from nautilus_trader.core.data import Data
from nautilus_trader.model.custom import customdataclass_pyo3
from nautilus_trader.model.identifiers import InstrumentId


@customdataclass_pyo3()
class VwapZoneSnapshot(Data):
    """One VWAP/zone snapshot per closed bar for a Binance instrument."""

    instrument_id: InstrumentId
    vwap: float = 0.0
    slope: float = 0.0
    low_zone: float = 0.0
    high_zone: float = 0.0
    close: float = 0.0


@customdataclass_pyo3()
class LiquidationTrigger(Data):
    """Edge-triggered liquidation event; only the firing side is ``True``."""

    instrument_id: InstrumentId
    long_triggered: bool = False
    short_triggered: bool = False


@customdataclass_pyo3()
class LiquidationVolumeSnapshot(Data):
    """Rolling liquidation notional (USD) per side after each volume recompute."""

    instrument_id: InstrumentId
    long_volume: float = 0.0
    short_volume: float = 0.0
    long_hit: bool = False
    short_hit: bool = False


@customdataclass_pyo3()
class LiquidationVerdict(Data):
    """Causal post-liquidation path verdict for a single print."""

    instrument_id: InstrumentId
    event_id: str = ""
    liq_side: str = ""
    notional: float = 0.0
    event_price: float = 0.0
    winner: str = "neutral"
    liq_move_pct: float = 0.0
    recovery_move_pct: float = 0.0
    dominance_ratio: float = 0.0
    time_to_dominance_sec: float = 0.0
    area_bias: float = 0.0
    status: str = "completed"
    completion_reason: str = ""


@customdataclass_pyo3()
class LiquidationVerdictStatus(Data):
    """Open post-liquidation observations still inside the verdict window."""

    pending_total: int = 0
    pending_by_coin_json: str = "{}"
