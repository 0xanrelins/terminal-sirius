# Tam Nautilus entegrasyonu — plan

> **2026-06:** Legacy `LiqPolyStrategy` (paper sim + live + monitor/backtest) kaldırıldı. TradingNode şu an yalnızca veri actor'ları + isteğe bağlı idle `PolymarketExecutionClient` çalıştırır. Yeni stratejiler sıfırdan `Strategy` + `submit_order` ile eklenecek.

## Master plan (kısa)

**Vizyon**

- **Çekirdek:** `TradingNode` içinde tek veri yüzeyi (Binance + Polymarket `DataClient`), tek exec yüzeyi (`PolymarketExecutionClient`), strateji + cache tek otorite.
- **BFF:** FastAPI yalnızca HTTP/WS, persist, konfig toggles; strateji/state üretmez.

**İlkeler**

1. **Nautilus tek otorite** — yeni stratejilerde sinyal, emir ve pozisyon state'i `Strategy` + `Cache` / `Portfolio` üzerinden.
2. **UI sözleşmesi** — market data WS tipleri sabit (`trade`, `bar`, `liquidation`, `polymarket`, …); trade UI kaldırıldı.
3. **Liquidation single-writer** — [`liquidations.py`](../backend/liquidations.py) + `LiquidationActor`; bypass yok.
4. **BFF strateji üretmez** — FastAPI yalnızca feed, klines, liquidation persist; emir köprüsü yok.

**Bitti sayılır (ölçülebilir)**

- Polymarket için doğrudan CLOB WS → `queue` yolu yok (hedef: tamamen kaldırılmış).
- `TradingNode` içinde Polymarket data client/factory tanımlı ve çalışır.
- Polymarket ticker/quote kaynağı Nautilus DataEngine/cache olur.
- Live emir tek yol: `submit_order` → ExecEngine/ExecClient.
- Direct CLOB emir helper path'leri (`nautilus_bridge/polymarket_exec.py`, duplicate order helpers) kaldırılmış veya devre dışı.
- Tek env hikayesi vardır (Nautilus isimleri veya tek map katmanı).
- App-level reconcile kaldırılmıştır veya yalnızca dokümante edilmiş startup smoke olarak kalır.

## Bugün vs hedef

| | Bugün (repo) | Sonraki strateji |
|---|--------|--------|
| Veri | Nautilus DataClient + Actors → queue | Aynı |
| Strateji | Yok (`TradingNode` actor-only) | `Strategy` on `TradingNode` |
| Emir | Idle `PolymarketExecutionClient` (creds + `POLYMARKET_EXEC_ENABLED`) | `Strategy.submit_order()` |
| Liq→Poly trade | Kaldırıldı (legacy BFF + DB) | Yeniden, yalnızca Nautilus lifecycle |

## Ne taşınır, ne kalkar

**Kalır (BFF):** FastAPI HTTP/WS, PostgreSQL (klines, liquidation bars/events), React chart/liq widget'ları.

**Kalktı:** `LiqPolyStrategy`, paper/live DB, `strategy_runtime` / `strategy_persist`, sandbox sim, Strategy Monitor, `backtest.py` iskeleti, direct CLOB helpers, custom Polymarket WS actor.

**Sonraki taşınma (yeni strateji):** 15m liq eşiği, Polymarket instrument seçimi, `submit_order`, settle — hepsi `Strategy` + ExecEngine içinde.

## Execution backlog (Faz 0–5)

### Faz 0 — Freeze ve güvenlik ağı ✅

- [x] **Scope:** [ws-api-contract.md](ws-api-contract.md) + `backend/ws_contract.py` donduruldu.
- [x] **Scope:** `backend/scripts/run_smoke_tests.sh` offline regression (stdlib only).
- [x] **DoD:** [.github/pull_request_template.md](../.github/pull_request_template.md) kontrat checklist.
- [x] **Rollback:** Kontrat PR'ı geri al veya smoke kırmızıysa merge etme.

### Faz 1 — Polymarket data içeri alınır (en büyük mimari düzeltme)

- [x] **Scope:** `TradingNode` içine `PolymarketDataClientConfig` + `PolymarketLiveDataClientFactory` eklenir; `POLYMARKET` data venue aktif olur.
- [x] **DoD:** Polymarket ticker/quote Nautilus DataEngine/cache'ten gelir (`PolymarketQuoteBridgeActor`); YES+NO token cache fix.
- [x] **Rollback:** `POLYMARKET_DATA_ENABLED=false` (Polymarket ticker kapalı).

### Faz 2 — PolymarketActor deprecation/temizlik ✅

- [x] **Scope:** `adapters/polymarket/actor.py` silindi; `/polymarket/subscribe` yalnız quote bridge.
- [x] **DoD:** Custom CLOB WS actor repo'da yok; tek Polymarket veri yolu DataClient + bridge.
- [x] **Rollback:** Git history veya `POLYMARKET_DATA_ENABLED=false` (ticker yok).

### Faz 3 — Exec ve emir tek yol ✅

- [x] **Scope:** `nautilus_env.prepare_polymarket_env()` tek giriş; node data/exec config ortak wallet struct.
- [x] **Scope:** `LiveExecEngineConfig.open_check_interval_secs` (default 30s, `POLYMARKET_OPEN_CHECK_INTERVAL_SEC`).
- [x] **DoD:** Direct CLOB helper yok; exec client node'da kayıtlı.
- [ ] **Sonraki:** Yeni strateji `submit_order` + ExecEngine open-check kullanır (`POLYMARKET_EXEC_ENABLED`).

### Faz 4 — Dead code silme ✅

- [x] **Scope:** `polymarket_exec.py` silindi; `orders.py` credential-only.
- [x] **Scope:** Legacy liq-poly stack tamamen kaldırıldı (2026-06).
- [x] **DoD:** `nautilus_bridge/`, `simulation/`, `live/`, `monitor/`, `backtest.py` yok.

### Faz 5 — Legacy trade kaldırıldı ✅

- [x] **Scope:** `LiqPolyStrategy`, BFF persist, sim/live REST, trade UI widget'ları.
- [x] **DoD:** `TradingNode` actor-only + idle exec client; smoke tests yeşil.

## Startup sırası (bugün)

`prepare_polymarket_env()` → `TradingNode` (data actors + optional exec) → FastAPI `_broadcast_loop` (`data_queue` only).

Smoke checklist: [nautilus-migration-smoke.md](nautilus-migration-smoke.md)

## Bu hafta sprinti (Faz 0–1)

### Sprint hedefi

- Faz 0'ı tamamen bitirmek, Faz 1'i "çalışır ama legacy fallback mevcut" seviyesine getirmek.

### Sprint backlog (öncelik sırası)

1. [x] UI event sözleşmesini dondur → [ws-api-contract.md](ws-api-contract.md).
2. [x] Regression smoke → `backend/scripts/run_smoke_tests.sh`.
3. [x] `TradingNode` içinde Polymarket data client/factory entegrasyonunu aç.
4. [x] Ticker/quote akışını Nautilus DataEngine/cache kaynağına bağla (`PolymarketQuoteBridgeActor`).
5. [x] Rollback flag: `POLYMARKET_DATA_ENABLED` (default true → DataClient + QuoteBridge).

**Varsayılan davranış (kod):** `POLYMARKET_DATA_ENABLED=true` → DataClient + QuoteBridge.

### Sprint DoD

- Faz 0 checklist'i tamamlanmış ve ekip tarafından referans alınır durumda.
- Faz 1 kapsamında Polymarket data akışı Nautilus kaynağından doğrulanmış.
- Legacy yol default dışı (flag gerektiren) hale getirilmiş.

### Sprint dışı (tamamlandı veya ertelendi)

- Yeni `Strategy` implementasyonu (liq→Poly veya başka) — Nautilus-native, BFF köprüsü olmadan.
