import { useCallback, useLayoutEffect, useRef, useState } from "react";
import GridLayout, { type Layout } from "react-grid-layout";
import "react-grid-layout/css/styles.css";
import "react-resizable/css/styles.css";
import type {
  CanvasState,
  CandlestickChartConfig,
  ComparisonChartConfig,
  LiqPostEventChartConfig,
  LiquidationSignalsConfig,
  PolymarketTickerConfig,
  WidgetConfig,
} from "../types";
import { AddWidgetModal } from "./AddWidgetModal";
import { CandlestickChart } from "./widgets/CandlestickChart";
import { normalizeComparisonSymbols } from "../lib/chartConfig";
import { ComparisonChart } from "./widgets/ComparisonChart";
import {
  LiqPostEventChart,
  normalizePostEventCoins,
  normalizePostEventSides,
} from "./widgets/LiqPostEventChart";
import {
  DEFAULT_MIN_NOTIONAL,
  LiquidationSignals,
  normalizeLiqCoins,
} from "./widgets/LiquidationSignals";
import { MarketTimes } from "./widgets/MarketTimes";
import { PolymarketTicker } from "./widgets/PolymarketTicker";
import { PolymarketSecondsChart } from "./widgets/PolymarketSecondsChart";
import { BarCountdown } from "./widgets/BarCountdown";
import { NewYorkTime } from "./widgets/NewYorkTime";
import { PaperTradeDashboard } from "./widgets/PaperTradeDashboard";
import { PriceTicker } from "./widgets/PriceTicker";
import {
  STRATEGY_BINANCE_SYMBOLS,
  StrategySignalsWidget,
} from "./widgets/StrategySignalsWidget";
import { LiquidationVerdictDashboard } from "./widgets/LiquidationVerdictDashboard";
import { normalizeVerdictCoins, normalizeVerdictSides } from "../lib/liquidationVerdict";
import styles from "./Canvas.module.css";

const COLS = 24;
const ROW_HEIGHT = 40;

type Props = {
  state: CanvasState;
  onChange: (next: CanvasState) => void;
};

function defaultLayout(id: string, type: WidgetConfig["type"]): Layout {
  const isChart =
    type === "candlestick_chart" ||
    type === "comparison_chart" ||
    type === "liq_post_event_chart" ||
    type === "polymarket_seconds_chart";
  const isLiq = type === "liquidation_signals";
  const isMarketTimes = type === "market_times";
  const isPaper = type === "paper_trade_dashboard";
  const isStrategySignals = type === "strategy_signals";
  const isVerdictDash = type === "liquidation_verdict_dashboard";
  const w = isPaper
    ? 16
    : isVerdictDash
      ? 12
      : isStrategySignals
        ? 6
        : isChart
          ? 14
          : isLiq
            ? 8
            : isMarketTimes
              ? 6
              : 5;
  const h = isPaper
    ? 16
    : isVerdictDash
      ? 12
    : isStrategySignals
      ? 10
    : isChart
      ? 9
      : isLiq
        ? 8
        : isMarketTimes
          ? 6
          : type === "polymarket_ticker"
            ? 4
            : 3;
  return { i: id, x: 0, y: Infinity, w, h, minW: 3, minH: 2 };
}

function handleLabel(cfg: WidgetConfig): string {
  if (cfg.type === "liquidation_signals") return "Liq Signals";
  if (cfg.type === "market_times") return "Market Times";
  if (cfg.type === "bar_countdown") return "15m Countdown";
  if (cfg.type === "new_york_time") return "New York";
  if (cfg.type === "paper_trade_dashboard") return "Paper Trade";
  if (cfg.type === "strategy_signals") return "Strategy Signals";
  if (cfg.type === "liquidation_verdict_dashboard") return "Liq Verdict";
  if (
    cfg.type === "candlestick_chart" ||
    cfg.type === "comparison_chart" ||
    cfg.type === "liq_post_event_chart" ||
    cfg.type === "polymarket_seconds_chart"
  ) {
    return "";
  }
  if (cfg.type === "price_ticker" && cfg.source === "polymarket") {
    return cfg.label ? `${cfg.label} 15m` : cfg.symbol;
  }
  return cfg.symbol;
}

function renderWidget(
  cfg: WidgetConfig,
  onUpdate: (id: string, patch: Partial<WidgetConfig>) => void
) {
  switch (cfg.type) {
    case "price_ticker":
      return (
        <PriceTicker
          symbol={cfg.symbol}
          source={cfg.source}
          label={cfg.label}
        />
      );
    case "candlestick_chart": {
      const chartCfg = cfg as CandlestickChartConfig;
      return (
        <CandlestickChart
          symbol={chartCfg.symbol}
          interval={chartCfg.interval}
          chartStyle={chartCfg.chartStyle}
          indicators={chartCfg.indicators ?? []}
          initialBars={chartCfg.initialBars}
          onConfigChange={(patch) => onUpdate(cfg.id, patch)}
        />
      );
    }
    case "comparison_chart": {
      const cmpCfg = cfg as ComparisonChartConfig;
      return (
        <ComparisonChart
          interval={cmpCfg.interval}
          symbols={normalizeComparisonSymbols(cmpCfg.symbols)}
          onConfigChange={(patch) => onUpdate(cfg.id, patch)}
        />
      );
    }
    case "liq_post_event_chart": {
      const liqChartCfg = cfg as LiqPostEventChartConfig;
      return (
        <LiqPostEventChart
          coins={normalizePostEventCoins(liqChartCfg.coins)}
          sides={normalizePostEventSides(liqChartCfg.sides)}
          minNotional={liqChartCfg.minNotional ?? DEFAULT_MIN_NOTIONAL}
          onConfigChange={(patch) => onUpdate(cfg.id, patch)}
        />
      );
    }
    case "polymarket_seconds_chart":
      return (
        <PolymarketSecondsChart
          series={cfg.series}
          interval={cfg.interval ?? "1s"}
          label={cfg.label}
          onConfigChange={(patch) => onUpdate(cfg.id, patch)}
        />
      );
    case "polymarket_ticker":
      return (
        <PolymarketTicker
          symbol={cfg.symbol}
          question={(cfg as PolymarketTickerConfig).question}
        />
      );
    case "liquidation_signals": {
      const liqCfg = cfg as LiquidationSignalsConfig;
      return (
        <LiquidationSignals
          minNotional={liqCfg.minNotional ?? DEFAULT_MIN_NOTIONAL}
          coins={normalizeLiqCoins(liqCfg.coins)}
          historyVersion={liqCfg.historyVersion}
          onConfigChange={(patch) => onUpdate(cfg.id, patch)}
        />
      );
    }
    case "market_times":
      return <MarketTimes />;
    case "bar_countdown":
      return <BarCountdown />;
    case "new_york_time":
      return <NewYorkTime />;
    case "paper_trade_dashboard":
      return (
        <PaperTradeDashboard
          curveMetric={cfg.curveMetric ?? "equity"}
          onConfigChange={(patch) => onUpdate(cfg.id, patch)}
        />
      );
    case "strategy_signals":
      return (
        <StrategySignalsWidget
          symbols={cfg.symbols ?? [...STRATEGY_BINANCE_SYMBOLS]}
          onConfigChange={(patch) => onUpdate(cfg.id, patch)}
        />
      );
    case "liquidation_verdict_dashboard":
      return (
        <LiquidationVerdictDashboard
          coins={normalizeVerdictCoins(cfg.coins)}
          sides={normalizeVerdictSides(cfg.sides)}
          onConfigChange={(patch) => onUpdate(cfg.id, patch)}
        />
      );
    default:
      return null;
  }
}

export function Canvas({ state, onChange }: Props) {
  const [showModal, setShowModal] = useState(false);

  const emit = useCallback(
    (patch: Partial<CanvasState>) => onChange({ ...state, ...patch }),
    [state, onChange]
  );

  const onLayoutChange = useCallback(
    (layout: Layout[]) => emit({ layout }),
    [emit]
  );

  const addWidget = useCallback(
    (cfg: WidgetConfig) => {
      emit({
        widgets: [...state.widgets, cfg],
        layout: [...state.layout, defaultLayout(cfg.id, cfg.type)],
      });
    },
    [state, emit]
  );

  const removeWidget = useCallback(
    (id: string) => {
      emit({
        widgets: state.widgets.filter((w) => w.id !== id),
        layout: state.layout.filter((l) => l.i !== id),
      });
    },
    [state, emit]
  );

  const updateWidget = useCallback(
    (id: string, patch: Partial<WidgetConfig>) => {
      emit({
        widgets: state.widgets.map((w) =>
          w.id === id ? ({ ...w, ...patch } as WidgetConfig) : w
        ),
      });
    },
    [state, emit]
  );

  const duplicateWidget = useCallback(
    (id: string) => {
      const src = state.widgets.find((w) => w.id === id);
      const srcLayout = state.layout.find((l) => l.i === id);
      if (!src || !srcLayout) return;

      const newId = `${src.type}-${Date.now()}`;
      const newCfg: WidgetConfig = { ...src, id: newId } as WidgetConfig;
      const newLayout: Layout = {
        ...srcLayout,
        i: newId,
        x: Math.min(srcLayout.x + 1, COLS - srcLayout.w),
        y: srcLayout.y + 1,
      };

      emit({
        widgets: [...state.widgets, newCfg],
        layout: [...state.layout, newLayout],
      });
    },
    [state, emit]
  );

  const canvasRef = useRef<HTMLDivElement>(null);
  const [gridWidth, setGridWidth] = useState(0);

  useLayoutEffect(() => {
    const el = canvasRef.current;
    if (!el) return;
    const update = () => setGridWidth(el.clientWidth);
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  if (state.widgets.length === 0) {
    return (
      <div ref={canvasRef} className={styles.canvas}>
        {showModal && (
          <AddWidgetModal onAdd={addWidget} onClose={() => setShowModal(false)} />
        )}
        <div className={styles.empty}>
          <p className={styles.emptyTitle}>Canvas is empty</p>
          <p className={styles.emptyHint}>Click <strong>+</strong> to add your first widget</p>
        </div>
        <button className={styles.addBtn} onClick={() => setShowModal(true)} title="Add widget">
          +
        </button>
      </div>
    );
  }

  return (
    <div ref={canvasRef} className={styles.canvas}>
      {showModal && (
        <AddWidgetModal onAdd={addWidget} onClose={() => setShowModal(false)} />
      )}

      {gridWidth > 0 && (
      <GridLayout
        className="layout"
        layout={state.layout}
        cols={COLS}
        rowHeight={ROW_HEIGHT}
        width={gridWidth}
        onLayoutChange={onLayoutChange}
        draggableHandle={`.${styles.handle}`}
        draggableCancel={`.${styles.actionBtn}, .chartToolbar, .comparisonToolbar, .signalsToolbar`}
        resizeHandles={["se"]}
        margin={[6, 6]}
      >
        {state.widgets.map((cfg) => (
          <div key={cfg.id} className={styles.cell}>
            <div className={styles.handle}>
              {cfg.type !== "candlestick_chart" &&
                cfg.type !== "comparison_chart" &&
                cfg.type !== "liq_post_event_chart" && (
                <span className={styles.handleLabel}>{handleLabel(cfg)}</span>
              )}
              <div className={styles.handleActions}>
                <button
                  className={styles.actionBtn}
                  onClick={() => duplicateWidget(cfg.id)}
                  title="Duplicate widget"
                >
                  ⊕
                </button>
                <button
                  className={`${styles.actionBtn} ${styles.removeBtn}`}
                  onClick={() => removeWidget(cfg.id)}
                  title="Remove widget"
                >
                  ✕
                </button>
              </div>
            </div>
            <div className={styles.widgetBody}>{renderWidget(cfg, updateWidget)}</div>
          </div>
        ))}
      </GridLayout>
      )}

      <button className={styles.addBtn} onClick={() => setShowModal(true)} title="Add widget">
        +
      </button>
    </div>
  );
}
