# WebSocket & REST contract (frozen)

**Do not break without updating:** `frontend/src/types.ts`, `backend/ws_contract.py`, and this file.

## WebSocket `GET /ws`

JSON messages; one object per frame. Optional `?symbols=` filters by `symbol`.

### Market data

| `type` | Producer | Notes |
|--------|----------|--------|
| `trade` | `BridgeActor` | Binance perp ticks |
| `bar` | `BridgeActor` / `RealtimeBucketActor` | OHLCV; `interval` e.g. `15m` or live `1s`/`5s` |
| `indicator` | `RealtimeBucketActor` | EMA / session VWAP / rolling VWAP point on `1s`/`5s` bucket close |
| `polymarket` | `PolymarketQuoteBridgeActor` | `yes_price` 0–1; optional `bid`/`ask` |
| `quote` | `BridgeActor` | Optional; Binance BBO |
| `liquidation` | `LiquidationUiBridgeActor` | Optional `bars[]` snapshot |

TypeScript union: `FeedMsg` in `frontend/src/types.ts`.

## REST (BFF)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/klines` | Historical OHLCV |
| GET | `/liquidations` | 15m liq bar totals |
| GET | `/liquidation-events` | Major-coin liq events |
| GET | `/liq-post-event/sessions` | Post-liq 30m % sessions from ParquetDataCatalog |
| GET | `/polymarket/markets` | Gamma search |
| GET | `/polymarket/presets` | Rolling 15m presets |
| POST | `/polymarket/subscribe` | `{ slug }` or `{ series }` |

### `GET /liq-post-event/sessions`

Query params: `symbols` (comma coins), `interval` (`30s` only; legacy `1s`/`5s` accepted), `min_notional`, `sides` (`LONG`,`SHORT`), optional `limit` (omit = all matching events in 7d lookback).

Response: `{ sessions: [{ session_id, symbol, side, notional, anchor_price, event_time, status, points[] }] }`.

## Change process

1. Update `types.ts` + `ws_contract.py` + this doc in the **same PR**.
2. Run `backend/scripts/run_smoke_tests.sh`.
3. Manual smoke: `docs/nautilus-migration-smoke.md`.
