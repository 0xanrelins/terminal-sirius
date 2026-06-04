# Parquet catalog archive

**Archived:** 2026-06-03

Legacy custom parquet (`BinanceSecondPrice`, `PolymarketSecondPrice`). Current live capture uses native `StreamingConfig` → `TradeTick`, `QuoteTick`, `BinanceFuturesLiquidation` under `backend/catalog/`.

## `parquet-catalog-2026-06-03/`

| Type | Folder |
|------|--------|
| Binance 1s last price | `data/custom_binance_second_price/` |
| Polymarket 1s UP/DOWN | `data/custom_polymarket_second_price/` |
| Binance liquidations | `data/custom_binance_liquidation_event/` |
| Import (legacy) | `data/bar/`, `data/custom_liquidation_tick/` |

Read via `CATALOG_PATH=archive/parquet-catalog-2026-06-03` (also the default in `backend/catalog/__init__.py`).
