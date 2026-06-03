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
  type ComparisonSymbol,
  symbolShort,
} from "../../lib/chartConfig";
import { barOpenTime, currentBarBucket } from "../../lib/barTime";
import {
  SessionBreaksPrimitive,
  computeUtcDayBoundaries,
} from "../../lib/dailySessionBreaks";
import type { BarMsg, Kline } from "../../types";
import styles from "./ComparisonChart.module.css";

type Props = {
  interval?: string;
  symbols: ComparisonSymbol[];
  onConfigChange: (patch: { interval?: string; symbols?: string[] }) => void;
};

type RawBar = { time: number; close: number };

const INITIAL_LIMIT = 200;
const PAGE_SIZE = 200;
const LOAD_THRESHOLD = 10;
/** Rebase % to left visible bar after pan/zoom settles (LC: avoid setData per frame). */
const REBASE_SETTLE_MS = 450;
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

/** % change from each symbol's close at the left visible bar time. */
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

/** Close at `time`, or last bar at/before `time` (series share the chart time axis). */
function baselineCloseAtTime(bars: RawBar[], time: number): number {
  if (bars.length === 0) return 0;
  let close = bars[0].close;
  for (const b of bars) {
    if (b.time > time) break;
    close = b.close;
  }
  return close;
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
  symbols,
  onConfigChange,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<Map<string, ISeriesApi<"Line">>>(new Map());
  const rawRef = useRef<Map<string, RawBar[]>>(new Map());
  const liveRef = useRef<Map<string, RawBar | null>>(new Map());
  const baselineRef = useRef<Map<string, number>>(new Map());
  const enabledSymbolsRef = useRef(new Set<string>(symbols));
  const loadingRef = useRef(false);
  const exhaustedRef = useRef<Set<string>>(new Set());
  const historyReadyRef = useRef(false);
  const baselineTimeRef = useRef<number | null>(null);
  const reindexingRef = useRef(false);
  const reindexTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const settleGenRef = useRef(0);
  const settleReindexRef = useRef<(() => void) | null>(null);
  const sessionBreaksRef = useRef<SessionBreaksPrimitive | null>(null);
  const [openIntervalMenu, setOpenIntervalMenu] = useState(false);
  const [loading, setLoading] = useState(true);
  const { subscribe } = useFeed();
  const enabledSet = new Set(symbols);
  enabledSymbolsRef.current = enabledSet;

  // Chart init, history load, infinite scroll, left-edge % anchor
  useEffect(() => {
    if (!containerRef.current) return;

    rawRef.current = new Map();
    liveRef.current = new Map();
    baselineRef.current = new Map();
    baselineTimeRef.current = null;
    reindexingRef.current = false;
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
        vertLines: { visible: false },
        horzLines: { visible: false },
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
      handleScale: { axisPressedMouseMove: true, mouseWheel: true, pinch: true },
      kineticScroll: { touch: false, mouse: false },
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

    const sessionBreaks = new SessionBreaksPrimitive();
    sessionBreaksRef.current = sessionBreaks;

    const updateSessionBreaks = () => {
      const raw = rawRef.current.get(REF_SYMBOL) ?? [];
      sessionBreaks.setBoundaries(computeUtcDayBoundaries(raw));
    };

    const refreshSessionBreaks = () => {
      sessionBreaks.refresh();
    };

    const applyPercentLines = (baselineTime: number) => {
      reindexingRef.current = true;
      const priceScale = chart.priceScale("right");
      priceScale.setAutoScale(false);
      try {
        for (const sym of COMPARISON_SYMBOLS) {
          if (!enabledSymbolsRef.current.has(sym)) continue;
          const raw = rawRef.current.get(sym) ?? [];
          const baseline = baselineCloseAtTime(raw, baselineTime);
          if (baseline <= 0) continue;
          baselineRef.current.set(sym, baseline);
          seriesRef.current.get(sym)?.setData(toPercentLine(raw, baseline));
        }
      } finally {
        priceScale.setAutoScale(true);
        reindexingRef.current = false;
      }
    };

    const leftEdgeBaselineTime = (): number | null => {
      const range = chart.timeScale().getVisibleRange();
      if (!range || typeof range.from !== "number") return null;
      return range.from;
    };

    const rebaseToLeftEdge = () => {
      if (!historyReadyRef.current) return;
      const baselineTime = leftEdgeBaselineTime();
      if (baselineTime === null) return;
      if (baselineTime === baselineTimeRef.current) return;

      baselineTimeRef.current = baselineTime;
      applyPercentLines(baselineTime);
      updateSessionBreaks();
      refreshSessionBreaks();
    };

    const scheduleSettleReindex = () => {
      const gen = ++settleGenRef.current;
      if (reindexTimerRef.current) clearTimeout(reindexTimerRef.current);
      reindexTimerRef.current = setTimeout(() => {
        if (gen !== settleGenRef.current) return;

        const runWhenStable = (attempt: number) => {
          if (gen !== settleGenRef.current) return;
          const t1 = leftEdgeBaselineTime();
          requestAnimationFrame(() => {
            if (gen !== settleGenRef.current) return;
            const t2 = leftEdgeBaselineTime();
            if (t1 === null || t2 === null) return;
            if (t1 !== t2 && attempt < 6) {
              setTimeout(() => runWhenStable(attempt + 1), 80);
              return;
            }
            if (t1 !== t2) return;
            rebaseToLeftEdge();
          });
        };

        runWhenStable(0);
        reindexTimerRef.current = null;
      }, REBASE_SETTLE_MS);
    };

    settleReindexRef.current = scheduleSettleReindex;

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
        scheduleSettleReindex();
      } catch (e) {
        console.error(e);
      } finally {
        loadingRef.current = false;
      }
    };

    const onVisibleLogicalRangeChange = (range: LogicalRange | null) => {
      if (!range) return;
      if (range.from < LOAD_THRESHOLD) void loadOlder();
    };

    chart.timeScale().subscribeVisibleLogicalRangeChange(onVisibleLogicalRangeChange);

    const onGestureEnd = () => scheduleSettleReindex();
    const chartEl = containerRef.current;
    chartEl.addEventListener("pointerup", onGestureEnd);
    chartEl.addEventListener("wheel", onGestureEnd, { passive: true });

    const ro = new ResizeObserver(() => {
      if (!containerRef.current) return;
      chart.applyOptions({
        width: containerRef.current.clientWidth,
        height: containerRef.current.clientHeight,
      });
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

        for (const sym of COMPARISON_SYMBOLS) {
          const series = seriesRef.current.get(sym);
          if (!series) continue;
          series.applyOptions({ visible: enabledSymbolsRef.current.has(sym) });
          if (!enabledSymbolsRef.current.has(sym)) continue;
          const raw = rawRef.current.get(sym) ?? [];
          const boot = raw[0]?.close ?? 0;
          if (boot > 0) {
            baselineRef.current.set(sym, boot);
            series.setData(toPercentLine(raw, boot));
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

        rebaseToLeftEdge();
        setLoading(false);
      })
      .catch((e) => {
        console.error(e);
        historyReadyRef.current = true;
        setLoading(false);
      });

    return () => {
      settleReindexRef.current = null;
      if (reindexTimerRef.current) clearTimeout(reindexTimerRef.current);
      chartEl.removeEventListener("pointerup", onGestureEnd);
      chartEl.removeEventListener("wheel", onGestureEnd);
      ro.disconnect();
      chart.timeScale().unsubscribeVisibleLogicalRangeChange(onVisibleLogicalRangeChange);
      chart.remove();
      chartRef.current = null;
      sessionBreaksRef.current = null;
      seriesRef.current.clear();
      rawRef.current.clear();
      liveRef.current.clear();
      baselineRef.current.clear();
      baselineTimeRef.current = null;
      reindexingRef.current = false;
      historyReadyRef.current = false;
    };
  }, [interval]);

  // Closed bars only — no forming-bar / tick updates (LC: update on interval close)
  useEffect(() => {
    const commitClosedBar = (symbol: string, bar: RawBar) => {
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
      liveRef.current.set(symbol, null);

      if (symbol === REF_SYMBOL) {
        sessionBreaksRef.current?.setBoundaries(computeUtcDayBoundaries(bars));
      }

      if (!enabledSymbolsRef.current.has(symbol)) return;
      if (reindexingRef.current) return;

      const baseline = baselineRef.current.get(symbol);
      if (!baseline || baseline <= 0) return;
      series.update({
        time: bar.time as UTCTimestamp,
        value: ((bar.close / baseline) - 1) * 100,
      });
    };

    const unsubs = COMPARISON_SYMBOLS.map((sym) =>
      subscribe(sym, (msg) => {
        if (msg.type !== "bar" || msg.interval !== interval) return;

        const m = msg as BarMsg;
        const t = m.time ?? barOpenTime(Math.floor(m.ts / 1e9), interval);
        const bar: RawBar = { time: t, close: parseFloat(m.close) };
        const openBucket = currentBarBucket(interval);

        if (t >= openBucket) {
          const prev = liveRef.current.get(sym);
          if (prev && prev.time < openBucket) {
            commitClosedBar(sym, prev);
          }
          liveRef.current.set(sym, bar);
          return;
        }

        commitClosedBar(sym, bar);
      })
    );

    return () => unsubs.forEach((u) => u());
  }, [interval, subscribe]);

  useEffect(() => {
    enabledSymbolsRef.current = new Set(symbols);
    if (!historyReadyRef.current) return;
    for (const sym of COMPARISON_SYMBOLS) {
      const series = seriesRef.current.get(sym);
      if (!series) continue;
      const visible = enabledSymbolsRef.current.has(sym);
      series.applyOptions({ visible });
      if (!visible) continue;
      const t = baselineTimeRef.current;
      if (t === null) continue;
      const raw = rawRef.current.get(sym) ?? [];
      const baseline = baselineCloseAtTime(raw, t);
      if (baseline > 0) {
        baselineRef.current.set(sym, baseline);
        reindexingRef.current = true;
        try {
          series.setData(toPercentLine(raw, baseline));
        } finally {
          reindexingRef.current = false;
        }
      }
    }
  }, [symbols]);

  useEffect(() => {
    if (!openIntervalMenu) return;
    const close = () => setOpenIntervalMenu(false);
    window.addEventListener("click", close);
    return () => window.removeEventListener("click", close);
  }, [openIntervalMenu]);

  const scrollToRealtime = () => {
    chartRef.current?.timeScale().scrollToRealTime();
    settleReindexRef.current?.();
    requestAnimationFrame(() => sessionBreaksRef.current?.refresh());
  };
  const stopMenuClick = (e: React.MouseEvent) => e.stopPropagation();

  const toggleSymbol = (sym: ComparisonSymbol) => {
    const next = enabledSet.has(sym)
      ? symbols.filter((s) => s !== sym)
      : [...symbols, sym];
    if (next.length === 0) return;
    onConfigChange({ symbols: next });
  };

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
          {COMPARISON_SYMBOLS.map((sym) => {
            const on = enabledSet.has(sym);
            const accent = COMPARISON_COLORS[sym];
            return (
              <button
                key={sym}
                type="button"
                className={`${styles.legendItem} ${on ? styles.legendItemOn : styles.legendItemOff}`}
                style={
                  on
                    ? { borderColor: accent, color: accent, background: `${accent}18` }
                    : undefined
                }
                onClick={() => toggleSymbol(sym)}
                aria-pressed={on}
              >
                <span
                  className={styles.legendDot}
                  style={{ background: accent, opacity: on ? 1 : 0.35 }}
                />
                {symbolShort(sym).replace("USDT", "")}
              </button>
            );
          })}
        </div>
      </div>

      <div className={styles.chartWrap}>
        {loading && <div className={styles.loading}>Loading…</div>}
        <div ref={containerRef} className={styles.chart} />
      </div>
    </div>
  );
}
