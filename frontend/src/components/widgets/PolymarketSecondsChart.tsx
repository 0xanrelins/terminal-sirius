import { useCallback, useEffect, useRef, useState } from "react";
import {
  createChart,
  LineSeries,
  type IChartApi,
  type ISeriesApi,
  type LineData,
  type UTCTimestamp,
} from "lightweight-charts";
import { useFeed } from "../../context/FeedContext";
import { barOpenTime } from "../../lib/barTime";
import { POLYMARKET_15M_PRESETS, seriesToSymbol } from "../../lib/polymarketPresets";
import { polymarketWindowStart, POLYMARKET_WINDOW_SEC } from "../../lib/polymarketWindow";
import styles from "./CandlestickChart.module.css";

const PM_INTERVALS = ["1s", "5s"] as const;
export type PolymarketChartInterval = (typeof PM_INTERVALS)[number];

type Props = {
  series: string;
  interval?: PolymarketChartInterval;
  label?: string;
  onConfigChange: (patch: { series?: string; interval?: PolymarketChartInterval }) => void;
};

type OhlcvPoint = {
  time: number;
  close: number;
};

function formatUpPrice(v: number): string {
  return `${(v * 100).toFixed(1)}¢`;
}

export function PolymarketSecondsChart({
  series,
  interval = "1s",
  label,
  onConfigChange,
}: Props) {
  const symbol = seriesToSymbol(series);
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const lineRef = useRef<ISeriesApi<"Line"> | null>(null);
  const pointsRef = useRef<LineData<UTCTimestamp>[]>([]);
  const windowStartRef = useRef<number | null>(null);
  const [openMenu, setOpenMenu] = useState<"series" | "interval" | null>(null);
  const { subscribe } = useFeed();

  const resetSeries = useCallback(() => {
    pointsRef.current = [];
    windowStartRef.current = null;
    lineRef.current?.setData([]);
  }, []);

  const applyBar = useCallback((bar: OhlcvPoint) => {
    const line = lineRef.current;
    if (!line) return;

    const w = polymarketWindowStart(bar.time);
    if (windowStartRef.current !== null && w !== windowStartRef.current) {
      pointsRef.current = [];
      line.setData([]);
    }
    windowStartRef.current = w;

    const t = bar.time as UTCTimestamp;
    const pts = pointsRef.current;
    const idx = pts.findIndex((p) => p.time === t);
    const point: LineData<UTCTimestamp> = { time: t, value: bar.close };
    if (idx >= 0) {
      pts[idx] = point;
    } else {
      pts.push(point);
      pts.sort((a, b) => (a.time as number) - (b.time as number));
    }
    line.update(point);
  }, []);

  useEffect(() => {
    fetch("/polymarket/subscribe", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ series }),
    }).catch(() => {});
  }, [series]);

  useEffect(() => {
    if (!containerRef.current) return;
    let cancelled = false;

    const chart = createChart(containerRef.current, {
      layout: {
        background: { color: "#0d0d12" },
        textColor: "#9ca3af",
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
        secondsVisible: true,
      },
      rightPriceScale: {
        borderColor: "#2a2a35",
        scaleMargins: { top: 0.1, bottom: 0.1 },
      },
      width: containerRef.current.clientWidth,
      height: containerRef.current.clientHeight,
    });

    const line = chart.addSeries(LineSeries, {
      color: "#a78bfa",
      lineWidth: 2,
      priceFormat: {
        type: "custom",
        formatter: (p: number) => formatUpPrice(p),
      },
      priceLineVisible: false,
    });

    chartRef.current = chart;
    lineRef.current = line;
    resetSeries();

    const ro = new ResizeObserver(() => {
      if (cancelled || !containerRef.current) return;
      chart.applyOptions({
        width: containerRef.current.clientWidth,
        height: containerRef.current.clientHeight,
      });
    });
    ro.observe(containerRef.current);

    return () => {
      cancelled = true;
      ro.disconnect();
      chart.remove();
      chartRef.current = null;
      lineRef.current = null;
    };
  }, [resetSeries]);

  useEffect(() => {
    resetSeries();
  }, [series, interval, resetSeries]);

  useEffect(() => {
    const unsub = subscribe(symbol, (msg) => {
      if (msg.type !== "bar" || msg.interval !== interval) return;
      const t =
        msg.time ?? barOpenTime(Math.floor(msg.ts / 1e9), interval);
      const bar: OhlcvPoint = {
        time: t,
        close: parseFloat(msg.close),
      };
      applyBar(bar);
    });
    return unsub;
  }, [symbol, interval, subscribe, applyBar]);

  useEffect(() => {
    if (!openMenu) return;
    const close = () => setOpenMenu(null);
    window.addEventListener("click", close);
    return () => window.removeEventListener("click", close);
  }, [openMenu]);

  const displayLabel = label ?? series.split("-")[0].toUpperCase();
  const stopMenuClick = (e: React.MouseEvent) => e.stopPropagation();

  return (
    <div className={styles.wrapper}>
      <div className={`${styles.toolbar} chartToolbar`} onClick={stopMenuClick}>
        <div className={styles.menuWrap}>
          <button
            type="button"
            className={`${styles.menuBtn} ${openMenu === "series" ? styles.menuBtnActive : ""}`}
            onClick={(e) => {
              e.stopPropagation();
              setOpenMenu((m) => (m === "series" ? null : "series"));
            }}
          >
            {displayLabel} UP
            <span className={styles.chevron}>▼</span>
          </button>
          {openMenu === "series" && (
            <div className={styles.menu}>
              {POLYMARKET_15M_PRESETS.map((p) => (
                <button
                  key={p.series}
                  type="button"
                  className={`${styles.menuItem} ${p.series === series ? styles.menuItemActive : ""}`}
                  onClick={() => {
                    onConfigChange({ series: p.series });
                    setOpenMenu(null);
                  }}
                >
                  {p.label} UP 15m
                  {p.series === series && <span className={styles.check}>✓</span>}
                </button>
              ))}
            </div>
          )}
        </div>

        <div className={styles.menuWrap}>
          <button
            type="button"
            className={`${styles.menuBtn} ${openMenu === "interval" ? styles.menuBtnActive : ""}`}
            onClick={(e) => {
              e.stopPropagation();
              setOpenMenu((m) => (m === "interval" ? null : "interval"));
            }}
          >
            {interval}
            <span className={styles.chevron}>▼</span>
          </button>
          {openMenu === "interval" && (
            <div className={styles.menu}>
              {PM_INTERVALS.map((iv) => (
                <button
                  key={iv}
                  type="button"
                  className={`${styles.menuItem} ${iv === interval ? styles.menuItemActive : ""}`}
                  onClick={() => {
                    onConfigChange({ interval: iv });
                    setOpenMenu(null);
                  }}
                >
                  {iv}
                  {iv === interval && <span className={styles.check}>✓</span>}
                </button>
              ))}
            </div>
          )}
        </div>

        <span className={styles.menuBtn} style={{ cursor: "default", opacity: 0.7 }}>
          15m window · resets every {POLYMARKET_WINDOW_SEC / 60}m
        </span>
      </div>
      <div ref={containerRef} className={styles.chart} />
    </div>
  );
}
