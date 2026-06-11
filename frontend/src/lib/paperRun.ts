/** Match paper WS/REST rows to the current Nautilus paper run. */
export function paperEventInRun(
  event: { run_started_ts?: number; ts: number },
  runStartedTs: number | null | undefined,
): boolean {
  if (runStartedTs == null) return true;
  if (event.run_started_ts != null) return event.run_started_ts === runStartedTs;
  return event.ts >= runStartedTs;
}

export function paperPositionRowKey(position: {
  position_id?: string | null;
  instrument_id: string;
  closed_ts?: number;
  opened_ts?: number;
}): string {
  if (position.position_id) return position.position_id;
  const ts = position.closed_ts ?? position.opened_ts ?? 0;
  return `${position.instrument_id}-${ts}`;
}
