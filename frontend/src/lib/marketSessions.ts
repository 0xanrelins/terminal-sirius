/** Cash & futures session presets for the Market Times widget. */

import { isExchangeHoliday } from "./exchangeHolidays";

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

const MAX_DAY_LOOKAHEAD = 14;

type ZonedCtx = {
  weekday: number;
  minutes: number;
  hhmm: string;
  /** YYYY-MM-DD in the market timezone */
  dateKey: string;
};

const formatterCache = new Map<string, Intl.DateTimeFormat>();

function getFormatter(timeZone: string): Intl.DateTimeFormat {
  let fmt = formatterCache.get(timeZone);
  if (!fmt) {
    fmt = new Intl.DateTimeFormat("en-US", {
      timeZone,
      weekday: "short",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    });
    formatterCache.set(timeZone, fmt);
  }
  return fmt;
}

function zonedContext(date: Date, timeZone: string): ZonedCtx {
  const parts = getFormatter(timeZone).formatToParts(date);

  const weekday = WEEKDAY_SHORT[parts.find((p) => p.type === "weekday")!.value];
  const year = parts.find((p) => p.type === "year")!.value;
  const month = parts.find((p) => p.type === "month")!.value;
  const day = parts.find((p) => p.type === "day")!.value;
  const hour = Number(parts.find((p) => p.type === "hour")!.value);
  const minute = Number(parts.find((p) => p.type === "minute")!.value);
  const minutes = hour * 60 + minute;
  const hhmm = `${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}`;
  const dateKey = `${year}-${month}-${day}`;
  return { weekday, minutes, hhmm, dateKey };
}

/** UTC instant when local calendar date hits targetMinutes (session open/close). */
function atZonedLocal(dateKey: string, targetMinutes: number, timeZone: string): Date {
  const [y, mo, d] = dateKey.split("-").map(Number);
  let lo = Date.UTC(y, mo - 1, d - 1, 0, 0);
  let hi = Date.UTC(y, mo - 1, d + 1, 23, 59);

  while (hi - lo > 60_000) {
    const mid = Math.floor((lo + hi) / 2);
    const ctx = zonedContext(new Date(mid), timeZone);
    const dayCmp = ctx.dateKey.localeCompare(dateKey);
    if (dayCmp < 0 || (dayCmp === 0 && ctx.minutes < targetMinutes)) {
      lo = mid;
    } else {
      hi = mid;
    }
  }

  let t = lo;
  const limit = hi + 2 * 60_000;
  while (t <= limit) {
    const ctx = zonedContext(new Date(t), timeZone);
    if (ctx.dateKey === dateKey && ctx.minutes >= targetMinutes) {
      return new Date(t - (ctx.minutes - targetMinutes) * 60_000);
    }
    if (ctx.dateKey > dateKey) break;
    t += 60_000;
  }

  return new Date(hi);
}

function nextCalendarDateKey(dateKey: string, timeZone: string): string {
  const noon = atZonedLocal(dateKey, 12 * 60, timeZone);
  return zonedContext(new Date(noon.getTime() + 25 * 3600_000), timeZone).dateKey;
}

function prevCalendarDateKey(dateKey: string, timeZone: string): string {
  const noon = atZonedLocal(dateKey, 12 * 60, timeZone);
  return zonedContext(new Date(noon.getTime() - 25 * 3600_000), timeZone).dateKey;
}

function ctxOnDateKey(dateKey: string, timeZone: string): ZonedCtx {
  return zonedContext(atZonedLocal(dateKey, 12 * 60, timeZone), timeZone);
}

function isCashWeekday(weekday: number): boolean {
  return weekday >= 1 && weekday <= 5;
}

function isTradingDay(sessionId: string, ctx: ZonedCtx): boolean {
  return isCashWeekday(ctx.weekday) && !isExchangeHoliday(sessionId, ctx.dateKey);
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

function firstTradingDayOpen(
  fromDateKey: string,
  sessionId: string,
  tz: string,
  segments: Segment[]
): Date {
  let dateKey = fromDateKey;
  for (let i = 0; i < MAX_DAY_LOOKAHEAD; i++) {
    const dayCtx = ctxOnDateKey(dateKey, tz);
    if (isTradingDay(sessionId, dayCtx)) {
      return atZonedLocal(dateKey, segments[0]!.open, tz);
    }
    dateKey = nextCalendarDateKey(dateKey, tz);
  }
  return atZonedLocal(dateKey, segments[0]!.open, tz);
}

function nextCashOpen(now: Date, sessionId: string, tz: string): Date {
  const ctx = zonedContext(now, tz);
  const segments = CASH_SEGMENTS[sessionId]!;

  if (isTradingDay(sessionId, ctx)) {
    for (const seg of segments) {
      if (ctx.minutes < seg.open) {
        return atZonedLocal(ctx.dateKey, seg.open, tz);
      }
    }
  }

  return firstTradingDayOpen(nextCalendarDateKey(ctx.dateKey, tz), sessionId, tz, segments);
}

function nextCashClose(now: Date, sessionId: string, tz: string): Date {
  const ctx = zonedContext(now, tz);
  const segments = CASH_SEGMENTS[sessionId]!;
  const active = activeCashSegment(segments, ctx.minutes);
  if (active) return atZonedLocal(ctx.dateKey, active.close, tz);
  return nextCashOpen(now, sessionId, tz);
}

function cmeIsHoliday(ctx: ZonedCtx): boolean {
  return isExchangeHoliday("cme", ctx.dateKey);
}

function cmeIsInMaintenance(ctx: ZonedCtx): boolean {
  if (cmeIsHoliday(ctx)) return false;
  if (ctx.weekday === 5 && ctx.minutes >= CME_WEEK_CLOSE) return true;
  if (ctx.weekday === 6) return true;
  if (ctx.weekday === 0 && ctx.minutes < CME_WEEK_OPEN) return true;
  if (ctx.weekday >= 1 && ctx.weekday <= 5) {
    if (ctx.minutes >= CME_MAINT_OPEN && ctx.minutes < CME_MAINT_CLOSE) return true;
  }
  return false;
}

function cmeIsOpen(ctx: ZonedCtx): boolean {
  if (cmeIsHoliday(ctx)) return false;
  if (ctx.weekday === 6) return false;
  if (ctx.weekday === 0 && ctx.minutes < CME_WEEK_OPEN) return false;
  if (ctx.weekday === 5 && ctx.minutes >= CME_WEEK_CLOSE) return false;
  if (cmeIsInMaintenance(ctx)) return false;
  return true;
}

function nextCmeMaintStartAfterDateKey(dateKey: string, tz: string): Date | null {
  const dayCtx = ctxOnDateKey(dateKey, tz);
  if (cmeIsHoliday(dayCtx)) return null;

  if (dayCtx.weekday >= 1 && dayCtx.weekday <= 4) {
    return atZonedLocal(dateKey, CME_MAINT_OPEN, tz);
  }
  if (dayCtx.weekday === 5) {
    return atZonedLocal(dateKey, CME_WEEK_CLOSE, tz);
  }
  if (dayCtx.weekday === 0) {
    return atZonedLocal(dateKey, CME_WEEK_OPEN, tz);
  }
  return null;
}

function nextCmeMaintStart(now: Date, tz: string): Date {
  const ctx = zonedContext(now, tz);

  if (!cmeIsHoliday(ctx)) {
    if (ctx.weekday >= 1 && ctx.weekday <= 4 && ctx.minutes < CME_MAINT_OPEN) {
      return atZonedLocal(ctx.dateKey, CME_MAINT_OPEN, tz);
    }
    if (ctx.weekday === 5 && ctx.minutes < CME_WEEK_CLOSE) {
      return atZonedLocal(ctx.dateKey, CME_WEEK_CLOSE, tz);
    }
    if (ctx.weekday === 0 && ctx.minutes >= CME_WEEK_OPEN && ctx.minutes < CME_MAINT_OPEN) {
      return atZonedLocal(ctx.dateKey, CME_MAINT_OPEN, tz);
    }
  }

  let dateKey = ctx.dateKey;
  for (let i = 0; i < MAX_DAY_LOOKAHEAD; i++) {
    dateKey = nextCalendarDateKey(dateKey, tz);
    const candidate = nextCmeMaintStartAfterDateKey(dateKey, tz);
    if (candidate && candidate.getTime() > now.getTime()) return candidate;
  }

  return new Date(now.getTime() + MAX_DAY_LOOKAHEAD * 24 * 3600_000);
}

function nextCmeResumeAfterDateKey(dateKey: string, tz: string): Date | null {
  const dayCtx = ctxOnDateKey(dateKey, tz);
  if (cmeIsHoliday(dayCtx)) return null;

  if (dayCtx.weekday === 0) {
    return atZonedLocal(dateKey, CME_WEEK_OPEN, tz);
  }
  if (dayCtx.weekday >= 1 && dayCtx.weekday <= 5) {
    return atZonedLocal(dateKey, CME_MAINT_CLOSE, tz);
  }
  return null;
}

function nextCmeResume(now: Date, tz: string): Date {
  const ctx = zonedContext(now, tz);

  if (!cmeIsHoliday(ctx)) {
    if (ctx.weekday === 0 && ctx.minutes < CME_WEEK_OPEN) {
      return atZonedLocal(ctx.dateKey, CME_WEEK_OPEN, tz);
    }
    if (
      ctx.weekday >= 1 &&
      ctx.weekday <= 5 &&
      ctx.minutes >= CME_MAINT_OPEN &&
      ctx.minutes < CME_MAINT_CLOSE
    ) {
      return atZonedLocal(ctx.dateKey, CME_MAINT_CLOSE, tz);
    }
  }

  let dateKey = ctx.dateKey;
  for (let i = 0; i < MAX_DAY_LOOKAHEAD; i++) {
    dateKey = nextCalendarDateKey(dateKey, tz);
    const candidate = nextCmeResumeAfterDateKey(dateKey, tz);
    if (candidate && candidate.getTime() > now.getTime()) return candidate;
  }

  return new Date(now.getTime() + MAX_DAY_LOOKAHEAD * 24 * 3600_000);
}

function lastCmeResumeAt(now: Date, tz: string): Date {
  const ctx = zonedContext(now, tz);

  if (!cmeIsHoliday(ctx)) {
    if (ctx.weekday === 0 && ctx.minutes >= CME_WEEK_OPEN) {
      return atZonedLocal(ctx.dateKey, CME_WEEK_OPEN, tz);
    }
    if (ctx.weekday >= 1 && ctx.weekday <= 5 && ctx.minutes >= CME_MAINT_CLOSE) {
      return atZonedLocal(ctx.dateKey, CME_MAINT_CLOSE, tz);
    }
  }

  let dateKey = prevCalendarDateKey(ctx.dateKey, tz);
  for (let i = 0; i < MAX_DAY_LOOKAHEAD; i++) {
    const dayCtx = ctxOnDateKey(dateKey, tz);
    if (!cmeIsHoliday(dayCtx)) {
      if (dayCtx.weekday === 0) {
        return atZonedLocal(dateKey, CME_WEEK_OPEN, tz);
      }
      if (dayCtx.weekday >= 1 && dayCtx.weekday <= 5) {
        return atZonedLocal(dateKey, CME_MAINT_CLOSE, tz);
      }
    }
    dateKey = prevCalendarDateKey(dateKey, tz);
  }

  return atZonedLocal(ctx.dateKey, CME_MAINT_CLOSE, tz);
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

  if (isExchangeHoliday(session.id, ctx.dateKey)) {
    const openAt = nextCashOpen(now, session.id, tz);
    const tmrw = isTomorrowOpen(now, openAt, tz);
    return {
      isOpen: false,
      localTimeHHmm: ctx.hhmm,
      statusLabel: "Closed",
      countdownLabel: isCashWeekday(ctx.weekday)
        ? "Holiday"
        : formatCountdown(msUntil(now, openAt), "Opens in", { tmrw }),
      progress01: null,
    };
  }

  const active = isTradingDay(session.id, ctx)
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
  const resumeAt = lastCmeResumeAt(now, tz);
  const maintAt = nextCmeMaintStart(now, tz);
  const span = maintAt.getTime() - resumeAt.getTime();
  if (span <= 0) return 0;
  return Math.min(1, Math.max(0, msUntil(resumeAt, now) / span));
}

function getCmeState(now: Date): MarketRowState {
  const tz = "America/New_York";
  const ctx = zonedContext(now, tz);

  if (cmeIsHoliday(ctx)) {
    const openAt = nextCmeResume(now, tz);
    const tmrw = isTomorrowOpen(now, openAt, tz);
    return {
      isOpen: false,
      localTimeHHmm: ctx.hhmm,
      statusLabel: "Closed",
      countdownLabel: isCashWeekday(ctx.weekday)
        ? "Holiday"
        : formatCountdown(msUntil(now, openAt), "Opens in", { tmrw }),
      progress01: null,
    };
  }

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

function msUntilNextMinute(now: Date, timeZone: string): number {
  const ctx = zonedContext(now, timeZone);
  const end = atZonedLocal(ctx.dateKey, ctx.minutes, timeZone).getTime() + 60_000;
  return Math.max(1, end - now.getTime());
}

function msUntilNextSessionEvent(session: MarketSession, now: Date): number {
  const tz = session.timezone;
  const nowMs = now.getTime();

  if (session.kind === "futures") {
    const ctx = zonedContext(now, tz);
    const at = cmeIsHoliday(ctx)
      ? nextCmeResume(now, tz)
      : cmeIsOpen(ctx)
        ? nextCmeMaintStart(now, tz)
        : nextCmeResume(now, tz);
    return Math.max(1, at.getTime() - nowMs);
  }

  const ctx = zonedContext(now, tz);
  const segments = CASH_SEGMENTS[session.id]!;
  if (isExchangeHoliday(session.id, ctx.dateKey)) {
    return Math.max(1, nextCashOpen(now, session.id, tz).getTime() - nowMs);
  }
  const active = isTradingDay(session.id, ctx)
    ? activeCashSegment(segments, ctx.minutes)
    : null;
  const at = active
    ? nextCashClose(now, session.id, tz)
    : nextCashOpen(now, session.id, tz);
  return Math.max(1, at.getTime() - nowMs);
}

/** Ms until the widget needs another refresh (no polling interval). */
export function getNextMarketTimesWakeMs(now: Date = new Date()): number {
  let wake = 24 * 60 * 60_000;
  for (const session of MARKET_SESSIONS) {
    wake = Math.min(wake, msUntilNextMinute(now, session.timezone));
    wake = Math.min(wake, msUntilNextSessionEvent(session, now));
  }
  return Math.max(1000, wake);
}
