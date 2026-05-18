import { useCallback, useState } from "react";
import { FeedProvider } from "./context/FeedContext";
import { Canvas } from "./components/Canvas";
import { TopBar } from "./components/TopBar";
import type {
  CanvasState,
  DashboardsStorage,
  LiquidationSignalsConfig,
  WidgetConfig,
} from "./types";
import {
  LIQ_HISTORY_VERSION,
  normalizeLiqCoins,
} from "./components/widgets/LiquidationSignals";
import styles from "./App.module.css";

const STORAGE_KEY = "sirius-dashboards";
const DEFAULT_NAME = "Main";

const DEFAULT_CANVAS: CanvasState = {
  widgets: [{ id: "btc-ticker-default", type: "price_ticker", symbol: "BTCUSDT-PERP.BINANCE" }],
  layout: [{ i: "btc-ticker-default", x: 0, y: 0, w: 5, h: 3, minW: 3, minH: 2 }],
};

function sanitizeWidget(w: WidgetConfig): WidgetConfig {
  if (w.type !== "liquidation_signals") return w;
  const liq = w as LiquidationSignalsConfig;
  const { history: _history, ...rest } = liq;
  return {
    ...rest,
    historyVersion: LIQ_HISTORY_VERSION,
    minNotional:
      typeof liq.minNotional === "number" && Number.isFinite(liq.minNotional)
        ? liq.minNotional
        : undefined,
    coins: normalizeLiqCoins(liq.coins),
  };
}

function sanitizeStorage(storage: DashboardsStorage): DashboardsStorage {
  const dashboards: Record<string, CanvasState> = {};
  for (const [name, canvas] of Object.entries(storage.dashboards)) {
    dashboards[name] = {
      ...canvas,
      widgets: (canvas.widgets ?? []).map(sanitizeWidget),
      layout: canvas.layout ?? [],
    };
  }
  return { ...storage, dashboards };
}

function load(): DashboardsStorage {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) return sanitizeStorage(JSON.parse(raw) as DashboardsStorage);
  } catch {}

  // Migrate from single-dashboard format (Faz 1-4)
  try {
    const old = localStorage.getItem("sirius-canvas");
    if (old) {
      const canvas = JSON.parse(old) as CanvasState;
      return { dashboards: { [DEFAULT_NAME]: canvas }, active: DEFAULT_NAME };
    }
  } catch {}

  return { dashboards: { [DEFAULT_NAME]: DEFAULT_CANVAS }, active: DEFAULT_NAME };
}

function save(storage: DashboardsStorage): void {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(storage));
}

export default function App() {
  const [storage, setStorage] = useState<DashboardsStorage>(load);

  const mutate = useCallback((next: DashboardsStorage) => {
    setStorage(next);
    save(next);
  }, []);

  const activeCanvas = storage.dashboards[storage.active] ?? DEFAULT_CANVAS;

  // ── Dashboard management ──────────────────────────────────────────────

  const handleCanvasChange = useCallback((state: CanvasState) => {
    setStorage((prev) => {
      const next: DashboardsStorage = {
        ...prev,
        dashboards: { ...prev.dashboards, [prev.active]: state },
      };
      save(next);
      return next;
    });
  }, []);

  const handleSwitch = useCallback(
    (name: string) => mutate({ ...storage, active: name }),
    [storage, mutate]
  );

  const handleAdd = useCallback(() => {
    let n = 1;
    while (storage.dashboards[`Dashboard ${n}`]) n++;
    const name = `Dashboard ${n}`;
    mutate({
      dashboards: { ...storage.dashboards, [name]: { widgets: [], layout: [] } },
      active: name,
    });
  }, [storage, mutate]);

  const handleRename = useCallback(
    (oldName: string, newName: string) => {
      const entries = Object.entries(storage.dashboards);
      const reordered = entries.map(([k, v]) => [k === oldName ? newName : k, v] as [string, CanvasState]);
      mutate({
        dashboards: Object.fromEntries(reordered),
        active: storage.active === oldName ? newName : storage.active,
      });
    },
    [storage, mutate]
  );

  const handleDelete = useCallback(
    (name: string) => {
      const rest = Object.fromEntries(
        Object.entries(storage.dashboards).filter(([k]) => k !== name)
      );
      const names = Object.keys(rest);
      mutate({ dashboards: rest, active: storage.active === name ? names[0] : storage.active });
    },
    [storage, mutate]
  );

  return (
    <FeedProvider>
      <div className={styles.app}>
        <TopBar
          dashboardNames={Object.keys(storage.dashboards)}
          active={storage.active}
          onSwitch={handleSwitch}
          onAdd={handleAdd}
          onRename={handleRename}
          onDelete={handleDelete}
        />
        <main className={styles.main}>
          <Canvas
            key={storage.active}
            state={activeCanvas}
            onChange={handleCanvasChange}
          />
        </main>
      </div>
    </FeedProvider>
  );
}
