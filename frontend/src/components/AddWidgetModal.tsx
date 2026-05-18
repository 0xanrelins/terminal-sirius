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
import type { WidgetConfig, WidgetType } from "../types";
import styles from "./AddWidgetModal.module.css";

type DataSource = "binance" | "polymarket";

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
          indicators: [],
          initialBars: clampInitialBars(initialBars),
        });
      } else if (binanceType === "comparison_chart") {
        onAdd({
          id,
          type: "comparison_chart",
          interval: "1m",
        });
      } else if (binanceType === "liquidation_signals") {
        onAdd({
          id,
          type: "liquidation_signals",
          minNotional: DEFAULT_MIN_NOTIONAL,
          coins: [...DEFAULT_LIQ_COINS],
          historyVersion: LIQ_HISTORY_VERSION,
        });
      } else if (binanceType === "simulation_panel") {
        onAdd({ id, type: "simulation_panel" });
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
      onAdd({
        id,
        type: "price_ticker",
        symbol: pmSelected.symbol,
        source: "polymarket",
        series: pmSelected.series,
        label: pmSelected.label,
      });
    }
    onClose();
  }

  const canSubmit =
    source === "binance"
      ? binanceType === "liquidation_signals" ||
        binanceType === "simulation_panel" ||
        binanceType === "comparison_chart" ||
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
                      "liquidation_signals",
                      "simulation_panel",
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
                            : t === "simulation_panel"
                              ? "Liq→Poly Sim"
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

              {binanceType === "liquidation_signals" && (
                <p className={styles.hint}>
                  BTC, ETH, SOL, DOGE, XRP liquidations. Pick coins and min $ from the widget toolbar.
                </p>
              )}

              {binanceType === "simulation_panel" && (
                <p className={styles.hint}>
                  Paper trades: 15m long-liq bar ≥ threshold → Polymarket UP on next window. Red candle → second bet on the following window.
                </p>
              )}
            </>
          )}

          {source === "polymarket" && (
            <>
              <label className={styles.label}>
                <span>Widget Type</span>
                <div className={styles.typeRow}>
                  <button type="button" className={`${styles.typeBtn} ${styles.active}`}>
                    Price Ticker
                  </button>
                </div>
              </label>

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
