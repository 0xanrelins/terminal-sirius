import { useEffect, useRef, useState } from "react";
import type { PolymarketMarket, WidgetConfig, WidgetType } from "../types";
import styles from "./AddWidgetModal.module.css";

type DataSource = "binance" | "polymarket";

type Props = {
  onAdd: (cfg: WidgetConfig) => void;
  onClose: () => void;
};

export function AddWidgetModal({ onAdd, onClose }: Props) {
  const [source, setSource] = useState<DataSource>("binance");

  // Binance fields
  const [binanceType, setBinanceType] = useState<WidgetType>("price_ticker");
  const [symbol, setSymbol] = useState("BTCUSDT-PERP.BINANCE");

  // Polymarket fields
  const [pmQuery, setPmQuery] = useState("");
  const [pmResults, setPmResults] = useState<PolymarketMarket[]>([]);
  const [pmSelected, setPmSelected] = useState<PolymarketMarket | null>(null);
  const [pmSearching, setPmSearching] = useState(false);

  const inputRef = useRef<HTMLInputElement>(null);
  const searchTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    inputRef.current?.focus();
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  // Debounced Polymarket search
  useEffect(() => {
    if (source !== "polymarket" || pmQuery.trim().length < 2) {
      setPmResults([]);
      return;
    }
    if (searchTimer.current) clearTimeout(searchTimer.current);
    searchTimer.current = setTimeout(async () => {
      setPmSearching(true);
      try {
        const r = await fetch(`/polymarket/markets?q=${encodeURIComponent(pmQuery)}&limit=10`);
        setPmResults(await r.json());
      } catch {
        setPmResults([]);
      } finally {
        setPmSearching(false);
      }
    }, 400);
  }, [pmQuery, source]);

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
        });
      } else {
        onAdd({ id, type: "price_ticker", symbol: symbol.toUpperCase() });
      }
    } else {
      if (!pmSelected) return;
      // POST subscribe so actor streams this slug
      fetch("/polymarket/subscribe", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ slug: pmSelected.slug }),
      }).catch(() => {});
      onAdd({
        id,
        type: "polymarket_ticker",
        symbol: `${pmSelected.slug}.POLYMARKET`,
        slug: pmSelected.slug,
        question: pmSelected.question,
      });
    }
    onClose();
  }

  const canSubmit = source === "binance" ? !!symbol.trim() : !!pmSelected;

  return (
    <div className={styles.overlay} onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className={styles.modal}>
        <header className={styles.header}>
          <span>Add Widget</span>
          <button className={styles.close} onClick={onClose}>✕</button>
        </header>

        <form onSubmit={submit} className={styles.form}>
          {/* Data source */}
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
                  {(["price_ticker", "candlestick_chart"] as WidgetType[]).map((t) => (
                    <button
                      key={t}
                      type="button"
                      className={`${styles.typeBtn} ${binanceType === t ? styles.active : ""}`}
                      onClick={() => setBinanceType(t)}
                    >
                      {t === "price_ticker" ? "Price Ticker" : "Candlestick Chart"}
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
                <p className={styles.hint}>
                  Pair, timeframe and indicators can be changed from the chart toolbar.
                </p>
              )}
            </>
          )}

          {source === "polymarket" && (
            <>
              <label className={styles.label}>
                <span>Search Market</span>
                <input
                  ref={source === "polymarket" ? inputRef : undefined}
                  className={styles.input}
                  value={pmQuery}
                  onChange={(e) => { setPmQuery(e.target.value); setPmSelected(null); }}
                  placeholder="e.g. Trump, Fed rate, Bitcoin…"
                  spellCheck={false}
                />
              </label>

              {pmSearching && <p className={styles.hint}>Searching…</p>}

              {pmResults.length > 0 && (
                <div className={styles.results}>
                  {pmResults.map((m) => (
                    <button
                      key={m.slug}
                      type="button"
                      className={`${styles.resultItem} ${pmSelected?.slug === m.slug ? styles.active : ""}`}
                      onClick={() => setPmSelected(m)}
                    >
                      <span className={styles.resultQ}>{m.question}</span>
                      {m.yes_price != null && (
                        <span className={styles.resultP}>
                          {(m.yes_price * 100).toFixed(0)}%
                        </span>
                      )}
                    </button>
                  ))}
                </div>
              )}

              {pmQuery.length >= 2 && !pmSearching && pmResults.length === 0 && (
                <p className={styles.hint}>No markets found.</p>
              )}
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
