import {
  LineStyle,
  type CanvasRenderingTarget2D,
  type DrawingUtils,
  type IChartApi,
  type IPrimitivePaneRenderer,
  type IPrimitivePaneView,
  type ISeriesPrimitive,
  type PrimitivePaneViewZOrder,
  type SeriesAttachedParameter,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";

export type BarTime = { time: number };

const SECONDS_PER_DAY = 86_400;

/** UTC midnight timestamps between the first and last bar (exclusive of range ends). */
export function computeUtcDayBoundaries(bars: BarTime[]): UTCTimestamp[] {
  if (bars.length < 2) return [];

  const first = bars[0].time;
  const last = bars[bars.length - 1].time;
  const out: UTCTimestamp[] = [];

  const d = new Date(first * 1000);
  d.setUTCHours(0, 0, 0, 0);
  let midnight = Math.floor(d.getTime() / 1000);
  if (midnight <= first) midnight += SECONDS_PER_DAY;

  while (midnight < last) {
    out.push(midnight as UTCTimestamp);
    midnight += SECONDS_PER_DAY;
  }

  return out;
}

type BreakOptions = {
  color: string;
  width: number;
};

const DEFAULT_OPTIONS: BreakOptions = {
  color: "rgba(124, 106, 255, 0.35)",
  width: 2,
};

class DailySessionBreaksRenderer implements IPrimitivePaneRenderer {
  constructor(
    private _xs: (number | null)[],
    private _options: BreakOptions
  ) {}

  draw(target: CanvasRenderingTarget2D, utils?: DrawingUtils): void {
    if (this._xs.length === 0) return;

    target.useBitmapCoordinateSpace((scope) => {
      const ctx = scope.context;
      const height = scope.bitmapSize.height;
      const ratio = scope.horizontalPixelRatio;
      const barWidth = Math.max(1, Math.round(this._options.width * ratio));

      utils?.setLineStyle(ctx, LineStyle.Dashed);
      ctx.strokeStyle = this._options.color;
      ctx.fillStyle = this._options.color;
      ctx.lineWidth = barWidth;

      for (const x of this._xs) {
        if (x === null) continue;
        const xScaled = Math.round(x * ratio);
        ctx.beginPath();
        ctx.moveTo(xScaled, 0);
        ctx.lineTo(xScaled, height);
        ctx.stroke();
        ctx.fillRect(xScaled - Math.floor(barWidth / 2), 0, barWidth, height);
      }
    });
  }
}

class DailySessionBreaksPaneView implements IPrimitivePaneView {
  private _xs: (number | null)[] = [];
  private _renderer: DailySessionBreaksRenderer;

  constructor(private _source: DailySessionBreaksPrimitive) {
    this._renderer = new DailySessionBreaksRenderer(this._xs, DEFAULT_OPTIONS);
  }

  zOrder(): PrimitivePaneViewZOrder {
    return "normal";
  }

  update(): void {
    const chart = this._source.chart;
    if (!chart) {
      this._xs = [];
      this._renderer = new DailySessionBreaksRenderer(this._xs, DEFAULT_OPTIONS);
      return;
    }
    const timeScale = chart.timeScale();
    this._xs = this._source.times.map((t) => timeScale.timeToCoordinate(t));
    this._renderer = new DailySessionBreaksRenderer(this._xs, DEFAULT_OPTIONS);
  }

  renderer(): IPrimitivePaneRenderer {
    return this._renderer;
  }
}

/**
 * UTC daily session separators at each 00:00 UTC between loaded bars.
 * Attach to a reference series after setData; call refresh() on pan/zoom.
 */
export class DailySessionBreaksPrimitive implements ISeriesPrimitive {
  private _chart: IChartApi | null = null;
  private _times: Time[] = [];
  private _paneView: DailySessionBreaksPaneView;
  private _requestUpdate: (() => void) | null = null;

  constructor() {
    this._paneView = new DailySessionBreaksPaneView(this);
  }

  get chart(): IChartApi | null {
    return this._chart;
  }

  get times(): readonly Time[] {
    return this._times;
  }

  attached(param: SeriesAttachedParameter): void {
    this._chart = param.chart;
    this._requestUpdate = param.requestUpdate;
    this.updateAllViews();
    this._requestUpdate?.();
  }

  detached(): void {
    this._chart = null;
    this._requestUpdate = null;
  }

  setBoundaries(times: number[]): void {
    this._times = times as Time[];
    this.refresh();
  }

  /** Re-map times → x coordinates (after pan/zoom or setData). */
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
