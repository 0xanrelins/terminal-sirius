"""
Typed Polymarket discovery data (native Nautilus custom data).

``ActivePolymarketMarket`` announces the currently-active YES/Up instrument for a
rolling 15m series. ``PolymarketQuoteBridgeActor`` (which already owns slug
rotation + instrument loading) publishes it via ``publish_data``; the strategy
consumes it via ``subscribe_data`` / ``on_data`` instead of doing its own HTTP
discovery. Lives in the Polymarket adapter package so strategy code depends on the
adapter (not the reverse).

No ``from __future__ import annotations`` — ``@customdataclass`` introspects real
type objects for its Arrow schema, which PEP 563 stringized annotations break.
"""

from nautilus_trader.core.data import Data
from nautilus_trader.model.custom import customdataclass
from nautilus_trader.model.identifiers import InstrumentId


@customdataclass
class ActivePolymarketMarket(Data):
    """Active Polymarket instruments for a rolling 15m series (both outcome tokens).

    ``instrument_id`` is the YES/Up token, ``no_instrument_id`` is the NO/Down token.
    The strategy goes long by BUYing YES and short by BUYing NO (Polymarket has no
    short-sell; a CASH account can only buy the outcome it wants).
    """

    instrument_id: InstrumentId
    no_instrument_id: InstrumentId
    series: str = ""
