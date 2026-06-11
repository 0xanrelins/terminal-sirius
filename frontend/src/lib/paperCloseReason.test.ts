import { describe, expect, it } from "vitest";
import {
  closeReasonLabel,
  isRecoveryExitReason,
  recoveryExitPctFromReason,
} from "./paperCloseReason";

describe("paperCloseReason", () => {
  it("parses recovery exit reasons", () => {
    expect(recoveryExitPctFromReason("recovery_exit_0p2")).toBe(0.2);
    expect(recoveryExitPctFromReason("recovery_exit_0p25")).toBe(0.25);
    expect(recoveryExitPctFromReason("recovery_exit_1")).toBe(1);
    expect(recoveryExitPctFromReason("settlement_expiry")).toBeNull();
  });

  it("formats recovery and settlement labels", () => {
    expect(closeReasonLabel("recovery_exit_0p2")).toBe("REC 0.2%");
    expect(closeReasonLabel("recovery_exit_0p5")).toBe("REC 0.5%");
    expect(closeReasonLabel("settlement_expiry")).toBe("EXPIRY");
    expect(isRecoveryExitReason("recovery_exit_0p2")).toBe(true);
  });
});
