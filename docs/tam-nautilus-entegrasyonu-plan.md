# Tam Nautilus entegrasyonu — plan

## Master plan (kısa)

**Vizyon**

- **Çekirdek:** `TradingNode` içinde tek veri yüzeyi (Binance + Polymarket `DataClient`), tek exec yüzeyi (`PolymarketExecutionClient`), strateji + cache tek otorite.
- **BFF:** FastAPI yalnızca HTTP/WS, persist, konfig toggles; strateji/state üretmez.

**İlkeler**

1. **Nautilus tek otorite** — sinyal, emir, cycle state strateji + ExecEngine cache'inde.
2. **UI sözleşmesi sabit** — `/live/*`, `/simulation/*`, WS event tipleri değişmez; sadece kaynak Nautilus olur.
3. **Liquidation single-writer** — [`liquidations.py`](../backend/liquidations.py) + `LiquidationActor`; bypass yok.
4. **Sim = live kodu** — farklı motor yok; config (paper vs live, threshold, assets) ayrılır.

**Bitti sayılır (ölçülebilir)**

- Polymarket için doğrudan CLOB WS → `queue` yolu yok (hedef: tamamen kaldırılmış).
- `TradingNode` içinde Polymarket data client/factory tanımlı ve çalışır.
- Polymarket ticker/quote kaynağı Nautilus DataEngine/cache olur.
- Live emir tek yol: `submit_order` → ExecEngine/ExecClient.
- Direct CLOB emir helper path'leri (`nautilus_bridge/polymarket_exec.py`, duplicate order helpers) kaldırılmış veya devre dışı.
- Tek env hikayesi vardır (Nautilus isimleri veya tek map katmanı).
- App-level reconcile kaldırılmıştır veya yalnızca dokümante edilmiş startup smoke olarak kalır.

## Bugün vs hedef

| | Bugün | Hedef |
|---|--------|--------|
| Veri | Nautilus Actors → queue | Nautilus DataClient + cache (tek kaynak) |
| Sinyal (threshold, leg1/2) | — | `LiqPolyStrategy` (live + sim on `TradingNode`) |
| Emir | — | `Strategy.submit_order()` → `PolymarketExecutionClient` |
| Retry / reconciliation | Strategy catch-up (liq/settle) + exec open-check | Nautilus ExecEngine + `strategy_catchup` |
| Sim | — | Aynı `LiqPolyStrategy` (`mode=sim`) |

## Ne taşınır, ne kalkar

**Taşınır → Nautilus:** 15m liq bar eşiği, cycle/leg mantığı, Polymarket slug/instrument seçimi, sizing, emir gönderimi, settle (15m bar kapanışı), Polymarket market data lifecycle.

**Kalır (BFF):** FastAPI HTTP/WS, PostgreSQL persist (UI geçmişi), React widget'lar, chart/klines endpoint'leri.

**Kalktı:** Eski live/sim motorları, `polymarket_exec` direct CLOB, `PolymarketActor` custom WS.

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
- [x] **Scope:** `exec_client_ready()` bağlantı kontrolü; startup catch-up vs exec reconciliation ayrımı dokümante.
- [x] **DoD:** Live emirler yalnız `submit_order` yolundan; direct CLOB helper yok.
- [ ] **Rollback:** Exec readiness kırılırsa deferred open + `LIVE_ENABLED=false` ile emirler durur.

### Faz 4 — Dead code silme ✅

- [x] **Scope:** `polymarket_exec.py` silindi; `orders.py` credential-only; `startup_reconcile.py` → `strategy_catchup.py`.
- [x] **Scope:** Kullanılmayan `set_liq_poly_strategy` / `get_polymarket_exec_client` kaldırıldı.
- [x] **DoD:** Direct CLOB helper yok; internal isimler catch-up vs exec reconciliation ayrımına uygun.
- [x] **Rollback:** Git history.

### Faz 5 — Sim = aynı strateji ✅

- [x] **Scope:** `LiveTradingEngine` / `SimulationEngine` kaldırıldı; config + reset doğrudan `main` + `strategy_runtime`.
- [x] **DoD:** `LiqPolyStrategy` live + sim aynı `TradingNode`; `test_liq_poly_live_sim_parity.py` aynı bar sinyalini doğrular.
- [x] **Not:** `backtest.py` ayrı iskelet — Nautilus backtest birleşimi ayrı iş (opsiyonel).

## Startup sırası (hedef)

`prepare_polymarket_env()` → `TradingNode` (data + exec) → strateji `on_start` → FastAPI fan-out. Emir, node hazır olmadan denenmez.

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

### Sprint dışı (bu haftaya alma)

- Exec/env sadeleştirme (Faz 3).
- Kalan dead code / reconcile sadeleştirme (Faz 4).
- Sim motorunun tam birleşimi (Faz 5).
