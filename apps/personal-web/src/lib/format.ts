const DAY = 24 * 60 * 60 * 1000;

function localMidnight(d: Date) {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
}

/** 마감 값(ISO 날짜/시각 또는 "always")을 날짜로. 해석할 수 없으면 null. */
export function parseDeadline(value: string | null | undefined): Date | null {
  if (!value || value === "always") return null;
  const d = new Date(/^\d{4}-\d{2}-\d{2}$/.test(value) ? `${value}T23:59:59` : value);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** 남은 날 수 (오늘 마감 = 0, 지남 = 음수). 날짜는 이 기기의 시간대 기준. */
export function daysLeft(value: string | null | undefined, now = new Date()): number | null {
  const d = parseDeadline(value);
  if (!d) return null;
  return Math.round((localMidnight(d) - localMidnight(now)) / DAY);
}

export function ddayLabel(value: string | null | undefined, now = new Date()): string | null {
  if (value === "always") return "상시";
  const n = daysLeft(value, now);
  if (n === null) return null;
  if (n === 0) return "D-day";
  return n > 0 ? `D-${n}` : `마감 ${-n}일 지남`;
}

const dateFmt = new Intl.DateTimeFormat("ko-KR", {
  year: "numeric",
  month: "short",
  day: "numeric",
});
const dateTimeFmt = new Intl.DateTimeFormat("ko-KR", {
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});

export function formatDate(value: string | null | undefined) {
  const d = value ? new Date(value) : null;
  return d && !Number.isNaN(d.getTime()) ? dateFmt.format(d) : "";
}

export function formatDateTime(value: string | null | undefined) {
  const d = value ? new Date(value) : null;
  return d && !Number.isNaN(d.getTime()) ? dateTimeFmt.format(d) : "";
}

/** 마감 시각을 사람이 읽는 꼴로. 날짜만 있으면 날짜만. */
export function formatDeadline(value: string | null | undefined) {
  if (!value) return "";
  if (value === "always") return "상시 채용";
  const d = parseDeadline(value);
  if (!d) return value;
  return /T/.test(value) ? dateTimeFmt.format(d) : dateFmt.format(d);
}

export function usd(value: string | number | null | undefined, digits = 4) {
  if (value === null || value === undefined) return "—";
  const n = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(n)) return String(value);
  return `$${n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: digits })}`;
}

export function int(n: number) {
  return n.toLocaleString("ko-KR");
}

export function hostOf(url: string) {
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
}
