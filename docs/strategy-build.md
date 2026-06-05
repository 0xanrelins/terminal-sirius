# Strategy Build Prompt

> Bu dosya, NautilusTrader'da bir stratejiyi sıfırdan inşa etmek için gereken
> tüm tanımları ve kararları içerir. Tamamlandığında bu dosyayı referans alarak
> strateji doğrudan kodlanabilir olacak.

---

## 1. Strateji Tanımı

**Ne yapar:**
5 coin'i (BTC, ETH, SOL, XRP, DOGE) Binance Perp ve Polymarket üzerinden izler.
Her saniye tüm indikatörleri yeniden hesaplar ve 3 katmanlı sinyal sistemiyle
trade kararı üretir (order aç, kapat, bekle).

**Hedef exchange:** Polymarket
**Hedef ortam:** Paper Trade (Sandbox) — önce, sonra live

---

## 2. Sistem Akışı

```
Adapter → Subscription → on_data() → Indicator → publish_signal()
                                                         ↓
                                          Strategy → recalculate() → Execution
```

| Adım | NautilusTrader Bileşeni | Açıklama |
|---|---|---|
| Adapter | `BinanceFuturesDataClient` | Binance Perp'e bağlan (sadece veri) |
| Adapter | `Polymarket` adapter | Polymarket'e bağlan (veri + execution) |
| Subscription | `subscribe_*()` | Ne dinleyeceğini seç |
| Yakalama | `on_data()` / `on_*()` | Ham veri gelince tetiklenir |
| Hesaplama | `Indicator` | Ham veriden metrik üret |
| Mesajlaşma | `publish_signal()` / `msgbus.publish()` | Actor → Strategy |
| Karar | `recalculate()` | Tüm sinyalleri topla → AL/SAT/BEKLE |
| Execution | `submit_order()` | Emri gönder |
| Zamanlayıcı | `clock.set_timer()` | Min. 1s'de bir recalculate() tetikle |

---

## 3. Mimari — Actor / Strategy

Her domain ayrı bir `Actor` — birbirinden bağımsız, test edilebilir:

```
LiquidationSignalActor  →  BinanceFuturesLiquidation dinler  →  sinyal yayınlar
VWAPActor         →  TradeTick → Bar → VWAP/ATR/LR     →  sinyal yayınlar
                              ↓
Strategy          →  hepsini dinler  →  recalculate()  →  karar
```

**Mesajlaşma:**
| Araç | Ne taşır | Ne zaman |
|---|---|---|
| `publish_signal()` | `str / int / float` | Basit skalar sinyal |
| `msgbus.publish()` + custom `Event` | Her türlü Python objesi | Karmaşık veri |

---

## 4. Subscriptions

### 4.1 Binance Futures (veri only)
- `BinanceFuturesLiquidation` — 5 coin için 5 ayrı subscription
- `TradeTick` — işlem akışı (Bar üretimi için)

### 4.2 Bar Üretimi
- Binance'te 1s bar yok — NautilusTrader native `BarAggregator` ile `TradeTick`'ten üretilir
- `1s Bar` → VWAP, ATR, LinearRegression için ortak kaynak

### 4.3 Polymarket (veri + execution)
- `subscribe_book_at_interval()` — order book snapshot (fiyat seviyesi)
- `subscribe_trades()` — gerçekleşen işlemler
- **Kısıt:** `can_unsubscribe=False` — subscribe sonrası bağlantı kesmeden çıkılamaz
- **Enstrüman tanımı:** `get_polymarket_instrument_id(condition_id, token_id)` ile
- **Market ID'leri:** implementasyonda eklenecek

### 4.4 Zaman
- `clock.set_timer()` — 1 saniyelik minimum tetikleyici

---

## 5. Indicator Tasarımı

### 5.1 Likidasyon İndikatörü
- **Kaynak:** `BinanceFuturesLiquidation`
- **Ham veri:** `side` (long/short), `price`, `last_filled_qty`, `accumulated_qty`, `ts_event`
- **Enstrümanlar:** BTCUSDT-PERP, ETHUSDT-PERP, SOLUSDT-PERP, XRPUSDT-PERP, DOGEUSDT-PERP
- **Her coin için ayrı:** 5 subscription, 5 indikatör, 5 threshold
- **Pencere:** 900s rolling
- **Metrik:** Toplam dolar hacmi (long ve short ayrı ayrı)
- **Trigger:** Coin'in 900s likidasyon hacmi threshold'u geçince → sinyal
- **Threshold değerleri:** Backtest ile belirlenecek (BTC >> DOGE)
- **Coin → Polymarket eşleşmesi:**
  - BTCUSDT-PERP → Polymarket BTC 15m market
  - ETHUSDT-PERP → Polymarket ETH 15m market
  - SOLUSDT-PERP → Polymarket SOL 15m market
  - XRPUSDT-PERP → Polymarket XRP 15m market
  - DOGEUSDT-PERP → Polymarket DOGE 15m market

### 5.2 VWAP + Trend + Zone (Cascaded)
- **Kaynak:** `TradeTick` → `BarAggregator(1s)` → `1s Bar`
```
1s Bar
  ↓
VolumeWeightedAveragePrice(900)          → fiyat seviyesi (USDT)
  ↓
LinearRegression(900)                    → slope (trend yönü + gücü)
  ↓
AverageTrueRange(900)                    → volatilite ölçümü
  ↓
Low  zone = VWAP - (ATR × çarpan)       → long entry bölgesi
High zone = VWAP + (ATR × çarpan)       → short entry bölgesi
```
- **Slope çıktısı:** `> 0` yukarı / `< 0` aşağı / `≈ 0` range
- **Çarpan:** Backtest ile belirlenecek (her coin için ayrı)

### 5.3 Expiry Zamanı
- **Kaynak:** `instrument.expiration_ns` + `self.clock.timestamp_ns()`
- **Hesaplama:** `time_to_expiry = instrument.expiration_ns - self.clock.timestamp_ns()`
- **Native:** Polymarket adapter `end_date_iso` → `expiration_ns` olarak yazar
- **Kullanım:** Expiry yakınsa threshold'lar sıkılaşır — exit kararını etkiler

---

## 6. Sinyal Yapısı — 3 Katman

```
KATMAN 1 — YÖN     (LinearRegression slope)
  slope > 0  → long bak
  slope ≈ 0  → her iki yön
  slope < 0  → short bak

KATMAN 2 — ZONE    (VWAP ± ATR)
  Long  → fiyat < Low zone
  Short → fiyat > High zone

KATMAN 3 — TRIGGER (likidasyon event)
  LONG likidasyon spike  → long trigger
  SHORT likidasyon spike → short trigger
```

**Tam sinyal tablosu:**
```
slope > 0  +  fiyat < Low zone   +  LONG likidasyon spike  →  LONG
slope < 0  +  fiyat > High zone  +  SHORT likidasyon spike →  SHORT
slope ≈ 0  +  fiyat < Low zone   +  LONG likidasyon spike  →  LONG
slope ≈ 0  +  fiyat > High zone  +  SHORT likidasyon spike →  SHORT
```

---

## 7. Karar Motoru — recalculate()

**Tetikleyici:** Herhangi bir sinyal veya timer (min. 1s)
**Çıktı:** `OPEN` / `CLOSE` / `HOLD`

### 7.0 Warm-up
Tüm indikatörler `initialized = True` olana kadar sinyal üretilmez.
- VWAP(900), ATR(900), LinearRegression(900) → 900 bar gerekli → ~900 saniye
- `indicator.initialized` kontrolü her `recalculate()` başında yapılır
- Hazır olmadan önce gelen likidasyon eventleri yok sayılır

### 7.1 Entry
3 katman hizalandığında → `OPEN` → market order

### 7.2 Exit (Resolution — hold)
Hard stop loss yok. **Strateji pozisyonu erken kapatmaz** (mid≈0.5 / son 60s kuralları kaldırıldı).
```
Pozisyon açık → recalc timer sadece HOLD (exit yok)
Pencere sonu   → Nautilus BinaryOption: pending resolution → InstrumentClose / settlement
Kazanç         → Polymarket resolution (Chainlink); kazanan share ≈ $1, kaybeden $0
```
Paper: Sandbox matching engine aynı expiry/settlement yolunu kullanır; UI PnL
`ReportProvider` + kapanmış pozisyonlardan gelir.

### 7.3 Karar Metodları
- **Threshold** — 3 katman sağlanmalı (entry)
- **State Machine** — pozisyon yönetimi
- **Kombinasyon** — exit için

### 7.4 Backtest Parametreleri
`BacktestEngine` + `StrategyConfig` ile optimize edilecek.

| Parametre | Açıklama | Test Aralığı |
|---|---|---|
| `atr_multiplier` | Zone genişliği (VWAP ± ATR × çarpan) | 0.5 – 3.0 |
| `slope_range_threshold` | Slope'un "range" sayıldığı eşik | 0.01 – 0.1 |
| `liq_threshold_btc` | BTC 900s likidasyon hacim eşiği ($) | Backtest ile |
| `liq_threshold_eth` | ETH 900s likidasyon hacim eşiği ($) | Backtest ile |
| `liq_threshold_sol` | SOL 900s likidasyon hacim eşiği ($) | Backtest ile |
| `liq_threshold_xrp` | XRP 900s likidasyon hacim eşiği ($) | Backtest ile |
| `liq_threshold_doge` | DOGE 900s likidasyon hacim eşiği ($) | Backtest ile |
| `pos_multiplier_small` | Küçük pozisyon çarpanı | 1.2 – 2.0 |
| `pos_multiplier_large` | Büyük pozisyon çarpanı | 2.5 – 5.0 |

---

## 8. Execution

**Order tipi:** Market order
**Exchange:** Polymarket

### 8.1 Paper Trade Yapısı
```
Veri      → BinanceFuturesDataClient  (gerçek, canlı)
Veri      → PolymarketDataClient      (gerçek, canlı)
Execution → SandboxExecutionClientConfig(venue="POLYMARKET") → simüle
```
- Başlangıç bakiyesi config'de tanımlanır
- `SandboxLiveExecClientFactory` ile `TradingNode`'a eklenir
- Live'a geçince: Sandbox → Polymarket execution client

### 8.2 Pozisyon Büyüklüğü
```
likidasyon hacmi = threshold × 1.5  → küçük pozisyon
likidasyon hacmi = threshold × 3.0  → büyük pozisyon
```
Çarpanlar backtest ile bulunacak.

---

## 9. Data Catalog

**Path:** `backend/catalog/`

### 9.1 Kaydedilmesi Gereken Veri
Backtest için aşağıdaki veriler `StreamingConfig` ile kaydedilecek:

| Veri | Tip | Coin | Amaç |
|---|---|---|---|
| `TradeTick` | Native | BTC, ETH, SOL, XRP, DOGE | 1s Bar → VWAP/ATR/LR |
| `BinanceFuturesLiquidation` | Native | BTC, ETH, SOL, XRP, DOGE | Likidasyon indikatörü |
| Polymarket fiyat | Native | BTC/ETH/SOL/XRP/DOGE 15m market | Exit / entry referans |

### 9.2 Kayıt Yöntemi
NautilusTrader native `StreamingConfig` — `TradingNode` çalışırken otomatik Parquet'e yazar:
```
TradingNodeConfig(
    streaming=StreamingConfig(
        catalog_path="backend/catalog",
        include_types=[TradeTick, BinanceFuturesLiquidation, ...]
    )
)
```

### 9.3 Instrument Tanımları
Backtest çalışmadan önce instrument tanımlarının katalogda olması gerekir:
- **Binance Perp:** `BinanceFuturesInstrumentProvider` → 5 coin tanımı çekip yaz
- **Polymarket:** `PolymarketInstrumentProvider` → her market için `BinaryOption` tanımı
- Kayıt: `catalog.write_data([instrument])` — bir kez yap, güncellenmesi gerekmez

### 9.4 Backtest Yapılandırması
Veri biriktikten sonra çalıştırmak için gereken config:
```
BacktestEngineConfig
  ↓
BacktestVenueConfig(name="POLYMARKET", oms_type=..., account_type=...)
  ↓
BacktestDataConfig(
    catalog_path="backend/catalog",
    data_cls=TradeTick,
    instrument_ids=[...],
    start_time=...,
    end_time=...
)
BacktestDataConfig(
    catalog_path="backend/catalog",
    data_cls=BinanceFuturesLiquidation,
    ...
)
```

### 9.5 Ne Kadar Veri Gerekli?
- Minimum warm-up: ~900 saniye (indikatör initialization)
- Anlamlı backtest: **2-4 hafta** likidasyon + TradeTick verisi
- Önce paper trade çalıştır → veri biriktir → backtest yap

---

## 10. Açık Kararlar

- [x] Polymarket market ID'leri — rolling slug + `PolymarketDataLoader.from_market_slug` (UP token)
- [ ] Diğer trigger eventler — likidasyon dışında _(ileriye bırakıldı)_
- [x] `StreamingConfig` — `CATALOG_STREAMING_ENABLED` → `node.py`
- [x] Instrument tanımları — `scripts/write_instruments_to_catalog.py`
- [x] `BacktestVenueConfig` + `BacktestDataConfig` — `backtest/run_config.py`, `scripts/run_terminal_sirius_backtest.py`

### 10.1 Implementasyon durumu (Terminal Sirius)

| Bileşen | Dosya | Not |
|---|---|---|
| `LiquidationSignalActor` | `backend/strategies/liquidation_signal_actor.py` | `BinanceFuturesLiquidation` (native) |
| `VwapSignalActor` | `backend/strategies/vwap_signal_actor.py` | `1-SECOND-LAST-INTERNAL` + LR/ATR |
| `TerminalSiriusStrategy` | `backend/strategies/terminal_sirius_strategy.py` | `STRATEGY_ENABLED=1` |
| `LiquidationUiBridgeActor` | `backend/liquidation_ui_bridge_actor.py` | UI/DB likidasyon köprüsü |
| Paper exec | `node.py` | `STRATEGY_PAPER_TRADE=true` → `SandboxExecutionClientConfig` |

**Likidasyon veri kaynağı:** `LiquidationSignalActor` ve `LiquidationUiBridgeActor` native `BinanceFuturesLiquidation` dinler (Binance DataClient `!forceOrder@arr`); custom WS yok.

**WIP:** Exit mantığı ve `pos_multiplier_*` pozisyon boyutu henüz plana göre tam değil.

### 10.2 Catalog + backtest akışı

```bash
# 1) Canlı veri biriktir (backend çalışırken)
CATALOG_STREAMING_ENABLED=true
CATALOG_PATH=backend/catalog   # varsayılan: backend/catalog/

# 2) Enstrüman tanımları
cd backend && python scripts/write_instruments_to_catalog.py

# 3) İsteğe bağlı: CandleFeed import
python scripts/import_to_catalog.py

# 4) Katalog envanteri
python scripts/catalog_stats.py

# 5) Backtest
python scripts/run_terminal_sirius_backtest.py --start 2026-06-01T00:00:00Z --end 2026-06-03T00:00:00Z
```

Native tipler: `StreamingConfig` → `TradeTick`, `QuoteTick`, `BinanceFuturesLiquidation`.
Liq Post Event fiyatları: `TradeTick` → saniyelik last price (`recorders/second_prices.py`).

---

## 11. Referanslar

_(Eklenecek)_
