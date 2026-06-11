import { useCallback, useEffect, useRef, useState } from "react";
import {
  createChart,
  CandlestickSeries,
  LineSeries,
  HistogramSeries,
  LineStyle,
  type IChartApi,
  type ISeriesApi,
  type IPriceLine,
  type CandlestickSeriesOptions,
  type CandlestickData,
  type LineData,
  type WhitespaceData,
  type UTCTimestamp,
  type LogicalRange,
} from "lightweight-charts";
import { useFeed } from "../../context/FeedContext";
import {
  CHART_INTERVALS,
  CHART_SYMBOLS,
  DEFAULT_EMA_PERIOD,
  DEFAULT_LIQ_THRESHOLD,
  DEFAULT_ROLLING_VWAP_PERIOD,
  DEFAULT_SESSION_BREAK_MINUTES,
  DEFAULT_SESSION_HLINE_MINUTES,
  DEFAULT_VWAP_PERIOD,
  INDICATOR_PRESETS,
  clampInitialBars,
  getEmaPeriod,
  getLiqThreshold,
  getRollingVwapPeriod,
  getSessionBreakMinutes,
  getSessionHLineMinutes,
  getVwapPeriod,
  type IndicatorPreset,
  indicatorLineColor,
  isPresetActive,
  presetId,
  symbolShort,
  VWAP_LINE_WIDTH,
} from "../../lib/chartConfig";
import {
  barOpenTime,
  currentBarBucket,
  INTERVAL_SECONDS,
  sessionBucketOpen,
} from "../../lib/barTime";
import {
  CANDLESTICK_NEXT_SESSION_BREAK_OPTIONS,
  CANDLESTICK_SESSION_BREAK_OPTIONS,
  SessionBreaksPrimitive,
  computeUtcIntervalBoundariesWithNext,
} from "../../lib/dailySessionBreaks";
import {
  SessionHorizontalLinesPrimitive,
  computeSessionHorizontalSegments,
} from "../../lib/sessionHorizontalLines";
import {
  binancePerpToPolySeries,
  polySeriesToFeedSymbol,
} from "../../lib/binancePolySeries";
import {
  paperEventMatchesChart,
  tradeMarkerForPaperEvent,
} from "../../lib/tradeSignalMarkers";
import { TradeSignalMarkersPrimitive } from "../../lib/tradeSignalMarkersPrimitive";
import {
  liqApiInterval,
  liqHistogramPoint,
  liqToHistogramData,
  liquidationBarForChart,
} from "../../lib/liquidationBar";
import {
  calculateEMA,
  calculateSessionVWAPSegments,
  calculateSessionVwapTailPoint,
  calculateVWAP,
  isAnchoredVwapType,
  type OhlcvBar,
} from "../../lib/chartIndicators";
import type {
  ChartStyle,
  ChartIndicator,
  IndicatorMsg,
  Kline,
  LiquidationBar,
  LiquidationMsg,
  PaperEventMsg,
  PolymarketMsg,
  TradeMsg,
} from "../../types";
import styles from "./CandlestickChart.module.css";

type Props = {
  symbol: string;
  interval?: string;
  chartStyle?: ChartStyle;
  indicators?: ChartIndicator[];
  /** Bar window for this widget (fetch + viewport on every symbol/interval load). */
  initialBars?: number;
  onConfigChange: (patch: {
    symbol?: string;
    interval?: string;
    chartStyle?: ChartStyle;
    indicators?: ChartIndicator[];
  }) => void;
};

const INITIAL_LIMIT = 500;
const PAGE_SIZE = 200;
const LOAD_THRESHOLD = 10;
const POLY_PANE_INDEX = 1;
const LIQ_PANE_HEIGHT = 150;
const POLY_PANE_HEIGHT = 400;
/** Fixed poly pane Y range: -0.05¢ … 100.5¢ in 0–1 series values. */
const POLY_PRICE_MIN = -0.0005;
const POLY_PRICE_MAX = 1.005;
const POLY_UP_COLOR = "#a78bfa";
/** Rolling in-memory window for 1s/5s live charts (OHLCV, poly, WS indicator lines). */
const REALTIME_WINDOW_MINUTES = 90;

function maxRealtimeBars(interval: string): number {
  const barSec = INTERVAL_SECONDS[interval] ?? 60;
  return Math.max(1, Math.floor((REALTIME_WINDOW_MINUTES * 60) / barSec));
}

function trimOhlcvBars(bars: OhlcvBar[], maxBars: number): OhlcvBar[] {
  if (bars.length <= maxBars) return bars;
  return bars.slice(bars.length - maxBars);
}

/** Drop points older than minTime; ISeriesApi.setData when the series window slides. */
function trimLineSeriesFromTime(
  series: ISeriesApi<"Line">,
  minTime: number
): void {
  const data = series.data();
  if (data.length === 0) return;
  if ((data[0].time as number) >= minTime) return;
  const trimmed = data.filter((p) => (p.time as number) >= minTime);
  if (trimmed.length === data.length) return;
  series.setData(trimmed as LineData<UTCTimestamp>[]);
}

/** Poly above liq: pane 1 = poly, pane 2 = liq when both enabled. */
function liqPaneIndex(hasPolymarketUpPane: boolean): number {
  return hasPolymarketUpPane ? 2 : 1;
}

/** Main chart pane weight; poly/liq use pixel targets as relative weights (LC multi-pane pattern). */
const MAIN_PANE_STRETCH_WEIGHT = 550;

/**
 * IPaneApi.setStretchFactor — setHeight on multiple panes fights itself when liq pane is added.
 * Weights ≈ desired px: poly 300, liq 150, main gets the rest.
 */
function applyIndicatorPaneHeights(
  chart: IChartApi,
  hasPoly: boolean,
  hasLiq: boolean
) {
  const panes = chart.panes();
  if (panes.length <= 1 || (!hasPoly && !hasLiq)) return;

  panes[0]?.setStretchFactor(MAIN_PANE_STRETCH_WEIGHT);

  if (hasPoly && hasLiq && panes.length >= 3) {
    panes[POLY_PANE_INDEX]?.setStretchFactor(POLY_PANE_HEIGHT);
    panes[2]?.setStretchFactor(LIQ_PANE_HEIGHT);
  } else if (hasPoly && panes.length >= 2) {
    panes[POLY_PANE_INDEX]?.setStretchFactor(POLY_PANE_HEIGHT);
  } else if (hasLiq && panes.length >= 2) {
    panes[liqPaneIndex(false)]?.setStretchFactor(LIQ_PANE_HEIGHT);
  }
}

function scheduleIndicatorPaneHeights(
  chart: IChartApi,
  hasPoly: boolean,
  hasLiq: boolean
) {
  const apply = () => applyIndicatorPaneHeights(chart, hasPoly, hasLiq);
  apply();
  requestAnimationFrame(() => {
    apply();
    requestAnimationFrame(apply);
  });
}

function formatPolyUpPrice(v: number): string {
  return `${(v * 100).toFixed(1)}¢`;
}

function applyPolyPriceScale(series: ISeriesApi<"Line">) {
  const scale = series.priceScale();
  scale.applyOptions({
    autoScale: false,
    scaleMargins: { top: 0.02, bottom: 0.02 },
  });
  scale.setAutoScale(false);
  scale.setVisibleRange({ from: POLY_PRICE_MIN, to: POLY_PRICE_MAX });
}

/** IPriceScaleApi — liq histogram needs autoscale after setData (empty setData on init breaks ticks until dbl-click). */
function applyLiqPriceScaleAutoscale(series: ISeriesApi<"Histogram">) {
  const scale = series.priceScale();
  scale.applyOptions({
    autoScale: true,
    scaleMargins: { top: 0.08, bottom: 0 },
  });
  scale.setAutoScale(true);
}
type OpenMenu = "symbol" | "interval" | "style" | "indicators" | null;

type LiqBucket = { long: number; short: number };
type PriceSeriesType = "candlestick" | "line";

const VWAP_SEG = ":seg:";

function vwapSegmentKey(baseId: string, index: number): string {
  return `${baseId}${VWAP_SEG}${index}`;
}

function toOhlcv(data: Kline[]): OhlcvBar[] {
  return data.map((k) => ({
    time: k.time as UTCTimestamp,
    open: k.open,
    high: k.high,
    low: k.low,
    close: k.close,
    volume: k.volume,
  }));
}

function toCandles(bars: OhlcvBar[]): CandlestickData<UTCTimestamp>[] {
  return bars.map(({ time, open, high, low, close }) => ({
    time,
    open,
    high,
    low,
    close,
  }));
}

function toLineData(bars: OhlcvBar[]) {
  return bars.map(({ time, close }) => ({
    time,
    value: close,
  }));
}

/** Seed placeholder for the open candle when REST only has closed bars. */
function ensureFormingBar(bars: OhlcvBar[], interval: string): OhlcvBar[] {
  if (bars.length === 0) return bars;
  const bucket = currentBarBucket(interval);
  const last = bars[bars.length - 1];
  if ((last.time as number) >= bucket) return bars;
  const price = last.close;
  return [
    ...bars,
    {
      time: bucket as UTCTimestamp,
      open: price,
      high: price,
      low: price,
      close: price,
      volume: 0,
    },
  ];
}

/** Bar count at/above this uses fitContent on first open (legacy full-chart view). */
const FIT_CONTENT_BAR_THRESHOLD = INITIAL_LIMIT;

/** Widget bar window: 500+ uses fitContent; smaller N shows last bars anchored right. */
function applyWidgetBarViewport(
  chart: IChartApi,
  totalBars: number,
  openBarCount: number
) {
  const ts = chart.timeScale();
  const visible = Math.min(openBarCount, totalBars);

  if (openBarCount >= FIT_CONTENT_BAR_THRESHOLD || visible >= totalBars) {
    ts.fitContent();
    return;
  }

  ts.setVisibleLogicalRange({
    from: totalBars - visible,
    to: totalBars - 1,
  });
  ts.scrollToRealTime();
}

function mergeWithLiveBar(bars: OhlcvBar[], live: OhlcvBar | null): OhlcvBar[] {
  if (!live) return bars;
  const idx = bars.findIndex((b) => b.time === live.time);
  if (idx >= 0) {
    const out = [...bars];
    out[idx] = live;
    return out;
  }
  const lastTime = bars[bars.length - 1]?.time as number | undefined;
  if (lastTime === undefined || (live.time as number) > lastTime) {
    return [...bars, live];
  }
  return bars;
}

function prepareHistoryBars(
  data: Kline[],
  interval: string,
  live: OhlcvBar | null
): OhlcvBar[] {
  return mergeWithLiveBar(ensureFormingBar(toOhlcv(data), interval), live);
}

function mergeOhlcvBars(existing: OhlcvBar[], older: OhlcvBar[]): OhlcvBar[] {
  if (older.length === 0) return existing;
  const byTime = new Map<number, OhlcvBar>();
  for (const bar of [...older, ...existing]) {
    byTime.set(bar.time as number, bar);
  }
  return Array.from(byTime.values()).sort(
    (a, b) => (a.time as number) - (b.time as number)
  );
}

const LIQ_FETCH_LIMIT = 10_000;
/** Max candle repaint rate from trade ticks (match backend forming-bar 500ms). */
const CANDLE_FLUSH_MS = 500;

async function fetchLiquidationsForRange(
  symbol: string,
  interval: string,
  fromTime: number,
  toTime: number
): Promise<LiquidationBar[]> {
  const params = new URLSearchParams({
    symbol,
    interval,
    from: String(fromTime),
    to: String(toTime),
    limit: String(LIQ_FETCH_LIMIT),
  });
  const r = await fetch(`/liquidations?${params}`);
  if (!r.ok) throw new Error(`liquidations ${r.status}`);
  return r.json() as Promise<LiquidationBar[]>;
}

function lineDataForIndicator(
  ind: ChartIndicator,
  candles: CandlestickData<UTCTimestamp>[],
  ohlcv: OhlcvBar[],
  chartInterval: string
) {
  if (ind.type === "ema") return calculateEMA(candles, ind.period);
  if (ind.type === "rolling_vwap") return calculateVWAP(ohlcv, ind.period);
  return [];
}

function anchoredVwapSegments(
  ohlcv: OhlcvBar[],
  period: number,
  chartInterval: string
) {
  return calculateSessionVWAPSegments(ohlcv, period, chartInterval);
}

function normalizeIndicators(raw: ChartIndicator[]): ChartIndicator[] {
  let ema: ChartIndicator | null = null;
  let vwap: ChartIndicator | null = null;
  let rollingVwap: ChartIndicator | null = null;
  let liquidations: ChartIndicator | null = null;
  let polymarketUp: ChartIndicator | null = null;
  let sessionBreaks: ChartIndicator | null = null;
  let sessionHlines: ChartIndicator | null = null;
  let tradeSignals: ChartIndicator | null = null;

  for (const ind of raw) {
    const t = (ind as { type: string }).type;
    if (t === "liquidations") {
      const threshold =
        "threshold" in ind && typeof ind.threshold === "number"
          ? ind.threshold
          : DEFAULT_LIQ_THRESHOLD;
      liquidations = { id: "liquidations", type: "liquidations", threshold };
      continue;
    }
    if (t === "polymarket_up") {
      polymarketUp = { id: "polymarket_up", type: "polymarket_up" };
      continue;
    }
    if (t === "session_breaks") {
      const periodMinutes =
        "periodMinutes" in ind && typeof ind.periodMinutes === "number"
          ? ind.periodMinutes
          : DEFAULT_SESSION_BREAK_MINUTES;
      sessionBreaks = {
        id: "session_breaks",
        type: "session_breaks",
        periodMinutes: Math.max(1, Math.floor(periodMinutes)),
      };
      continue;
    }
    if (t === "session_hlines") {
      const periodMinutes =
        "periodMinutes" in ind && typeof ind.periodMinutes === "number"
          ? ind.periodMinutes
          : DEFAULT_SESSION_HLINE_MINUTES;
      sessionHlines = {
        id: "session_hlines",
        type: "session_hlines",
        periodMinutes: Math.max(1, Math.floor(periodMinutes)),
      };
      continue;
    }
    if (t === "trade_signals") {
      tradeSignals = { id: "trade_signals", type: "trade_signals" };
      continue;
    }
    if (t === "vwap") {
      const period =
        "period" in ind && typeof ind.period === "number"
          ? ind.period
          : DEFAULT_VWAP_PERIOD;
      vwap = { id: "vwap", type: "vwap", period: Math.max(1, period) };
      continue;
    }
    if (t === "session_vwap") {
      const period =
        "period" in ind && typeof ind.period === "number"
          ? ind.period
          : DEFAULT_VWAP_PERIOD;
      vwap = { id: "vwap", type: "vwap", period: Math.max(1, period) };
      continue;
    }
    if (t === "rolling_vwap") {
      const period =
        "period" in ind && typeof ind.period === "number"
          ? ind.period
          : DEFAULT_ROLLING_VWAP_PERIOD;
      rollingVwap = {
        id: "rolling_vwap",
        type: "rolling_vwap",
        period: Math.max(1, period),
      };
      continue;
    }
    if (t === "ema" || t === "sma") {
      let period =
        "period" in ind && typeof ind.period === "number"
          ? ind.period
          : DEFAULT_EMA_PERIOD;
      const id = (ind as { id?: string }).id;
      if (id?.startsWith("ema-")) {
        const parsed = parseInt(id.slice(4), 10);
        if (!Number.isNaN(parsed)) period = parsed;
      }
      ema = { id: "ema", type: "ema", period: Math.max(1, period) };
    }
  }

  const out: ChartIndicator[] = [];
  if (ema) out.push(ema);
  if (vwap) out.push(vwap);
  if (rollingVwap) out.push(rollingVwap);
  if (liquidations) out.push(liquidations);
  if (polymarketUp) out.push(polymarketUp);
  if (sessionBreaks) out.push(sessionBreaks);
  if (sessionHlines) out.push(sessionHlines);
  if (tradeSignals) out.push(tradeSignals);
  return out;
}

export function CandlestickChart({
  symbol,
  interval = "1m",
  chartStyle = "candlestick",
  indicators: indicatorsProp = [],
  initialBars: initialBarsProp,
  onConfigChange,
}: Props) {
  const isRealtimeOnlyInterval = interval === "1s" || interval === "5s";
  const indicators = normalizeIndicators(indicatorsProp);
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const lineSeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  const priceSeriesTypeRef = useRef<PriceSeriesType>("candlestick");
  const maSeriesRef = useRef<Map<string, ISeriesApi<"Line">>>(new Map());
  const liqSeriesRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const liqDataRef = useRef<Map<number, LiqBucket>>(new Map());
  /** True once setData has been called with real data — update() fails on a never-painted series. */
  const polySeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  const polyMidLineRef = useRef<IPriceLine | null>(null);
  const polyPointsRef = useRef<(LineData<UTCTimestamp> | WhitespaceData<UTCTimestamp>)[]>([]);
  const polyCurrentSlugRef = useRef<string | null>(null);
  const liqPaneWithPolyRef = useRef<boolean | null>(null);
  const ohlcvBarsRef = useRef<OhlcvBar[]>([]);
  const liveBarRef = useRef<OhlcvBar | null>(null);
  const paintTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastPaintAtRef = useRef(0);
  const loadingRef = useRef(false);
  const liqLoadingRef = useRef(false);
  const exhaustedRef = useRef(false);
  const historyReadyRef = useRef(false);
  const chartEffectGenRef = useRef(0);
  const openBarCountRef = useRef(clampInitialBars(initialBarsProp));
  const indicatorsRef = useRef(indicators);
  const [openMenu, setOpenMenu] = useState<OpenMenu>(null);
  const { subscribe } = useFeed();

  indicatorsRef.current = indicators;
  const hasEma = indicators.some((i) => i.type === "ema");
  const hasLiquidations = indicators.some((i) => i.type === "liquidations");
  const hasPolymarketUp = indicators.some((i) => i.type === "polymarket_up");
  const polySeries = binancePerpToPolySeries(symbol);
  const polyPaneActive = hasPolymarketUp && !!polySeries && isRealtimeOnlyInterval;
  const hasVwap = indicators.some((i) => i.type === "vwap");
  const hasRollingVwap = indicators.some((i) => i.type === "rolling_vwap");
  const emaPeriod = getEmaPeriod(indicators);
  const liqThreshold = getLiqThreshold(indicators);
  const vwapPeriod = getVwapPeriod(indicators);
  const rollingVwapPeriod = getRollingVwapPeriod(indicators);
  const hasSessionBreaks = indicators.some((i) => i.type === "session_breaks");
  const hasSessionHlines = indicators.some((i) => i.type === "session_hlines");
  const hasTradeSignals = indicators.some((i) => i.type === "trade_signals");
  const sessionBreakMinutes = getSessionBreakMinutes(indicators);
  const sessionHlineMinutes = getSessionHLineMinutes(indicators);

  // Draft states: let the user type freely; commit to config only on blur
  const [emaDraft, setEmaDraft] = useState<string>(() => String(emaPeriod));
  const [vwapDraft, setVwapDraft] = useState<string>(() => String(vwapPeriod));
  const [rollingVwapDraft, setRollingVwapDraft] = useState<string>(() => String(rollingVwapPeriod));
  const [liqDraft, setLiqDraft] = useState<string>(() => String(liqThreshold));
  const [sessionBreakDraft, setSessionBreakDraft] = useState<string>(() =>
    String(sessionBreakMinutes)
  );
  const [sessionHlineDraft, setSessionHlineDraft] = useState<string>(() =>
    String(sessionHlineMinutes)
  );

  // Keep drafts in sync when external config changes (e.g. widget reset)
  const prevEmaPeriod = useRef(emaPeriod);
  const prevVwapPeriod = useRef(vwapPeriod);
  const prevRollingVwapPeriod = useRef(rollingVwapPeriod);
  const prevLiqThreshold = useRef(liqThreshold);
  const prevSessionBreakMinutes = useRef(sessionBreakMinutes);
  const prevSessionHlineMinutes = useRef(sessionHlineMinutes);
  if (prevEmaPeriod.current !== emaPeriod) { prevEmaPeriod.current = emaPeriod; setEmaDraft(String(emaPeriod)); }
  if (prevVwapPeriod.current !== vwapPeriod) { prevVwapPeriod.current = vwapPeriod; setVwapDraft(String(vwapPeriod)); }
  if (prevRollingVwapPeriod.current !== rollingVwapPeriod) { prevRollingVwapPeriod.current = rollingVwapPeriod; setRollingVwapDraft(String(rollingVwapPeriod)); }
  if (prevLiqThreshold.current !== liqThreshold) { prevLiqThreshold.current = liqThreshold; setLiqDraft(String(liqThreshold)); }
  if (prevSessionBreakMinutes.current !== sessionBreakMinutes) {
    prevSessionBreakMinutes.current = sessionBreakMinutes;
    setSessionBreakDraft(String(sessionBreakMinutes));
  }
  if (prevSessionHlineMinutes.current !== sessionHlineMinutes) {
    prevSessionHlineMinutes.current = sessionHlineMinutes;
    setSessionHlineDraft(String(sessionHlineMinutes));
  }

  const sessionBreaksRef = useRef<SessionBreaksPrimitive | null>(null);
  const sessionBreakAttachedRef = useRef(false);
  const sessionHlinesRef = useRef<SessionHorizontalLinesPrimitive | null>(null);
  const sessionHlinesAttachedRef = useRef(false);
  const tradeSignalsPrimitiveRef = useRef<TradeSignalMarkersPrimitive | null>(null);
  const tradeSignalsAttachedRef = useRef(false);

  const syncTradeSignalsPrimitive = useCallback(() => {
    const series = candleSeriesRef.current ?? lineSeriesRef.current;
    const primitive = tradeSignalsPrimitiveRef.current;
    if (!series || !primitive) return;

    const enabled = indicatorsRef.current.some((i) => i.type === "trade_signals");
    if (enabled) {
      if (!tradeSignalsAttachedRef.current) {
        series.attachPrimitive(primitive);
        tradeSignalsAttachedRef.current = true;
      }
      primitive.refresh();
    } else if (tradeSignalsAttachedRef.current) {
      series.detachPrimitive(primitive);
      tradeSignalsAttachedRef.current = false;
      primitive.clearMarkers();
    }
  }, []);

  const applyPaperTradeEvent = useCallback(
    (ev: PaperEventMsg) => {
      if (!indicatorsRef.current.some((i) => i.type === "trade_signals")) return;
      if (!paperEventMatchesChart(ev, symbol)) return;
      const marker = tradeMarkerForPaperEvent(ev, interval);
      if (!marker) return;
      tradeSignalsPrimitiveRef.current?.addMarker(marker);
      syncTradeSignalsPrimitive();
    },
    [symbol, interval, syncTradeSignalsPrimitive]
  );

  const pushLiqToSeries = useCallback(() => {
    const series = liqSeriesRef.current;
    if (!series) return;
    const threshold = getLiqThreshold(indicatorsRef.current);
    const bars: LiquidationBar[] = Array.from(liqDataRef.current.entries())
      .map(([time, v]) => ({ time, long: v.long, short: v.short }))
      .sort((a, b) => a.time - b.time);
    if (bars.length === 0) return;

    series.setData(liqToHistogramData(bars, threshold));
    applyLiqPriceScaleAutoscale(series);
    requestAnimationFrame(() => {
      if (liqSeriesRef.current !== series) return;
      series.priceScale().setAutoScale(true);
    });
  }, []);

  const trimPolyPoints = useCallback(
    (pts: (LineData<UTCTimestamp> | WhitespaceData<UTCTimestamp>)[]) => {
      const maxPts = maxRealtimeBars(interval);
      if (pts.length <= maxPts) return pts;
      return pts.slice(pts.length - maxPts);
    },
    [interval]
  );

  const pushPolyToSeries = useCallback(() => {
    polySeriesRef.current?.setData(polyPointsRef.current);
  }, []);

  const clearPolyData = useCallback(() => {
    polyPointsRef.current = [];
    polyCurrentSlugRef.current = null;
    polySeriesRef.current?.setData([]);
  }, []);

  const applyPolyBar = useCallback(
    (time: number, close: number, slug?: string) => {
      const line = polySeriesRef.current;
      if (!line) return;

      const pts = polyPointsRef.current;
      const slugChanged = slug != null && polyCurrentSlugRef.current != null && slug !== polyCurrentSlugRef.current;
      if (slugChanged && pts.length > 0) {
        const boundaryTime = (Math.floor(time / 900) * 900) as UTCTimestamp;
        const beforeBoundary = pts.filter((p) => (p.time as number) < boundaryTime);
        const gapPoint: WhitespaceData<UTCTimestamp> = { time: boundaryTime };
        const withGap = trimPolyPoints([...beforeBoundary, gapPoint]);
        polyPointsRef.current = withGap;
        line.setData(withGap);
      }
      if (slug != null) polyCurrentSlugRef.current = slug;

      const t = time as UTCTimestamp;
      const color = close >= 0.5 ? "#22c55e" : "#ef4444";
      const point: LineData<UTCTimestamp> = { time: t, value: close, color };
      const current = polyPointsRef.current;
      const idx = current.findIndex((p) => p.time === t);
      if (idx >= 0) {
        current[idx] = point;
      } else {
        polyPointsRef.current = trimPolyPoints(
          [...current, point].sort((a, b) => (a.time as number) - (b.time as number))
        );
      }
      line.update(point);
    },
    [trimPolyPoints]
  );

  const syncPolySeries = useCallback(
    (chart: IChartApi, enabled: boolean) => {
      if (!enabled) {
        if (polySeriesRef.current) {
          chart.removeSeries(polySeriesRef.current);
          polySeriesRef.current = null;
          polyMidLineRef.current = null;
        }
        polyPointsRef.current = [];
        return;
      }

      if (!polySeriesRef.current) {
        polySeriesRef.current = chart.addSeries(
          LineSeries,
          {
            color: POLY_UP_COLOR,
            lineWidth: 2,
            priceLineVisible: false,
            lastValueVisible: true,
            priceFormat: {
              type: "custom",
              formatter: (p: number) => formatPolyUpPrice(p),
            },
          },
          POLY_PANE_INDEX
        );
        polyMidLineRef.current = polySeriesRef.current.createPriceLine({
          price: 0.5,
          color: "#4a4a55",
          lineWidth: 1,
          lineStyle: LineStyle.Dotted,
          axisLabelVisible: false,
        });
        applyPolyPriceScale(polySeriesRef.current);
      } else if (polySeriesRef.current) {
        applyPolyPriceScale(polySeriesRef.current);
      }
      pushPolyToSeries();
      if (polySeriesRef.current) {
        applyPolyPriceScale(polySeriesRef.current);
      }
      const liqOn = indicatorsRef.current.some((i) => i.type === "liquidations");
      scheduleIndicatorPaneHeights(chart, true, liqOn);
    },
    [pushPolyToSeries]
  );

  const applyLiqBar = useCallback((time: number, long: number, short: number) => {
    liqDataRef.current.set(time, { long, short });
    const series = liqSeriesRef.current;
    if (!series) return;
    const threshold = getLiqThreshold(indicatorsRef.current);
    const hadData = series.data().length > 0;
    series.update(liqHistogramPoint(time, long, short, threshold));
    if (!hadData) {
      applyLiqPriceScaleAutoscale(series);
    }
  }, []);

  const syncLiqSeries = useCallback(
    (chart: IChartApi, enabled: boolean, withPoly: boolean) => {
      if (!enabled) {
        if (liqSeriesRef.current) {
          chart.removeSeries(liqSeriesRef.current);
          liqSeriesRef.current = null;
        }
        liqDataRef.current.clear();
        liqPaneWithPolyRef.current = null;
        return;
      }

      if (
        liqSeriesRef.current &&
        liqPaneWithPolyRef.current !== null &&
        liqPaneWithPolyRef.current !== withPoly
      ) {
        chart.removeSeries(liqSeriesRef.current);
        liqSeriesRef.current = null;
      }
      liqPaneWithPolyRef.current = withPoly;

      if (!liqSeriesRef.current) {
        const paneIdx = liqPaneIndex(withPoly);
        liqSeriesRef.current = chart.addSeries(
          HistogramSeries,
          {
            priceFormat: { type: "volume" },
            priceLineVisible: false,
            lastValueVisible: false,
          },
          paneIdx
        );
        applyLiqPriceScaleAutoscale(liqSeriesRef.current);
      }
      if (liqDataRef.current.size > 0) {
        pushLiqToSeries();
      }
      scheduleIndicatorPaneHeights(chart, withPoly, true);
    },
    [pushLiqToSeries]
  );

  const applyMaIndicator = useCallback(
    (
      chart: IChartApi,
      ind: ChartIndicator,
      ohlcv: OhlcvBar[],
      candles: CandlestickData<UTCTimestamp>[],
      activeKeys: Set<string>
    ) => {
      const prev = maSeriesRef.current;
      const color = indicatorLineColor(ind.type);

      if (isAnchoredVwapType(ind.type)) {
        const segments = anchoredVwapSegments(ohlcv, ind.period, interval);
        segments.forEach((data) => {
          if (data.length === 0) return;
          const bucket = sessionBucketOpen(data[0].time as number, interval, ind.period);
          const key = vwapSegmentKey(ind.id, bucket);
          activeKeys.add(key);
          let line = prev.get(key);
          if (!line) {
            line = chart.addSeries(LineSeries, {
              color,
              lineWidth: VWAP_LINE_WIDTH,
              priceLineVisible: false,
              lastValueVisible: false,
            });
            prev.set(key, line);
          } else {
            line.applyOptions({ color, lineWidth: VWAP_LINE_WIDTH });
          }
          line.setData(data);
        });
        return;
      }

      if (ind.type !== "ema" && ind.type !== "rolling_vwap") return;

      activeKeys.add(ind.id);
      let line = prev.get(ind.id);
      if (!line) {
        line = chart.addSeries(LineSeries, {
          color,
          lineWidth: 1,
          priceLineVisible: false,
          lastValueVisible: false,
        });
        prev.set(ind.id, line);
      } else {
        line.applyOptions({ color });
      }
      line.setData(lineDataForIndicator(ind, candles, ohlcv, interval));
    },
    [interval, isRealtimeOnlyInterval]
  );

  const syncMaSeries = useCallback(
    (chart: IChartApi, next: ChartIndicator[]) => {
      const prev = maSeriesRef.current;
      const activeKeys = new Set<string>();

      const ohlcv = ohlcvBarsRef.current;
      const candles = toCandles(ohlcv);

      next.forEach((ind) => {
        if (
          ind.type !== "ema" &&
          ind.type !== "vwap" &&
          ind.type !== "session_vwap" &&
          ind.type !== "rolling_vwap"
        ) {
          return;
        }
        applyMaIndicator(chart, ind, ohlcv, candles, activeKeys);
      });

      for (const [key, line] of prev) {
        if (activeKeys.has(key)) continue;
        chart.removeSeries(line);
        prev.delete(key);
      }
    },
    [applyMaIndicator]
  );

  const refreshMaSeries = useCallback(() => {
    const chart = chartRef.current;
    if (!chart) return;
    syncMaSeries(chart, indicatorsRef.current);
  }, [syncMaSeries]);

  const updateSessionVwapTail = useCallback(() => {
    if (!historyReadyRef.current) return;
    const chart = chartRef.current;
    if (!chart) return;

    const vwapInd = indicatorsRef.current.find(
      (i) => i.type === "vwap" || i.type === "session_vwap"
    );
    if (!vwapInd) return;

    const point = calculateSessionVwapTailPoint(
      ohlcvBarsRef.current,
      vwapInd.period,
      interval
    );
    if (!point || point.value === undefined) return;

    const bucket = sessionBucketOpen(point.time as number, interval, vwapInd.period);
    const key = vwapSegmentKey(vwapInd.id, bucket);
    const prev = maSeriesRef.current;
    let line = prev.get(key);

    if (!line) {
      syncMaSeries(
        chart,
        indicatorsRef.current.filter(
          (i) => i.type === "vwap" || i.type === "session_vwap"
        )
      );
      line = prev.get(key);
      if (!line) return;
    }

    line.update(point);
  }, [interval, syncMaSeries]);

  const applyBackendIndicator = useCallback(
    (msg: IndicatorMsg) => {
      if (!historyReadyRef.current) return;
      const chart = chartRef.current;
      if (!chart) return;

      const ind =
        msg.indicator === "ema"
          ? indicatorsRef.current.find((i) => i.type === "ema")
          : msg.indicator === "vwap"
            ? indicatorsRef.current.find(
                (i) => i.type === "vwap" || i.type === "session_vwap"
              )
            : indicatorsRef.current.find((i) => i.type === "rolling_vwap");
      if (!ind) return;
      if (msg.period !== ind.period) return;

      const time = msg.time as UTCTimestamp;
      const point =
        msg.value != null
          ? { time, value: parseFloat(msg.value) }
          : { time };

      const prev = maSeriesRef.current;
      const lineType =
        ind.type === "session_vwap" ? "vwap" : ind.type === "rolling_vwap" ? "rolling_vwap" : ind.type;
      const color = indicatorLineColor(
        lineType as "ema" | "vwap" | "rolling_vwap"
      );

      if (msg.indicator === "vwap") {
        const bucket = sessionBucketOpen(msg.time, interval, ind.period);
        const key = vwapSegmentKey(ind.id, bucket);
        let line = prev.get(key);
        if (!line) {
          line = chart.addSeries(LineSeries, {
            color,
            lineWidth: VWAP_LINE_WIDTH,
            priceLineVisible: false,
            lastValueVisible: false,
          });
          prev.set(key, line);
        }
        line.update(point);
        return;
      }

      let line = prev.get(ind.id);
      if (!line) {
        line = chart.addSeries(LineSeries, {
          color,
          lineWidth: 1,
          priceLineVisible: false,
          lastValueVisible: false,
        });
        prev.set(ind.id, line);
      }
      line.update(point);
    },
    [interval]
  );

  const setPriceSeriesData = useCallback((data: OhlcvBar[]) => {
    if (priceSeriesTypeRef.current === "line") {
      lineSeriesRef.current?.setData(toLineData(data));
    } else {
      candleSeriesRef.current?.setData(toCandles(data));
    }
  }, []);

  const updatePriceSeries = useCallback((bar: OhlcvBar) => {
    if (priceSeriesTypeRef.current === "line") {
      lineSeriesRef.current?.update({ time: bar.time, value: bar.close });
    } else {
      candleSeriesRef.current?.update({
        time: bar.time,
        open: bar.open,
        high: bar.high,
        low: bar.low,
        close: bar.close,
      });
    }
  }, []);

  const getPriceSeries = useCallback(() => {
    return candleSeriesRef.current ?? lineSeriesRef.current;
  }, []);

  const updateSessionBreakBoundaries = useCallback(() => {
    const primitive = sessionBreaksRef.current;
    if (!primitive) return;
    if (!indicatorsRef.current.some((i) => i.type === "session_breaks")) return;
    const bars = ohlcvBarsRef.current;
    const minutes = getSessionBreakMinutes(indicatorsRef.current);
    const { boundaries, next } = computeUtcIntervalBoundariesWithNext(
      bars,
      minutes * 60
    );
    primitive.setBoundaries(boundaries, next);
  }, []);

  /** Re-anchor the upcoming session line on forming-bar paints (setBoundaries → refresh). */
  const updateSessionBreakTail = useCallback(() => {
    updateSessionBreakBoundaries();
  }, [updateSessionBreakBoundaries]);

  const updateSessionHLines = useCallback(() => {
    const primitive = sessionHlinesRef.current;
    if (!primitive) return;
    if (!indicatorsRef.current.some((i) => i.type === "session_hlines")) return;
    const bars = ohlcvBarsRef.current;
    const minutes = getSessionHLineMinutes(indicatorsRef.current);
    primitive.setSegments(
      computeSessionHorizontalSegments(
        bars.map((b) => ({ time: b.time as number, open: b.open })),
        minutes
      )
    );
  }, []);

  const syncSessionBreaks = useCallback(() => {
    const series = getPriceSeries();
    const primitive = sessionBreaksRef.current;
    if (!series || !primitive) return;

    const enabled = indicatorsRef.current.some((i) => i.type === "session_breaks");
    if (enabled) {
      if (!sessionBreakAttachedRef.current) {
        series.attachPrimitive(primitive);
        sessionBreakAttachedRef.current = true;
      }
      updateSessionBreakBoundaries();
    } else if (sessionBreakAttachedRef.current) {
      series.detachPrimitive(primitive);
      sessionBreakAttachedRef.current = false;
    }
  }, [getPriceSeries, updateSessionBreakBoundaries]);

  const syncSessionHlines = useCallback(() => {
    const series = getPriceSeries();
    const primitive = sessionHlinesRef.current;
    if (!series || !primitive) return;

    const enabled = indicatorsRef.current.some((i) => i.type === "session_hlines");
    if (enabled) {
      if (!sessionHlinesAttachedRef.current) {
        series.attachPrimitive(primitive);
        sessionHlinesAttachedRef.current = true;
      }
      updateSessionHLines();
    } else if (sessionHlinesAttachedRef.current) {
      series.detachPrimitive(primitive);
      sessionHlinesAttachedRef.current = false;
    }
  }, [getPriceSeries, updateSessionHLines]);

  const enforceRealtimeWindow = useCallback((): boolean => {
    if (interval !== "1s" && interval !== "5s") return false;

    const maxBars = maxRealtimeBars(interval);
    const bars = ohlcvBarsRef.current;
    if (bars.length <= maxBars) return false;

    const trimmed = trimOhlcvBars(bars, maxBars);
    const minTime = trimmed[0]?.time as number;
    ohlcvBarsRef.current = trimmed;
    setPriceSeriesData(trimmed);

    for (const t of [...liqDataRef.current.keys()]) {
      if (t < minTime) liqDataRef.current.delete(t);
    }
    if (liqSeriesRef.current && liqDataRef.current.size > 0) {
      pushLiqToSeries();
    }

    for (const line of maSeriesRef.current.values()) {
      trimLineSeriesFromTime(line, minTime);
    }

    updateSessionBreakBoundaries();
    updateSessionHLines();
    return true;
  }, [
    interval,
    setPriceSeriesData,
    pushLiqToSeries,
    updateSessionBreakBoundaries,
    updateSessionHLines,
  ]);

  const setOhlcvData = useCallback(
    (data: OhlcvBar[]) => {
      ohlcvBarsRef.current = data;
      setPriceSeriesData(data);
      refreshMaSeries();
      updateSessionBreakBoundaries();
      updateSessionHLines();
    },
    [
      refreshMaSeries,
      setPriceSeriesData,
      updateSessionBreakBoundaries,
      updateSessionHLines,
    ]
  );

  const liveOpsRef = useRef({
    updatePriceSeries,
    refreshMaSeries,
    updateSessionVwapTail,
    updateSessionBreakTail,
    applyLiqBar,
    applyBackendIndicator,
    enforceRealtimeWindow,
    updateSessionBreakBoundaries,
    updateSessionHLines,
  });
  liveOpsRef.current = {
    updatePriceSeries,
    refreshMaSeries,
    updateSessionVwapTail,
    updateSessionBreakTail,
    applyLiqBar,
    applyBackendIndicator,
    enforceRealtimeWindow,
    updateSessionBreakBoundaries,
    updateSessionHLines,
  };

  const loadLiqForOhlcv = useCallback(async () => {
    if (!indicatorsRef.current.some((i) => i.type === "liquidations")) return;
    if (isRealtimeOnlyInterval) return;
    const bars = ohlcvBarsRef.current;
    if (bars.length === 0 || liqLoadingRef.current) return;

    const liqInterval = liqApiInterval(interval);
    const fromTime = bars[0].time as number;
    const toTime = bars[bars.length - 1].time as number;
    liqLoadingRef.current = true;
    try {
      const data = await fetchLiquidationsForRange(
        symbol,
        liqInterval,
        fromTime,
        toTime
      );
      for (const b of data) {
        liqDataRef.current.set(b.time, { long: b.long, short: b.short });
      }
      pushLiqToSeries();
    } catch (e) {
      console.error(e);
    } finally {
      liqLoadingRef.current = false;
    }
  }, [symbol, interval, isRealtimeOnlyInterval, pushLiqToSeries]);

  // Chart init + history + infinite scroll
  useEffect(() => {
    if (!containerRef.current) return;

    let cancelled = false;
    let historyScrollEnabled = false;
    const effectGen = ++chartEffectGenRef.current;
    const barLimit = openBarCountRef.current;

    ohlcvBarsRef.current = [];
    liveBarRef.current = null;
    loadingRef.current = false;
    liqLoadingRef.current = false;
    exhaustedRef.current = false;
    historyReadyRef.current = false;
    maSeriesRef.current.clear();
    liqSeriesRef.current = null;
    liqDataRef.current.clear();
    polySeriesRef.current = null;
    polyPointsRef.current = [];

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
        vertLines: { visible: false },
        horzLines: { visible: false },
      },
      crosshair: {
        vertLine: { color: "#444" },
        horzLine: { color: "#444" },
      },
      timeScale: {
        borderColor: "#2a2a35",
        timeVisible: true,
        secondsVisible: isRealtimeOnlyInterval,
      },
      rightPriceScale: { borderColor: "#2a2a35" },
      width: containerRef.current.clientWidth,
      height: containerRef.current.clientHeight,
    });

    if (chartStyle === "line") {
      const series = chart.addSeries(LineSeries, {
        color: "#facc15",
        lineWidth: 2,
        priceLineVisible: false,
      });
      lineSeriesRef.current = series;
      candleSeriesRef.current = null;
      priceSeriesTypeRef.current = "line";
    } else {
      const series = chart.addSeries(CandlestickSeries, {
        upColor: "#22c55e",
        downColor: "#ef4444",
        borderUpColor: "#22c55e",
        borderDownColor: "#ef4444",
        wickUpColor: "#22c55e",
        wickDownColor: "#ef4444",
      } as CandlestickSeriesOptions);
      candleSeriesRef.current = series;
      lineSeriesRef.current = null;
      priceSeriesTypeRef.current = "candlestick";
    }

    chartRef.current = chart;

    const sessionBreaks = new SessionBreaksPrimitive(
      CANDLESTICK_SESSION_BREAK_OPTIONS,
      CANDLESTICK_NEXT_SESSION_BREAK_OPTIONS
    );
    sessionBreaksRef.current = sessionBreaks;
    sessionBreakAttachedRef.current = false;

    const sessionHlines = new SessionHorizontalLinesPrimitive();
    sessionHlinesRef.current = sessionHlines;
    sessionHlinesAttachedRef.current = false;

    const tradeSignalsPrimitive = new TradeSignalMarkersPrimitive();
    tradeSignalsPrimitiveRef.current = tradeSignalsPrimitive;
    tradeSignalsAttachedRef.current = false;

    syncMaSeries(chart, indicatorsRef.current);
    const inds = indicatorsRef.current;
    const initPoly =
      inds.some((i) => i.type === "polymarket_up") &&
      !!binancePerpToPolySeries(symbol) &&
      isRealtimeOnlyInterval;
    syncPolySeries(chart, initPoly);
    syncLiqSeries(
      chart,
      inds.some((i) => i.type === "liquidations"),
      initPoly
    );
    scheduleIndicatorPaneHeights(
      chart,
      initPoly,
      inds.some((i) => i.type === "liquidations")
    );
    syncSessionBreaks();
    syncSessionHlines();
    syncTradeSignalsPrimitive();

    const fetchKlines = async (before?: number): Promise<Kline[]> => {
      const params = new URLSearchParams({
        symbol,
        interval,
        limit: String(before === undefined ? barLimit : PAGE_SIZE),
      });
      if (before !== undefined) params.set("before", String(before));
      const r = await fetch(`/klines?${params}`);
      if (!r.ok) throw new Error(`klines ${r.status}`);
      return r.json();
    };

    const loadOlder = async () => {
      if (loadingRef.current || exhaustedRef.current) return;
      const oldest = ohlcvBarsRef.current[0]?.time as number | undefined;
      if (oldest === undefined) return;

      loadingRef.current = true;
      try {
        const data = await fetchKlines(oldest);
        const older = toOhlcv(data).filter((b) => (b.time as number) < oldest);
        if (older.length === 0) {
          exhaustedRef.current = true;
          return;
        }
        setOhlcvData(mergeOhlcvBars(ohlcvBarsRef.current, older));
        void loadLiqForOhlcv();
      } catch (e) {
        console.error(e);
      } finally {
        loadingRef.current = false;
      }
    };

    const onVisibleRangeChange = (range: LogicalRange | null) => {
      sessionBreaksRef.current?.refresh();
      sessionHlinesRef.current?.refresh();
      if (!historyScrollEnabled || !range || range.from >= LOAD_THRESHOLD) return;
      void loadOlder();
    };

    chart.timeScale().subscribeVisibleLogicalRangeChange(onVisibleRangeChange);

    const ro = new ResizeObserver(() => {
      if (!containerRef.current) return;
      chart.applyOptions({
        width: containerRef.current.clientWidth,
        height: containerRef.current.clientHeight,
      });
      scheduleIndicatorPaneHeights(
        chart,
        initPoly,
        inds.some((i) => i.type === "liquidations")
      );
    });
    ro.observe(containerRef.current);

    const isStale = () =>
      cancelled || chartRef.current !== chart || effectGen !== chartEffectGenRef.current;

    if (isRealtimeOnlyInterval) {
      historyReadyRef.current = true;
      exhaustedRef.current = true;
      return () => {
        cancelled = true;
        chartEffectGenRef.current += 1;
        ro.disconnect();
        chart.timeScale().unsubscribeVisibleLogicalRangeChange(onVisibleRangeChange);
        chart.remove();
        chartRef.current = null;
        candleSeriesRef.current = null;
        lineSeriesRef.current = null;
        priceSeriesTypeRef.current = "candlestick";
        maSeriesRef.current.clear();
        liqSeriesRef.current = null;
        liqDataRef.current.clear();
        sessionBreaksRef.current = null;
        sessionBreakAttachedRef.current = false;
        sessionHlinesRef.current = null;
        sessionHlinesAttachedRef.current = false;
        tradeSignalsPrimitiveRef.current?.clearMarkers();
        tradeSignalsPrimitiveRef.current = null;
        tradeSignalsAttachedRef.current = false;
        ohlcvBarsRef.current = [];
        liveBarRef.current = null;
        historyReadyRef.current = false;
      };
    }

    fetchKlines()
      .then((data) => {
        if (isStale()) return;

        const bars = prepareHistoryBars(data, interval, liveBarRef.current);
        const bucket = currentBarBucket(interval);
        const last = bars[bars.length - 1];
        if (last && (last.time as number) === bucket) {
          liveBarRef.current = last;
        }
        setOhlcvData(bars);
        void loadLiqForOhlcv();
        historyReadyRef.current = true;

        requestAnimationFrame(() => {
          if (isStale()) return;

          applyWidgetBarViewport(chart, bars.length, barLimit);

          if (!isStale()) {
            historyScrollEnabled = true;
          }
        });
      })
      .catch((e) => {
        if (isStale()) return;
        console.error(e);
        historyReadyRef.current = true;
      });

    return () => {
      cancelled = true;
      chartEffectGenRef.current += 1;
      ro.disconnect();
      chart.timeScale().unsubscribeVisibleLogicalRangeChange(onVisibleRangeChange);
      chart.remove();
      chartRef.current = null;
      candleSeriesRef.current = null;
      lineSeriesRef.current = null;
      priceSeriesTypeRef.current = "candlestick";
      maSeriesRef.current.clear();
      liqSeriesRef.current = null;
      liqDataRef.current.clear();
      polySeriesRef.current = null;
      polyPointsRef.current = [];
      sessionBreaksRef.current = null;
      sessionBreakAttachedRef.current = false;
      sessionHlinesRef.current = null;
      sessionHlinesAttachedRef.current = false;
      tradeSignalsPrimitiveRef.current?.clearMarkers();
      tradeSignalsPrimitiveRef.current = null;
      tradeSignalsAttachedRef.current = false;
      ohlcvBarsRef.current = [];
      liveBarRef.current = null;
      historyReadyRef.current = false;
    };
  }, [
    symbol,
    interval,
    chartStyle,
    isRealtimeOnlyInterval,
    setOhlcvData,
    syncMaSeries,
    syncLiqSeries,
    syncPolySeries,
    syncSessionBreaks,
    syncSessionHlines,
    syncTradeSignalsPrimitive,
    loadLiqForOhlcv,
  ]);

  // Sync indicators when toggled from toolbar
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    syncMaSeries(chart, indicators);
    syncPolySeries(chart, polyPaneActive);
    syncLiqSeries(chart, hasLiquidations, polyPaneActive);
    scheduleIndicatorPaneHeights(chart, polyPaneActive, hasLiquidations);
    syncSessionBreaks();
    syncSessionHlines();
    syncTradeSignalsPrimitive();
  }, [
    indicators,
    hasLiquidations,
    hasTradeSignals,
    polyPaneActive,
    syncMaSeries,
    syncLiqSeries,
    syncPolySeries,
    syncSessionBreaks,
    syncSessionHlines,
    syncTradeSignalsPrimitive,
  ]);

  useEffect(() => {
    if (!hasTradeSignals) return;
    return subscribe("*", (msg) => {
      if (msg.type !== "paper_event") return;
      applyPaperTradeEvent(msg as PaperEventMsg);
    });
  }, [hasTradeSignals, subscribe, applyPaperTradeEvent]);

  useEffect(() => {
    if (!polyPaneActive) return;
    clearPolyData();
  }, [symbol, interval, polyPaneActive, clearPolyData]);

  useEffect(() => {
    if (!polyPaneActive || !polySeries) return;

    const pmSymbol = polySeriesToFeedSymbol(polySeries);
    const unsub = subscribe(pmSymbol, (msg) => {
      if (msg.type === "bar" && msg.interval === interval) {
        const t = msg.time ?? barOpenTime(Math.floor(msg.ts / 1e9), interval);
        applyPolyBar(t, parseFloat(msg.close));
      } else if (msg.type === "polymarket") {
        const pm = msg as PolymarketMsg;
        applyPolyBar(barOpenTime(Math.floor(pm.ts / 1e9), interval), pm.yes_price, pm.slug);
      }
    });
    return unsub;
  }, [polyPaneActive, polySeries, interval, subscribe, applyPolyBar]);

  useEffect(() => {
    if (hasLiquidations) pushLiqToSeries();
  }, [hasLiquidations, liqThreshold, pushLiqToSeries]);

  // Reload liq when indicator enabled and OHLCV already loaded
  useEffect(() => {
    if (!hasLiquidations || ohlcvBarsRef.current.length === 0) return;
    void loadLiqForOhlcv();
  }, [hasLiquidations, loadLiqForOhlcv]);

  // Live updates — stable subscription; handler reads latest ops via liveOpsRef.
  useEffect(() => {
    lastPaintAtRef.current = 0;

    const mergeBarIntoStore = (bar: OhlcvBar) => {
      const bars = ohlcvBarsRef.current;
      const idx = bars.findIndex((b) => b.time === bar.time);
      if (idx >= 0) bars[idx] = bar;
      else {
        bars.push(bar);
        bars.sort((a, b) => (a.time as number) - (b.time as number));
      }
    };

    const paintCandleSeries = (bar: OhlcvBar) => {
      if (!historyReadyRef.current) return;
      liveOpsRef.current.updatePriceSeries(bar);
    };

    const flushTradeCandle = (bar: OhlcvBar) => {
      paintCandleSeries(bar);
      liveOpsRef.current.updateSessionVwapTail();
      liveOpsRef.current.updateSessionBreakTail();
      lastPaintAtRef.current = Date.now();
    };

    const scheduleTradeCandlePaint = (bar: OhlcvBar) => {
      liveBarRef.current = bar;
      mergeBarIntoStore(bar);

      if (!historyReadyRef.current) return;

      const elapsed = Date.now() - lastPaintAtRef.current;
      if (elapsed >= CANDLE_FLUSH_MS) {
        if (paintTimerRef.current) {
          clearTimeout(paintTimerRef.current);
          paintTimerRef.current = null;
        }
        flushTradeCandle(bar);
        return;
      }

      if (paintTimerRef.current) return;
      paintTimerRef.current = setTimeout(() => {
        paintTimerRef.current = null;
        const pending = liveBarRef.current;
        if (pending) flushTradeCandle(pending);
      }, CANDLE_FLUSH_MS - elapsed);
    };

    const commitOfficialBar = (bar: OhlcvBar) => {
      if (paintTimerRef.current) {
        clearTimeout(paintTimerRef.current);
        paintTimerRef.current = null;
      }
      liveBarRef.current = bar;
      mergeBarIntoStore(bar);
      const ops = liveOpsRef.current;
      const windowTrimmed = isRealtimeOnlyInterval && ops.enforceRealtimeWindow();
      if (!windowTrimmed) paintCandleSeries(bar);
      if (!isRealtimeOnlyInterval) {
        ops.refreshMaSeries();
        if (indicatorsRef.current.some((i) => i.type === "session_breaks")) {
          ops.updateSessionBreakBoundaries();
        }
        if (indicatorsRef.current.some((i) => i.type === "session_hlines")) {
          ops.updateSessionHLines();
        }
      } else if (!windowTrimmed) {
        ops.updateSessionVwapTail();
        ops.updateSessionBreakTail();
      }
      lastPaintAtRef.current = Date.now();
    };

    const unsub = subscribe(symbol, (msg) => {
      const ops = liveOpsRef.current;

      if (msg.type === "liquidation") {
        const liq = msg as LiquidationMsg;
        const snap = liquidationBarForChart(liq.bars, interval);
        if (snap) {
          ops.applyLiqBar(snap.time, snap.long, snap.short);
        }
        return;
      }

      if (msg.type === "bar" && msg.interval === interval) {
        const t =
          msg.time ??
          barOpenTime(Math.floor(msg.ts / 1e9), interval);
        const bar: OhlcvBar = {
          time: t as UTCTimestamp,
          open: parseFloat(msg.open),
          high: parseFloat(msg.high),
          low: parseFloat(msg.low),
          close: parseFloat(msg.close),
          volume: parseFloat(msg.volume),
        };
        commitOfficialBar(bar);
        return;
      }

      if (msg.type === "indicator" && msg.interval === interval) {
        ops.applyBackendIndicator(msg as IndicatorMsg);
        return;
      }

      if (isRealtimeOnlyInterval) return;

      if (msg.type === "trade") {
        const price = parseFloat((msg as TradeMsg).price);
        const barTime = barOpenTime(Math.floor(msg.ts / 1e9), interval) as UTCTimestamp;
        const cur = liveBarRef.current;
        const prevVol =
          cur && (cur.time as number) === barTime
            ? cur.volume
            : ohlcvBarsRef.current.find((b) => b.time === barTime)?.volume ?? 0;

        const next: OhlcvBar =
          !cur || (cur.time as number) !== barTime
            ? {
                time: barTime,
                open: price,
                high: price,
                low: price,
                close: price,
                volume: prevVol,
              }
            : {
                time: barTime,
                open: cur.open,
                high: Math.max(cur.high, price),
                low: Math.min(cur.low, price),
                close: price,
                volume: cur.volume,
              };

        scheduleTradeCandlePaint(next);
      }
    });

    return () => {
      if (paintTimerRef.current) clearTimeout(paintTimerRef.current);
      paintTimerRef.current = null;
      unsub();
    };
  }, [symbol, interval, subscribe, isRealtimeOnlyInterval]);

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
        : preset.type === "polymarket_up"
          ? { id, type: "polymarket_up" }
          : preset.type === "trade_signals"
            ? { id, type: "trade_signals" }
          : preset.type === "session_breaks"
            ? {
                id,
                type: "session_breaks",
                periodMinutes: preset.periodMinutes,
              }
            : preset.type === "session_hlines"
              ? {
                  id,
                  type: "session_hlines",
                  periodMinutes: preset.periodMinutes,
                }
              : preset.type === "vwap"
              ? { id, type: "vwap", period: preset.period }
              : preset.type === "rolling_vwap"
                ? { id, type: "rolling_vwap", period: preset.period }
                : { id, type: "ema", period: preset.period };
    onConfigChange({ indicators: [...indicators, added] });
  };

  const setEmaPeriod = (value: number) => {
    const next = Math.max(1, Math.floor(value) || DEFAULT_EMA_PERIOD);
    onConfigChange({
      indicators: indicators.map((i) =>
        i.type === "ema" ? { ...i, period: next } : i
      ),
    });
  };

  const setVwapPeriod = (value: number) => {
    const next = Math.max(1, Math.floor(value) || DEFAULT_VWAP_PERIOD);
    onConfigChange({
      indicators: indicators.map((i) =>
        i.type === "vwap" ? { ...i, period: next } : i
      ),
    });
  };

  const setRollingVwapPeriod = (value: number) => {
    const next = Math.max(1, Math.floor(value) || DEFAULT_ROLLING_VWAP_PERIOD);
    onConfigChange({
      indicators: indicators.map((i) =>
        i.type === "rolling_vwap" ? { ...i, period: next } : i
      ),
    });
  };

  const setLiqThreshold = (value: number) => {
    const next = Math.max(0, value);
    onConfigChange({
      indicators: indicators.map((i) =>
        i.type === "liquidations" ? { ...i, threshold: next } : i
      ),
    });
  };

  const setSessionBreakMinutes = (value: number) => {
    const next = Math.max(1, Math.floor(value) || DEFAULT_SESSION_BREAK_MINUTES);
    onConfigChange({
      indicators: indicators.map((i) =>
        i.type === "session_breaks" ? { ...i, periodMinutes: next } : i
      ),
    });
  };

  const setSessionHlineMinutes = (value: number) => {
    const next = Math.max(1, Math.floor(value) || DEFAULT_SESSION_HLINE_MINUTES);
    onConfigChange({
      indicators: indicators.map((i) =>
        i.type === "session_hlines" ? { ...i, periodMinutes: next } : i
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
            className={`${styles.menuBtn} ${openMenu === "style" ? styles.menuBtnActive : ""}`}
            onClick={(e) => {
              e.stopPropagation();
              setOpenMenu((m) => (m === "style" ? null : "style"));
            }}
          >
            {chartStyle === "line" ? "Line" : "Candlestick"}
            <span className={styles.chevron}>▼</span>
          </button>
          {openMenu === "style" && (
            <div className={styles.menu}>
              <button
                type="button"
                className={`${styles.menuItem} ${chartStyle === "candlestick" ? styles.menuItemActive : ""}`}
                onClick={() => {
                  onConfigChange({ chartStyle: "candlestick" });
                  setOpenMenu(null);
                }}
              >
                Candlestick
                {chartStyle === "candlestick" && <span className={styles.check}>✓</span>}
              </button>
              <button
                type="button"
                className={`${styles.menuItem} ${chartStyle === "line" ? styles.menuItemActive : ""}`}
                onClick={() => {
                  onConfigChange({ chartStyle: "line" });
                  setOpenMenu(null);
                }}
              >
                Line
                {chartStyle === "line" && <span className={styles.check}>✓</span>}
              </button>
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
              {hasEma && (
                <div className={styles.thresholdRow}>
                  <span className={styles.thresholdLabel}>EMA period (bars)</span>
                  <input
                    type="number"
                    className={styles.thresholdInput}
                    min={1}
                    step={1}
                    value={emaDraft}
                    onClick={stopMenuClick}
                    onChange={(e) => setEmaDraft(e.target.value)}
                    onBlur={(e) => {
                      const n = parseInt(e.target.value, 10);
                      const next = Number.isFinite(n) && n >= 1 ? n : DEFAULT_EMA_PERIOD;
                      setEmaDraft(String(next));
                      setEmaPeriod(next);
                    }}
                  />
                </div>
              )}
              {hasVwap && (
                <div className={styles.thresholdRow}>
                  <span className={styles.thresholdLabel}>VWAP session (bars)</span>
                  <input
                    type="number"
                    className={styles.thresholdInput}
                    min={1}
                    step={1}
                    value={vwapDraft}
                    onClick={stopMenuClick}
                    onChange={(e) => setVwapDraft(e.target.value)}
                    onBlur={(e) => {
                      const n = parseInt(e.target.value, 10);
                      const next = Number.isFinite(n) && n >= 1 ? n : DEFAULT_VWAP_PERIOD;
                      setVwapDraft(String(next));
                      setVwapPeriod(next);
                    }}
                  />
                </div>
              )}
              {hasRollingVwap && (
                <div className={styles.thresholdRow}>
                  <span className={styles.thresholdLabel}>Rolling VWAP period (bars)</span>
                  <input
                    type="number"
                    className={styles.thresholdInput}
                    min={1}
                    step={1}
                    value={rollingVwapDraft}
                    onClick={stopMenuClick}
                    onChange={(e) => setRollingVwapDraft(e.target.value)}
                    onBlur={(e) => {
                      const n = parseInt(e.target.value, 10);
                      const next = Number.isFinite(n) && n >= 1 ? n : DEFAULT_ROLLING_VWAP_PERIOD;
                      setRollingVwapDraft(String(next));
                      setRollingVwapPeriod(next);
                    }}
                  />
                </div>
              )}
              {hasSessionBreaks && (
                <div className={styles.thresholdRow}>
                  <span className={styles.thresholdLabel}>Session (minutes)</span>
                  <input
                    type="number"
                    className={styles.thresholdInput}
                    min={1}
                    step={1}
                    value={sessionBreakDraft}
                    onClick={stopMenuClick}
                    onChange={(e) => setSessionBreakDraft(e.target.value)}
                    onBlur={(e) => {
                      const n = parseInt(e.target.value, 10);
                      const next =
                        Number.isFinite(n) && n >= 1
                          ? n
                          : DEFAULT_SESSION_BREAK_MINUTES;
                      setSessionBreakDraft(String(next));
                      setSessionBreakMinutes(next);
                    }}
                  />
                </div>
              )}
              {hasSessionHlines && (
                <div className={styles.thresholdRow}>
                  <span className={styles.thresholdLabel}>Session lines (minutes)</span>
                  <input
                    type="number"
                    className={styles.thresholdInput}
                    min={1}
                    step={1}
                    value={sessionHlineDraft}
                    onClick={stopMenuClick}
                    onChange={(e) => setSessionHlineDraft(e.target.value)}
                    onBlur={(e) => {
                      const n = parseInt(e.target.value, 10);
                      const next =
                        Number.isFinite(n) && n >= 1
                          ? n
                          : DEFAULT_SESSION_HLINE_MINUTES;
                      setSessionHlineDraft(String(next));
                      setSessionHlineMinutes(next);
                    }}
                  />
                </div>
              )}
              {hasLiquidations && (
                <div className={styles.thresholdRow}>
                  <span className={styles.thresholdLabel}>Liq threshold ($)</span>
                  <input
                    type="number"
                    className={styles.thresholdInput}
                    min={0}
                    step={1000}
                    value={liqDraft}
                    onClick={stopMenuClick}
                    onChange={(e) => setLiqDraft(e.target.value)}
                    onBlur={(e) => {
                      const n = parseFloat(e.target.value);
                      const next = Number.isFinite(n) && n >= 0 ? n : 0;
                      setLiqDraft(String(next));
                      setLiqThreshold(next);
                    }}
                  />
                </div>
              )}
            </div>
          )}
        </div>

        {hasPolymarketUp && !polySeries && (
          <span className={styles.menuBtn} style={{ cursor: "default", opacity: 0.65 }}>
            No Polymarket 15m for this symbol
          </span>
        )}
        {hasPolymarketUp && polySeries && !isRealtimeOnlyInterval && (
          <span className={styles.menuBtn} style={{ cursor: "default", opacity: 0.65 }}>
            Polymarket UP: 1s/5s only
          </span>
        )}
      </div>
      <div ref={containerRef} className={styles.chart} />
    </div>
  );
}