# Parquet catalog archive

**Archived:** 2026-06-03 · **Moved out of repo:** 2026-06-04

> The ~200 MB snapshot was moved to **`~/Documents/sirius-archive/parquet-catalog-2026-06-03/`** to keep this project folder light. Only this pointer stays in the repo.

Legacy custom parquet (`BinanceSecondPrice`, `PolymarketSecondPrice`). Current live capture uses native `StreamingConfig` → `TradeTick`, `QuoteTick`, `BinanceFuturesLiquidation` under `backend/catalog/`.

## `parquet-catalog-2026-06-03/`

| Type | Folder |
|------|--------|
| Binance 1s last price | `data/custom_binance_second_price/` |
| Polymarket 1s UP/DOWN | `data/custom_polymarket_second_price/` |
| Binance liquidations | `data/custom_binance_liquidation_event/` |
| Import (legacy) | `data/bar/`, `data/custom_liquidation_tick/` |

Read via `CATALOG_PATH=~/Documents/sirius-archive/parquet-catalog-2026-06-03`, or set `CATALOG_USE_ARCHIVE=1` (resolves to that path in `backend/catalog/__init__.py`; override with `CATALOG_ARCHIVE_PATH`).
