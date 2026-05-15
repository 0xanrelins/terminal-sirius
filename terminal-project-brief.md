# Trading Terminal — Project Brief

## Vizyon

Bloomberg tarzı, kişisel kullanım için tasarlanmış bir trading terminal. Temel fark: sabit bir arayüz değil, **boş bir kanvas** — kullanıcı istediği widget'ı oluşturur, yerleştirir, veri kaynağına bağlar ve kendi dashboard'unu inşa eder.

## Temel Kullanıcı Deneyimi

Kullanıcı boş bir kanvasa gelir. Chart mı ister, fiyat ekranı mı, orderbook mu, haber akışı mı — kanvasa sürükler, yerleştirir, boyutlandırır. Her widget bağımsız bir veri kaynağına bağlanır. Layout kaydedilir, sonraki oturumda aynı şekilde açılır.

## Stack

| Katman | Teknoloji | Açıklama |
|---|---|---|
| UI Layout Engine | `react-grid-layout` | Drag/drop, resize, snap-to-grid kanvas |
| Charting | `TradingView Lightweight Charts` | Widget içinde çalışan performanslı chart |
| Backend | `Nautilus Trader` | Market data, WS, REST, order management |
| Veri Kaynakları | Binance, Polymarket | Exchange + prediction market |
| UI State | `localStorage` | Layout pozisyonları, widget konfigürasyonu |
| Trading State | `Nautilus Cache + PostgreSQL` | Pozisyonlar, orderlar, tarihsel veri |

## Mimari

```
Nautilus Trader (Python)
  └── MessageBus → FastAPI WebSocket Bridge → Browser

Browser (React)
  └── react-grid-layout
        └── Widget[]
              └── TradingView Lightweight Chart
              └── Orderbook Table
              └── Price Ticker
              └── News Feed
              └── ...
```

## Veri Akışı

Nautilus Trader merkezi veri motoru olarak çalışır. Binance ve Polymarket adapter'ları üzerinden veri alır, internal `MessageBus` üzerinden dağıtır. FastAPI üzerindeki ince bir WebSocket bridge bu veriyi browser'a iletir. Her widget ilgili veri kanalına subscribe olur.

## State Yönetimi

- **UI Layout** — `localStorage`: widget pozisyonları, boyutları, hangi sembole bağlı olduğu JSON olarak saklanır. Sayfa kapansa bile layout korunur.
- **Trading Data** — Nautilus `Cache` (in-memory, hızlı erişim) + `PostgreSQL` (persistence, tarihsel veri).

## Kapsam

Tek kullanıcı, kişisel kullanım. Authentication, multi-tenancy yok. Hız ve esneklik öncelikli.

## Kaynaklar

| Teknoloji | Kaynak |
|---|---|
| Nautilus Trader | [github.com/nautechsystems/nautilus_trader](https://github.com/nautechsystems/nautilus_trader) |
| react-grid-layout | [github.com/react-grid-layout/react-grid-layout](https://github.com/react-grid-layout/react-grid-layout) |
| TradingView Lightweight Charts | [github.com/tradingview/lightweight-charts](https://github.com/tradingview/lightweight-charts) |
| Lightweight Charts Docs | [tradingview.github.io/lightweight-charts](https://tradingview.github.io/lightweight-charts/) |
