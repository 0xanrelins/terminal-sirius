# Parquet catalog archive

**Archived:** 2026-06-03

Market recorder stopped (`MARKET_RECORDER_ENABLED=false`). Live writes go to `backend/catalog/data/` only if recording is re-enabled.

## `parquet-catalog-2026-06-03/`

| Type | Folder |
|------|--------|
| Binance 1s last price | `data/custom_binance_second_price/` |
| Polymarket 1s UP/DOWN | `data/custom_polymarket_second_price/` |
| Binance liquidations | `data/custom_binance_liquidation_event/` |
| Import (legacy) | `data/bar/`, `data/custom_liquidation_tick/` |

Read via `CATALOG_PATH=archive/parquet-catalog-2026-06-03` (also the default in `backend/catalog/__init__.py`).
