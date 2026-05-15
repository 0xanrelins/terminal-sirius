import { useEffect, useRef } from "react";
import {
  createChart,
  CandlestickSeries,
  type IChartApi,
  type ISeriesApi,
  type CandlestickSeriesOptions,
  type CandlestickData,
  type UTCTimestamp,
} from "lightweight-charts";
import { useFeed } from "../../context/FeedContext";
import type { BarMsg, Kline, TradeMsg } from "../../types";
import styles from "./CandlestickChart.module.css";

type Props = { symbol: string; interval?: string };

const INTERVAL_SECONDS: Record<string, number> = {
  "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
  "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600, "12h": 43200,
  "1d": 86400,
};

export function CandlestickChart({ symbol, interval = "1m" }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const liveBarRef = useRef<CandlestickData<UTCTimestamp> | null>(null);
  const { subscribe } = useFeed();

  // Init chart + load historical data
  useEffect(() => {
    if (!containerRef.current) return;

    const chart = createChart(containerRef.current, {
      layout: {
        background: { color: "#131318" },
        textColor: "#888",
      },
      grid: {
        vertLines: { color: "#1e1e28" },
        horzLines: { color: "#1e1e28" },
      },
      crosshair: {
        vertLine: { color: "#444" },
        horzLine: { color: "#444" },
      },
      timeScale: {
        borderColor: "#2a2a35",
        timeVisible: true,
        secondsVisible: false,
      },
      rightPriceScale: { borderColor: "#2a2a35" },
      width: containerRef.current.clientWidth,
      height: containerRef.current.clientHeight,
    });

    const series = chart.addSeries(CandlestickSeries, {
      upColor: "#22c55e",
      downColor: "#ef4444",
      borderUpColor: "#22c55e",
      borderDownColor: "#ef4444",
      wickUpColor: "#22c55e",
      wickDownColor: "#ef4444",
    } as CandlestickSeriesOptions);

    chartRef.current = chart;
    seriesRef.current = series;

    // Resize observer
    const ro = new ResizeObserver(() => {
      if (!containerRef.current) return;
      chart.applyOptions({
        width: containerRef.current.clientWidth,
        height: containerRef.current.clientHeight,
      });
    });
    ro.observe(containerRef.current);

    // Historical klines
    fetch(`/klines?symbol=${encodeURIComponent(symbol)}&interval=${interval}&limit=500`)
      .then((r) => r.json())
      .then((data: Kline[]) => {
        series.setData(
          data.map((k) => ({
            time: k.time as UTCTimestamp,
            open: k.open,
            high: k.high,
            low: k.low,
            close: k.close,
          }))
        );
        chart.timeScale().fitContent();
      })
      .catch(console.error);

    return () => {
      ro.disconnect();
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
      liveBarRef.current = null;
    };
  }, [symbol, interval]);

  // Live updates: prefer bar messages from Nautilus, fall back to building from trade ticks
  useEffect(() => {
    const barSec = INTERVAL_SECONDS[interval] ?? 60;

    const unsub = subscribe(symbol, (msg) => {
      const series = seriesRef.current;
      if (!series) return;

      if (msg.type === "bar" && msg.interval === interval) {
        // Completed bar from Nautilus
        const bar: CandlestickData<UTCTimestamp> = {
          time: Math.floor(msg.ts / 1e9) as UTCTimestamp,
          open: parseFloat(msg.open),
          high: parseFloat(msg.high),
          low: parseFloat(msg.low),
          close: parseFloat(msg.close),
        };
        series.update(bar);
        liveBarRef.current = null;
        return;
      }

      if (msg.type === "trade") {
        // Build live bar from trade ticks (fills gap before Nautilus emits a completed bar)
        const price = parseFloat(msg.price);
        const barTime = (Math.floor(msg.ts / 1e9 / barSec) * barSec) as UTCTimestamp;
        const cur = liveBarRef.current;

        if (!cur || cur.time !== barTime) {
          liveBarRef.current = { time: barTime, open: price, high: price, low: price, close: price };
        } else {
          liveBarRef.current = {
            time: barTime,
            open: cur.open,
            high: Math.max(cur.high, price),
            low: Math.min(cur.low, price),
            close: price,
          };
        }

        series.update(liveBarRef.current);
      }
    });

    return unsub;
  }, [symbol, interval, subscribe]);

  return (
    <div className={styles.wrapper}>
      <div className={styles.header}>
        <span className={styles.symbol}>{symbol.replace("-PERP.BINANCE", "")} PERP</span>
        <span className={styles.interval}>{interval}</span>
      </div>
      <div ref={containerRef} className={styles.chart} />
    </div>
  );
}
