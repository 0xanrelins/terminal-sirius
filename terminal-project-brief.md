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
Nautilus (BridgeActor, PolymarketActor, LiquidationActor)
  → data_queue → FastAPI (HTTP + /ws) → React (react-grid-layout + widgets)
  → PostgreSQL (klines, liquidation_bars, sim/live)
```

Detay: `docs/architecture.md`.

## Veri Akışı

Nautilus Actors canlı veriyi işler; FastAPI ince köprü olarak UI'a sabit JSON sözleşmesi sunar. Likidasyon tek yazıcı hat (`LiquidationActor` veya fallback stream) → `liquidation_bars` + events. UI yalnızca FastAPI'yi bilir; Postgres/Nautilus'a doğrudan bağlanmaz.

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
