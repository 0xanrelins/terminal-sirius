import { useCallback, useEffect, useRef, useState } from "react";
import {
  createChart,
  LineSeries,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
} from "lightweight-charts";
import {
  assignActiveColorIndices,
  chartTimeToMinuteLabel,
  formatPostEventNotional,
  normalizePostEventCoins,
  normalizePostEventSides,
  pointsToLineData,
  POST_EVENT_COINS,
  POST_EVENT_INTERVAL,
  sessionLineColor,
  sessionsFetchUrl,
  SYNTHETIC_BASE_EPOCH,
  WINDOW_SEC,
  type PostEventChartInterval,
  type PostEventCoin,
  type PostEventSession,
  type PostEventSessionsResponse,
  type PostEventSide,
} from "../../lib/liqPostEventChart";
import { DEFAULT_MIN_NOTIONAL } from "./LiquidationSignals";
import styles from "./LiqPostEventChart.module.css";

const SIDES: PostEventSide[] = ["LONG", "SHORT"];
const RENDER_BATCH_SIZE = 8;

const COIN_COLORS: Record<PostEventCoin, string> = {
  BTC: "#f7931a",
  ETH: "#627eea",
  SOL: "#14f195",
  XRP: "#38bdf8",
  DOGE: "#c2a633",
};

type Props = {
  coins?: PostEventCoin[];
  sides?: PostEventSide[];
  minNotional?: number;
  onConfigChange: (patch: {
    coins?: PostEventCoin[];
    sides?: PostEventSide[];
    minNotional?: number;
  }) => void;
};

export function LiqPostEventChart({
  coins: coinsProp,
  sides: sidesProp,
  minNotional = DEFAULT_MIN_NOTIONAL,
  onConfigChange,
}: Props) {
  const coins = normalizePostEventCoins(coinsProp);
  const sides = normalizePostEventSides(sidesProp);
  const coinSet = new Set(coins);
  const sideSet = new Set(sides);

  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const baselineSeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  const seriesRef = useRef<Map<string, ISeriesApi<"Line">>>(new Map());
  const colorIndexRef = useRef<Map<string, number>>(new Map());
  const fetchGenRef = useRef(0);
  const renderGenRef = useRef(0);
  const chartReadyRef = useRef(false);
  const pendingSessionsRef = useRef<PostEventSession[] | null>(null);
  const applySessionsRef = useRef<(sessions: PostEventSession[]) => void>(() => {});

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [sessionCount, setSessionCount] = useState(0);
  const [draftThreshold, setDraftThreshold] = useState(String(minNotional));

  useEffect(() => {
    setDraftThreshold(String(minNotional));
  }, [minNotional]);

  const applyVisibleRange = useCallback((chart: IChartApi) => {
    chart.timeScale().setVisibleRange({
      from: SYNTHETIC_BASE_EPOCH as UTCTimestamp,
      to: (SYNTHETIC_BASE_EPOCH + WINDOW_SEC) as UTCTimestamp,
    });
  }, []);

  const upsertSessionSeries = useCallback(
    (
      chart: IChartApi,
      session: PostEventSession,
      colorIdx: number
    ): number => {
      let series = seriesRef.current.get(session.session_id);
      const idx =
        session.status === "active"
          ? colorIndexRef.current.get(session.session_id) ?? colorIdx
          : colorIdx;

      if (!series) {
        series = chart.addSeries(LineSeries, {
          color: sessionLineColor(session, idx),
          lineWidth: session.status === "active" ? 2 : 1,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: true,
          title: `${session.symbol} ${session.side}`,
        });
        seriesRef.current.set(session.session_id, series);
      }

      const lineIdx =
        session.status === "active"
          ? colorIndexRef.current.get(session.session_id) ?? 0
          : 0;
      series.applyOptions({
        color: sessionLineColor(session, lineIdx),
        lineWidth: session.status === "active" ? 2 : 1,
      });
      const lineData = pointsToLineData(session.points);
      if (lineData.length > 0) {
        series.setData(lineData);
      }
      return session.status === "active" ? colorIdx + 1 : colorIdx;
    },
    []
  );

  const scheduleApplySessions = useCallback(
    (sessions: PostEventSession[]) => {
      const chart = chartRef.current;
      if (!chart || !chartReadyRef.current) {
        pendingSessionsRef.current = sessions;
        return;
      }

      const gen = ++renderGenRef.current;

      try {
        colorIndexRef.current = assignActiveColorIndices(
          sessions,
          colorIndexRef.current
        );

        const seen = new Set<string>();
        let batchIdx = 0;
        let colorIdx = 0;

        const finish = () => {
          if (gen !== renderGenRef.current) return;
          for (const [id, series] of seriesRef.current) {
            if (!seen.has(id)) {
              chart.removeSeries(series);
              seriesRef.current.delete(id);
              colorIndexRef.current.delete(id);
            }
          }
          applyVisibleRange(chart);
          baselineSeriesRef.current?.setSeriesOrder(0);
          setSessionCount(sessions.length);
          pendingSessionsRef.current = null;
        };

        const processBatch = () => {
          if (gen !== renderGenRef.current) return;
          const end = Math.min(batchIdx + RENDER_BATCH_SIZE, sessions.length);
          for (; batchIdx < end; batchIdx += 1) {
            const session = sessions[batchIdx];
            seen.add(session.session_id);
            colorIdx = upsertSessionSeries(chart, session, colorIdx);
          }
          if (batchIdx < sessions.length) {
            requestAnimationFrame(processBatch);
          } else {
            finish();
          }
        };

        if (sessions.length === 0) {
          finish();
        } else {
          requestAnimationFrame(processBatch);
        }
      } catch (e) {
        console.error("LiqPostEventChart applySessions failed:", e);
        setError(e instanceof Error ? e.message : "Chart render failed");
      }
    },
    [applyVisibleRange, upsertSessionSeries]
  );

  applySessionsRef.current = scheduleApplySessions;

  const fetchSessions = useCallback(async (): Promise<PostEventSession[]> => {
    const url = sessionsFetchUrl({
      coins,
      minNotional,
      sides,
    });
    const r = await fetch(url);
    if (!r.ok) throw new Error(`liq-post-event ${r.status}`);
    const data = (await r.json()) as PostEventSessionsResponse;
    return data.sessions ?? [];
  }, [coins, minNotional, sides]);

  const loadSessions = useCallback(async () => {
    const gen = ++fetchGenRef.current;
    setLoading(true);
    setError(null);
    try {
      const sessions = await fetchSessions();
      if (gen !== fetchGenRef.current) return sessions;
      applySessionsRef.current(sessions);
      return sessions;
    } catch (e) {
      if (gen === fetchGenRef.current) {
        setError(e instanceof Error ? e.message : "Load failed");
      }
      return [];
    } finally {
      if (gen === fetchGenRef.current) setLoading(false);
    }
  }, [fetchSessions]);

  useEffect(() => {
    let cancelled = false;
    let ro: ResizeObserver | null = null;
    let rafId = 0;

    const mountChart = () => {
      const el = containerRef.current;
      if (cancelled || !el) return;
      if (el.clientWidth <= 0 || el.clientHeight <= 0) {
        rafId = requestAnimationFrame(mountChart);
        return;
      }

      chartReadyRef.current = false;
      seriesRef.current.clear();

      const chart = createChart(el, {
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
          timeFormatter: (t: UTCTimestamp) =>
            chartTimeToMinuteLabel(t as number),
        },
        timeScale: {
          borderColor: "#2a2a35",
          timeVisible: true,
          secondsVisible: false,
          fixLeftEdge: true,
          fixRightEdge: true,
        },
        rightPriceScale: { borderColor: "#2a2a35" },
        handleScroll: false,
        handleScale: false,
        width: el.clientWidth,
        height: el.clientHeight,
      });

      const baselineSeries = chart.addSeries(LineSeries, {
        color: "#facc15",
        lineWidth: 3,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      });
      baselineSeries.setData([
        { time: SYNTHETIC_BASE_EPOCH as UTCTimestamp, value: 0 },
        {
          time: (SYNTHETIC_BASE_EPOCH + WINDOW_SEC) as UTCTimestamp,
          value: 0,
        },
      ]);
      baselineSeriesRef.current = baselineSeries;

      chartRef.current = chart;
      chartReadyRef.current = true;

      if (pendingSessionsRef.current) {
        applySessionsRef.current(pendingSessionsRef.current);
      }

      ro = new ResizeObserver(() => {
        if (!containerRef.current || !chartRef.current) return;
        chartRef.current.applyOptions({
          width: containerRef.current.clientWidth,
          height: containerRef.current.clientHeight,
        });
      });
      ro.observe(el);
    };

    rafId = requestAnimationFrame(mountChart);

    return () => {
      cancelled = true;
      cancelAnimationFrame(rafId);
      renderGenRef.current += 1;
      ro?.disconnect();
      chartReadyRef.current = false;
      chartRef.current?.remove();
      chartRef.current = null;
      baselineSeriesRef.current = null;
      seriesRef.current.clear();
    };
  }, []);

  useEffect(() => {
    void loadSessions();
    return () => {
      fetchGenRef.current += 1;
    };
  }, [loadSessions]);

  const toggleCoin = (coin: PostEventCoin) => {
    const next = coinSet.has(coin)
      ? coins.filter((c) => c !== coin)
      : [...coins, coin];
    if (next.length === 0) return;
    onConfigChange({ coins: next });
  };

  const toggleSide = (side: PostEventSide) => {
    const next = sideSet.has(side)
      ? sides.filter((s) => s !== side)
      : [...sides, side];
    if (next.length === 0) return;
    onConfigChange({ sides: next });
  };

  const commitThreshold = () => {
    const next = Math.max(0, Number(draftThreshold.replace(/,/g, "")) || 0);
    setDraftThreshold(String(next));
    onConfigChange({ minNotional: next });
  };

  return (
    <div className={styles.wrapper}>
      <div className={styles.toolbar}>
        <span className={styles.title}>Liq Post-Event</span>
        <span className={styles.intervalBadge}>{POST_EVENT_INTERVAL}</span>

        {POST_EVENT_COINS.map((coin) => {
          const on = coinSet.has(coin);
          const accent = COIN_COLORS[coin];
          return (
            <button
              key={coin}
              type="button"
              className={`${styles.chip} ${on ? styles.chipOn : ""}`}
              style={
                on
                  ? {
                      borderColor: accent,
                      color: accent,
                      background: `${accent}18`,
                    }
                  : undefined
              }
              onClick={() => toggleCoin(coin)}
              aria-pressed={on}
            >
              {coin}
            </button>
          );
        })}

        {SIDES.map((side) => {
          const on = sideSet.has(side);
          const accent = side === "LONG" ? "#ef4444" : "#22c55e";
          return (
            <button
              key={side}
              type="button"
              className={`${styles.chip} ${on ? styles.chipOn : ""}`}
              style={
                on
                  ? {
                      borderColor: accent,
                      color: accent,
                      background: `${accent}18`,
                    }
                  : undefined
              }
              onClick={() => toggleSide(side)}
              aria-pressed={on}
            >
              {side}
            </button>
          );
        })}

        <label className={styles.threshold}>
          <span>Min $</span>
          <input
            className={styles.thresholdInput}
            value={draftThreshold}
            onChange={(e) => setDraftThreshold(e.target.value)}
            onBlur={commitThreshold}
            onKeyDown={(e) => {
              if (e.key === "Enter") e.currentTarget.blur();
            }}
            inputMode="numeric"
            spellCheck={false}
          />
        </label>

        <button
          type="button"
          className={styles.refreshBtn}
          onClick={() => void loadSessions()}
          disabled={loading}
        >
          Refresh
        </button>

        <span className={styles.meta}>
          {loading ? "…" : `${sessionCount} lines`}
        </span>
      </div>

      <div className={styles.chartWrap}>
        <div className={styles.chart} ref={containerRef} />
      </div>

      {error && !loading && sessionCount === 0 && (
        <div className={styles.emptyOverlay}>{error}</div>
      )}
      {!loading && !error && sessionCount === 0 && (
        <div className={styles.emptyOverlay}>
          No sessions ≥ {formatPostEventNotional(minNotional)} for selected filters
        </div>
      )}
    </div>
  );
}

export {
  normalizePostEventCoins,
  normalizePostEventSides,
  POST_EVENT_COINS,
  type PostEventCoin,
  type PostEventSide,
  type PostEventChartInterval,
};
