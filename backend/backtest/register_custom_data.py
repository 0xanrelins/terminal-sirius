"""Register Terminal Sirius custom ``Data`` types for Rust catalog / backtest replay."""
from __future__ import annotations

_REGISTERED = False


def register_terminal_sirius_custom_data() -> None:
    """Call once before ``BacktestNode.run()`` (idempotent)."""
    global _REGISTERED
    if _REGISTERED:
        return

    from nautilus_trader.core.nautilus_pyo3.model import register_custom_data_class

    from adapters.polymarket.messages import ActivePolymarketMarket
    from recorders.data_types import LiquidationTick
    from strategies.messages import LiquidationTrigger, LiquidationVolumeSnapshot, VwapZoneSnapshot

    for cls in (
        LiquidationTick,
        VwapZoneSnapshot,
        LiquidationTrigger,
        LiquidationVolumeSnapshot,
        ActivePolymarketMarket,
    ):
        register_custom_data_class(cls)

    _REGISTERED = True
