"""Custom data subscriptions (live msgbus vs backtest catalog replay)."""
from __future__ import annotations

from nautilus_trader.model.data import DataType
from nautilus_trader.model.identifiers import ClientId

BACKTEST_CLIENT_ID = ClientId("BACKTEST")


def subscribe_custom_data(component, data_cls: type, *, backtest: bool) -> None:
    """
    Subscribe to a ``@customdataclass_pyo3`` type.

    Live: msgbus only (``publish_data`` from actors).
    Backtest: also ``ClientId("BACKTEST")`` so ``BacktestDataConfig`` rows are loaded.
    """
    data_type = DataType(data_cls)
    if backtest:
        component.subscribe_data(data_type, client_id=BACKTEST_CLIENT_ID)
    else:
        component.subscribe_data(data_type)
