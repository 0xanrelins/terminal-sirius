export const MAJOR_COLORS: Record<string, string> = {
  BTC: "#f7931a",
  ETH: "#627eea",
  SOL: "#14f195",
  DOGE: "#c2a633",
  XRP: "#38bdf8",
  HYPE: "#7c3aed",
  BNB: "#f0b90b",
};

/** Coins supported by sim/live engines (must match backend simulation.config ASSETS). */
export const TRADE_COINS = ["BTC", "ETH", "SOL", "XRP", "DOGE"] as const;
export type TradeCoin = (typeof TRADE_COINS)[number];

export const SIM_COINS = TRADE_COINS;
export type SimCoin = TradeCoin;
export const DEFAULT_SIM_COINS: SimCoin[] = [...SIM_COINS];

export const LIVE_COINS = TRADE_COINS;
export type LiveCoin = TradeCoin;
export const DEFAULT_LIVE_COINS: LiveCoin[] = ["XRP", "DOGE"];

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

const ALL_SIDES_SET = new Set(["long", "short"]);
const ALL_LEGS_SET = new Set(["l1", "l2", "s1", "s2"]);

export function aggregateAssetStats(
  selected: readonly string[],
  activeSidesOrByAsset?: ReadonlySet<string> | Record<string, AssetSideStats>,
  activeLegsOrByAssetSide?: ReadonlySet<string> | Record<string, Record<string, AssetSideStats>>,
  byAssetSideLeg?: Record<string, Record<string, Record<string, AssetSideStats>>>
): AggregatedStats {
  // Legacy call: aggregateAssetStats(selected, byAsset, byAssetSide)
  if (activeSidesOrByAsset === undefined || !(activeSidesOrByAsset instanceof Set)) {
    const byAsset = activeSidesOrByAsset as Record<string, AssetSideStats> | undefined;
    const byAssetSide = activeLegsOrByAssetSide as Record<string, Record<string, AssetSideStats>> | undefined;
    let total_bets = 0, wins = 0, total_pnl_usd = 0, open_bets = 0, long_open = 0, short_open = 0;
    for (const asset of selected) {
      const a = byAsset?.[asset];
      if (a) { total_bets += a.total_bets; wins += a.wins; total_pnl_usd += a.total_pnl_usd; open_bets += a.open_bets; }
      const lng = byAssetSide?.[asset]?.long;
      const sht = byAssetSide?.[asset]?.short;
      if (lng) long_open += lng.open_bets;
      if (sht) short_open += sht.open_bets;
    }
    const losses = total_bets - wins;
    return { total_bets, wins, losses, win_rate: total_bets > 0 ? Math.round((wins / total_bets) * 1000) / 10 : 0, total_pnl_usd: Math.round(total_pnl_usd * 10000) / 10000, open_bets, long_open, short_open };
  }

  const activeSides = activeSidesOrByAsset as ReadonlySet<string>;
  const activeLegs = activeLegsOrByAssetSide as ReadonlySet<string> ?? ALL_LEGS_SET;

  let total_bets = 0;
  let wins = 0;
  let total_pnl_usd = 0;
  let open_bets = 0;
  let long_open = 0;
  let short_open = 0;

  for (const asset of selected) {
    for (const side of (["long", "short"] as const)) {
      if (!activeSides.has(side)) continue;
      for (const legNum of [1, 2]) {
        const legKey = `${side === "long" ? "l" : "s"}${legNum}`;
        if (!activeLegs.has(legKey)) continue;
        const s = byAssetSideLeg?.[asset]?.[side]?.[String(legNum)];
        if (!s) continue;
        total_bets += s.total_bets;
        wins += s.wins;
        total_pnl_usd += s.total_pnl_usd;
        open_bets += s.open_bets;
        if (side === "long") long_open += s.open_bets;
        else short_open += s.open_bets;
      }
    }
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

export { ALL_SIDES_SET, ALL_LEGS_SET };
