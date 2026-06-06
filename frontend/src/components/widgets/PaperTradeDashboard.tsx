import { useEffect, useMemo, useRef, useState } from "react";
import {
  createChart,
  LineSeries,
  type IChartApi,
  type ISeriesApi,
  type LineData,
  type UTCTimestamp,
} from "lightweight-charts";
import {
  Group as PanelGroup,
  Panel,
  Separator as PanelResizeHandle,
  useDefaultLayout,
} from "react-resizable-panels";
import { useFeed } from "../../context/FeedContext";
import type {
  PaperEquityPoint,
  PaperEventMsg,
  PaperMarketFields,
  PaperSnapshotMsg,
} from "../../types";
import styles from "./PaperTradeDashboard.module.css";

type Props = {
  curveMetric?: "equity" | "total_pnl";
  onConfigChange: (patch: { curveMetric?: "equity" | "total_pnl" }) => void;
};

type FeedEvent = PaperEventMsg & { _id: string };

const MAX_FEED_ROWS = 200;
const MAX_CURVE_POINTS = 20_000;
const STALE_MS = 8000;
const PANEL_STORAGE_KEY = "paper-trade-dashboard-panels-v1";

// ── formatting helpers ────────────────────────────────────────────────────────

function fmtMoney(v: number | null | undefined, ccy?: string | null): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const sign = v < 0 ? "-" : "";
  const abs = Math.abs(v);
  const body = abs.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  return `${sign}${body}${ccy ? ` ${ccy}` : ""}`;
}

function fmtSignedMoney(v: number | null | undefined, ccy?: string | null): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const s = fmtMoney(v, ccy);
  return v > 0 ? `+${s}` : s;
}

function fmtNum(v: number | null | undefined, digits = 4): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return v.toLocaleString(undefined, { maximumFractionDigits: digits });
}

function fmtPct(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return `${(v * 100).toFixed(1)}%`;
}

function fmtDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "—";
  const s = Math.floor(seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${sec}s`;
  return `${sec}s`;
}

function fmtClock(tsNs: number): string {
  const d = new Date(tsNs / 1e6);
  return d.toLocaleTimeString(undefined, { hour12: false });
}

/** Lookup analyzer stat by key prefix (names carry suffixes like "(252 days)"). */
function statByPrefix(
  stats: Record<string, number | string> | undefined,
  prefix: string
): number | null {
  if (!stats) return null;
  for (const [k, v] of Object.entries(stats)) {
    if (k.toLowerCase().startsWith(prefix.toLowerCase())) {
      const n = typeof v === "number" ? v : parseFloat(v);
      return Number.isNaN(n) ? null : n;
    }
  }
  return null;
}

function pnlClass(v: number | null | undefined): string {
  if (v === null || v === undefined || v === 0) return "";
  return v > 0 ? styles.pos : styles.neg;
}

function settlementLabel(outcome: string | null | undefined): string {
  if (outcome === "won") return "WON";
  if (outcome === "lost") return "LOST";
  if (outcome === "push") return "PUSH";
  return "—";
}

function settlementClass(outcome: string | null | undefined): string {
  if (outcome === "won") return styles.pos;
  if (outcome === "lost") return styles.neg;
  return "";
}

const KIND_LABEL: Record<string, string> = {
  fill: "FILL",
  position_open: "OPEN",
  position_close: "CLOSE",
  position_change: "CHG",
  order_rejected: "REJECT",
  order_denied: "DENIED",
};

function shortInstrument(iid: string): string {
  // Polymarket ids are long token hashes — trim for display.
  const dot = iid.lastIndexOf(".");
  const base = dot > 0 ? iid.slice(0, dot) : iid;
  if (base.length > 14) return `${base.slice(0, 6)}…${base.slice(-4)}`;
  return base;
}

function marketTitle(row: PaperMarketFields & { instrument_id: string }): string {
  if (row.market_label && row.market_label !== "—") return row.market_label;
  return shortInstrument(row.instrument_id);
}

function marketTooltip(row: PaperMarketFields & { instrument_id: string }): string {
  const parts: string[] = [];
  if (row.market_question) parts.push(row.market_question);
  else if (row.market_window) parts.push(row.market_window);
  if (row.market_slug) parts.push(`slug: ${row.market_slug}`);
  if (row.underlying) parts.push(`underlying: ${row.underlying}`);
  parts.push(row.instrument_id);
  return parts.join("\n");
}

export function PaperTradeDashboard({ curveMetric = "equity", onConfigChange }: Props) {
  const { subscribe, status } = useFeed();
  const [snapshot, setSnapshot] = useState<PaperSnapshotMsg | null>(null);
  const [events, setEvents] = useState<FeedEvent[]>([]);
  const [lastSnapshotMs, setLastSnapshotMs] = useState<number>(0);
  const [now, setNow] = useState<number>(Date.now());

  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  const curveRef = useRef<Map<number, number>>(new Map());
  const metricRef = useRef(curveMetric);
  metricRef.current = curveMetric;

  // ── chart init ──────────────────────────────────────────────────────────
  useEffect(() => {
    if (!containerRef.current) return;
    const chart = createChart(containerRef.current, {
      layout: {
        background: { color: "#131318" },
        textColor: "#888",
        attributionLogo: false,
      },
      grid: {
        vertLines: { visible: false },
        horzLines: { color: "#1d1d24" },
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
      rightPriceScale: { borderColor: "#2a2a35" },
      width: containerRef.current.clientWidth,
      height: containerRef.current.clientHeight,
    });
    const series = chart.addSeries(LineSeries, {
      color: "#4ea1ff",
      lineWidth: 2,
      priceLineVisible: true,
      lastValueVisible: true,
    });
    chartRef.current = chart;
    seriesRef.current = series;

    const ro = new ResizeObserver(() => {
      if (!containerRef.current) return;
      chart.applyOptions({
        width: containerRef.current.clientWidth,
        height: containerRef.current.clientHeight,
      });
    });
    ro.observe(containerRef.current);

    return () => {
      ro.disconnect();
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
    };
  }, []);

  const redrawCurve = () => {
    const series = seriesRef.current;
    if (!series) return;
    const data: LineData<UTCTimestamp>[] = Array.from(curveRef.current.entries())
      .sort((a, b) => a[0] - b[0])
      .map(([t, v]) => ({ time: t as UTCTimestamp, value: v }));
    series.setData(data);
  };

  const pushCurvePoint = (tsNs: number, value: number | null | undefined) => {
    if (value === null || value === undefined || Number.isNaN(value)) return;
    const t = Math.floor(tsNs / 1e9);
    if (t <= 0) return;
    const map = curveRef.current;
    const existed = map.has(t);
    map.set(t, value);
    if (map.size > MAX_CURVE_POINTS) {
      const oldest = Math.min(...map.keys());
      map.delete(oldest);
    }
    const series = seriesRef.current;
    if (!series) return;
    if (existed) {
      redrawCurve();
    } else {
      series.update({ time: t as UTCTimestamp, value });
    }
  };

  // ── seed from REST ──────────────────────────────────────────────────────
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [eqRes, evRes] = await Promise.all([
          fetch("/paper/equity?limit=5000"),
          fetch("/paper/events?limit=200"),
        ]);
        if (cancelled) return;
        if (eqRes.ok) {
          const pts: PaperEquityPoint[] = await eqRes.json();
          const map = curveRef.current;
          for (const p of pts) {
            const value = metricRef.current === "total_pnl" ? p.total_pnl : p.equity;
            if (value === null || value === undefined) continue;
            const t = Math.floor(p.ts / 1e9);
            if (t > 0) map.set(t, value);
          }
          redrawCurve();
        }
        if (evRes.ok) {
          const evs: PaperEventMsg[] = await evRes.json();
          setEvents(
            evs.map((e, i) => ({ ...e, _id: `seed-${e.ts}-${i}` })).slice(0, MAX_FEED_ROWS)
          );
        }
      } catch {
        // best-effort seed; live WS will fill in
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // ── live WS (account-level messages carry no symbol → subscribe "*") ──────
  useEffect(() => {
    return subscribe("*", (msg) => {
      if (msg.type === "paper_snapshot") {
        const snap = msg as PaperSnapshotMsg;
        setSnapshot(snap);
        setLastSnapshotMs(Date.now());
        const value =
          metricRef.current === "total_pnl"
            ? snap.pnl?.total
            : snap.account?.equity;
        pushCurvePoint(snap.ts, value);
      } else if (msg.type === "paper_event") {
        const ev = msg as PaperEventMsg;
        setEvents((prev) =>
          [{ ...ev, _id: `${ev.ts}-${Math.random().toString(36).slice(2, 7)}` }, ...prev].slice(
            0,
            MAX_FEED_ROWS
          )
        );
      }
    });
  }, [subscribe]);

  // ── re-plot when metric toggles ──────────────────────────────────────────
  useEffect(() => {
    redrawCurve();
  }, [curveMetric]);

  // ── clock tick for uptime + staleness ────────────────────────────────────
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);

  const account = snapshot?.account ?? null;
  const ccy = account?.currency ?? snapshot?.pnl?.currency ?? null;
  const stats = snapshot?.stats;

  const health = useMemo(() => {
    if (status !== "open") return { label: "DISCONNECTED", cls: styles.dotRed };
    if (!snapshot) return { label: "WAITING", cls: styles.dotAmber };
    if (lastSnapshotMs && now - lastSnapshotMs > STALE_MS)
      return { label: "STALE", cls: styles.dotAmber };
    return { label: "LIVE", cls: styles.dotGreen };
  }, [status, snapshot, lastSnapshotMs, now]);

  // current drawdown % from peak of the in-memory curve
  const drawdown = useMemo(() => {
    const vals = Array.from(curveRef.current.entries())
      .sort((a, b) => a[0] - b[0])
      .map((e) => e[1]);
    if (vals.length === 0) return null;
    let peak = vals[0];
    let last = vals[0];
    for (const v of vals) {
      if (v > peak) peak = v;
      last = v;
    }
    if (peak <= 0) return null;
    return (last - peak) / peak; // <= 0
  }, [snapshot]);

  const winRate = statByPrefix(stats, "Win Rate");
  const profitFactor = statByPrefix(stats, "Profit Factor");
  const sharpe = statByPrefix(stats, "Sharpe Ratio");
  const sortino = statByPrefix(stats, "Sortino Ratio");
  const expectancy = statByPrefix(stats, "Expectancy");

  const positions = snapshot?.positions ?? [];
  const closedPositions = snapshot?.closed_positions ?? [];
  const counts = snapshot?.counts;
  const layoutStorage = typeof window !== "undefined" ? window.localStorage : undefined;
  const { defaultLayout, onLayoutChanged } = useDefaultLayout({
    id: PANEL_STORAGE_KEY,
    panelIds: ["paper-top", "paper-open", "paper-closed", "paper-activity"],
    storage: layoutStorage,
  });

  return (
    <div className={styles.root}>
      {/* Header */}
      <div className={styles.header}>
        <div className={styles.titleWrap}>
          <span className={styles.title}>PAPER TRADE</span>
          <span className={styles.statusPill}>
            <span className={`${styles.dot} ${health.cls}`} />
            {health.label}
          </span>
        </div>
        <div className={styles.headerMeta}>
          {snapshot?.run?.paper === false && <span className={styles.liveTag}>LIVE EXEC</span>}
          <span>{snapshot?.run?.venue ?? "—"}</span>
          <span className={styles.dim}>·</span>
          <span title={snapshot?.run?.trader_id}>
            {snapshot?.run?.trader_id ?? "—"}
          </span>
          <span className={styles.dim}>·</span>
          <span>up {snapshot ? fmtDuration(snapshot.run.uptime_s + (now - lastSnapshotMs) / 1000) : "—"}</span>
        </div>
      </div>

      <div className={styles.panelsRoot}>
        <PanelGroup
          id={PANEL_STORAGE_KEY}
          orientation="vertical"
          defaultLayout={defaultLayout}
          onLayoutChanged={onLayoutChanged}
        >
          <Panel id="paper-top" defaultSize={34} minSize={20} className={styles.panelWithMin}>
            <div className={styles.topPanelContent}>
              {/* KPI row */}
              <div className={styles.kpis}>
                <Kpi label="Equity" value={fmtMoney(account?.equity, ccy)} />
                <Kpi
                  label="Total PnL"
                  value={fmtSignedMoney(snapshot?.pnl?.total, ccy)}
                  cls={pnlClass(snapshot?.pnl?.total)}
                />
                <Kpi
                  label="Unrealized"
                  value={fmtSignedMoney(snapshot?.pnl?.unrealized, ccy)}
                  cls={pnlClass(snapshot?.pnl?.unrealized)}
                />
                <Kpi
                  label="Realized"
                  value={fmtSignedMoney(snapshot?.pnl?.realized, ccy)}
                  cls={pnlClass(snapshot?.pnl?.realized)}
                />
                <Kpi label="Win Rate" value={fmtPct(winRate)} />
                <Kpi label="Profit Factor" value={fmtNum(profitFactor, 2)} />
                <Kpi label="Sharpe" value={fmtNum(sharpe, 2)} />
                <Kpi
                  label="Max DD"
                  value={drawdown === null ? "—" : fmtPct(drawdown)}
                  cls={drawdown && drawdown < 0 ? styles.neg : ""}
                />
                <Kpi label="Expectancy" value={fmtNum(expectancy, 2)} />
                <Kpi label="Sortino" value={fmtNum(sortino, 2)} />
                <Kpi label="Exposure" value={fmtMoney(snapshot?.exposure?.net, ccy)} />
                <Kpi
                  label="Trades"
                  value={counts ? String(counts.closed_trades) : "—"}
                  sub={counts ? `${counts.fills ?? 0} fills` : undefined}
                />
              </div>

              {/* Equity curve */}
              <div className={styles.chartSection}>
                <div className={`${styles.sectionBar} ${styles.curveToolbar} chartToolbar`}>
                  <span className={styles.sectionTitle}>
                    {curveMetric === "total_pnl" ? "PnL Curve" : "Equity Curve"}
                  </span>
                  <div className={styles.toggle}>
                    {(["equity", "total_pnl"] as const).map((m) => (
                      <button
                        key={m}
                        type="button"
                        className={`${styles.toggleBtn} ${curveMetric === m ? styles.toggleActive : ""}`}
                        onClick={() => onConfigChange({ curveMetric: m })}
                      >
                        {m === "equity" ? "Equity" : "PnL"}
                      </button>
                    ))}
                  </div>
                </div>
                <div ref={containerRef} className={styles.chart} />
              </div>
            </div>
          </Panel>
          <PanelResizeHandle className={styles.resizeHandle} />
          <Panel id="paper-open" defaultSize={22} minSize={12} className={styles.panelWithMin}>
            <div className={styles.tablePanel}>
              <div className={styles.sectionBar}>
                <span className={styles.sectionTitle}>Open Positions</span>
                <span className={styles.count}>{positions.length}</span>
              </div>
              <div className={styles.tableScroll}>
                <table className={styles.table}>
                  <colgroup>
                    <col className={styles.colMarketOpen} />
                    <col className={styles.colSideOpen} />
                    <col className={styles.colQtyOpen} />
                    <col className={styles.colAvgOpen} />
                    <col className={styles.colUpnlOpen} />
                    <col className={styles.colAgeOpen} />
                  </colgroup>
                  <thead>
                    <tr>
                      <th>Market</th>
                      <th>Side</th>
                      <th className={styles.right}>Qty</th>
                      <th className={styles.right}>Avg</th>
                      <th className={styles.right}>uPnL</th>
                      <th className={styles.right}>Age</th>
                    </tr>
                  </thead>
                  <tbody>
                    {positions.length === 0 && (
                      <tr>
                        <td className={styles.emptyCell} colSpan={6}>
                          No open positions
                        </td>
                      </tr>
                    )}
                    {positions.map((p) => (
                      <tr key={p.instrument_id}>
                        <td className={styles.marketCell} title={marketTooltip(p)}>
                          {marketTitle(p)}
                        </td>
                        <td>
                          <span className={p.side === "LONG" ? styles.pos : styles.neg}>
                            {p.side}
                          </span>
                        </td>
                        <td className={styles.right}>{fmtNum(p.quantity, 2)}</td>
                        <td className={styles.right}>{fmtNum(p.avg_px_open, 4)}</td>
                        <td className={`${styles.right} ${pnlClass(p.unrealized_pnl)}`}>
                          {fmtSignedMoney(p.unrealized_pnl)}
                        </td>
                        <td className={styles.right}>{fmtDuration(p.duration_s)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </Panel>
          <PanelResizeHandle className={styles.resizeHandle} />
          <Panel id="paper-closed" defaultSize={22} minSize={12} className={styles.panelWithMin}>
            <div className={styles.tablePanel}>
              <div className={styles.sectionBar}>
                <span className={styles.sectionTitle}>Closed Positions</span>
                <span className={styles.count}>{closedPositions.length}</span>
              </div>
              <div className={styles.tableScroll}>
                <table className={styles.table}>
                  <colgroup>
                    <col className={styles.colMarketClosed} />
                    <col className={styles.colSideClosed} />
                    <col className={styles.colQtyClosed} />
                    <col className={styles.colOpenClosed} />
                    <col className={styles.colCloseClosed} />
                    <col className={styles.colOutcomeClosed} />
                    <col className={styles.colRpnlClosed} />
                    <col className={styles.colAgeClosed} />
                  </colgroup>
                  <thead>
                    <tr>
                      <th>Market</th>
                      <th>Side</th>
                      <th className={styles.right}>Qty</th>
                      <th className={styles.right}>Open</th>
                      <th className={styles.right}>Close</th>
                      <th className={styles.center}>W/L</th>
                      <th className={styles.right}>rPnL</th>
                      <th className={styles.right}>Age</th>
                    </tr>
                  </thead>
                  <tbody>
                    {closedPositions.length === 0 && (
                      <tr>
                        <td className={styles.emptyCell} colSpan={8}>
                          No closed positions yet
                        </td>
                      </tr>
                    )}
                    {closedPositions.map((p, idx) => (
                      <tr key={`${p.instrument_id}-${p.closed_ts ?? p.opened_ts}-${idx}`}>
                        <td className={styles.marketCell} title={marketTooltip(p)}>
                          {marketTitle(p)}
                        </td>
                        <td>
                          <span className={p.side === "LONG" ? styles.pos : styles.neg}>
                            {p.side}
                          </span>
                        </td>
                        <td className={styles.right}>{fmtNum(p.quantity, 2)}</td>
                        <td className={styles.right}>{fmtNum(p.avg_px_open, 4)}</td>
                        <td className={styles.right}>{fmtNum(p.avg_px_close, 4)}</td>
                        <td
                          className={`${styles.center} ${settlementClass(p.settlement_outcome)}`}
                        >
                          {settlementLabel(p.settlement_outcome)}
                        </td>
                        <td className={`${styles.right} ${pnlClass(p.realized_pnl)}`}>
                          {fmtSignedMoney(p.realized_pnl)}
                        </td>
                        <td className={styles.right}>{fmtDuration(p.duration_s)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </Panel>
          <PanelResizeHandle className={styles.resizeHandle} />
          <Panel id="paper-activity" defaultSize={22} minSize={12} className={styles.panelWithMin}>
            <div className={styles.tablePanel}>
              <div className={styles.sectionBar}>
                <span className={styles.sectionTitle}>Activity</span>
                <span className={styles.count}>{events.length}</span>
              </div>
              <div className={styles.feedScroll}>
                {events.length === 0 && (
                  <div className={styles.emptyCell}>No activity yet</div>
                )}
                {events.map((e) => (
                  <div
                    key={e._id}
                    className={styles.feedRow}
                    title={e.entry_signal_tooltip || e.reason || marketTooltip(e)}
                  >
                    <span className={styles.feedTime}>{fmtClock(e.ts)}</span>
                    <span className={`${styles.feedKind} ${feedKindClass(e.kind)}`}>
                      {KIND_LABEL[e.kind] ?? e.kind}
                    </span>
                    <span className={styles.feedInstr} title={marketTooltip(e)}>
                      {marketTitle(e)}
                    </span>
                    <span className={styles.feedDetail}>{feedDetail(e)}</span>
                  </div>
                ))}
              </div>
            </div>
          </Panel>
        </PanelGroup>
      </div>
    </div>
  );
}

function feedKindClass(kind: string): string {
  if (kind === "fill" || kind === "position_open") return styles.kindGreen;
  if (kind === "position_close") return styles.kindBlue;
  if (kind === "order_rejected" || kind === "order_denied") return styles.kindRed;
  return styles.kindDim;
}

function feedDetail(e: FeedEvent): string {
  if (e.kind === "fill") {
    return `${e.side ?? ""} ${fmtNum(e.quantity, 2)} @ ${fmtNum(e.price, 4)}`;
  }
  if (e.kind === "position_open") {
    return `${e.side ?? ""} ${fmtNum(e.quantity, 2)} @ ${fmtNum(e.price, 4)}`;
  }
  if (e.kind === "position_close") {
    const duration = e.duration_s !== null && e.duration_s !== undefined
      ? ` in ${fmtDuration(e.duration_s)}`
      : "";
    const wl = e.settlement_outcome ? ` ${settlementLabel(e.settlement_outcome)}` : "";
    return `pnl ${fmtSignedMoney(e.realized_pnl)}${wl}${duration}`;
  }
  if (e.kind === "order_rejected" || e.kind === "order_denied") {
    return e.reason ?? "";
  }
  return "";
}

function Kpi({
  label,
  value,
  cls,
  sub,
}: {
  label: string;
  value: string;
  cls?: string;
  sub?: string;
}) {
  return (
    <div className={styles.kpi}>
      <div className={styles.kpiLabel}>{label}</div>
      <div className={`${styles.kpiValue} ${cls ?? ""}`}>{value}</div>
      {sub && <div className={styles.kpiSub}>{sub}</div>}
    </div>
  );
}
