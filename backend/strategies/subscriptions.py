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
        # Live intra-node custom data (actors -> strategy via publish_data) does not
        # require a DataClient command. Newer Nautilus runtimes require client_id or
        # instrument_id for subscribe_data(), so subscribe directly on the msgbus topic.
        topic = f"data.{data_type.topic}"
        component.msgbus.subscribe(topic=topic, handler=component.handle_data)
