// 날짜 표시 공용 도우미: 공간 표시 시간대(설정 화면, c0021 workspace-settings)로 보인다.
// 설정을 읽기 전(또는 읽지 못하면)에는 이 기기의 시간대를 쓴다. AppShell이 설정을 읽어 정한다.
import { useSyncExternalStore } from "react";

const DAY = 24 * 60 * 60 * 1000;
const DATE_ONLY = /^\d{4}-\d{2}-\d{2}$/;

let zone: string | undefined;
const listeners = new Set<() => void>();
const cache = new Map<string, Intl.DateTimeFormat>();

function valid(name: string) {
  try {
    new Intl.DateTimeFormat("en-US", { timeZone: name });
    return true;
  } catch {
    return false;
  }
}

/** 공간 표시 시간대를 정한다 (IANA 이름). 비우면 기기 시간대. */
export function setDisplayTimeZone(name: string | null | undefined) {
  const next = name && valid(name) ? name : undefined;
  if (next === zone) return;
  zone = next;
  listeners.forEach((l) => l());
}

/** 지금 쓰는 표시 시간대 이름 */
export function displayTimeZone(): string {
  return zone ?? Intl.DateTimeFormat().resolvedOptions().timeZone;
}

/** 표시 시간대가 바뀌면 다시 그린다 */
export function useDisplayTimeZone(): string {
  return useSyncExternalStore(
    (cb) => {
      listeners.add(cb);
      return () => listeners.delete(cb);
    },
    displayTimeZone,
    displayTimeZone,
  );
}

function fmt(kind: string, opts: Intl.DateTimeFormatOptions, timeZone = displayTimeZone()) {
  const key = `${kind}|${timeZone}`;
  let f = cache.get(key);
  if (!f) {
    f = new Intl.DateTimeFormat("ko-KR", { ...opts, timeZone });
    cache.set(key, f);
  }
  return f;
}

const DATE: Intl.DateTimeFormatOptions = { year: "numeric", month: "short", day: "numeric" };
const DATE_TIME: Intl.DateTimeFormatOptions = {
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
};

function toDate(value: string | Date | null | undefined): Date | null {
  if (!value) return null;
  const d = value instanceof Date ? value : new Date(value);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** 표시 시간대의 날짜 부분 */
function parts(d: Date) {
  const out: Record<string, number> = {};
  for (const p of fmt("parts", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  }).formatToParts(d)) {
    if (p.type !== "literal") out[p.type] = Number(p.value);
  }
  return out as { year: number; month: number; day: number; hour: number; minute: number; second: number };
}

const pad = (n: number) => String(n).padStart(2, "0");

/** 표시 시간대의 날짜 "YYYY-MM-DD" */
export function dateKey(value: string | Date): string {
  if (typeof value === "string" && DATE_ONLY.test(value)) return value;
  const d = toDate(value);
  if (!d) return "";
  const p = parts(d);
  return `${p.year}-${pad(p.month)}-${pad(p.day)}`;
}

function keyToUtc(key: string) {
  const [y, m, d] = key.split("-").map(Number);
  return Date.UTC(y!, m! - 1, d!);
}

/** 날짜만 있는 값("2026-11-17")은 시간대와 무관한 달력 날짜로 보인다 */
export function formatDate(value: string | null | undefined) {
  if (value && DATE_ONLY.test(value)) return fmt("date", DATE, "UTC").format(keyToUtc(value));
  const d = toDate(value);
  return d ? fmt("date", DATE).format(d) : "";
}

export function formatDateTime(value: string | null | undefined) {
  const d = toDate(value);
  return d ? fmt("datetime", DATE_TIME).format(d) : "";
}

/** 오늘 화면 머리말: "11월 17일 월요일" */
export function formatToday(now = new Date()) {
  return fmt("today", { month: "long", day: "numeric", weekday: "long" }).format(now);
}

/** 마감 값(ISO 날짜/시각 또는 "always")을 날짜로. 해석할 수 없으면 null. 날짜만 있으면 그날 끝. */
export function parseDeadline(value: string | null | undefined): Date | null {
  if (!value || value === "always") return null;
  if (DATE_ONLY.test(value)) {
    const iso = fromLocalInput(`${value}T23:59`);
    return iso ? new Date(iso) : null;
  }
  return toDate(value);
}

/** 남은 날 수 (오늘 마감 = 0, 지남 = 음수). 날짜는 표시 시간대 기준. */
export function daysLeft(value: string | null | undefined, now = new Date()): number | null {
  if (!value || value === "always") return null;
  const key = DATE_ONLY.test(value) ? value : toDate(value) ? dateKey(value) : "";
  if (!key) return null;
  return Math.round((keyToUtc(key) - keyToUtc(dateKey(now))) / DAY);
}

export function ddayLabel(value: string | null | undefined, now = new Date()): string | null {
  if (value === "always") return "상시";
  const n = daysLeft(value, now);
  if (n === null) return null;
  if (n === 0) return "D-day";
  return n > 0 ? `D-${n}` : `마감 ${-n}일 지남`;
}

/** 마감 시각을 사람이 읽는 꼴로. 날짜만 있으면 날짜만. */
export function formatDeadline(value: string | null | undefined) {
  if (!value) return "";
  if (value === "always") return "상시 채용";
  if (DATE_ONLY.test(value)) return formatDate(value);
  const d = toDate(value);
  return d ? formatDateTime(value) : value;
}

/** ISO 시각 → `<input type="datetime-local">` 값 (표시 시간대의 벽시계) */
export function toLocalInput(iso: string | null | undefined): string {
  const d = toDate(iso);
  if (!d) return "";
  const p = parts(d);
  return `${p.year}-${pad(p.month)}-${pad(p.day)}T${pad(p.hour)}:${pad(p.minute)}`;
}

/** `<input type="datetime-local">` 값(표시 시간대의 벽시계) → ISO 시각. 비면 null */
export function fromLocalInput(value: string | null | undefined): string | null {
  const m = value ? /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(value) : null;
  if (!m) return null;
  const [y, mo, d, h, mi] = m.slice(1).map(Number) as [number, number, number, number, number];
  const wall = Date.UTC(y, mo - 1, d, h, mi);
  // 표시 시간대의 오프셋을 두 번 맞춘다 (서머타임 경계)
  let t = wall;
  for (let i = 0; i < 2; i++) {
    const p = parts(new Date(t));
    const shown = Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute, p.second);
    t = t - (shown - wall);
  }
  return new Date(t).toISOString();
}
