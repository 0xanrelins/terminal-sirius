> **Archived** — see [../README.md](../README.md) for current docs.

# Trading Terminal — Implementation Plan

## Mimari (güncel)

Tek kaynak likidasyon + Nautilus Actors. Detay: [docs/architecture.md](docs/architecture.md).

- **Engine:** `BridgeActor`, `PolymarketQuoteBridgeActor`, `LiquidationActor` → `data_queue`
- **API:** FastAPI — UI yalnızca HTTP/WS sözleşmesi
- **Likidasyon:** Tek writer (Actor veya fallback stream); `PERSIST_LIQUIDATION_EVENTS_TO_DB=1`
- **Sim/Live:** `liquidation_bars` + DB dedup `(symbol, liq_bar_open, side)`

---

## Faz 1: Temel Altyapı

**Hedef:** Her şeyin konuştuğu iskelet ayağa kalksın.

1. Nautilus Trader kurulumu — Binance adapter bağlantısı, basit bir sembol üzerinde veri akışı doğrulanır.
2. FastAPI WebSocket bridge — Nautilus `MessageBus`'tan veri alıp browser'a ileten minimal server yazılır.
3. React uygulaması başlatılır — `react-grid-layout` entegre edilir, boş kanvas çalışır hale gelir.
4. Browser ↔ FastAPI WebSocket bağlantısı kurulur — tek bir fiyat tickerı kanvasta canlı güncellenir.

**Geçiş kriteri:** BTC/USDT perp fiyatı kanvastaki bir widget'ta canlı akar.

---

## Faz 2: Widget Sistemi

**Hedef:** Kanvasa widget eklenebilir, her widget bağımsız veri kanalına subscribe olabilir.

1. Widget factory kurulur — tip (chart, ticker, orderbook...) ve sembol seçilerek yeni widget oluşturulur.
2. TradingView Lightweight Charts entegrasyonu — chart widget'ı içinde çalışır, OHLCV verisi akar.
3. Pub/sub katmanı — browser tarafında her widget kendi kanalına subscribe olur, gereksiz veri almaz.
4. İlk widget seti tamamlanır: **Candlestick Chart**.

**Geçiş kriteri:** Kanvasa birden fazla farklı widget eklenebilir, her biri farklı sembolü takip edebilir.

---

## Faz 3: State Yönetimi

**Hedef:** Layout kaybolmaz, veri persist olur.

1. UI Layout → `localStorage`: widget pozisyonları, boyutları ve sembol konfigürasyonu JSON olarak kaydedilir. Sayfa yenilendiğinde layout geri yüklenir.
2. PostgreSQL kurulumu — Nautilus `CacheDatabase` entegrasyonu, tarihsel OHLCV verisi saklanır.
3. Sayfa açıldığında Nautilus'tan tarihsel veri çekilir, chart doldurulur, ardından live feed devreye girer.

**Geçiş kriteri:** Terminal kapatılıp açıldığında layout ve veriler kaldığı yerden devam eder.

---

## Faz 4: Polymarket Entegrasyonu

**Hedef:** İkinci veri kaynağı eklenir.

1. Polymarket için Nautilus custom adapter yazılır.
2. Prediction market verisi (fiyat, likidite, volume) widget'lara akar.
3. Binance ve Polymarket verisi aynı kanvas üzerinde yan yana görüntülenebilir.

**Geçiş kriteri:** Bir Polymarket marketi kanvasta Binance widget'ıyla yan yana çalışır.

---

## Faz 5: UX & Polish

**Hedef:** Kullanım akıcı hale gelir.

1. Widget ekleme akışı iyileştirilir — "+" butonu, sembol arama, tip seçimi.
2. Widget'lar kanvastan kaldırılabilir, kopyalanabilir.
3. Birden fazla layout (dashboard) kaydedilebilir ve aralarında geçiş yapılabilir.
4. Genel görsel tutarlılık — dark theme, tipografi, renk sistemi.

---

## Bağımlılık Sırası

```
Faz 1 → Faz 2 → Faz 3 → Faz 4
                              ↓
                           Faz 5
```

Faz 4 ve Faz 5 paralel yürütülebilir.

---

## Referanslar

| Teknoloji | Kaynak |
|---|---|
| Nautilus Trader | [github.com/nautechsystems/nautilus_trader](https://github.com/nautechsystems/nautilus_trader) |
| react-grid-layout | [github.com/react-grid-layout/react-grid-layout](https://github.com/react-grid-layout/react-grid-layout) |
| TradingView Lightweight Charts | [github.com/tradingview/lightweight-charts](https://github.com/tradingview/lightweight-charts) |
| Lightweight Charts Docs | [tradingview.github.io/lightweight-charts](https://tradingview.github.io/lightweight-charts/) |
