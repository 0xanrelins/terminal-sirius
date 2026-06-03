import {
  LineStyle,
  type CanvasRenderingTarget2D,
  type DrawingUtils,
  type IChartApi,
  type IPrimitivePaneRenderer,
  type IPrimitivePaneView,
  type ISeriesApi,
  type ISeriesPrimitive,
  type ITimeScaleApi,
  type PrimitivePaneViewZOrder,
  type SeriesAttachedParameter,
  type SeriesType,
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

/** UTC epoch-aligned interval boundaries strictly between first and last bar time. */
export function computeUtcIntervalBoundaries(
  bars: BarTime[],
  intervalSeconds: number
): UTCTimestamp[] {
  if (bars.length < 2) return [];

  const sessionSec = Math.max(1, Math.floor(intervalSeconds));
  const first = bars[0].time;
  const last = bars[bars.length - 1].time;
  const out: UTCTimestamp[] = [];

  let t = Math.ceil(first / sessionSec) * sessionSec;
  if (t <= first) t += sessionSec;

  while (t < last) {
    out.push(t as UTCTimestamp);
    t += sessionSec;
  }

  return out;
}

/** First UTC epoch-aligned boundary strictly after `afterTimeSec`. */
export function nextUtcIntervalBoundary(
  afterTimeSec: number,
  intervalSeconds: number
): number {
  const sessionSec = Math.max(1, Math.floor(intervalSeconds));
  let t = Math.ceil(afterTimeSec / sessionSec) * sessionSec;
  if (t <= afterTimeSec) t += sessionSec;
  return t;
}

/** Historical session lines plus the upcoming boundary from now / last bar. */
export function computeUtcIntervalBoundariesWithNext(
  bars: BarTime[],
  intervalSeconds: number,
  nowSec = Math.floor(Date.now() / 1000)
): { boundaries: UTCTimestamp[]; next: UTCTimestamp | null } {
  const boundaries = computeUtcIntervalBoundaries(bars, intervalSeconds);
  const sessionSec = Math.max(1, Math.floor(intervalSeconds));

  const anchor =
    bars.length > 0
      ? Math.max(nowSec, bars[bars.length - 1].time)
      : nowSec;
  const next = nextUtcIntervalBoundary(anchor, sessionSec) as UTCTimestamp;

  if (boundaries.some((t) => t === next)) {
    return { boundaries, next: null };
  }

  return { boundaries, next };
}

/**
 * Map time → x. Uses ITimeScaleApi.timeToCoordinate; when that returns null
 * (future / off-scale), linear extrapolation from the host series' last two bars
 * (recommended for series-primitive plugins — see LW Charts #855).
 */
export function coordinateForTime(
  timeScale: ITimeScaleApi,
  series: ISeriesApi<SeriesType> | null,
  time: Time
): number | null {
  const direct = timeScale.timeToCoordinate(time);
  if (direct !== null) return direct;
  if (!series) return null;

  const data = series.data();
  if (data.length < 2) return null;

  const p1 = data[data.length - 2];
  const p2 = data[data.length - 1];
  const x1 = timeScale.timeToCoordinate(p1.time);
  const x2 = timeScale.timeToCoordinate(p2.time);
  if (x1 === null || x2 === null) return null;

  const t1 = p1.time as number;
  const t2 = p2.time as number;
  const target = time as number;
  if (t2 <= t1) return null;

  return x1 + ((x2 - x1) * (target - t1)) / (t2 - t1);
}

export type BreakOptions = {
  color: string;
  width: number;
  lineStyle: LineStyle;
  /** When false, draw a filled bar (Compare chart default). */
  strokeOnly: boolean;
};

export const DEFAULT_BREAK_OPTIONS: BreakOptions = {
  color: "rgba(124, 106, 255, 0.35)",
  width: 2,
  lineStyle: LineStyle.Dashed,
  strokeOnly: false,
};

/** Orange dotted vertical lines for the candlestick chart session-break indicator. */
export const CANDLESTICK_SESSION_BREAK_OPTIONS: BreakOptions = {
  color: "rgba(255, 149, 0, 0.85)",
  width: 3,
  lineStyle: LineStyle.Dotted,
  strokeOnly: true,
};

/** Upcoming session boundary — slightly brighter dashed line. */
export const CANDLESTICK_NEXT_SESSION_BREAK_OPTIONS: BreakOptions = {
  color: "rgba(255, 180, 80, 0.9)",
  width: 3,
  lineStyle: LineStyle.Dashed,
  strokeOnly: true,
};

function drawVerticalLines(
  ctx: CanvasRenderingContext2D,
  height: number,
  ratio: number,
  xs: (number | null)[],
  options: BreakOptions,
  utils?: DrawingUtils
): void {
  const barWidth = Math.max(1, Math.round(options.width * ratio));
  utils?.setLineStyle(ctx, options.lineStyle);
  ctx.strokeStyle = options.color;
  ctx.fillStyle = options.color;
  ctx.lineWidth = barWidth;

  for (const x of xs) {
    if (x === null) continue;
    const xScaled = Math.round(x * ratio);
    ctx.beginPath();
    ctx.moveTo(xScaled, 0);
    ctx.lineTo(xScaled, height);
    ctx.stroke();
    if (!options.strokeOnly) {
      ctx.fillRect(xScaled - Math.floor(barWidth / 2), 0, barWidth, height);
    }
  }
}

class SessionBreaksRenderer implements IPrimitivePaneRenderer {
  constructor(
    private _xs: (number | null)[],
    private _options: BreakOptions,
    private _nextX: number | null,
    private _nextOptions: BreakOptions | null
  ) {}

  draw(target: CanvasRenderingTarget2D, utils?: DrawingUtils): void {
    if (this._xs.length === 0 && this._nextX === null) return;

    target.useBitmapCoordinateSpace((scope) => {
      const ctx = scope.context;
      const height = scope.bitmapSize.height;
      const ratio = scope.horizontalPixelRatio;

      drawVerticalLines(ctx, height, ratio, this._xs, this._options, utils);

      if (this._nextX !== null && this._nextOptions) {
        drawVerticalLines(
          ctx,
          height,
          ratio,
          [this._nextX],
          this._nextOptions,
          utils
        );
      }
    });
  }
}

class SessionBreaksPaneView implements IPrimitivePaneView {
  private _xs: (number | null)[] = [];
  private _nextX: number | null = null;
  private _renderer: SessionBreaksRenderer;

  constructor(private _source: SessionBreaksPrimitive) {
    this._renderer = new SessionBreaksRenderer(
      this._xs,
      this._source.options,
      this._nextX,
      this._source.nextOptions
    );
  }

  zOrder(): PrimitivePaneViewZOrder {
    return "normal";
  }

  update(): void {
    const chart = this._source.chart;
    const options = this._source.options;
    const nextOptions = this._source.nextOptions;
    if (!chart) {
      this._xs = [];
      this._nextX = null;
      this._renderer = new SessionBreaksRenderer(
        this._xs,
        options,
        this._nextX,
        nextOptions
      );
      return;
    }
    const timeScale = chart.timeScale();
    const series = this._source.series;
    this._xs = this._source.times.map((t) =>
      coordinateForTime(timeScale, series, t)
    );
    this._nextX =
      this._source.nextTime !== null
        ? coordinateForTime(timeScale, series, this._source.nextTime)
        : null;
    this._renderer = new SessionBreaksRenderer(
      this._xs,
      options,
      this._nextX,
      nextOptions
    );
  }

  renderer(): IPrimitivePaneRenderer {
    return this._renderer;
  }
}

/**
 * UTC session separators on the chart pane.
 * Attach to a reference series after setData; call refresh() on pan/zoom.
 */
export class SessionBreaksPrimitive implements ISeriesPrimitive {
  private _chart: IChartApi | null = null;
  private _series: ISeriesApi<SeriesType> | null = null;
  private _times: Time[] = [];
  private _nextTime: Time | null = null;
  private _options: BreakOptions;
  private _nextOptions: BreakOptions | null;
  private _paneView: SessionBreaksPaneView;
  private _requestUpdate: (() => void) | null = null;

  constructor(
    options: BreakOptions = DEFAULT_BREAK_OPTIONS,
    nextOptions: BreakOptions | null = null
  ) {
    this._options = { ...options };
    this._nextOptions = nextOptions ? { ...nextOptions } : null;
    this._paneView = new SessionBreaksPaneView(this);
  }

  get chart(): IChartApi | null {
    return this._chart;
  }

  get series(): ISeriesApi<SeriesType> | null {
    return this._series;
  }

  get times(): readonly Time[] {
    return this._times;
  }

  get nextTime(): Time | null {
    return this._nextTime;
  }

  get options(): BreakOptions {
    return this._options;
  }

  get nextOptions(): BreakOptions | null {
    return this._nextOptions;
  }

  applyOptions(partial: Partial<BreakOptions>): void {
    this._options = { ...this._options, ...partial };
    this.refresh();
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

  setBoundaries(times: number[], nextTime?: number | null): void {
    this._times = times as Time[];
    this._nextTime =
      nextTime != null && this._nextOptions != null ? (nextTime as Time) : null;
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

/** @deprecated Use SessionBreaksPrimitive */
export const DailySessionBreaksPrimitive = SessionBreaksPrimitive;
