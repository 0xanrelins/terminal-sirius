import { useCallback, useEffect, useRef, useState } from "react";
import {
  createChart,
  CandlestickSeries,
  LineSeries,
  HistogramSeries,
  type IChartApi,
  type ISeriesApi,
  type CandlestickSeriesOptions,
  type CandlestickData,
  type UTCTimestamp,
  type LogicalRange,
} from "lightweight-charts";
import { useFeed } from "../../context/FeedContext";
import {
  CHART_INTERVALS,
  CHART_SYMBOLS,
  DEFAULT_LIQ_THRESHOLD,
  INDICATOR_PRESETS,
  getLiqThreshold,
  type IndicatorPreset,
  isPresetActive,
  maColor,
  presetId,
  symbolShort,
} from "../../lib/chartConfig";
import { barOpenTime, currentBarBucket } from "../../lib/barTime";
import { calculateEMA } from "../../lib/chartIndicators";
import type {
  ChartIndicator,
  Kline,
  LiquidationBar,
  LiquidationMsg,
  TradeMsg,
} from "../../types";
import styles from "./CandlestickChart.module.css";

type Props = {
  symbol: string;
  interval?: string;
  indicators?: ChartIndicator[];
  onConfigChange: (patch: {
    symbol?: string;
    interval?: string;
    indicators?: ChartIndicator[];
  }) => void;
};

const INTERVAL_SECONDS: Record<string, number> = {
  "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
  "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600, "12h": 43200,
  "1d": 86400,
};

const INITIAL_LIMIT = 500;
const PAGE_SIZE = 200;
const LOAD_THRESHOLD = 10;
const LIQ_PANE_INDEX = 1;
const LIQ_PANE_HEIGHT = 110;
const LONG_LIQ_COLOR = "#ef4444";
const SHORT_LIQ_COLOR = "#22c55e";
const LIQ_MUTED_COLOR = "#4a4a55";

type OpenMenu = "symbol" | "interval" | "indicators" | null;

type LiqBucket = { long: number; short: number };

function liqHistColor(long: number, short: number, threshold: number): string {
  const total = long + short;
  if (total < threshold) return LIQ_MUTED_COLOR;
  return long >= short ? LONG_LIQ_COLOR : SHORT_LIQ_COLOR;
}

function liqToHistogramData(bars: LiquidationBar[], threshold: number) {
  return bars.map((b) => ({
    time: b.time as UTCTimestamp,
    value: b.long + b.short,
    color: liqHistColor(b.long, b.short, threshold),
  }));
}

function toCandles(data: Kline[]): CandlestickData<UTCTimestamp>[] {
  return data.map((k) => ({
    time: k.time as UTCTimestamp,
    open: k.open,
    high: k.high,
    low: k.low,
    close: k.close,
  }));
}

/** Seed placeholder for the open candle when REST only has closed bars. */
function ensureFormingBar(
  candles: CandlestickData<UTCTimestamp>[],
  interval: string
): CandlestickData<UTCTimestamp>[] {
  if (candles.length === 0) return candles;
  const bucket = currentBarBucket(interval);
  const last = candles[candles.length - 1];
  if ((last.time as number) >= bucket) return candles;
  const price = last.close;
  return [
    ...candles,
    {
      time: bucket as UTCTimestamp,
      open: price,
      high: price,
      low: price,
      close: price,
    },
  ];
}

function mergeWithLiveBar(
  candles: CandlestickData<UTCTimestamp>[],
  live: CandlestickData<UTCTimestamp> | null
): CandlestickData<UTCTimestamp>[] {
  if (!live) return candles;
  const idx = candles.findIndex((b) => b.time === live.time);
  if (idx >= 0) {
    const out = [...candles];
    out[idx] = live;
    return out;
  }
  const lastTime = candles[candles.length - 1]?.time as number | undefined;
  if (lastTime === undefined || (live.time as number) > lastTime) {
    return [...candles, live];
  }
  return candles;
}

function prepareHistoryCandles(
  data: Kline[],
  interval: string,
  live: CandlestickData<UTCTimestamp> | null
): CandlestickData<UTCTimestamp>[] {
  return mergeWithLiveBar(ensureFormingBar(toCandles(data), interval), live);
}

function mergeCandles(
  existing: CandlestickData<UTCTimestamp>[],
  older: CandlestickData<UTCTimestamp>[]
): CandlestickData<UTCTimestamp>[] {
  if (older.length === 0) return existing;
  const byTime = new Map<number, CandlestickData<UTCTimestamp>>();
  for (const bar of [...older, ...existing]) {
    byTime.set(bar.time as number, bar);
  }
  return Array.from(byTime.values()).sort(
    (a, b) => (a.time as number) - (b.time as number)
  );
}

function normalizeIndicators(raw: ChartIndicator[]): ChartIndicator[] {
  return raw.map((ind) => {
    const t = (ind as { type: string }).type;
    if (t === "liquidations") {
      const threshold =
        "threshold" in ind && typeof ind.threshold === "number"
          ? ind.threshold
          : DEFAULT_LIQ_THRESHOLD;
      return { id: "liquidations", type: "liquidations" as const, threshold };
    }
    if (t === "ema" || t === "sma") {
      const period = "period" in ind ? ind.period : 20;
      return { id: `ema-${period}`, type: "ema" as const, period };
    }
    return ind;
  });
}

export function CandlestickChart({
  symbol,
  interval = "1m",
  indicators: indicatorsProp = [],
  onConfigChange,
}: Props) {
  const indicators = normalizeIndicators(indicatorsProp);
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const maSeriesRef = useRef<Map<string, ISeriesApi<"Line">>>(new Map());
  const liqSeriesRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const liqDataRef = useRef<Map<number, LiqBucket>>(new Map());
  const barsRef = useRef<CandlestickData<UTCTimestamp>[]>([]);
  const liveBarRef = useRef<CandlestickData<UTCTimestamp> | null>(null);
  const loadingRef = useRef(false);
  const exhaustedRef = useRef(false);
  const historyReadyRef = useRef(false);
  const indicatorsRef = useRef(indicators);
  const [openMenu, setOpenMenu] = useState<OpenMenu>(null);
  const { subscribe } = useFeed();

  indicatorsRef.current = indicators;
  const hasLiquidations = indicators.some((i) => i.type === "liquidations");
  const liqThreshold = getLiqThreshold(indicators);

  const pushLiqToSeries = useCallback(() => {
    const threshold = getLiqThreshold(indicatorsRef.current);
    const bars: LiquidationBar[] = Array.from(liqDataRef.current.entries())
      .map(([time, v]) => ({ time, long: v.long, short: v.short }))
      .sort((a, b) => a.time - b.time);
    liqSeriesRef.current?.setData(liqToHistogramData(bars, threshold));
  }, []);

  const applyLiqBar = useCallback((time: number, long: number, short: number) => {
    liqDataRef.current.set(time, { long, short });
    if (!liqSeriesRef.current) return;
    const threshold = getLiqThreshold(indicatorsRef.current);
    liqSeriesRef.current.update({
      time: time as UTCTimestamp,
      value: long + short,
      color: liqHistColor(long, short, threshold),
    });
  }, []);

  const syncLiqSeries = useCallback(
    (chart: IChartApi, enabled: boolean) => {
      if (!enabled) {
        if (liqSeriesRef.current) {
          chart.removeSeries(liqSeriesRef.current);
          liqSeriesRef.current = null;
        }
        liqDataRef.current.clear();
        return;
      }

      if (!liqSeriesRef.current) {
        liqSeriesRef.current = chart.addSeries(
          HistogramSeries,
          {
            priceFormat: { type: "volume" },
            priceLineVisible: false,
            lastValueVisible: false,
          },
          LIQ_PANE_INDEX
        );
        const liqPane = chart.panes()[LIQ_PANE_INDEX];
        if (liqPane) liqPane.setHeight(LIQ_PANE_HEIGHT);
      }
      pushLiqToSeries();
    },
    [pushLiqToSeries]
  );

  const refreshMaSeries = useCallback(() => {
    const bars = barsRef.current;
    indicatorsRef.current.forEach((ind, idx) => {
      const line = maSeriesRef.current.get(ind.id);
      if (!line || ind.type !== "ema") return;
      line.setData(calculateEMA(bars, ind.period));
    });
  }, []);

  const syncMaSeries = useCallback(
    (chart: IChartApi, next: ChartIndicator[]) => {
      const prev = maSeriesRef.current;
      const nextIds = new Set(next.map((i) => i.id));

      for (const [id, line] of prev) {
        if (!nextIds.has(id)) {
          chart.removeSeries(line);
          prev.delete(id);
        }
      }

      next.forEach((ind, idx) => {
        if (ind.type !== "ema") return;
        let line = prev.get(ind.id);
        if (!line) {
          line = chart.addSeries(LineSeries, {
            color: maColor(idx),
            lineWidth: 1,
            priceLineVisible: false,
            lastValueVisible: false,
          });
          prev.set(ind.id, line);
        }
        line.setData(calculateEMA(barsRef.current, ind.period));
      });
    },
    []
  );

  const setCandleData = useCallback(
    (data: CandlestickData<UTCTimestamp>[]) => {
      barsRef.current = data;
      seriesRef.current?.setData(data);
      refreshMaSeries();
    },
    [refreshMaSeries]
  );

  // Chart init + history + infinite scroll
  useEffect(() => {
    if (!containerRef.current) return;

    barsRef.current = [];
    liveBarRef.current = null;
    loadingRef.current = false;
    exhaustedRef.current = false;
    historyReadyRef.current = false;
    maSeriesRef.current.clear();
    liqSeriesRef.current = null;
    liqDataRef.current.clear();

    const chart = createChart(containerRef.current, {
      layout: {
        background: { color: "#131318" },
        textColor: "#888",
        attributionLogo: false,
        panes: {
          separatorColor: "#2a2a35",
          separatorHoverColor: "#3a3a48",
          enableResize: true,
        },
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
    syncMaSeries(chart, indicatorsRef.current);
    syncLiqSeries(chart, indicatorsRef.current.some((i) => i.type === "liquidations"));

    const fetchKlines = async (before?: number): Promise<Kline[]> => {
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
      if (loadingRef.current || exhaustedRef.current) return;
      const oldest = barsRef.current[0]?.time as number | undefined;
      if (oldest === undefined) return;

      loadingRef.current = true;
      try {
        const data = await fetchKlines(oldest);
        const older = toCandles(data).filter((b) => (b.time as number) < oldest);
        if (older.length === 0) {
          exhaustedRef.current = true;
          return;
        }
        setCandleData(mergeCandles(barsRef.current, older));
      } catch (e) {
        console.error(e);
      } finally {
        loadingRef.current = false;
      }
    };

    const onVisibleRangeChange = (range: LogicalRange | null) => {
      if (!range || range.from >= LOAD_THRESHOLD) return;
      void loadOlder();
    };

    chart.timeScale().subscribeVisibleLogicalRangeChange(onVisibleRangeChange);

    const ro = new ResizeObserver(() => {
      if (!containerRef.current) return;
      chart.applyOptions({
        width: containerRef.current.clientWidth,
        height: containerRef.current.clientHeight,
      });
    });
    ro.observe(containerRef.current);

    fetchKlines()
      .then((data) => {
        const candles = prepareHistoryCandles(data, interval, liveBarRef.current);
        const bucket = currentBarBucket(interval);
        const last = candles[candles.length - 1];
        if (last && (last.time as number) === bucket) {
          liveBarRef.current = last;
        }
        setCandleData(candles);
        historyReadyRef.current = true;
        chart.timeScale().fitContent();
      })
      .catch((e) => {
        console.error(e);
        historyReadyRef.current = true;
      });

    return () => {
      ro.disconnect();
      chart.timeScale().unsubscribeVisibleLogicalRangeChange(onVisibleRangeChange);
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
      maSeriesRef.current.clear();
      liqSeriesRef.current = null;
      liqDataRef.current.clear();
      barsRef.current = [];
      liveBarRef.current = null;
      historyReadyRef.current = false;
    };
  }, [symbol, interval, setCandleData, syncMaSeries, syncLiqSeries]);

  // Sync indicators when toggled from toolbar
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    syncMaSeries(chart, indicators);
    syncLiqSeries(chart, hasLiquidations);
  }, [indicators, hasLiquidations, syncMaSeries, syncLiqSeries]);

  useEffect(() => {
    if (hasLiquidations) pushLiqToSeries();
  }, [hasLiquidations, liqThreshold, pushLiqToSeries]);

  // Load liquidation history
  useEffect(() => {
    if (!hasLiquidations) return;

    const params = new URLSearchParams({
      symbol,
      interval,
      limit: String(INITIAL_LIMIT),
    });
    fetch(`/liquidations?${params}`)
      .then((r) => {
        if (!r.ok) throw new Error(`liquidations ${r.status}`);
        return r.json() as Promise<LiquidationBar[]>;
      })
      .then((data) => {
        liqDataRef.current.clear();
        for (const b of data) {
          liqDataRef.current.set(b.time, { long: b.long, short: b.short });
        }
        pushLiqToSeries();
      })
      .catch(console.error);
  }, [symbol, interval, hasLiquidations, pushLiqToSeries]);

  // Live updates
  useEffect(() => {
    const commitBar = (bar: CandlestickData<UTCTimestamp>) => {
      const series = seriesRef.current;
      if (!series || !historyReadyRef.current) return;

      const bars = barsRef.current;
      const idx = bars.findIndex((b) => b.time === bar.time);
      if (idx >= 0) bars[idx] = bar;
      else {
        bars.push(bar);
        bars.sort((a, b) => (a.time as number) - (b.time as number));
      }
      series.update(bar);
      refreshMaSeries();
    };

    const unsub = subscribe(symbol, (msg) => {
      if (msg.type === "liquidation") {
        const liq = msg as LiquidationMsg;
        const barSec = INTERVAL_SECONDS[interval] ?? 60;
        const t = Math.floor(liq.time / barSec) * barSec;
        const cur = { ...(liqDataRef.current.get(t) ?? { long: 0, short: 0 }) };
        if (liq.side === "SELL") cur.long += liq.notional;
        else cur.short += liq.notional;
        applyLiqBar(t, cur.long, cur.short);
        return;
      }

      if (msg.type === "bar" && msg.interval === interval) {
        const t =
          msg.time ??
          barOpenTime(Math.floor(msg.ts / 1e9), interval);
        const bar: CandlestickData<UTCTimestamp> = {
          time: t as UTCTimestamp,
          open: parseFloat(msg.open),
          high: parseFloat(msg.high),
          low: parseFloat(msg.low),
          close: parseFloat(msg.close),
        };
        liveBarRef.current = bar;
        commitBar(bar);
        return;
      }

      if (msg.type === "trade") {
        const price = parseFloat((msg as TradeMsg).price);
        const barTime = barOpenTime(Math.floor(msg.ts / 1e9), interval) as UTCTimestamp;
        const cur = liveBarRef.current;

        if (!cur || (cur.time as number) !== barTime) {
          liveBarRef.current = {
            time: barTime,
            open: price,
            high: price,
            low: price,
            close: price,
          };
        } else {
          liveBarRef.current = {
            time: barTime,
            open: cur.open,
            high: Math.max(cur.high, price),
            low: Math.min(cur.low, price),
            close: price,
          };
        }

        commitBar(liveBarRef.current);
      }
    });

    return unsub;
  }, [symbol, interval, subscribe, refreshMaSeries, applyLiqBar]);

  // Close menus on outside click
  useEffect(() => {
    if (!openMenu) return;
    const close = () => setOpenMenu(null);
    window.addEventListener("click", close);
    return () => window.removeEventListener("click", close);
  }, [openMenu]);

  const togglePreset = (preset: IndicatorPreset) => {
    const id = presetId(preset);
    const exists = indicators.some((i) => i.id === id);
    if (exists) {
      onConfigChange({ indicators: indicators.filter((i) => i.id !== id) });
      return;
    }
    const added: ChartIndicator =
      preset.type === "liquidations"
        ? { id, type: "liquidations", threshold: DEFAULT_LIQ_THRESHOLD }
        : { id, type: "ema", period: preset.period };
    onConfigChange({ indicators: [...indicators, added] });
  };

  const setLiqThreshold = (value: number) => {
    const next = Math.max(0, value);
    onConfigChange({
      indicators: indicators.map((i) =>
        i.type === "liquidations" ? { ...i, threshold: next } : i
      ),
    });
  };

  const activeIndicatorCount = indicators.length;

  const stopMenuClick = (e: React.MouseEvent) => e.stopPropagation();

  return (
    <div className={styles.wrapper}>
      <div
        className={`${styles.toolbar} chartToolbar`}
        onClick={stopMenuClick}
      >
        <div className={styles.menuWrap}>
          <button
            type="button"
            className={`${styles.menuBtn} ${openMenu === "symbol" ? styles.menuBtnActive : ""}`}
            onClick={(e) => {
              e.stopPropagation();
              setOpenMenu((m) => (m === "symbol" ? null : "symbol"));
            }}
          >
            {symbolShort(symbol)} PERP
            <span className={styles.chevron}>▼</span>
          </button>
          {openMenu === "symbol" && (
            <div className={styles.menu}>
              {CHART_SYMBOLS.map((s) => (
                <button
                  key={s}
                  type="button"
                  className={`${styles.menuItem} ${s === symbol ? styles.menuItemActive : ""}`}
                  onClick={() => {
                    onConfigChange({ symbol: s });
                    setOpenMenu(null);
                  }}
                >
                  {symbolShort(s)} PERP
                  {s === symbol && <span className={styles.check}>✓</span>}
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
              {CHART_INTERVALS.map((iv) => (
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

        <div className={styles.menuWrap}>
          <button
            type="button"
            className={`${styles.menuBtn} ${openMenu === "indicators" ? styles.menuBtnActive : ""}`}
            onClick={(e) => {
              e.stopPropagation();
              setOpenMenu((m) => (m === "indicators" ? null : "indicators"));
            }}
          >
            Indicators{activeIndicatorCount > 0 ? ` (${activeIndicatorCount})` : ""}
            <span className={styles.chevron}>▼</span>
          </button>
          {openMenu === "indicators" && (
            <div className={styles.menu}>
              {INDICATOR_PRESETS.map((preset) => {
                const id = presetId(preset);
                const active = isPresetActive(indicators, preset);
                return (
                  <button
                    key={id}
                    type="button"
                    className={`${styles.menuItem} ${active ? styles.menuItemActive : ""}`}
                    onClick={() => togglePreset(preset)}
                  >
                    {preset.label}
                    {active && <span className={styles.check}>✓</span>}
                  </button>
                );
              })}
              {hasLiquidations && (
                <div className={styles.thresholdRow}>
                  <span className={styles.thresholdLabel}>Liq threshold ($)</span>
                  <input
                    type="number"
                    className={styles.thresholdInput}
                    min={0}
                    step={1000}
                    value={liqThreshold}
                    onClick={stopMenuClick}
                    onChange={(e) => setLiqThreshold(Number(e.target.value) || 0)}
                  />
                </div>
              )}
            </div>
          )}
        </div>
      </div>
      <div ref={containerRef} className={styles.chart} />
    </div>
  );
}