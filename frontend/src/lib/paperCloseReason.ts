const RECOVERY_EXIT_REASON = /^recovery_exit_(\d+(?:p\d+)?)$/;
const LIQUIDATION_EXIT_REASON = /^liquidation_exit_(\d+(?:p\d+)?)$/;
const TIME_STOP_REASON = /^time_stop_(\d+)s$/;

function formatRecoveryPct(pct: number): string {
  return `${parseFloat(pct.toFixed(6))}%`;
}

export function recoveryExitPctFromReason(reason: string | null | undefined): number | null {
  if (!reason) return null;
  const match = reason.match(RECOVERY_EXIT_REASON);
  if (!match) return null;
  const pct = parseFloat(match[1].replace("p", "."));
  return Number.isFinite(pct) ? pct : null;
}

function liquidationExitPctFromReason(reason: string | null | undefined): number | null {
  if (!reason) return null;
  const match = reason.match(LIQUIDATION_EXIT_REASON);
  if (!match) return null;
  const pct = parseFloat(match[1].replace("p", "."));
  return Number.isFinite(pct) ? pct : null;
}

export function closeReasonLabel(reason: string | null | undefined): string {
  if (!reason) return "—";
  const recoveryPct = recoveryExitPctFromReason(reason);
  if (recoveryPct !== null) return `REC ${formatRecoveryPct(recoveryPct)}`;
  const liquidationPct = liquidationExitPctFromReason(reason);
  if (liquidationPct !== null) return `LIQ ${formatRecoveryPct(liquidationPct)}`;
  const timeStop = reason.match(TIME_STOP_REASON);
  if (timeStop) return `TIME ${timeStop[1]}s`;
  if (reason === "settlement_expiry") return "EXPIRY";
  return reason.replace(/_/g, " ").toUpperCase();
}

export function isRecoveryExitReason(reason: string | null | undefined): boolean {
  return recoveryExitPctFromReason(reason) !== null;
}

export function isSettlementCloseReason(reason: string | null | undefined): boolean {
  return reason === "settlement_expiry";
}
