# WebSocket & REST contract (frozen)

**Do not break without updating:** `frontend/src/types.ts`, `backend/ws_contract.py`, and this file.

## WebSocket `GET /ws`

JSON messages; one object per frame. Global events (`simulation_*`, `live_*`) are not filtered by `?symbols=`.

### Market data

| `type` | Producer | Notes |
|--------|----------|--------|
| `trade` | `BridgeActor` | Binance perp ticks |
| `bar` | `BridgeActor` | OHLCV; `interval` e.g. `15m` |
| `polymarket` | `PolymarketQuoteBridgeActor` | `yes_price` 0–1; optional `bid`/`ask` |
| `quote` | `BridgeActor` | Optional; Binance BBO |
| `liquidation` | `LiquidationActor` / stream | Optional `bars[]` snapshot |

### Simulation

| `type` | When |
|--------|------|
| `simulation_signal` | Leg-1 signal (paper) |
| `simulation_bet_open` | Paper bet opened |
| `simulation_bet_settle` | Paper bet settled |
| `simulation_cycle_closed` | Cycle complete (`cycle_id`, `asset`, `side`) |

### Live

| `type` | When |
|--------|------|
| `live_signal` | Signal (incl. `dry_run` when orders off) |
| `live_bet_open` | Real bet recorded |
| `live_bet_settle` | Bet settled |
| `live_cycle_closed` | Cycle complete (`cycle_id`, `asset`, `side`) |
| `live_order_error` | Nautilus/CLOB reject |

TypeScript unions: `FeedMsg` in `frontend/src/types.ts`.

## REST (BFF)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/klines` | Historical OHLCV |
| GET | `/liquidations` | 15m liq bar totals |
| GET | `/liquidation-events` | Major-coin liq events |
| GET | `/polymarket/markets` | Gamma search |
| GET | `/polymarket/presets` | Rolling 15m presets |
| POST | `/polymarket/subscribe` | `{ slug }` or `{ series }` |
| GET | `/simulation/status` | Paper stats + config |
| GET | `/simulation/bets` | Paper history |
| POST | `/simulation/reconcile` | Strategy catch-up |
| POST | `/simulation/reset` | Clear paper DB |
| POST | `/simulation/config` | Env + runtime refresh |
| GET | `/live/status` | Live stats + exec readiness |
| GET | `/live/bets` | Live history |
| POST | `/live/reconcile` | Strategy catch-up |
| POST | `/live/config` | Env + runtime refresh |

## Pricing (entry / live)

Polymarket **prices** for bets come from Nautilus `DataClient` quotes (`quote_registry` / cache), not Gamma `outcomePrices` or direct CLOB REST. Gamma is metadata only (slug, token ids, search).

## Change process

1. Update `types.ts` + `ws_contract.py` + this doc in the **same PR**.
2. Run `backend/scripts/run_smoke_tests.sh`.
3. Manual smoke: `docs/nautilus-migration-smoke.md`.
