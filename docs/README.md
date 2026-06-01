# Documentation index

**Start here** for current system behavior.

| Doc | Use when |
|-----|----------|
| [architecture.md](architecture.md) | Layers, Nautilus `TradingNode`, liquidation single-writer, what was removed |
| [ws-api-contract.md](ws-api-contract.md) | Frozen `/ws` and REST shapes (`types.ts`, `ws_contract.py`) |
| [market-recorder.md](market-recorder.md) | Parquet `MarketRecorderActor` on the shared node |
| [nautilus-migration-smoke.md](nautilus-migration-smoke.md) | Offline smoke + manual restart checklist |
| [roadmap.md](roadmap.md) | Next work: new `Strategy` on Nautilus (no BFF trade bridge) |

## Nautilus rule of thumb

- **Data / indicators:** `Actor` + `DataClient` → `data_queue` → FastAPI `/ws`
- **Future orders:** `Strategy` subclass → `submit_order` → `PolymarketExecutionClient` (already on node when `POLYMARKET_EXEC_ENABLED`)
- **Do not reintroduce:** FastAPI `strategy_runtime`, custom persist loops, or parallel paper/live motors
