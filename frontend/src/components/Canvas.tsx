import { useCallback, useState } from "react";
import GridLayout, { type Layout } from "react-grid-layout";
import "react-grid-layout/css/styles.css";
import "react-resizable/css/styles.css";
import type {
  CanvasState,
  CandlestickChartConfig,
  ComparisonChartConfig,
  LiquidationSignalsConfig,
  PolymarketTickerConfig,
  WidgetConfig,
} from "../types";
import { AddWidgetModal } from "./AddWidgetModal";
import { CandlestickChart } from "./widgets/CandlestickChart";
import { normalizeComparisonSymbols } from "../lib/chartConfig";
import { ComparisonChart } from "./widgets/ComparisonChart";
import {
  DEFAULT_MIN_NOTIONAL,
  LiquidationSignals,
  normalizeLiqCoins,
} from "./widgets/LiquidationSignals";
import { LiveTradePanel } from "./widgets/LiveTradePanel";
import { SimulationPanel } from "./widgets/SimulationPanel";
import { PolymarketTicker } from "./widgets/PolymarketTicker";
import { PriceTicker } from "./widgets/PriceTicker";
import styles from "./Canvas.module.css";

const COLS = 24;
const ROW_HEIGHT = 40;

type Props = {
  state: CanvasState;
  onChange: (next: CanvasState) => void;
};

function defaultLayout(id: string, type: WidgetConfig["type"]): Layout {
  const isChart = type === "candlestick_chart" || type === "comparison_chart";
  const isLiq = type === "liquidation_signals";
  const isSim = type === "simulation_panel" || type === "live_trade_panel";
  const w = isChart ? 14 : isLiq || isSim ? 8 : 5;
  const h = isChart ? 9 : isLiq ? 8 : isSim ? 10 : type === "polymarket_ticker" ? 4 : 3;
  return { i: id, x: 0, y: Infinity, w, h, minW: 3, minH: 2 };
}

function handleLabel(cfg: WidgetConfig): string {
  if (cfg.type === "liquidation_signals") return "Liq Signals";
  if (cfg.type === "simulation_panel") return "Liq→Poly Sim";
  if (cfg.type === "live_trade_panel") return "Live Trade";
  if (cfg.type === "candlestick_chart" || cfg.type === "comparison_chart") return "";
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
    case "simulation_panel":
      return <SimulationPanel />;
    case "live_trade_panel":
      return <LiveTradePanel />;
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

  if (state.widgets.length === 0) {
    return (
      <div className={styles.canvas}>
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
    <div className={styles.canvas}>
      {showModal && (
        <AddWidgetModal onAdd={addWidget} onClose={() => setShowModal(false)} />
      )}

      <GridLayout
        className="layout"
        layout={state.layout}
        cols={COLS}
        rowHeight={ROW_HEIGHT}
        width={window.innerWidth}
        onLayoutChange={onLayoutChange}
        draggableHandle={`.${styles.handle}`}
        draggableCancel={`.${styles.actionBtn}, .chartToolbar, .comparisonToolbar, .signalsToolbar, .simulationToolbar`}
        resizeHandles={["se"]}
        margin={[6, 6]}
      >
        {state.widgets.map((cfg) => (
          <div key={cfg.id} className={styles.cell}>
            <div className={styles.handle}>
              {cfg.type !== "candlestick_chart" && cfg.type !== "comparison_chart" && (
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

      <button className={styles.addBtn} onClick={() => setShowModal(true)} title="Add widget">
        +
      </button>
    </div>
  );
}
