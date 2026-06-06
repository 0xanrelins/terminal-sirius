import { useEffect, useRef, useState } from "react";
import {
  CANDLESTICK_BAR_PRESETS,
  DEFAULT_CANDLESTICK_BARS,
  clampInitialBars,
} from "../lib/chartConfig";
import { POLYMARKET_15M_PRESETS, seriesToSymbol, type PolymarketPreset } from "../lib/polymarketPresets";
import {
  DEFAULT_LIQ_COINS,
  DEFAULT_MIN_NOTIONAL,
  LIQ_HISTORY_VERSION,
} from "./widgets/LiquidationSignals";
import { DEFAULT_POST_EVENT_COINS } from "../lib/liqPostEventChart";
import type { WidgetConfig, WidgetType } from "../types";
import styles from "./AddWidgetModal.module.css";

type DataSource = "binance" | "polymarket";
type PolymarketWidgetType = "price_ticker" | "polymarket_seconds_chart";

type Props = {
  onAdd: (cfg: WidgetConfig) => void;
  onClose: () => void;
};

export function AddWidgetModal({ onAdd, onClose }: Props) {
  const [source, setSource] = useState<DataSource>("binance");

  const [binanceType, setBinanceType] = useState<WidgetType>("price_ticker");
  const [symbol, setSymbol] = useState("BTCUSDT-PERP.BINANCE");
  const [initialBars, setInitialBars] = useState(DEFAULT_CANDLESTICK_BARS);

  const [pmPresets, setPmPresets] = useState<PolymarketPreset[]>([]);
  const [pmSelected, setPmSelected] = useState<PolymarketPreset | null>(null);
  const [pmLoading, setPmLoading] = useState(false);
  const [pmWidgetType, setPmWidgetType] =
    useState<PolymarketWidgetType>("polymarket_seconds_chart");
  const [pmInterval, setPmInterval] = useState<"1s" | "5s">("1s");

  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  useEffect(() => {
    if (source !== "polymarket") return;
    setPmLoading(true);
    fetch("/polymarket/presets")
      .then((r) => r.json())
      .then((data: PolymarketPreset[]) => setPmPresets(data))
      .catch(() => {
        setPmPresets(
          POLYMARKET_15M_PRESETS.map((p) => ({
            ...p,
            symbol: seriesToSymbol(p.series),
            current_slug: "",
            yes_price: null,
            question: null,
          }))
        );
      })
      .finally(() => setPmLoading(false));
  }, [source]);

  function submit(e: React.FormEvent) {
    e.preventDefault();
    const id = `widget-${Date.now()}`;

    if (source === "binance") {
      if (binanceType === "candlestick_chart") {
        onAdd({
          id,
          type: "candlestick_chart",
          symbol: "BTCUSDT-PERP.BINANCE",
          interval: "1m",
          chartStyle: "candlestick",
          indicators: [],
          initialBars: clampInitialBars(initialBars),
        });
      } else if (binanceType === "comparison_chart") {
        onAdd({
          id,
          type: "comparison_chart",
          interval: "1m",
        });
      } else if (binanceType === "liq_post_event_chart") {
        onAdd({
          id,
          type: "liq_post_event_chart",
          coins: [...DEFAULT_POST_EVENT_COINS],
          sides: ["LONG", "SHORT"],
          minNotional: DEFAULT_MIN_NOTIONAL,
        });
      } else if (binanceType === "liquidation_signals") {
        onAdd({
          id,
          type: "liquidation_signals",
          minNotional: DEFAULT_MIN_NOTIONAL,
          coins: [...DEFAULT_LIQ_COINS],
          historyVersion: LIQ_HISTORY_VERSION,
        });
      } else if (binanceType === "market_times") {
        onAdd({ id, type: "market_times" });
      } else if (binanceType === "bar_countdown") {
        onAdd({ id, type: "bar_countdown" });
      } else if (binanceType === "paper_trade_dashboard") {
        onAdd({ id, type: "paper_trade_dashboard", curveMetric: "equity" });
      } else if (binanceType === "strategy_signals") {
        onAdd({ id, type: "strategy_signals" });
      } else {
        onAdd({ id, type: "price_ticker", symbol: symbol.toUpperCase(), source: "binance" });
      }
    } else {
      if (!pmSelected) return;
      fetch("/polymarket/subscribe", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ series: pmSelected.series }),
      }).catch(() => {});
      if (pmWidgetType === "polymarket_seconds_chart") {
        onAdd({
          id,
          type: "polymarket_seconds_chart",
          series: pmSelected.series,
          interval: pmInterval,
          label: pmSelected.label,
        });
      } else {
        onAdd({
          id,
          type: "price_ticker",
          symbol: pmSelected.symbol,
          source: "polymarket",
          series: pmSelected.series,
          label: pmSelected.label,
        });
      }
    }
    onClose();
  }

  const canSubmit =
    source === "binance"
      ? binanceType === "liquidation_signals" ||
        binanceType === "market_times" ||
        binanceType === "bar_countdown" ||
        binanceType === "paper_trade_dashboard" ||
        binanceType === "strategy_signals" ||
        binanceType === "comparison_chart" ||
        binanceType === "liq_post_event_chart" ||
        binanceType === "candlestick_chart" ||
        !!symbol.trim()
      : !!pmSelected;

  return (
    <div className={styles.overlay} onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className={styles.modal}>
        <header className={styles.header}>
          <span>Add Widget</span>
          <button className={styles.close} onClick={onClose}>✕</button>
        </header>

        <form onSubmit={submit} className={styles.form}>
          <label className={styles.label}>
            <span>Data Source</span>
            <div className={styles.typeRow}>
              {(["binance", "polymarket"] as DataSource[]).map((s) => (
                <button
                  key={s}
                  type="button"
                  className={`${styles.typeBtn} ${source === s ? styles.active : ""}`}
                  onClick={() => setSource(s)}
                >
                  {s === "binance" ? "Binance" : "Polymarket"}
                </button>
              ))}
            </div>
          </label>

          {source === "binance" && (
            <>
              <label className={styles.label}>
                <span>Widget Type</span>
                <div className={styles.typeRow}>
                  {(
                    [
                      "price_ticker",
                      "candlestick_chart",
                      "comparison_chart",
                      "liq_post_event_chart",
                      "liquidation_signals",
                      "market_times",
                      "bar_countdown",
                      "paper_trade_dashboard",
                      "strategy_signals",
                    ] as WidgetType[]
                  ).map((t) => (
                    <button
                      key={t}
                      type="button"
                      className={`${styles.typeBtn} ${binanceType === t ? styles.active : ""}`}
                      onClick={() => setBinanceType(t)}
                    >
                      {t === "price_ticker"
                        ? "Price Ticker"
                        : t === "candlestick_chart"
                          ? "Candlestick Chart"
                          : t === "comparison_chart"
                            ? "Compare Chart"
                            : t === "liq_post_event_chart"
                              ? "Liq Post-Event"
                              : t === "market_times"
                                  ? "Market Times"
                                  : t === "bar_countdown"
                                    ? "15m Countdown"
                                    : t === "paper_trade_dashboard"
                                      ? "Paper Trade"
                                      : t === "strategy_signals"
                                        ? "Strategy Signals"
                                        : "Liq Signals"}
                    </button>
                  ))}
                </div>
              </label>

              {binanceType === "price_ticker" && (
                <label className={styles.label}>
                  <span>Symbol</span>
                  <input
                    ref={inputRef}
                    className={styles.input}
                    value={symbol}
                    onChange={(e) => setSymbol(e.target.value)}
                    placeholder="e.g. BTCUSDT-PERP.BINANCE"
                    spellCheck={false}
                  />
                </label>
              )}

              {binanceType === "candlestick_chart" && (
                <>
                  <label className={styles.label}>
                    <span>Candles on first open</span>
                    <div className={styles.intervalRow}>
                      {CANDLESTICK_BAR_PRESETS.map((n) => (
                        <button
                          key={n}
                          type="button"
                          className={`${styles.intervalBtn} ${initialBars === n ? styles.active : ""}`}
                          onClick={() => setInitialBars(n)}
                        >
                          {n}
                        </button>
                      ))}
                    </div>
                    <input
                      className={styles.input}
                      type="number"
                      min={10}
                      max={1000}
                      step={1}
                      value={initialBars}
                      onChange={(e) =>
                        setInitialBars(clampInitialBars(Number(e.target.value)))
                      }
                    />
                  </label>
                  <p className={styles.hint}>
                    Only the first time the chart opens. Pair, timeframe and indicators are in the chart toolbar; history and zoom stay as today.
                  </p>
                </>
              )}

              {binanceType === "comparison_chart" && (
                <p className={styles.hint}>
                  BTC, ETH, SOL, DOGE, XRP perpetuals — % from visible left (0%), UTC daily session lines, recent window on open.
                </p>
              )}

              {binanceType === "liq_post_event_chart" && (
                <p className={styles.hint}>
                  Post-liquidation 30m % move from catalog. 30s resolution — side, coins and min $ in the widget toolbar.
                </p>
              )}

              {binanceType === "liquidation_signals" && (
                <p className={styles.hint}>
                  BTC, ETH, SOL, DOGE, XRP liquidations. Pick coins and min $ from the widget toolbar.
                </p>
              )}

              {binanceType === "market_times" && (
                <p className={styles.hint}>
                  NYSE, LSE, TSE, ASX, CME — local time and session open/close countdown.
                </p>
              )}

              {binanceType === "bar_countdown" && (
                <p className={styles.hint}>
                  UTC 15m bar close — aligned to :00, :15, :30, :45 (charts, liq bars, Polymarket windows).
                </p>
              )}

              {binanceType === "paper_trade_dashboard" && (
                <p className={styles.hint}>
                  Live sandbox paper-trade monitor — equity curve, PnL, open positions/orders,
                  win rate, Sharpe and fill activity. Requires STRATEGY_ENABLED on the backend.
                </p>
              )}
            </>
          )}

          {source === "polymarket" && (
            <>
              <label className={styles.label}>
                <span>Widget Type</span>
                <div className={styles.typeRow}>
                  <button
                    type="button"
                    className={`${styles.typeBtn} ${pmWidgetType === "polymarket_seconds_chart" ? styles.active : ""}`}
                    onClick={() => setPmWidgetType("polymarket_seconds_chart")}
                  >
                    UP Chart (1s/5s)
                  </button>
                  <button
                    type="button"
                    className={`${styles.typeBtn} ${pmWidgetType === "price_ticker" ? styles.active : ""}`}
                    onClick={() => setPmWidgetType("price_ticker")}
                  >
                    Price Ticker
                  </button>
                </div>
              </label>

              {pmWidgetType === "polymarket_seconds_chart" && (
                <label className={styles.label}>
                  <span>Interval</span>
                  <div className={styles.intervalRow}>
                    {(["1s", "5s"] as const).map((iv) => (
                      <button
                        key={iv}
                        type="button"
                        className={`${styles.intervalBtn} ${pmInterval === iv ? styles.active : ""}`}
                        onClick={() => setPmInterval(iv)}
                      >
                        {iv}
                      </button>
                    ))}
                  </div>
                  <p className={styles.hint}>
                    Live UP probability line; chart clears at each new 15m market window.
                  </p>
                </label>
              )}

              <label className={styles.label}>
                <span>Market (15m Up/Down)</span>
                {pmLoading && <p className={styles.hint}>Loading…</p>}
                <div className={styles.results}>
                  {pmPresets.map((m) => (
                    <button
                      key={m.series}
                      type="button"
                      className={`${styles.resultItem} ${pmSelected?.series === m.series ? styles.active : ""}`}
                      onClick={() => setPmSelected(m)}
                    >
                      <span className={styles.resultQ}>{m.label} Up/Down 15m</span>
                      {m.yes_price != null && (
                        <span className={styles.resultP}>
                          UP {(m.yes_price * 100).toFixed(0)}%
                        </span>
                      )}
                    </button>
                  ))}
                </div>
              </label>

              <p className={styles.hint}>
                Markets roll every 15 minutes; the backend tracks the active window automatically.
              </p>
            </>
          )}

          <div className={styles.actions}>
            <button type="button" className={styles.cancel} onClick={onClose}>Cancel</button>
            <button type="submit" className={styles.submit} disabled={!canSubmit}>
              Add Widget
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
