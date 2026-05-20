/** Cash & futures session presets for the Market Times widget. */

export type MarketSessionKind = "cash" | "futures";

export type MarketSession = {
  id: string;
  city: string;
  exchange: string;
  timezone: string;
  kind: MarketSessionKind;
};

export type MarketRowState = {
  isOpen: boolean;
  localTimeHHmm: string;
  statusLabel: "Open" | "Closed";
  countdownLabel: string;
  /** 0–1 while open; null when closed */
  progress01: number | null;
};

export const MARKET_SESSIONS: MarketSession[] = [
  { id: "nyse", city: "New York", exchange: "NYSE", timezone: "America/New_York", kind: "cash" },
  { id: "lse", city: "London", exchange: "LSE", timezone: "Europe/London", kind: "cash" },
  { id: "tse", city: "Tokyo", exchange: "TSE", timezone: "Asia/Tokyo", kind: "cash" },
  { id: "asx", city: "Sydney", exchange: "ASX", timezone: "Australia/Sydney", kind: "cash" },
  { id: "cme", city: "US Futures", exchange: "CME", timezone: "America/New_York", kind: "futures" },
];

type Segment = { open: number; close: number };

const WEEKDAY_SHORT: Record<string, number> = {
  Sun: 0,
  Mon: 1,
  Tue: 2,
  Wed: 3,
  Thu: 4,
  Fri: 5,
  Sat: 6,
};

const CASH_SEGMENTS: Record<string, Segment[]> = {
  nyse: [{ open: 9 * 60 + 30, close: 16 * 60 }],
  lse: [{ open: 8 * 60, close: 16 * 60 + 30 }],
  tse: [
    { open: 9 * 60, close: 11 * 60 + 30 },
    { open: 12 * 60 + 30, close: 15 * 60 },
  ],
  asx: [{ open: 10 * 60, close: 16 * 60 }],
};

const CME_MAINT_OPEN = 17 * 60;
const CME_MAINT_CLOSE = 18 * 60;
const CME_WEEK_OPEN = 18 * 60; // Sunday 18:00 ET
const CME_WEEK_CLOSE = 17 * 60; // Friday 17:00 ET

type ZonedCtx = {
  weekday: number;
  minutes: number;
  hhmm: string;
};

function zonedContext(date: Date, timeZone: string): ZonedCtx {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone,
    weekday: "short",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).formatToParts(date);

  const weekday = WEEKDAY_SHORT[parts.find((p) => p.type === "weekday")!.value];
  const hour = Number(parts.find((p) => p.type === "hour")!.value);
  const minute = Number(parts.find((p) => p.type === "minute")!.value);
  const minutes = hour * 60 + minute;
  const hhmm = `${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}`;
  return { weekday, minutes, hhmm };
}

function isCashWeekday(weekday: number): boolean {
  return weekday >= 1 && weekday <= 5;
}

function inSegment(minutes: number, seg: Segment): boolean {
  return minutes >= seg.open && minutes < seg.close;
}

function activeCashSegment(segments: Segment[], minutes: number): Segment | null {
  for (const seg of segments) {
    if (inSegment(minutes, seg)) return seg;
  }
  return null;
}

function msUntil(from: Date, to: Date): number {
  return Math.max(0, to.getTime() - from.getTime());
}

function formatHm(ms: number): string {
  const totalMin = Math.max(0, Math.ceil(ms / 60_000));
  const h = Math.floor(totalMin / 60);
  const m = totalMin % 60;
  return `${h}h ${m}m`;
}

function formatCountdown(
  ms: number,
  prefix: string,
  opts?: { tmrw?: boolean }
): string {
  const body = formatHm(ms);
  if (opts?.tmrw) return `${prefix} tmrw ${body}`;
  return `${prefix} ${body}`;
}

function isTomorrowOpen(now: Date, openAt: Date, tz: string): boolean {
  const nowCtx = zonedContext(now, tz);
  const openCtx = zonedContext(openAt, tz);
  if (openCtx.weekday !== (nowCtx.weekday + 1) % 7) return false;
  const dayMs = 24 * 60 * 60_000;
  const diff = openAt.getTime() - now.getTime();
  return diff > 0 && diff < dayMs * 1.5;
}

function scanForward(
  now: Date,
  tz: string,
  match: (ctx: ZonedCtx) => boolean,
  maxMinutes = 8 * 24 * 60
): Date {
  let t = now.getTime();
  const end = t + maxMinutes * 60_000;
  while (t <= end) {
    const d = new Date(t);
    if (match(zonedContext(d, tz))) return d;
    t += 60_000;
  }
  return new Date(end);
}

function scanBackward(
  now: Date,
  tz: string,
  match: (ctx: ZonedCtx) => boolean,
  maxMinutes = 8 * 24 * 60
): Date {
  let t = now.getTime();
  const start = t - maxMinutes * 60_000;
  while (t >= start) {
    const d = new Date(t);
    if (match(zonedContext(d, tz))) return d;
    t -= 60_000;
  }
  return new Date(start);
}

function nextCashOpen(now: Date, sessionId: string, tz: string): Date {
  const segments = CASH_SEGMENTS[sessionId]!;
  return scanForward(now, tz, (ctx) => {
    if (!isCashWeekday(ctx.weekday)) return false;
    return segments.some((seg) => ctx.minutes === seg.open);
  });
}

function nextCashClose(now: Date, sessionId: string, tz: string): Date {
  const segments = CASH_SEGMENTS[sessionId]!;
  return scanForward(now, tz, (ctx) => {
    if (!isCashWeekday(ctx.weekday)) return false;
    return segments.some((seg) => ctx.minutes === seg.close);
  });
}

function cmeIsInMaintenance(ctx: ZonedCtx): boolean {
  if (ctx.weekday === 5 && ctx.minutes >= CME_WEEK_CLOSE) return true;
  if (ctx.weekday === 6) return true;
  if (ctx.weekday === 0 && ctx.minutes < CME_WEEK_OPEN) return true;
  if (ctx.weekday >= 1 && ctx.weekday <= 5) {
    if (ctx.minutes >= CME_MAINT_OPEN && ctx.minutes < CME_MAINT_CLOSE) return true;
  }
  return false;
}

function cmeIsOpen(ctx: ZonedCtx): boolean {
  if (ctx.weekday === 6) return false;
  if (ctx.weekday === 0 && ctx.minutes < CME_WEEK_OPEN) return false;
  if (ctx.weekday === 5 && ctx.minutes >= CME_WEEK_CLOSE) return false;
  if (cmeIsInMaintenance(ctx)) return false;
  return true;
}

function nextCmeMaintStart(now: Date, tz: string): Date {
  return scanForward(now, tz, (ctx) => {
    if (ctx.weekday === 5 && ctx.minutes === CME_WEEK_CLOSE) return true;
    if (ctx.weekday >= 0 && ctx.weekday <= 5 && ctx.minutes === CME_MAINT_OPEN) {
      if (ctx.weekday === 5) return true;
      if (ctx.weekday >= 1 && ctx.weekday <= 4) return true;
      if (ctx.weekday === 0 && ctx.minutes >= CME_WEEK_OPEN) return true;
    }
    return false;
  });
}

function nextCmeResume(now: Date, tz: string): Date {
  return scanForward(now, tz, (ctx) => {
    if (ctx.weekday === 0 && ctx.minutes === CME_WEEK_OPEN) return true;
    if (ctx.weekday >= 1 && ctx.weekday <= 4 && ctx.minutes === CME_MAINT_CLOSE) return true;
    return false;
  });
}

function cashProgress(segments: Segment[], minutes: number): number {
  const seg = activeCashSegment(segments, minutes);
  if (!seg) return 0;
  const span = seg.close - seg.open;
  if (span <= 0) return 0;
  return Math.min(1, Math.max(0, (minutes - seg.open) / span));
}

function getCashState(session: MarketSession, now: Date): MarketRowState {
  const tz = session.timezone;
  const ctx = zonedContext(now, tz);
  const segments = CASH_SEGMENTS[session.id]!;
  const active = isCashWeekday(ctx.weekday)
    ? activeCashSegment(segments, ctx.minutes)
    : null;

  if (active) {
    const closeAt = nextCashClose(now, session.id, tz);
    return {
      isOpen: true,
      localTimeHHmm: ctx.hhmm,
      statusLabel: "Open",
      countdownLabel: formatCountdown(msUntil(now, closeAt), "Closes in"),
      progress01: cashProgress(segments, ctx.minutes),
    };
  }

  const openAt = nextCashOpen(now, session.id, tz);
  const tmrw = isTomorrowOpen(now, openAt, tz);
  return {
    isOpen: false,
    localTimeHHmm: ctx.hhmm,
    statusLabel: "Closed",
    countdownLabel: formatCountdown(msUntil(now, openAt), "Opens in", { tmrw }),
    progress01: null,
  };
}

function cmeProgress01(now: Date, tz: string): number {
  const resumeAt = scanBackward(now, tz, (ctx) => {
    if (ctx.weekday === 0 && ctx.minutes === CME_WEEK_OPEN) return true;
    if (ctx.weekday >= 1 && ctx.weekday <= 4 && ctx.minutes === CME_MAINT_CLOSE) return true;
    if (ctx.weekday === 5 && ctx.minutes === CME_MAINT_CLOSE) return true;
    return false;
  });
  const maintAt = nextCmeMaintStart(now, tz);
  const span = maintAt.getTime() - resumeAt.getTime();
  if (span <= 0) return 0;
  return Math.min(1, Math.max(0, msUntil(resumeAt, now) / span));
}

function getCmeState(now: Date): MarketRowState {
  const tz = "America/New_York";
  const ctx = zonedContext(now, tz);

  if (cmeIsOpen(ctx)) {
    const maintAt = nextCmeMaintStart(now, tz);
    return {
      isOpen: true,
      localTimeHHmm: ctx.hhmm,
      statusLabel: "Open",
      countdownLabel: formatCountdown(msUntil(now, maintAt), "Maint. in"),
      progress01: cmeProgress01(now, tz),
    };
  }

  if (cmeIsInMaintenance(ctx)) {
    const resumeAt = nextCmeResume(now, tz);
    return {
      isOpen: false,
      localTimeHHmm: ctx.hhmm,
      statusLabel: "Closed",
      countdownLabel: formatCountdown(msUntil(now, resumeAt), "Opens in"),
      progress01: null,
    };
  }

  const openAt = nextCmeResume(now, tz);
  const tmrw = isTomorrowOpen(now, openAt, tz);
  return {
    isOpen: false,
    localTimeHHmm: ctx.hhmm,
    statusLabel: "Closed",
    countdownLabel: formatCountdown(msUntil(now, openAt), "Opens in", { tmrw }),
    progress01: null,
  };
}

export function getMarketRowState(session: MarketSession, now: Date): MarketRowState {
  if (session.kind === "futures") return getCmeState(now);
  return getCashState(session, now);
}
