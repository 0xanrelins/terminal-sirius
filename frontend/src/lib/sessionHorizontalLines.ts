import {
  LineStyle,
  type CanvasRenderingTarget2D,
  type DrawingUtils,
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

export type SessionBar = { time: number; open: number };

export type SessionHorizontalSegment = {
  start: number;
  end: number;
  price: number;
};

/** Open-price horizontal segment for each UTC epoch-aligned session overlapping the bars. */
export function computeSessionHorizontalSegments(
  bars: SessionBar[],
  periodMinutes: number
): SessionHorizontalSegment[] {
  if (bars.length === 0) return [];

  const sessionSec = Math.max(1, Math.floor(periodMinutes)) * 60;
  const first = bars[0].time;
  const last = bars[bars.length - 1].time;
  const out: SessionHorizontalSegment[] = [];

  let sessionStart = Math.floor(first / sessionSec) * sessionSec;

  while (sessionStart <= last) {
    const sessionEnd = sessionStart + sessionSec;
    if (sessionEnd > first) {
      const price = openAtSessionStart(bars, sessionStart);
      if (price !== null) {
        out.push({ start: sessionStart, end: sessionEnd, price });
      }
    }
    sessionStart += sessionSec;
  }

  return out;
}

function openAtSessionStart(bars: SessionBar[], sessionStart: number): number | null {
  const exact = bars.find((b) => b.time === sessionStart);
  if (exact) return exact.open;
  for (const b of bars) {
    if (b.time >= sessionStart) return b.open;
  }
  return null;
}

export type SessionHLineOptions = {
  color: string;
  width: number;
  lineStyle: LineStyle;
};

export const DEFAULT_SESSION_HLINE_OPTIONS: SessionHLineOptions = {
  color: "rgba(255, 149, 0, 0.85)",
  width: 2,
  lineStyle: LineStyle.Dotted,
};

type MappedSegment = {
  x1: number | null;
  x2: number | null;
  y: number | null;
};

class SessionHorizontalLinesRenderer implements IPrimitivePaneRenderer {
  constructor(
    private _segments: MappedSegment[],
    private _options: SessionHLineOptions
  ) {}

  draw(target: CanvasRenderingTarget2D, utils?: DrawingUtils): void {
    if (this._segments.length === 0) return;

    target.useBitmapCoordinateSpace((scope) => {
      const ctx = scope.context;
      const width = scope.bitmapSize.width;
      const ratio = scope.horizontalPixelRatio;
      const lineWidth = Math.max(1, Math.round(this._options.width * ratio));

      utils?.setLineStyle(ctx, this._options.lineStyle);
      ctx.strokeStyle = this._options.color;
      ctx.lineWidth = lineWidth;

      for (const seg of this._segments) {
        if (seg.y === null) continue;
        const y = Math.round(seg.y * scope.verticalPixelRatio);
        let x1 = seg.x1;
        let x2 = seg.x2;
        if (x1 === null && x2 === null) continue;
        if (x1 === null) x1 = 0;
        if (x2 === null) x2 = width / ratio;
        const xStart = Math.round(Math.min(x1, x2) * ratio);
        const xEnd = Math.round(Math.max(x1, x2) * ratio);
        if (xEnd <= xStart) continue;
        ctx.beginPath();
        ctx.moveTo(xStart, y);
        ctx.lineTo(xEnd, y);
        ctx.stroke();
      }
    });
  }
}

class SessionHorizontalLinesPaneView implements IPrimitivePaneView {
  private _segments: MappedSegment[] = [];
  private _renderer: SessionHorizontalLinesRenderer;

  constructor(private _source: SessionHorizontalLinesPrimitive) {
    this._renderer = new SessionHorizontalLinesRenderer(
      this._segments,
      this._source.options
    );
  }

  zOrder(): PrimitivePaneViewZOrder {
    return "bottom";
  }

  update(): void {
    const chart = this._source.chart;
    const series = this._source.series;
    const options = this._source.options;
    if (!chart || !series) {
      this._segments = [];
      this._renderer = new SessionHorizontalLinesRenderer(this._segments, options);
      return;
    }
    const timeScale = chart.timeScale();
    this._segments = this._source.segments.map((seg) => ({
      x1: coordinateForTime(timeScale, series, seg.start),
      x2: coordinateForTime(timeScale, series, seg.end),
      y: series.priceToCoordinate(seg.price),
    }));
    this._renderer = new SessionHorizontalLinesRenderer(this._segments, options);
  }

  renderer(): IPrimitivePaneRenderer {
    return this._renderer;
  }
}

/**
 * Horizontal open-price lines per UTC session bucket (ISeriesPrimitive).
 * Uses ISeriesApi.priceToCoordinate + ITimeScaleApi via coordinateForTime.
 */
export class SessionHorizontalLinesPrimitive implements ISeriesPrimitive {
  private _chart: IChartApi | null = null;
  private _series: ISeriesApi<SeriesType> | null = null;
  private _segments: SessionHorizontalSegment[] = [];
  private _options: SessionHLineOptions;
  private _paneView: SessionHorizontalLinesPaneView;
  private _requestUpdate: (() => void) | null = null;

  constructor(options: SessionHLineOptions = DEFAULT_SESSION_HLINE_OPTIONS) {
    this._options = { ...options };
    this._paneView = new SessionHorizontalLinesPaneView(this);
  }

  get chart(): IChartApi | null {
    return this._chart;
  }

  get series(): ISeriesApi<SeriesType> | null {
    return this._series;
  }

  get segments(): readonly SessionHorizontalSegment[] {
    return this._segments;
  }

  get options(): SessionHLineOptions {
    return this._options;
  }

  attached(param: SeriesAttachedParameter): void {
    this._chart = param.chart;
    this._series = param.series;
    this._requestUpdate = param.requestUpdate;
    this.updateAllViews();
    this._requestUpdate?.();
  }

  detached(): void {
    this._chart = null;
    this._series = null;
    this._requestUpdate = null;
  }

  setSegments(segments: SessionHorizontalSegment[]): void {
    this._segments = segments;
    this.refresh();
  }

  refresh(): void {
    this.updateAllViews();
    this._requestUpdate?.();
  }

  updateAllViews(): void {
    this._paneView.update();
  }

  paneViews(): readonly IPrimitivePaneView[] {
    return [this._paneView];
  }
}
