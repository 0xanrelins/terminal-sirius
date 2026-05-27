# Nautilus migration — smoke checklist

Contract reference: [ws-api-contract.md](ws-api-contract.md)

## Offline regression (run first)

```bash
cd backend && chmod +x scripts/run_smoke_tests.sh && ./scripts/run_smoke_tests.sh
```

Run after backend restart (`uvicorn` + `.env` loaded).

## Backtest (catalog)

```bash
cd backend && python backtest.py --start 2026-04-01 --end 2026-05-01
```

Requires parquet catalog under `backend/catalog/data` (see import scripts). Uses `BacktestEngine` + `LiqPolyStrategy` mode `backtest` (no PostgreSQL).

## Polymarket data

- [ ] Log: `Polymarket DataClient enabled` + `PolymarketQuoteBridgeActor enabled`
- [ ] UI Polymarket ticker updates (`type: polymarket` on `/ws`)
- [ ] No flood of `Cannot find instrument` errors (sibling tokens loaded)

## Live execution

- [ ] `GET /live/status` → `exec_client_ready: true`, `orders_ready: true` (when `LIVE_ENABLED` + creds)
- [ ] Log: `Polymarket ExecutionClient enabled`
- [ ] Orders only via Nautilus (no `[live-order]` direct CLOB logs)

## Sim / live parity

- [ ] `pytest backend/tests/test_liq_poly_live_sim_parity.py` passes
- [ ] Same threshold on sim + live → both fire on identical liq bar (manual spot-check optional)

## Config / reset

- [ ] `POST /simulation/config` changes thresholds without restart
- [ ] `POST /simulation/reset` clears DB + refreshes sim runtime
- [ ] `POST /live/config` updates live runtime

## Unit tests (no network)

Same as offline regression: `./scripts/run_smoke_tests.sh` (uses `unittest`, no pytest required).
