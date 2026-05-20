import { describe, expect, it } from "vitest";
import {
  getMarketRowState,
  MARKET_SESSIONS,
} from "./marketSessions";

const nyse = MARKET_SESSIONS.find((s) => s.id === "nyse")!;
const cme = MARKET_SESSIONS.find((s) => s.id === "cme")!;

describe("getMarketRowState", () => {
  it("NYSE is open mid-session on a weekday", () => {
    // Wed 2025-01-15 10:00 America/New_York (EST)
    const now = new Date("2025-01-15T15:00:00Z");
    const row = getMarketRowState(nyse, now);
    expect(row.isOpen).toBe(true);
    expect(row.statusLabel).toBe("Open");
    expect(row.countdownLabel).toMatch(/^Closes in \d+h \d+m$/);
    expect(row.progress01).not.toBeNull();
    expect(row.progress01!).toBeGreaterThan(0);
    expect(row.progress01!).toBeLessThan(1);
  });

  it("NYSE is closed before the open", () => {
    // Wed 2025-01-15 08:00 America/New_York
    const now = new Date("2025-01-15T13:00:00Z");
    const row = getMarketRowState(nyse, now);
    expect(row.isOpen).toBe(false);
    expect(row.statusLabel).toBe("Closed");
    expect(row.countdownLabel).toMatch(/^Opens in \d+h \d+m$/);
    expect(row.progress01).toBeNull();
  });

  it("cash markets are closed on Saturday", () => {
    // Sat 2025-01-18 10:00 America/New_York
    const now = new Date("2025-01-18T15:00:00Z");
    for (const session of MARKET_SESSIONS.filter((s) => s.kind === "cash")) {
      const row = getMarketRowState(session, now);
      expect(row.isOpen).toBe(false);
      expect(row.statusLabel).toBe("Closed");
    }
  });

  it("CME shows maintenance countdown during daily halt", () => {
    // Wed 2025-01-15 17:30 America/New_York
    const now = new Date("2025-01-15T22:30:00Z");
    const row = getMarketRowState(cme, now);
    expect(row.isOpen).toBe(false);
    expect(row.countdownLabel).toMatch(/^Opens in \d+h \d+m$/);
  });

  it("CME is open with Maint. countdown while trading", () => {
    // Wed 2025-01-15 10:00 America/New_York
    const now = new Date("2025-01-15T15:00:00Z");
    const row = getMarketRowState(cme, now);
    expect(row.isOpen).toBe(true);
    expect(row.countdownLabel).toMatch(/^Maint\. in \d+h \d+m$/);
  });
});
