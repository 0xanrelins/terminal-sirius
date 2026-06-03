# Nautilus Market Recorder

Lightweight historical recorder for:

- Binance perpetual `TradeTick` -> 1-second last price snapshots.
- Polymarket `QuoteTick` -> 1-second UP/DOWN snapshots.
- Binance force-order liquidations -> event-level rows (no aggregation).

## Runtime

Recorder runs on the **same** `TradingNode` as the live backend (`node.py`).

Start:

`cd backend && ./scripts/run_backend.sh`

Parquet recording is **off** by default (`MARKET_RECORDER_ENABLED=false`). Historical data: `archive/parquet-catalog-2026-06-03/`.

`scripts/run_market_recorder.py` is deprecated (do not run a second node).

For continuous recording, run with `RELOAD=0` (default in `run_backend.sh`).
`uvicorn --reload` restarts the Nautilus node and interrupts parquet flushes.

Live catalog appends use `ParquetDataCatalog.write_data(..., skip_disjoint_check=True)`
per Nautilus docs (`concepts/data.md`).

### Environment

- `RECORDER_BINANCE_INSTRUMENTS`: CSV, default:
  `BTCUSDT-PERP.BINANCE,ETHUSDT-PERP.BINANCE,SOLUSDT-PERP.BINANCE,XRPUSDT-PERP.BINANCE,DOGEUSDT-PERP.BINANCE,HYPEUSDT-PERP.BINANCE`
- `RECORDER_POLYMARKET_SERIES`: CSV, default:
  `btc-updown-15m,eth-updown-15m,sol-updown-15m,xrp-updown-15m,doge-updown-15m,hype-updown-15m`
- `RECORDER_FLUSH_INTERVAL_MS`: default `1000`
- `RECORDER_MAX_BATCH_ROWS`: default `5000`
- `CATALOG_PATH`: optional parquet catalog root

## Stored Data Types

- `BinanceSecondPrice(ts_event, symbol, last_price, ts_init)`
- `PolymarketSecondPrice(ts_event, market, up_last_price, down_last_price, ts_init)`
- `BinanceLiquidationEvent(ts_event, symbol, side, price, quantity, ts_init)`

## Lookup

Use `backend/recorders/lookup.py` for nearest timestamp lookups:

- liquidation event time -> nearest Binance second price
- generic event time -> nearest Polymarket UP/DOWN snapshot

## Reconnect and Stability

- Actors resubscribe in `on_start`.
- Liquidation stream reconnect loop stays inside `LiquidationActor`.
- Catalog writer is bounded and flushes by interval or row limit.

## Smoke Checklist

- Backend starts; log shows `MarketRecorderActor enabled`.
- Binance and Polymarket snapshots are written every second under normal flow.
- Liquidation rows appear as event-level parquet entries.
- Process restart does not crash and resumes writing.
