import type { LineData, UTCTimestamp } from "lightweight-charts";

export const POST_EVENT_COINS = ["BTC", "ETH", "SOL", "XRP", "DOGE"] as const;
export type PostEventCoin = (typeof POST_EVENT_COINS)[number];
export const DEFAULT_POST_EVENT_COINS: PostEventCoin[] = ["BTC"];
export type PostEventSide = "LONG" | "SHORT";
export type PostEventChartInterval = "30s";
export const POST_EVENT_INTERVAL: PostEventChartInterval = "30s";
export type PostEventSessionStatus = "active" | "completed";

export const SYNTHETIC_BASE_EPOCH = 1_700_000_000;
export const WINDOW_SEC = 1800;
export const COMPLETED_COLOR = "#787888";

export const ACTIVE_LINE_COLORS = [
  "#f7931a",
  "#627eea",
  "#14f195",
  "#38bdf8",
  "#c2a633",
  "#a78bfa",
  "#22d3ee",
  "#f472b6",
];

export type PostEventPoint = {
  elapsed_sec: number;
  pct: number;
};

export type PostEventSession = {
  session_id: string;
  symbol: string;
  side: PostEventSide;
  notional: number;
  anchor_price: number;
  event_time: number;
  status: PostEventSessionStatus;
  points: PostEventPoint[];
};

export type PostEventSessionsResponse = {
  sessions: PostEventSession[];
};

const COIN_SET = new Set<string>(POST_EVENT_COINS);

export function normalizePostEventCoins(coins: unknown): PostEventCoin[] {
  if (!Array.isArray(coins)) return [...DEFAULT_POST_EVENT_COINS];
  const picked = coins.filter(
    (c): c is PostEventCoin => typeof c === "string" && COIN_SET.has(c)
  );
  return picked.length > 0 ? picked : [...DEFAULT_POST_EVENT_COINS];
}

export function normalizePostEventSides(sides: unknown): PostEventSide[] {
  if (!Array.isArray(sides)) return ["LONG", "SHORT"];
  const picked = sides.filter(
    (s): s is PostEventSide => s === "LONG" || s === "SHORT"
  );
  return picked.length > 0 ? picked : ["LONG", "SHORT"];
}

export function normalizePostEventInterval(_interval: unknown): PostEventChartInterval {
  return POST_EVENT_INTERVAL;
}

export function elapsedToChartTime(elapsedSec: number): UTCTimestamp {
  return (SYNTHETIC_BASE_EPOCH + elapsedSec) as UTCTimestamp;
}

export function chartTimeToMinuteLabel(time: number): string {
  const elapsed = Math.max(0, Math.min(WINDOW_SEC, time - SYNTHETIC_BASE_EPOCH));
  const min = Math.round(elapsed / 60);
  return `${min}m`;
}

export function pointsToLineData(points: PostEventPoint[]): LineData<UTCTimestamp>[] {
  const byTime = new Map<number, number>();
  for (const p of points) {
    byTime.set(p.elapsed_sec, p.pct);
  }
  return Array.from(byTime.entries())
    .sort((a, b) => a[0] - b[0])
    .map(([elapsed, pct]) => ({
      time: elapsedToChartTime(elapsed),
      value: pct,
    }));
}

export function sessionLineColor(
  session: PostEventSession,
  colorIndex: number
): string {
  if (session.status === "completed") return COMPLETED_COLOR;
  return ACTIVE_LINE_COLORS[colorIndex % ACTIVE_LINE_COLORS.length];
}

/** Stable color index for active sessions by first-seen order. */
export function assignActiveColorIndices(
  sessions: PostEventSession[],
  prev: Map<string, number>
): Map<string, number> {
  const out = new Map<string, number>();
  let next = 0;
  for (const s of sessions) {
    if (s.status !== "active") continue;
    const existing = prev.get(s.session_id);
    if (existing !== undefined) {
      out.set(s.session_id, existing);
      next = Math.max(next, existing + 1);
    }
  }
  for (const s of sessions) {
    if (s.status !== "active") continue;
    if (out.has(s.session_id)) continue;
    out.set(s.session_id, next);
    next += 1;
  }
  return out;
}

export function sessionsFetchUrl(params: {
  coins: PostEventCoin[];
  minNotional: number;
  sides: PostEventSide[];
}): string {
  const q = new URLSearchParams({
    symbols: params.coins.join(","),
    interval: POST_EVENT_INTERVAL,
    min_notional: String(params.minNotional),
    sides: params.sides.join(","),
  });
  return `/liq-post-event/sessions?${q}`;
}

export function formatPostEventNotional(n: number): string {
  if (n >= 1_000_000) return `$${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `$${Math.round(n / 1_000)}k`;
  return `$${Math.round(n)}`;
}
