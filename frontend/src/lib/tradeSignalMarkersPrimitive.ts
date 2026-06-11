import {
  type CanvasRenderingTarget2D,
  type IChartApi,
  type IPrimitivePaneRenderer,
  type IPrimitivePaneView,
  type ISeriesApi,
  type ISeriesPrimitive,
  type PrimitivePaneViewZOrder,
  type SeriesAttachedParameter,
  type SeriesType,
} from "lightweight-charts";
import { coordinateForTime } from "./dailySessionBreaks";
import {
  TRADE_EXIT_COLOR,
  TRADE_LONG_COLOR,
  TRADE_SHORT_COLOR,
  type TradeSignalMarker,
} from "./tradeSignalMarkers";

type MappedMarker = {
  x: number | null;
  y: number | null;
  direction: TradeSignalMarker["direction"];
  action: TradeSignalMarker["action"];
};

const ARROW_PX = 11;
const LONG_OFFSET_PX = 14;
const SHORT_OFFSET_PX = 14;
const STACK_STEP_PX = 12;

type BarHighLow = { high: number; low: number };

function buildBarIndex(
  series: ISeriesApi<SeriesType>
): Map<number, BarHighLow> {
  const index = new Map<number, BarHighLow>();
  for (const bar of series.data()) {
    const t = bar.time as number;
    if ("high" in bar && "low" in bar) {
      index.set(t, { high: bar.high as number, low: bar.low as number });
    } else if ("value" in bar) {
      const v = bar.value as number;
      index.set(t, { high: v, low: v });
    }
  }
  return index;
}

function drawArrowUp(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  color: string,
  size: number
): void {
  ctx.fillStyle = color;
  ctx.beginPath();
  ctx.moveTo(x, y);
  ctx.lineTo(x - size, y + size * 1.4);
  ctx.lineTo(x + size, y + size * 1.4);
  ctx.closePath();
  ctx.fill();
}

function drawArrowDown(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  color: string,
  size: number
): void {
  ctx.fillStyle = color;
  ctx.beginPath();
  ctx.moveTo(x, y);
  ctx.lineTo(x - size, y - size * 1.4);
  ctx.lineTo(x + size, y - size * 1.4);
  ctx.closePath();
  ctx.fill();
}

function markerColor(m: MappedMarker): string {
  if (m.action === "exit") return TRADE_EXIT_COLOR;
  return m.direction === "LONG" ? TRADE_LONG_COLOR : TRADE_SHORT_COLOR;
}

function markerPointsUp(m: MappedMarker): boolean {
  if (m.action === "exit") return m.direction === "SHORT";
  return m.direction === "LONG";
}

class TradeSignalMarkersRenderer implements IPrimitivePaneRenderer {
  constructor(private _mapped: MappedMarker[]) {}

  draw(target: CanvasRenderingTarget2D): void {
    if (this._mapped.length === 0) return;

    target.useBitmapCoordinateSpace((scope) => {
      const ctx = scope.context;
      const hr = scope.horizontalPixelRatio;
      const vr = scope.verticalPixelRatio;
      const arrow = Math.max(4, Math.round(ARROW_PX * hr));

      for (const m of this._mapped) {
        if (m.x === null || m.y === null) continue;
        const x = Math.round(m.x * hr);
        const y = Math.round(m.y * vr);
        const color = markerColor(m);
        if (markerPointsUp(m)) {
          drawArrowUp(ctx, x, y, color, arrow);
        } else {
          drawArrowDown(ctx, x, y, color, arrow);
        }
      }
    });
  }
}

class TradeSignalMarkersPaneView implements IPrimitivePaneView {
  private _mapped: MappedMarker[] = [];
  private _renderer = new TradeSignalMarkersRenderer(this._mapped);

  constructor(private _source: TradeSignalMarkersPrimitive) {}

  zOrder(): PrimitivePaneViewZOrder {
    return "top";
  }

  update(): void {
    const chart = this._source.chart;
    const series = this._source.series;
    if (!chart || !series) {
      this._mapped = [];
      this._renderer = new TradeSignalMarkersRenderer(this._mapped);
      return;
    }

    const timeScale = chart.timeScale();
    const barIndex = buildBarIndex(series);
    const byTime = new Map<number, TradeSignalMarker[]>();
    for (const m of this._source.markers) {
      const t = m.time as number;
      const group = byTime.get(t);
      if (group) group.push(m);
      else byTime.set(t, [m]);
    }

    this._mapped = this._source.markers.map((m) => {
      const group = byTime.get(m.time as number) ?? [m];
      const center = (group.length - 1) / 2;
      const xBase = coordinateForTime(timeScale, series, m.time);
      const candle = barIndex.get(m.time as number) ?? null;
      if (xBase === null || candle === null) {
        return { x: xBase, y: null, direction: m.direction, action: m.action };
      }
      const x = xBase + (m.stackIndex - center) * STACK_STEP_PX;
      const y =
        m.direction === "LONG"
          ? series.priceToCoordinate(candle.low)! + LONG_OFFSET_PX
          : series.priceToCoordinate(candle.high)! - SHORT_OFFSET_PX;
      return { x, y, direction: m.direction, action: m.action };
    });
    this._renderer = new TradeSignalMarkersRenderer(this._mapped);
  }

  renderer(): IPrimitivePaneRenderer {
    return this._renderer;
  }
}

/** Canvas arrows for paper-trade entry/exit events (ISeriesPrimitive). */
export class TradeSignalMarkersPrimitive implements ISeriesPrimitive {
  private _chart: IChartApi | null = null;
  private _series: ISeriesApi<SeriesType> | null = null;
  private _markers: TradeSignalMarker[] = [];
  private _markerTimes = new Set<number>();
  private _paneView: TradeSignalMarkersPaneView;
  private _requestUpdate: (() => void) | null = null;
  private _cachedFirstTime: number | null = null;
  private _cachedLastTime: number | null = null;
  private _cachedLength = 0;

  constructor() {
    this._paneView = new TradeSignalMarkersPaneView(this);
  }

  get chart(): IChartApi | null {
    return this._chart;
  }

  get series(): ISeriesApi<SeriesType> | null {
    return this._series;
  }

  get markers(): readonly TradeSignalMarker[] {
    return this._markers;
  }

  attached(param: SeriesAttachedParameter): void {
    this._chart = param.chart;
    this._series = param.series;
    this._requestUpdate = param.requestUpdate;
    this._series.subscribeDataChanged(this._onDataChanged);
    this._resetDataCache();
    this.updateAllViews();
    this._requestUpdate?.();
  }

  detached(): void {
    this._series?.unsubscribeDataChanged(this._onDataChanged);
    this._chart = null;
    this._series = null;
    this._requestUpdate = null;
    this._resetDataCache();
  }

  addMarker(marker: TradeSignalMarker): void {
    if (this._markers.some((m) => m.id === marker.id)) return;
    const stackIndex = this._markers.filter((m) => m.time === marker.time).length;
    this._markers = [...this._markers, { ...marker, stackIndex }].sort(
      (a, b) => (a.time as number) - (b.time as number)
    );
    this._markerTimes.add(marker.time as number);
    this.refresh();
  }

  clearMarkers(): void {
    this._markers = [];
    this._markerTimes.clear();
    this.refresh();
  }

  /** Library lifecycle — remap coordinates before each draw pass. */
  updateAllViews(): void {
    this._paneView.update();
  }

  /** Marker set changed or series window shifted — schedule a redraw. */
  refresh(): void {
    this.updateAllViews();
    this._requestUpdate?.();
  }

  paneViews(): readonly IPrimitivePaneView[] {
    return [this._paneView];
  }

  private _resetDataCache(): void {
    this._cachedFirstTime = null;
    this._cachedLastTime = null;
    this._cachedLength = 0;
  }

  /** ISeriesApi.subscribeDataChanged — repaint when marker bars or series shape changes. */
  private _onDataChanged = (): void => {
    if (this._markers.length === 0) return;
    const series = this._series;
    if (!series) return;

    const data = series.data();
    if (data.length === 0) return;

    const firstT = data[0].time as number;
    const lastT = data[data.length - 1].time as number;
    const len = data.length;

    const structureChanged =
      this._cachedLength !== len ||
      this._cachedFirstTime !== firstT ||
      this._cachedLastTime !== lastT;

    this._cachedFirstTime = firstT;
    this._cachedLastTime = lastT;
    this._cachedLength = len;

    if (structureChanged || this._markerTimes.has(lastT)) {
      this.refresh();
    }
  };
}
