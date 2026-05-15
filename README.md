# Terminal Sirius

Bloomberg tarzı, kişisel kullanım için tasarlanmış trading terminali. Sabit bir arayüz değil, boş bir kanvas — istediğin widget'ı ekler, yerleştirir, veri kaynağına bağlarsın.

---

## Mimari

```
Nautilus Trader (Python)
  ├── BridgeActor      → Binance Futures USDT-M canlı feed
  │     trade tick / quote tick / 1m-1d bar
  └── PolymarketActor  → Polymarket CLOB WebSocket
        price_change / book events

        ↓  thread-safe Queue
        
FastAPI (Python)
  ├── GET  /klines          → tarihsel OHLCV (PostgreSQL-first, Binance fallback)
  ├── GET  /polymarket/markets?q=  → Gamma API market arama
  ├── POST /polymarket/subscribe   → runtime slug ekleme
  └── WS   /ws              → tüm canlı veriyi browser'a yayınlar

        ↓  WebSocket
        
Browser (React + TypeScript)
  ├── FeedContext           → tek WS bağlantısı, sembol bazlı pub/sub
  └── Canvas (react-grid-layout)
        ├── PriceTicker          widget
        ├── CandlestickChart     widget  (TradingView Lightweight Charts)
        └── PolymarketTicker     widget
```

---

## Kurulum

### Gereksinimler

- Python 3.11+
- Node.js 20+
- Docker (PostgreSQL için)

### 1. PostgreSQL

```bash
docker compose up -d
```

### 2. Backend

```bash
cd backend
pip install -r requirements.txt
```

`.env` dosyasını düzenle:

```env
DATABASE_URL=postgresql://sirius:sirius@localhost:5432/sirius

# Boot'ta subscribe olunacak Polymarket slug'ları (virgülle ayrılmış, boş bırakılabilir)
POLYMARKET_SLUGS=will-donald-trump-win-the-2024-us-presidential-election
```

Başlat:

```bash
uvicorn main:app --reload --port 8000
```

### 3. Frontend

```bash
cd frontend
npm install
npm run dev
```

Tarayıcıda aç: `http://localhost:3000`

---

## Kullanım

### Widget ekleme

Sağ alttaki `+` butonuna tıkla.

| Kaynak | Widget Türleri |
|---|---|
| Binance | Price Ticker, Candlestick Chart |
| Polymarket | Polymarket Ticker |

**Binance:** Sembol gir (örn. `BTCUSDT-PERP.BINANCE`), türü ve interval'i seç.

**Polymarket:** Arama kutusuna konu yaz (örn. "Trump", "Fed rate"), çıkan listeden seç. Seçimle birlikte backend `POST /polymarket/subscribe` çağrılır ve canlı stream başlar.

### Widget işlemleri

Widget üzerine gelince handle'da iki buton belirir:

- `⊕` — Aynı konfigürasyonla kopyala
- `✕` — Kaldır

Widgetlar handle çubuğundan sürüklenir, köşeden boyutlandırılır.

### Çoklu dashboard

TopBar'da:

| İşlem | Nasıl |
|---|---|
| Geçiş | Sekmeye tıkla |
| Yeni dashboard | `+` butonuna tıkla |
| Yeniden adlandır | Sekmeye çift tıkla → Enter |
| Sil | `×` ikonuna tıkla |

Tüm layout ve widget konfigürasyonları `localStorage`'da saklanır.

---

## Proje Yapısı

```
Terminal Sirius/
├── docker-compose.yml
├── .env
│
├── backend/
│   ├── main.py                   # FastAPI app + WebSocket bridge
│   ├── node.py                   # Nautilus TradingNode factory
│   ├── bridge_actor.py           # Binance → data_queue (Nautilus Actor)
│   ├── db.py                     # asyncpg pool + klines schema
│   ├── klines.py                 # DB-first OHLCV, Binance fallback
│   ├── requirements.txt
│   └── adapters/
│       └── polymarket/
│           ├── actor.py          # Polymarket CLOB WS (Nautilus Actor)
│           └── gamma.py          # Gamma API REST client
│
└── frontend/
    ├── vite.config.ts
    └── src/
        ├── App.tsx               # Dashboard state yönetimi
        ├── types.ts
        ├── context/
        │   └── FeedContext.tsx   # Merkezi WebSocket pub/sub
        └── components/
            ├── TopBar.tsx        # Dashboard sekmeleri + WS status
            ├── Canvas.tsx        # react-grid-layout kanvas
            └── widgets/
                ├── PriceTicker.tsx
                ├── CandlestickChart.tsx
                └── PolymarketTicker.tsx
```

---

## Veri Akışı

### Canlı veri

```
Binance WS / Polymarket CLOB WS
  → Nautilus Actor (BridgeActor / PolymarketActor)
    → data_queue (thread-safe Queue)
      → FastAPI broadcast loop
        → WebSocket /ws
          → FeedContext (sembol bazlı dispatch)
            → Widget (subscribe callback)
```

### Tarihsel veri (CandlestickChart mount)

```
1. GET /klines?symbol=…&interval=…&limit=500
2. Backend: PostgreSQL'de ≥%80 veri var mı?
   → Evet: PostgreSQL'den döner
   → Hayır: Binance REST → PostgreSQL'e yazar → döner
3. Nautilus her kapanan bar'ı PostgreSQL'e persist eder
   → Sonraki cold start'ta DB-first çalışır
```

---

## Ortam Değişkenleri

| Değişken | Varsayılan | Açıklama |
|---|---|---|
| `DATABASE_URL` | `postgresql://sirius:sirius@localhost:5432/sirius` | PostgreSQL bağlantısı |
| `POLYMARKET_SLUGS` | _(boş)_ | Boot'ta subscribe olunacak slug'lar |

---

## Stack

| Katman | Teknoloji |
|---|---|
| Market data engine | [Nautilus Trader](https://github.com/nautechsystems/nautilus_trader) |
| Backend API | FastAPI + uvicorn |
| Veritabanı | PostgreSQL (asyncpg) |
| UI framework | React 18 + TypeScript + Vite |
| Layout engine | [react-grid-layout](https://github.com/react-grid-layout/react-grid-layout) |
| Charting | [TradingView Lightweight Charts v5](https://github.com/tradingview/lightweight-charts) |
| Veri kaynakları | Binance Futures USDT-M, Polymarket CLOB |
