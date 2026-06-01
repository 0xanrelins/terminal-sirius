# Nautilus — smoke checklist

Post–trade-removal: node is **actor-only** (no `LiqPolyStrategy`). Contract: [ws-api-contract.md](ws-api-contract.md). Index: [README.md](README.md).

## Offline regression (run first)

```bash
cd backend && chmod +x scripts/run_smoke_tests.sh && ./scripts/run_smoke_tests.sh
```

Run after backend restart (`./scripts/run_backend.sh` or `uvicorn` + `.env` loaded). Logs: `backend/logs/uvicorn.log`.

## Polymarket data

- [ ] Log: `Polymarket DataClient enabled` + `PolymarketQuoteBridgeActor enabled`
- [ ] UI Polymarket ticker updates (`type: polymarket` on `/ws`)
- [ ] No flood of `Cannot find instrument` errors (sibling tokens loaded)

## Execution client (idle)

- [ ] Log: `Polymarket ExecutionClient enabled (idle, no strategies)` when `POLYMARKET_EXEC_ENABLED` + creds
- [ ] No `LiqPolyStrategy` or `StrategyMonitorActor` in startup logs

## Feed / liquidations

- [ ] `GET /liquidations` returns bars
- [ ] Liq Signals widget receives `type: liquidation` on `/ws`
- [ ] `/simulation/*` and `/live/*` return 404

## Unit tests (no network)

`./scripts/run_smoke_tests.sh` (uses `unittest`, no pytest required).
