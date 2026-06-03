import { describe, expect, it } from "vitest";
import {
  computeUtcDayBoundaries,
  computeUtcIntervalBoundaries,
  computeUtcIntervalBoundariesWithNext,
  coordinateForTime,
  nextUtcIntervalBoundary,
  type BarTime,
} from "./dailySessionBreaks";

function bars(times: number[]): BarTime[] {
  return times.map((time) => ({ time }));
}

describe("computeUtcIntervalBoundaries", () => {
  it("returns empty for fewer than 2 bars", () => {
    expect(computeUtcIntervalBoundaries([], 900)).toEqual([]);
    expect(computeUtcIntervalBoundaries(bars([100]), 900)).toEqual([]);
  });

  it("places 15-minute UTC epoch boundaries between first and last bar", () => {
    const sessionSec = 15 * 60;
    const first = 1_700_000_000;
    const last = first + sessionSec * 4;
    const boundaries = computeUtcIntervalBoundaries(bars([first, last]), sessionSec);

    expect(boundaries.length).toBeGreaterThan(0);
    for (const t of boundaries) {
      expect(t).toBeGreaterThan(first);
      expect(t).toBeLessThan(last);
      expect(t % sessionSec).toBe(0);
    }
    expect(boundaries[0]).toBeGreaterThan(first);
    expect(boundaries[boundaries.length - 1]).toBeLessThan(last);
  });

  it("increments by interval seconds", () => {
    const sessionSec = 60;
    const first = 0;
    const last = sessionSec * 5;
    const boundaries = computeUtcIntervalBoundaries(bars([first, last]), sessionSec);
    expect(boundaries).toEqual([60, 120, 180, 240]);
  });
});

describe("nextUtcIntervalBoundary", () => {
  it("returns the next epoch-aligned bucket after anchor", () => {
    const sessionSec = 15 * 60;
    const bucket =
      Math.floor(1_700_000_000 / sessionSec) * sessionSec;
    expect(nextUtcIntervalBoundary(bucket + 1, sessionSec)).toBe(
      bucket + sessionSec
    );
    expect(nextUtcIntervalBoundary(bucket + sessionSec - 1, sessionSec)).toBe(
      bucket + sessionSec
    );
  });
});

describe("computeUtcIntervalBoundariesWithNext", () => {
  it("includes next boundary after last bar / now", () => {
    const sessionSec = 15 * 60;
    const first = 1_700_000_000;
    const last = first + sessionSec * 2;
    const nowSec = last + sessionSec / 2;
    const { boundaries, next } = computeUtcIntervalBoundariesWithNext(
      bars([first, last]),
      sessionSec,
      nowSec
    );
    expect(boundaries.length).toBeGreaterThan(0);
    expect(next).not.toBeNull();
    expect(next!).toBeGreaterThan(last);
    expect(next! % sessionSec).toBe(0);
  });

  it("returns next only when no bars loaded", () => {
    const sessionSec = 900;
    const nowSec = 1_700_000_100;
    const { boundaries, next } = computeUtcIntervalBoundariesWithNext(
      [],
      sessionSec,
      nowSec
    );
    expect(boundaries).toEqual([]);
    expect(next).toBe(nextUtcIntervalBoundary(nowSec, sessionSec));
  });
});

describe("coordinateForTime", () => {
  it("extrapolates x when timeToCoordinate returns null", () => {
    const timeScale = {
      timeToCoordinate: (time: number) => {
        if (time === 100) return 10;
        if (time === 160) return 40;
        return null;
      },
    };
    const series = {
      data: () => [{ time: 100 }, { time: 160 }],
    };
    expect(coordinateForTime(timeScale as never, series as never, 220)).toBe(70);
  });
});

describe("computeUtcDayBoundaries", () => {
  it("returns UTC midnight lines between bars (calendar algorithm)", () => {
    const dayStart = Date.UTC(2024, 0, 15, 0, 0, 0) / 1000;
    const first = dayStart + 3600;
    const last = dayStart + SECONDS_PER_DAY * 2;
    const boundaries = computeUtcDayBoundaries(bars([first, last]));
    expect(boundaries.length).toBeGreaterThanOrEqual(1);
    for (const t of boundaries) {
      const d = new Date(t * 1000);
      expect(d.getUTCHours()).toBe(0);
      expect(d.getUTCMinutes()).toBe(0);
    }
  });
});

const SECONDS_PER_DAY = 86_400;
