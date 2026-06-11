import { describe, expect, it } from "vitest";
import type { PaperEventMsg } from "../types";
import {
  paperEventMatchesChart,
  paperFillMatchesSymbol,
  parseCloseDirection,
  parseUnderlyingDirection,
  parseUnderlyingSymbol,
  tradeMarkerForFill,
  tradeMarkerForPaperEvent,
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

function closeEvent(
  overrides: Partial<PaperEventMsg> = {}
): PaperEventMsg {
  return {
    type: "paper_event",
    kind: "position_close",
    ts: 1_700_000_120_000_000_000,
    instrument_id: "POLY-YES",
    market_series: "btc-updown-15m",
    market_outcome: "YES",
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

  it("falls back to generic entry_signal_tooltip direction=", () => {
    expect(
      parseUnderlyingDirection(
        fillEvent({
          entry_signal_tooltip:
            "Entry signal @ market submit: direction=LONG, strategy_id=fresh_paper",
        })
      )
    ).toBe("LONG");
  });

  it("builds long entry marker at bar open time", () => {
    const m = tradeMarkerForPaperEvent(
      fillEvent({ underlying_direction: "LONG" }),
      "1m"
    );
    expect(m?.direction).toBe("LONG");
    expect(m?.action).toBe("entry");
    expect(m?.time).toBe(1_700_000_040);
  });

  it("builds short entry marker at bar open time", () => {
    const m = tradeMarkerForPaperEvent(
      fillEvent({ underlying_direction: "SHORT" }),
      "1m"
    );
    expect(m?.direction).toBe("SHORT");
    expect(m?.action).toBe("entry");
  });

  it("builds exit marker from position_close", () => {
    const m = tradeMarkerForPaperEvent(closeEvent(), "1s");
    expect(m?.action).toBe("exit");
    expect(m?.direction).toBe("LONG");
    expect(m?.time).toBe(1_700_000_120);
  });

  it("parses close direction from market_outcome", () => {
    expect(parseCloseDirection(closeEvent())).toBe("LONG");
    expect(
      parseCloseDirection(closeEvent({ market_outcome: "NO" }))
    ).toBe("SHORT");
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

  it("parses underlying from generic tooltip symbol=", () => {
    expect(
      parseUnderlyingSymbol(
        fillEvent({
          underlying: undefined,
          entry_signal_tooltip:
            "Entry signal @ market submit: strategy_id=fresh_paper, symbol=BTCUSDT-PERP.BINANCE",
        })
      )
    ).toBe("BTCUSDT-PERP.BINANCE");
  });

  it("matches chart symbol via underlying on fills", () => {
    expect(
      paperEventMatchesChart(
        fillEvent({ underlying: "BTCUSDT-PERP.BINANCE" }),
        "BTCUSDT-PERP.BINANCE"
      )
    ).toBe(true);
    expect(
      paperEventMatchesChart(
        fillEvent({ underlying: "ETHUSDT-PERP.BINANCE" }),
        "BTCUSDT-PERP.BINANCE"
      )
    ).toBe(false);
  });

  it("matches chart symbol via market_series on position_close", () => {
    expect(
      paperEventMatchesChart(closeEvent(), "BTCUSDT-PERP.BINANCE")
    ).toBe(true);
    expect(
      paperEventMatchesChart(
        closeEvent({ market_series: "eth-updown-15m" }),
        "BTCUSDT-PERP.BINANCE"
      )
    ).toBe(false);
  });

  it("dedupes by client_order_id", () => {
    expect(
      tradeMarkerKey(fillEvent({ client_order_id: "O-1" }))
    ).toBe("O-1");
  });

  it("keeps deprecated aliases working", () => {
    expect(
      paperFillMatchesSymbol(
        fillEvent({ underlying: "BTCUSDT-PERP.BINANCE" }),
        "BTCUSDT-PERP.BINANCE"
      )
    ).toBe(true);
    expect(
      tradeMarkerForFill(fillEvent({ underlying_direction: "LONG" }), "1m")
        ?.action
    ).toBe("entry");
  });
});
