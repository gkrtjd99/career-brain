// 날짜 표시는 공간 표시 시간대를 쓰는 공용 도우미(lib/dates.ts)로 옮겼다. 예전 import 경로를 위해 다시 내보낸다.
export { daysLeft, ddayLabel, formatDate, formatDateTime, formatDeadline, parseDeadline } from "./dates";

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
