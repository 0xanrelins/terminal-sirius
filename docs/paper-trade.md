# Paper Trade — Checklist

Polymarket **Sandbox** execution + canlı Binance/Poly veri. Catalog kaydı ile paralel çalışır.

## Ön koşullar

- [ ] Postgres ayakta: `docker compose up -d postgres`
- [ ] Backend venv kurulu (`backend/.venv`)
- [ ] `.env` dosyası repo kökünde (`.env.example` referans)

## `.env` — paper trade

```bash
# Strateji + sandbox (gerçek emir yok)
STRATEGY_ENABLED=true
STRATEGY_PAPER_TRADE=true
STRATEGY_STARTING_BALANCE="10_000 USDC"
STRATEGY_TRADE_SIZE=10

# Likidasyon eşikleri ($, 900s rolling) — günlük ayarla
LIQ_THRESHOLD_BTC=500000
LIQ_THRESHOLD_ETH=200000
LIQ_THRESHOLD_SOL=100000
LIQ_THRESHOLD_XRP=50000
LIQ_THRESHOLD_DOGE=25000

# Loglarda PAPER fill/position görmek için
NAUTILUS_LOG_LEVEL=INFO

# Catalog kaydı (paper ile birlikte OK)
CATALOG_STREAMING_ENABLED=true
RELOAD=0
```

`POLYMARKET_PRIVATE_KEY` opsiyonel (Poly data loader public slug kullanır); quote bridge için önerilir.

## Başlat

```bash
cd backend
./scripts/paper_trade_check.sh    # ön kontrol
./scripts/run_catalog_recorder_daemon.sh stop   # varsa eski süreç
./scripts/run_catalog_recorder_daemon.sh start  # .env'deki STRATEGY_* ile aynı node
```

Veya foreground: `./scripts/run_backend.sh`

## Doğrulama (ilk 20 dk)

| Kontrol | Beklenen |
|---------|----------|
| Log: `TerminalSiriusStrategy + signal actors enabled — paper` | ✅ |
| Log: `[strategy] liq thresholds` | Eşikler `.env` ile uyumlu |
| Log: `Polymarket Sandbox execution` | ✅ |
| İlk ~**15 dk** | Warm-up — **trade yok** (900s bar/VWAP) |
| Sonrası | `PAPER submit` / `PAPER fill` / `PAPER position` (INFO log) |

```bash
tail -f backend/logs/uvicorn.log | grep -E 'strategy|PAPER|TerminalSirius|Sandbox'
./scripts/paper_trade_check.sh
```

## Günlük optimizasyon döngüsü

1. Gün sonu: log’dan trade sayısı, fill fiyatları, kapanış PnL not al
2. Çok az sinyal → ilgili coin `LIQ_THRESHOLD_*` düşür
3. Çok gürültü → eşiği yükselt
4. `.env` güncelle → daemon restart: `./scripts/run_catalog_recorder_daemon.sh stop && start`
5. Catalog birikmeye devam eder; ileride `run_terminal_sirius_backtest.py` ile doğrula

## Bilinen sınırlar

- Likidasyon: native `BinanceFuturesLiquidation` (Binance DataClient `!forceOrder@arr`)
- Exit: hard stop loss yok; VWAP + expiry heuristic (WIP)
- `pos_multiplier_*` henüz pozisyon boyutuna bağlı değil — sabit `STRATEGY_TRADE_SIZE`
- Paper PnL takibi log üzerinden (`NAUTILUS_LOG_LEVEL=INFO`)

## Durdur

```bash
cd backend && ./scripts/run_catalog_recorder_daemon.sh stop
# STRATEGY_ENABLED=false yap veya .env'den kaldır — sadece catalog moduna dön
```
