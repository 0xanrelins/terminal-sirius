# Terminal Sirius

Bloomberg tarzı, kişisel kullanım için tasarlanmış trading terminali. Sabit bir arayüz değil, boş bir kanvas — istediğin widget'ı ekler, yerleştirir, veri kaynağına bağlarsın.

**Docs:** [docs/README.md](docs/README.md) (architecture, API contract, roadmap).

---

## Mimari

```
Nautilus TradingNode (Python)
  ├── BridgeActor / RealtimeBucketActor     → Binance bars, 1s/5s, indicators
  ├── PolymarketQuoteBridgeActor            → Polymarket quotes (DataClient)
  ├── PolymarketRealtimeBucketActor         → Polymarket 1s/5s UP bars
  ├── LiquidationActor                      → !forceOrder@arr (tek liq writer)
  └── MarketRecorderActor (optional)        → Parquet catalog

        ↓  thread-safe Queue
        
FastAPI (Python)
  ├── GET  /klines          → tarihsel OHLCV (PostgreSQL-first, Binance fallback)
  ├── GET  /polymarket/markets?q=  → Gamma API market arama
  ├── POST /polymarket/subscribe   → runtime slug ekleme
  ├── GET  /liquidations    → 15m liq bar totals
  ├── GET  /liquidation-events → major-coin liq list (backend persist)
  ├── GET  /liq-post-event/sessions → catalog research
  └── WS   /ws              → market data (trade, bar, liquidation, polymarket)

        ↓  WebSocket
        
Browser (React + TypeScript)
  ├── FeedContext           → tek WS bağlantısı, sembol bazlı pub/sub
  └── Canvas (react-grid-layout)
        ├── PriceTicker / CandlestickChart / ComparisonChart
        ├── LiquidationSignals / LiqPostEventChart
        └── PolymarketSecondsChart
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

Başlat (önerilen — log dosyasına yazar, agent/debug için):

```bash
cd backend
chmod +x scripts/run_backend.sh   # ilk sefer
./scripts/run_backend.sh
```

Log: `backend/logs/uvicorn.log` (`tail -f backend/logs/uvicorn.log`)

Doğrudan uvicorn (log dosyası yok):

```bash
cd backend
uvicorn main:app --reload --port 8000
```

**Likidasyon:** Tek yazıcı = backend stream (`liquidation_stream` veya Nautilus `LiquidationActor`). `PERSIST_LIQUIDATION_EVENTS_TO_DB=1` (varsayılan) ile `liquidation_bars`, ham events ve Liq Signals geçmişi aynı hat üzerinden dolar.

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
├── docs/
│   ├── README.md                 # Doc index (start here)
│   ├── architecture.md
│   ├── ws-api-contract.md
│   └── roadmap.md
├── backend/
│   ├── main.py                   # FastAPI BFF + WS
│   ├── node.py                   # Nautilus TradingNode (data actors only)
│   ├── bridge_actor.py           # Binance bars/ticks
│   ├── liquidation_actor.py      # Binance !forceOrder (tek liq writer)
│   ├── liquidations.py           # Parse + bar aggregate
│   ├── db.py                     # PostgreSQL schema + queries
│   ├── nautilus_env.py           # Polymarket env + L2 derive
│   └── adapters/polymarket/      # gamma, rolling, quote_bridge_actor
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
Binance WS / Polymarket DataClient / !forceOrder
  → Nautilus Actors (Bridge / QuoteBridge / Liquidation)
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
| `DATABASE_URL` | `postgresql://sirius:sirius@localhost:5432/sirius` | PostgreSQL |
| `PERSIST_LIQUIDATION_EVENTS_TO_DB` | `1` | Ham liq + Liq Signals geçmişi |
| `POLYMARKET_SLUGS` | _(boş)_ | Boot Polymarket slug'ları |
| `POLYMARKET_EXEC_ENABLED` | `false` | Idle Polymarket ExecutionClient (future strategies) |
| `POLYMARKET_DATA_ENABLED` | `true` | Polymarket ticker stream |

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
