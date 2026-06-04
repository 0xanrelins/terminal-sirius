"""Custom Nautilus data types (legacy archive + CandleFeed import only).

Live capture: ``TradeTick`` / ``QuoteTick`` via ``StreamingConfig``;
``LiquidationTick`` via ``LiquidationFeedActor`` → ``ParquetDataCatalog.write_data``.
Liq Post Event reads ``TradeTick`` from the catalog.
"""
from nautilus_trader.core.data import Data
from nautilus_trader.model.custom import customdataclass
from nautilus_trader.model.custom import customdataclass_pyo3


@customdataclass
class BinanceSecondPrice(Data):
    """One-second last trade snapshot for a Binance perpetual symbol."""

    symbol: str = ""
    last_price: float = 0.0


@customdataclass
class PolymarketSecondPrice(Data):
    """One-second up/down snapshot for a Polymarket rolling market."""

    market: str = ""
    up_last_price: float = 0.0
    down_last_price: float = 0.0


@customdataclass_pyo3()
class LiquidationTick(Data):
    """Single liquidation event for ParquetDataCatalog replay."""

    symbol: str = ""
    side: str = ""
    notional: float = 0.0
    price: float = 0.0
    quantity: float = 0.0
