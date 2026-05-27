"""Custom Nautilus data types for liq → strategy pipeline and catalog storage."""
from nautilus_trader.core.data import Data
from nautilus_trader.model.custom import customdataclass


@customdataclass
class LiqBar15mUpdate(Data):
    """Published when a 15m liq bar bucket changes."""

    symbol: str = ""
    bar_open: int = 0
    long_total: float = 0.0
    short_total: float = 0.0
    signal_ts: int = 0


@customdataclass
class LiquidationTick(Data):
    """Single forced-liquidation event — stored in DataCatalog for backtest."""

    symbol: str = ""
    side: str = ""       # SELL (long liq) | BUY (short liq)
    price: float = 0.0
    quantity: float = 0.0
    usd_value: float = 0.0
