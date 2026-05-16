import { useEffect, useRef, useState } from "react";
import {
  createChart,
  LineSeries,
  type IChartApi,
  type ISeriesApi,
  type LineData,
  type UTCTimestamp,
  type LogicalRange,
} from "lightweight-charts";
import { useFeed } from "../../context/FeedContext";
import {
  CHART_INTERVALS,
  COMPARISON_COLORS,
  COMPARISON_SYMBOLS,
  symbolShort,
} from "../../lib/chartConfig";
import { barOpenTime, currentBarBucket } from "../../lib/barTime";
import {
  DailySessionBreaksPrimitive,
  computeUtcDayBoundaries,
} from "../../lib/dailySessionBreaks";
import type { BarMsg, Kline, TradeMsg } from "../../types";
import styles from "./ComparisonChart.module.css";

type Props = {
  interval?: string;
  onConfigChange: (patch: { interval?: string }) => void;
};

type RawBar = { time: number; close: number };

const INITIAL_LIMIT = 2000;
const PAGE_SIZE = 200;
const LOAD_THRESHOLD = 10;
const REINDEX_DEBOUNCE_MS = 50;
/** Reference series for time axis and daily session lines (BTC). */
const REF_SYMBOL = COMPARISON_SYMBOLS[0];

function klinesToRaw(data: Kline[]): RawBar[] {
  return data.map((k) => ({ time: k.time, close: k.close }));
}

function mergeRaw(existing: RawBar[], older: RawBar[]): RawBar[] {
  if (older.length === 0) return existing;
  const byTime = new Map<number, RawBar>();
  for (const bar of [...older, ...existing]) {
    byTime.set(bar.time, bar);
  }
  return Array.from(byTime.values()).sort((a, b) => a.time - b.time);
}

/** % change from baseline; left visible bar = 0%. */
function toPercentLine(
  bars: RawBar[],
  baseline: number
): LineData<UTCTimestamp>[] {
  if (bars.length === 0 || baseline <= 0) return [];
  return bars.map((b) => ({
    time: b.time as UTCTimestamp,
    value: ((b.close / baseline) - 1) * 100,
  }));
}

function baselineFromIndex(bars: RawBar[], fromIndex: number): number {
  if (bars.length === 0) return 0;
  const idx = Math.min(Math.max(0, Math.floor(fromIndex)), bars.length - 1);
  return bars[idx].close;
}

function ensureFormingBar(bars: RawBar[], interval: string): RawBar[] {
  if (bars.length === 0) return bars;
  const bucket = currentBarBucket(interval);
  const last = bars[bars.length - 1];
  if (last.time >= bucket) return bars;
  return [...bars, { time: bucket, close: last.close }];
}

/** Bars to show on open (~24h per interval) so UTC midnight is usually visible. */
function visibleBarsForInterval(interval: string, total: number): number {
  const perDay: Record<string, number> = {
    "1m": 1440,
    "3m": 480,
    "5m": 288,
    "15m": 96,
    "30m": 48,
    "1h": 24,
    "4h": 6,
    "1d": 2,
  };
  return Math.min(total, perDay[interval] ?? 96);
}

function mergeWithLive(bars: RawBar[], live: RawBar | null): RawBar[] {
  if (!live) return bars;
  const idx = bars.findIndex((b) => b.time === live.time);
  if (idx >= 0) {
    const out = [...bars];
    out[idx] = live;
    return out;
  }
  const lastTime = bars[bars.length - 1]?.time;
  if (lastTime === undefined || live.time > lastTime) {
    return [...bars, live];
  }
  return bars;
}

export function ComparisonChart({
  interval = "1m",
  onConfigChange,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<Map<string, ISeriesApi<"Line">>>(new Map());
  const rawRef = useRef<Map<string, RawBar[]>>(new Map());
  const liveRef = useRef<Map<string, RawBar | null>>(new Map());
  const baselineRef = useRef<Map<string, number>>(new Map());
  const loadingRef = useRef(false);
  const exhaustedRef = useRef<Set<string>>(new Set());
  const historyReadyRef = useRef(false);
  const reindexTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const sessionBreaksRef = useRef<DailySessionBreaksPrimitive | null>(null);
  const [openIntervalMenu, setOpenIntervalMenu] = useState(false);
  const [loading, setLoading] = useState(true);
  const { subscribe } = useFeed();

  // Chart init, history load, infinite scroll, visible rebasing
  useEffect(() => {
    if (!containerRef.current) return;

    rawRef.current = new Map();
    liveRef.current = new Map();
    baselineRef.current = new Map();
    loadingRef.current = false;
    exhaustedRef.current = new Set();
    historyReadyRef.current = false;
    seriesRef.current.clear();
    setLoading(true);

    for (const sym of COMPARISON_SYMBOLS) {
      rawRef.current.set(sym, []);
      liveRef.current.set(sym, null);
    }

    const chart = createChart(containerRef.current, {
      layout: {
        background: { color: "#131318" },
        textColor: "#888",
        attributionLogo: false,
      },
      grid: {
        vertLines: { color: "#1e1e28" },
        horzLines: { color: "#1e1e28" },
      },
      crosshair: {
        vertLine: { color: "#444" },
        horzLine: { color: "#444" },
      },
      localization: {
        priceFormatter: (p: number) =>
          `${p >= 0 ? "+" : ""}${p.toFixed(2)}%`,
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

    for (const sym of COMPARISON_SYMBOLS) {
      const series = chart.addSeries(LineSeries, {
        color: COMPARISON_COLORS[sym],
        lineWidth: 2,
        priceLineVisible: false,
        lastValueVisible: true,
        title: symbolShort(sym).replace("USDT", ""),
      });
      seriesRef.current.set(sym, series);
    }

    chartRef.current = chart;

    const sessionBreaks = new DailySessionBreaksPrimitive();
    sessionBreaksRef.current = sessionBreaks;

    const updateSessionBreaks = () => {
      const raw = rawRef.current.get(REF_SYMBOL) ?? [];
      sessionBreaks.setBoundaries(computeUtcDayBoundaries(raw));
    };

    const refreshSessionBreaks = () => {
      sessionBreaks.refresh();
    };

    const reindexToVisible = () => {
      if (!historyReadyRef.current) return;
      const range = chart.timeScale().getVisibleLogicalRange();
      if (!range) return;

      for (const sym of COMPARISON_SYMBOLS) {
        const raw = rawRef.current.get(sym) ?? [];
        const baseline = baselineFromIndex(raw, range.from);
        if (baseline <= 0) continue;
        baselineRef.current.set(sym, baseline);
        seriesRef.current.get(sym)?.setData(toPercentLine(raw, baseline));
      }
    };

    const scheduleReindex = () => {
      if (reindexTimerRef.current) clearTimeout(reindexTimerRef.current);
      reindexTimerRef.current = setTimeout(() => {
        reindexToVisible();
        updateSessionBreaks();
        refreshSessionBreaks();
        reindexTimerRef.current = null;
      }, REINDEX_DEBOUNCE_MS);
    };

    const fetchKlines = async (symbol: string, before?: number): Promise<Kline[]> => {
      const params = new URLSearchParams({
        symbol,
        interval,
        limit: String(before === undefined ? INITIAL_LIMIT : PAGE_SIZE),
      });
      if (before !== undefined) params.set("before", String(before));
      const r = await fetch(`/klines?${params}`);
      if (!r.ok) throw new Error(`klines ${r.status}`);
      return r.json();
    };

    const loadOlder = async () => {
      if (loadingRef.current) return;

      const pending = COMPARISON_SYMBOLS.filter(
        (sym) => !exhaustedRef.current.has(sym)
      );
      if (pending.length === 0) return;

      loadingRef.current = true;
      try {
        await Promise.all(
          pending.map(async (sym) => {
            const bars = rawRef.current.get(sym) ?? [];
            const oldest = bars[0]?.time;
            if (oldest === undefined) return;

            const data = await fetchKlines(sym, oldest);
            const older = klinesToRaw(data).filter((b) => b.time < oldest);
            if (older.length === 0) {
              exhaustedRef.current.add(sym);
              return;
            }
            rawRef.current.set(sym, mergeRaw(bars, older));
          })
        );
        scheduleReindex();
      } catch (e) {
        console.error(e);
      } finally {
        loadingRef.current = false;
      }
    };

    const onVisibleLogicalRangeChange = (range: LogicalRange | null) => {
      if (!range) return;
      if (range.from < LOAD_THRESHOLD) void loadOlder();
      scheduleReindex();
    };

    chart.timeScale().subscribeVisibleLogicalRangeChange(onVisibleLogicalRangeChange);

    const ro = new ResizeObserver(() => {
      if (!containerRef.current) return;
      chart.applyOptions({
        width: containerRef.current.clientWidth,
        height: containerRef.current.clientHeight,
      });
      scheduleReindex();
    });
    ro.observe(containerRef.current);

    Promise.all(
      COMPARISON_SYMBOLS.map(async (sym) => {
        const data = await fetchKlines(sym);
        const live = liveRef.current.get(sym) ?? null;
        const bars = mergeWithLive(
          ensureFormingBar(klinesToRaw(data), interval),
          live
        );
        const bucket = currentBarBucket(interval);
        const last = bars[bars.length - 1];
        if (last && last.time === bucket) {
          liveRef.current.set(sym, last);
        }
        rawRef.current.set(sym, bars);
      })
    )
      .then(() => {
        historyReadyRef.current = true;

        // Seed series so the time scale can scroll, then rebase to visible left edge.
        for (const sym of COMPARISON_SYMBOLS) {
          const raw = rawRef.current.get(sym) ?? [];
          const seedIdx = Math.max(0, raw.length - 80);
          const seedBaseline = raw[seedIdx]?.close ?? raw[0]?.close ?? 0;
          if (seedBaseline > 0) {
            baselineRef.current.set(sym, seedBaseline);
            seriesRef.current.get(sym)?.setData(toPercentLine(raw, seedBaseline));
          }
        }

        const refSeries = seriesRef.current.get(REF_SYMBOL);
        refSeries?.attachPrimitive(sessionBreaks);

        const refRaw = rawRef.current.get(REF_SYMBOL) ?? [];
        const visibleCount = visibleBarsForInterval(interval, refRaw.length);
        chart.timeScale().setVisibleLogicalRange({
          from: Math.max(0, refRaw.length - visibleCount),
          to: refRaw.length + 2,
        });

        requestAnimationFrame(() => {
          updateSessionBreaks();
          refreshSessionBreaks();
          scheduleReindex();
        });
        setLoading(false);
      })
      .catch((e) => {
        console.error(e);
        historyReadyRef.current = true;
        setLoading(false);
      });

    return () => {
      if (reindexTimerRef.current) clearTimeout(reindexTimerRef.current);
      ro.disconnect();
      chart.timeScale().unsubscribeVisibleLogicalRangeChange(onVisibleLogicalRangeChange);
      chart.remove();
      chartRef.current = null;
      sessionBreaksRef.current = null;
      seriesRef.current.clear();
      rawRef.current.clear();
      liveRef.current.clear();
      baselineRef.current.clear();
      historyReadyRef.current = false;
    };
  }, [interval]);

  // Live updates for all tracked symbols
  useEffect(() => {
    const commitPoint = (symbol: string, bar: RawBar) => {
      const series = seriesRef.current.get(symbol);
      if (!series || !historyReadyRef.current) return;

      const bars = rawRef.current.get(symbol) ?? [];
      const idx = bars.findIndex((b) => b.time === bar.time);
      if (idx >= 0) bars[idx] = bar;
      else {
        bars.push(bar);
        bars.sort((a, b) => a.time - b.time);
      }
      rawRef.current.set(symbol, bars);

      if (symbol === REF_SYMBOL) {
        sessionBreaksRef.current?.setBoundaries(computeUtcDayBoundaries(bars));
      }

      const baseline = baselineRef.current.get(symbol);
      if (!baseline || baseline <= 0) return;
      series.update({
        time: bar.time as UTCTimestamp,
        value: ((bar.close / baseline) - 1) * 100,
      });
    };

    const unsubs = COMPARISON_SYMBOLS.map((sym) =>
      subscribe(sym, (msg) => {
        if (msg.type === "bar" && msg.interval === interval) {
          const m = msg as BarMsg;
          const t = m.time ?? barOpenTime(Math.floor(m.ts / 1e9), interval);
          const bar: RawBar = { time: t, close: parseFloat(m.close) };
          liveRef.current.set(sym, bar);
          commitPoint(sym, bar);
          return;
        }

        if (msg.type === "trade") {
          const m = msg as TradeMsg;
          const price = parseFloat(m.price);
          const barTime = barOpenTime(Math.floor(m.ts / 1e9), interval);
          const bar: RawBar = { time: barTime, close: price };
          liveRef.current.set(sym, bar);
          commitPoint(sym, bar);
        }
      })
    );

    return () => unsubs.forEach((u) => u());
  }, [interval, subscribe]);

  useEffect(() => {
    if (!openIntervalMenu) return;
    const close = () => setOpenIntervalMenu(false);
    window.addEventListener("click", close);
    return () => window.removeEventListener("click", close);
  }, [openIntervalMenu]);

  const scrollToRealtime = () => {
    chartRef.current?.timeScale().scrollToRealTime();
    requestAnimationFrame(() => sessionBreaksRef.current?.refresh());
  };

  const stopMenuClick = (e: React.MouseEvent) => e.stopPropagation();

  return (
    <div className={styles.wrapper}>
      <div
        className={`${styles.toolbar} comparisonToolbar`}
        onClick={stopMenuClick}
      >
        <span className={styles.title}>Compare</span>

        <div className={styles.menuWrap}>
          <button
            type="button"
            className={`${styles.menuBtn} ${openIntervalMenu ? styles.menuBtnActive : ""}`}
            onClick={(e) => {
              e.stopPropagation();
              setOpenIntervalMenu((v) => !v);
            }}
          >
            {interval}
            <span className={styles.chevron}>▼</span>
          </button>
          {openIntervalMenu && (
            <div className={styles.menu}>
              {CHART_INTERVALS.map((iv) => (
                <button
                  key={iv}
                  type="button"
                  className={`${styles.menuItem} ${iv === interval ? styles.menuItemActive : ""}`}
                  onClick={() => {
                    onConfigChange({ interval: iv });
                    setOpenIntervalMenu(false);
                  }}
                >
                  {iv}
                </button>
              ))}
            </div>
          )}
        </div>

        <div className={styles.legend}>
          {COMPARISON_SYMBOLS.map((sym) => (
            <span key={sym} className={styles.legendItem}>
              <span
                className={styles.legendDot}
                style={{ background: COMPARISON_COLORS[sym] }}
              />
              {symbolShort(sym).replace("USDT", "")}
            </span>
          ))}
        </div>

        <button type="button" className={styles.rtBtn} onClick={scrollToRealtime}>
          Go to realtime
        </button>
      </div>

      <div className={styles.chartWrap}>
        {loading && <div className={styles.loading}>Loading…</div>}
        <div ref={containerRef} className={styles.chart} />
      </div>
    </div>
  );
}
