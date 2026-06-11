import { describe, expect, it } from "vitest";
import { paperEventInRun, paperPositionRowKey } from "./paperRun";

describe("paperRun", () => {
  it("matches events stamped with run_started_ts", () => {
    expect(paperEventInRun({ run_started_ts: 100, ts: 200 }, 100)).toBe(true);
    expect(paperEventInRun({ run_started_ts: 99, ts: 200 }, 100)).toBe(false);
  });

  it("falls back to event ts for legacy rows", () => {
    expect(paperEventInRun({ ts: 150 }, 100)).toBe(true);
    expect(paperEventInRun({ ts: 50 }, 100)).toBe(false);
  });

  it("prefers position_id for table row keys", () => {
    expect(
      paperPositionRowKey({
        position_id: "P-1",
        instrument_id: "0xabc.POLYMARKET",
        opened_ts: 1,
      }),
    ).toBe("P-1");
  });
});
