export const MAJOR_COLORS: Record<string, string> = {
  BTC: "#f7931a",
  ETH: "#627eea",
  SOL: "#14f195",
  DOGE: "#c2a633",
  XRP: "#38bdf8",
  HYPE: "#7c3aed",
  BNB: "#f0b90b",
};

export const SIM_COINS = ["BTC", "ETH", "SOL", "XRP", "DOGE"] as const;
export type SimCoin = (typeof SIM_COINS)[number];
export const DEFAULT_SIM_COINS: SimCoin[] = [...SIM_COINS];

export const LIVE_COINS = ["SOL", "DOGE"] as const;
export type LiveCoin = (typeof LIVE_COINS)[number];
export const DEFAULT_LIVE_COINS: LiveCoin[] = [...LIVE_COINS];

export type AssetSideStats = {
  total_bets: number;
  wins: number;
  losses: number;
  win_rate: number;
  total_pnl_usd: number;
  open_bets: number;
};

export type AggregatedStats = {
  total_bets: number;
  wins: number;
  losses: number;
  win_rate: number;
  total_pnl_usd: number;
  open_bets: number;
  long_open: number;
  short_open: number;
};

export function normalizeCoins<T extends string>(
  coins: unknown,
  allowed: readonly T[],
  defaultCoins: readonly T[]
): T[] {
  const allowedSet = new Set<string>(allowed);
  if (!Array.isArray(coins)) return [...defaultCoins];
  const picked = coins.filter(
    (c): c is T => typeof c === "string" && allowedSet.has(c)
  );
  return picked.length > 0 ? picked : [...defaultCoins];
}

export function formatPairs(coins: readonly string[]): string {
  return coins.join("/");
}

export function aggregateAssetStats(
  selected: readonly string[],
  byAsset?: Record<string, AssetSideStats>,
  byAssetSide?: Record<string, Record<string, AssetSideStats>>
): AggregatedStats {
  let total_bets = 0;
  let wins = 0;
  let total_pnl_usd = 0;
  let open_bets = 0;
  let long_open = 0;
  let short_open = 0;

  for (const asset of selected) {
    const a = byAsset?.[asset];
    if (a) {
      total_bets += a.total_bets;
      wins += a.wins;
      total_pnl_usd += a.total_pnl_usd;
      open_bets += a.open_bets;
    }
    const long = byAssetSide?.[asset]?.long;
    const short = byAssetSide?.[asset]?.short;
    if (long) long_open += long.open_bets;
    if (short) short_open += short.open_bets;
  }

  const losses = total_bets - wins;
  return {
    total_bets,
    wins,
    losses,
    win_rate: total_bets > 0 ? Math.round((wins / total_bets) * 1000) / 10 : 0,
    total_pnl_usd: Math.round(total_pnl_usd * 10000) / 10000,
    open_bets,
    long_open,
    short_open,
  };
}
