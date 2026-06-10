import { POST_EVENT_COINS, type PostEventCoin } from "./liqPostEventChart";

export type VerdictWinner = "liquidation" | "recovery" | "neutral";
export type VerdictStatus = "completed" | "expired";
export type VerdictSide = "LONG" | "SHORT";

export type LiquidationVerdictRow = {
  event_id: string;
  symbol: string;
  liq_side: VerdictSide;
  notional: number;
  event_price: number;
  winner: VerdictWinner;
  liq_move_pct: number;
  recovery_move_pct: number;
  dominance_ratio: number;
  time_to_dominance_sec: number;
  area_bias: number;
  status: VerdictStatus;
  completion_reason?: string;
  event_time: number;
  ts?: number;
};

export type LiquidationVerdictMsg = {
  type: "liquidation_verdict";
  verdict: LiquidationVerdictRow;
  tape: LiquidationVerdictRow[];
  pending?: number;
  pending_by_symbol?: Record<string, number>;
};

export type LiquidationVerdictResponse = {
  verdicts: LiquidationVerdictRow[];
};

export type LiquidationVerdictStats = {
  count: number;
  completed: number;
  expired: number;
  recovery_rate: number;
  avg_dominance: number;
  avg_time: number;
  avg_area: number;
};

const COIN_SET = new Set<string>(POST_EVENT_COINS);

const EMPTY_STATS: LiquidationVerdictStats = {
  count: 0,
  completed: 0,
  expired: 0,
  recovery_rate: 0,
  avg_dominance: 0,
  avg_time: 0,
  avg_area: 0,
};

export function normalizeVerdictCoins(coins: unknown): PostEventCoin[] {
  if (!Array.isArray(coins)) return ["BTC"];
  const picked = coins.filter(
    (c): c is PostEventCoin => typeof c === "string" && COIN_SET.has(c)
  );
  return picked.length > 0 ? picked : ["BTC"];
}

export function normalizeVerdictSides(sides: unknown): VerdictSide[] {
  if (!Array.isArray(sides)) return ["LONG", "SHORT"];
  const picked = sides.filter(
    (s): s is VerdictSide => s === "LONG" || s === "SHORT"
  );
  return picked.length > 0 ? picked : ["LONG", "SHORT"];
}

function verdictQueryParams(params: {
  coins: PostEventCoin[];
  sides: VerdictSide[];
  limit?: number;
}): URLSearchParams {
  const q = new URLSearchParams({
    symbols: params.coins.join(","),
    sides: params.sides.join(","),
    min_notional: "0",
  });
  if (params.limit !== undefined) {
    q.set("limit", String(params.limit));
  }
  return q;
}

/** Full persisted list (limit=0 → no cap on DB rows). */
export function verdictFetchUrl(params: {
  coins: PostEventCoin[];
  sides: VerdictSide[];
  limit?: number;
}): string {
  const q = verdictQueryParams({ ...params, limit: params.limit ?? 0 });
  return `/liq-verdict/recent?${q}`;
}

/** Cumulative aggregates over all persisted verdicts (header cards). */
export function verdictStatsUrl(params: {
  coins: PostEventCoin[];
  sides: VerdictSide[];
}): string {
  return `/liq-verdict/stats?${verdictQueryParams(params)}`;
}

export function emptyVerdictStats(): LiquidationVerdictStats {
  return { ...EMPTY_STATS };
}

export function formatVerdictNotional(n: number): string {
  if (n >= 1_000_000) return `$${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `$${Math.round(n / 1_000)}k`;
  return `$${Math.round(n)}`;
}

export function winnerLabel(winner: VerdictWinner): string {
  if (winner === "recovery") return "recovery";
  if (winner === "liquidation") return "liq";
  return "neutral";
}

export function completionReasonLabel(reason?: string): string {
  if (reason === "liq_threshold") return "L≥0.2%";
  if (reason === "recovery_threshold") return "R≥0.2%";
  return "—";
}
