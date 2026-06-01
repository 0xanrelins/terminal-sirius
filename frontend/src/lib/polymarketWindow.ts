/** Rolling 15m Polymarket contract window (matches backend WINDOW_SEC). */
export const POLYMARKET_WINDOW_SEC = 900;

export function polymarketWindowStart(timeSec: number): number {
  return Math.floor(timeSec / POLYMARKET_WINDOW_SEC) * POLYMARKET_WINDOW_SEC;
}
