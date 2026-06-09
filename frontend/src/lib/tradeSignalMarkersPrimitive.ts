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
  type UTCTimestamp,
} from "lightweight-charts";
import { coordinateForTime } from "./dailySessionBreaks";
import { TRADE_LONG_COLOR, TRADE_SHORT_COLOR, type TradeSignalMarker } from "./tradeSignalMarkers";

type MappedMarker = {
  x: number | null;
  y: number | null;
  direction: TradeSignalMarker["direction"];
};

const ARROW_PX = 11;
const LONG_OFFSET_PX = 14;
const SHORT_OFFSET_PX = 14;

function candleAtTime(
  series: ISeriesApi<SeriesType>,
  time: UTCTimestamp
): { high: number; low: number } | null {
  for (const bar of series.data()) {
    if (bar.time !== time) continue;
    if ("high" in bar && "low" in bar) {
      return { high: bar.high as number, low: bar.low as number };
    }
    if ("value" in bar) {
      const v = bar.value as number;
      return { high: v, low: v };
    }
    return null;
  }
  return null;
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
        if (m.direction === "LONG") {
          drawArrowUp(ctx, x, y, TRADE_LONG_COLOR, arrow);
        } else {
          drawArrowDown(ctx, x, y, TRADE_SHORT_COLOR, arrow);
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
    this._mapped = this._source.markers.map((m) => {
      const x = coordinateForTime(timeScale, series, m.time);
      const candle = candleAtTime(series, m.time);
      if (x === null || candle === null) {
        return { x, y: null, direction: m.direction };
      }
      const y =
        m.direction === "LONG"
          ? series.priceToCoordinate(candle.low)! + LONG_OFFSET_PX
          : series.priceToCoordinate(candle.high)! - SHORT_OFFSET_PX;
      return { x, y, direction: m.direction };
    });
    this._renderer = new TradeSignalMarkersRenderer(this._mapped);
  }

  renderer(): IPrimitivePaneRenderer {
    return this._renderer;
  }
}

/** Canvas arrows for paper-trade entry fills (ISeriesPrimitive). */
export class TradeSignalMarkersPrimitive implements ISeriesPrimitive {
  private _chart: IChartApi | null = null;
  private _series: ISeriesApi<SeriesType> | null = null;
  private _markers: TradeSignalMarker[] = [];
  private _paneView: TradeSignalMarkersPaneView;
  private _requestUpdate: (() => void) | null = null;

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
    this.refresh();
  }

  detached(): void {
    this._chart = null;
    this._series = null;
    this._requestUpdate = null;
  }

  addMarker(marker: TradeSignalMarker): void {
    if (this._markers.some((m) => m.id === marker.id)) return;
    this._markers = [...this._markers, marker].sort(
      (a, b) => (a.time as number) - (b.time as number)
    );
    this.refresh();
  }

  clearMarkers(): void {
    this._markers = [];
    this.refresh();
  }

  refresh(): void {
    this._paneView.update();
    this._requestUpdate?.();
  }

  paneViews(): readonly IPrimitivePaneView[] {
    return [this._paneView];
  }
}
