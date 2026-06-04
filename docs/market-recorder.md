# Nautilus catalog streaming

Historical data for backtest and Liq Post Event research. Captured on the **same** `TradingNode` as the live backend (`node.py`).

## Runtime

Native Nautilus `StreamingConfig` writes feather/parquet while the node runs:

```bash
cd backend && ./scripts/run_catalog_recorder_daemon.sh start
```

Or foreground: `cd backend && ./scripts/run_backend.sh` with `CATALOG_STREAMING_ENABLED=true` (default).

Use `RELOAD=0` (default in `run_backend.sh`). `uvicorn --reload` restarts the Nautilus node and interrupts catalog flushes.

Archive snapshot (read-only): set `CATALOG_USE_ARCHIVE=1` or see `archive/parquet-catalog-2026-06-03/`.

## Environment

| Variable | Default | Purpose |
|----------|---------|---------|
| `CATALOG_STREAMING_ENABLED` | `true` | Native `StreamingConfig` on `TradingNode` |
| `CATALOG_PATH` | `backend/catalog/` | Parquet root |
| `RECORDER_FLUSH_INTERVAL_MS` | `1000` | Streaming flush interval |
| `RECORDER_MAX_BATCH_ROWS` | `5000` | Streaming batch size |

## Stored data (native types)

| Type | Source | Used by |
|------|--------|---------|
| `TradeTick` | Binance perp | Backtest, Liq Post Event (% lines) |
| `QuoteTick` | Polymarket | Backtest |
| `LiquidationTick` | `LiquidationFeedActor` → `catalog.write_data` | Backtest, liquidation signals |

Live feather files land under `catalog/live/<run-id>/`.

Legacy custom types (`BinanceSecondPrice`, `PolymarketSecondPrice`) may exist in older archive parquet.

## Scripts

```bash
cd backend
python scripts/repair_catalog.py                 # fix broken trade_tick metadata / rebuild from live
python scripts/repair_catalog.py --symbols BTCUSDT-PERP.BINANCE  # one symbol only (keeps others)
python scripts/write_instruments_to_catalog.py   # one-time instrument defs (repair also writes Binance)
python scripts/catalog_stats.py                  # row counts
python scripts/run_terminal_sirius_backtest.py   # backtest (needs data)
python scripts/import_to_catalog.py              # optional CandleFeed import
```

## Liq Post Event

`GET /liq-post-event/sessions` reads native catalog rows:

- Events: `BinanceFuturesLiquidation` (+ legacy `LiquidationTick` from imports)
- Prices: `TradeTick` aggregated to per-second last price (`recorders/second_prices.py`)

Requires `CATALOG_STREAMING_ENABLED=true` and enough live runtime to accumulate data.

## Smoke checklist

- Backend starts; log shows `Catalog streaming → …`
- After a few minutes: `python scripts/catalog_stats.py` shows `TradeTick` rows
- Liq Post Event endpoint returns sessions when liquidations + ticks exist in range
