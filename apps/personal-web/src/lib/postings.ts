// 공고 생애주기 표시 도우미: 접수 상태·시기·지원 상태를 한 줄 글로.
import type { PostingApplication, PostingLifecycle, PostingSummary } from "@career-brain/client";
import { daysLeft, ddayLabel, formatDate, formatDeadline } from "./dates";
import { hostOf } from "./format";
import { APP_STATUS } from "./labels";

export const LIFECYCLE_LABEL: Record<PostingLifecycle, string> = {
  open: "접수 중",
  upcoming: "접수 예정",
  always: "상시",
  closed: "마감",
  unknown: "마감일 모름",
};

/** 제목이 없으면(가져오기 실패 등) 주소의 호스트 이름 */
export function postingTitle(p: Pick<PostingSummary, "title" | "canonical_url">) {
  return p.title ?? hostOf(p.canonical_url);
}

/** "2026-H2" → "2026 하반기" */
export function seasonLabel(key: string | null | undefined) {
  const m = key ? /^(\d{4})-H([12])$/.exec(key) : null;
  if (!m) return "시기 모름";
  return `${m[1]} ${m[2] === "1" ? "상반기" : "하반기"}`;
}

/** 오른쪽 위 한 마디: D-3 · D-day · 3일 뒤 접수 시작 · 상시 · 마감 · 마감일 모름 */
export function timingLabel(p: Pick<PostingSummary, "deadline" | "opens_at" | "lifecycle">): string {
  const lc = p.lifecycle ?? "unknown";
  if (lc === "upcoming") {
    const n = daysLeft(p.opens_at);
    if (n === null || n <= 0) return "오늘 접수 시작";
    return n === 1 ? "내일 접수 시작" : `${n}일 뒤 접수 시작`;
  }
  if (lc === "open") return ddayLabel(p.deadline) ?? "접수 중";
  if (lc === "always") return "상시";
  if (lc === "closed") return "마감";
  return "마감일 모름";
}

/** D-day가 3일 안이면 진하게 */
export function timingSoon(p: Pick<PostingSummary, "deadline" | "opens_at" | "lifecycle">) {
  const lc = p.lifecycle ?? "unknown";
  const n = lc === "open" ? daysLeft(p.deadline) : lc === "upcoming" ? daysLeft(p.opens_at) : null;
  return n !== null && n >= 0 && n <= 3;
}

/** 올해 날짜는 연도를 빼고 보인다: "2026년 11월 30일" → "11월 30일" */
function short(text: string, now = new Date()) {
  const year = `${now.getFullYear()}년 `;
  return text.startsWith(year) ? text.slice(year.length) : text;
}

/** 두 번째 줄의 날짜: "11월 30일 마감" · "11월 20일 접수 시작 · 12월 1일 마감" · "상시 채용" */
export function datesLine(p: Pick<PostingSummary, "deadline" | "opens_at" | "lifecycle">): string | null {
  const end = p.deadline === "always" ? "상시 채용" : p.deadline ? `${short(formatDeadline(p.deadline))} 마감` : null;
  // 접수 시작은 시작 전이거나 마감을 모를 때만 (이미 접수 중이면 마감만)
  if (p.opens_at && (p.lifecycle === "upcoming" || !end)) {
    const start = `${short(formatDate(p.opens_at) || formatDeadline(p.opens_at))} 접수 시작`;
    return end ? `${start} · ${end}` : start;
  }
  return end;
}

/** 지원 상태 한 마디: 지원 전 · 문항 1/2 · 작성 중 · 제출함 · 1차 면접 · 통과 … */
export function applicationLabel(a: PostingApplication | null | undefined): string {
  if (!a) return "지원 전";
  if (a.status === "draft")
    return a.questions ? `문항 ${a.questions} · ${a.answered ? `초안 ${a.answered}` : "초안 전"}` : "작성 중";
  if (a.status === "submitted") {
    if (!a.stage) return "제출함";
    if (a.stage.outcome === "pending") return a.stage.name;
    if (a.stage.outcome === "failed") return `${a.stage.name} 탈락`;
    if (a.stage.outcome === "passed") return `${a.stage.name} 통과`;
    return a.stage.name;
  }
  return APP_STATUS[a.status].label;
}
