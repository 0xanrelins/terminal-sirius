"""Custom Nautilus data types for lightweight market recorder."""
from nautilus_trader.core.data import Data
from nautilus_trader.model.custom import customdataclass


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


@customdataclass
class BinanceLiquidationEvent(Data):
    """Single Binance force-order liquidation event (no aggregation)."""

    symbol: str = ""
    side: str = ""  # SELL -> long liquidation, BUY -> short liquidation
    price: float = 0.0
    quantity: float = 0.0


@customdataclass
class LiquidationTick(Data):
    """Single liquidation event for ParquetDataCatalog replay."""

    symbol: str = ""
    side: str = ""
    notional: float = 0.0
    price: float = 0.0
    quantity: float = 0.0
