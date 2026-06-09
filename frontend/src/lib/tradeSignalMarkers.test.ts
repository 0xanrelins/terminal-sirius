import { describe, expect, it } from "vitest";
import type { PaperEventMsg } from "../types";
import {
  paperFillMatchesSymbol,
  parseUnderlyingDirection,
  parseUnderlyingSymbol,
  tradeMarkerForFill,
  tradeMarkerKey,
} from "./tradeSignalMarkers";

function fillEvent(
  overrides: Partial<PaperEventMsg> = {}
): PaperEventMsg {
  return {
    type: "paper_event",
    kind: "fill",
    ts: 1_700_000_040_000_000_000,
    instrument_id: "POLY-YES",
    underlying: "BTCUSDT-PERP.BINANCE",
    ...overrides,
  };
}

describe("tradeSignalMarkers", () => {
  it("reads underlying_direction from payload", () => {
    expect(
      parseUnderlyingDirection(
        fillEvent({ underlying_direction: "LONG" })
      )
    ).toBe("LONG");
  });

  it("falls back to entry_signal_tooltip dir=", () => {
    expect(
      parseUnderlyingDirection(
        fillEvent({
          entry_signal_tooltip:
            "Entry signal @ market submit: dir=SHORT, sym=BTCUSDT-PERP.BINANCE",
        })
      )
    ).toBe("SHORT");
  });

  it("builds long marker at bar open time", () => {
    const m = tradeMarkerForFill(
      fillEvent({ underlying_direction: "LONG" }),
      "1m"
    );
    expect(m?.direction).toBe("LONG");
    expect(m?.time).toBe(1_700_000_040);
  });

  it("builds short marker at bar open time", () => {
    const m = tradeMarkerForFill(
      fillEvent({ underlying_direction: "SHORT" }),
      "1m"
    );
    expect(m?.direction).toBe("SHORT");
  });

  it("parses underlying from tooltip sym=", () => {
    expect(
      parseUnderlyingSymbol(
        fillEvent({
          underlying: undefined,
          entry_signal_tooltip:
            "Entry signal @ market submit: dir=LONG, sym=BTCUSDT-PERP.BINANCE",
        })
      )
    ).toBe("BTCUSDT-PERP.BINANCE");
  });

  it("matches chart symbol via underlying", () => {
    expect(
      paperFillMatchesSymbol(
        fillEvent({ underlying: "BTCUSDT-PERP.BINANCE" }),
        "BTCUSDT-PERP.BINANCE"
      )
    ).toBe(true);
    expect(
      paperFillMatchesSymbol(
        fillEvent({ underlying: "ETHUSDT-PERP.BINANCE" }),
        "BTCUSDT-PERP.BINANCE"
      )
    ).toBe(false);
  });

  it("dedupes by client_order_id", () => {
    expect(
      tradeMarkerKey(fillEvent({ client_order_id: "O-1" }))
    ).toBe("O-1");
  });
});
