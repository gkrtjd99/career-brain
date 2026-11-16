// API 토큰 만료 임박 판단 (설정 화면 토큰 관리·오늘 화면 안내가 같은 기준을 쓴다).
import type { ApiToken } from "@career-brain/client";
import { daysLeft } from "./dates";

/** 만료까지 이 날 수 이하로 남으면 "곧 만료"로 알린다 */
export const TOKEN_SOON_DAYS = 14;

/** 사용 가능한 토큰의 만료까지 남은 날 수 (표시 시간대 날짜 기준). 만료 없음·폐기·만료면 null */
export function tokenDaysLeft(t: ApiToken, now = new Date()): number | null {
  if (t.status !== "active" || !t.expires_at) return null;
  return daysLeft(t.expires_at, now);
}

export function tokenExpiresSoon(t: ApiToken, now = new Date()): boolean {
  const n = tokenDaysLeft(t, now);
  return n !== null && n <= TOKEN_SOON_DAYS;
}

export function tokenExpiryText(n: number): string {
  return n <= 0 ? "오늘 만료" : `${n}일 뒤 만료`;
}
