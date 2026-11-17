import type { Credential, CredentialExtractResult, CredentialField, CredentialKind, CredentialStatus } from "@career-brain/client";
import { formatDate } from "./dates";

export const CREDENTIAL_KIND_LABEL: Record<CredentialKind, string> = {
  license: "자격증",
  language: "어학",
  course: "수료",
  award: "수상",
  degree: "학력",
  other: "기타 증명",
};

export const CREDENTIAL_KIND_OPTIONS: { value: CredentialKind; label: string }[] = (
  ["license", "language", "course", "award", "degree", "other"] as const
).map((value) => ({ value, label: CREDENTIAL_KIND_LABEL[value] }));

export const CREDENTIAL_STATUS_LABEL: Record<CredentialStatus, string> = {
  extracted: "확인 전",
  self_confirmed: "확인함",
  evidence_supported: "증빙 있음",
};

export const CREDENTIAL_FIELD_LABEL: Record<CredentialField, string> = {
  name: "이름",
  issuer: "발급 기관",
  number: "번호",
  score: "점수·등급",
  acquired_on: "취득일",
  expires_on: "만료일",
  started_on: "시작일",
  ended_on: "종료일",
  hours: "시간",
};

/** 뽑기 결과 한 문장 */
export function extractResultText(r: CredentialExtractResult): string {
  if (r.credential_id) return `항목 ${r.fields.length}개를 뽑았습니다. 확인해 주세요.`;
  switch (r.reason) {
    case "not_parsed":
      return "아직 읽지 못한 자료입니다. 읽기가 끝나면 다시 시도하세요.";
    case "no_text":
      return "읽은 글이 없습니다. 사진에서는 글자를 읽지 않습니다. 항목을 직접 추가하세요.";
    case "already_recorded":
      return "이 자료의 기록이 이미 있습니다.";
    default:
      return "뽑을 항목을 찾지 못했습니다. 항목을 직접 추가하세요.";
  }
}

/** 만료 표시 글자: 지났으면 "만료됨", 90일 안이면 "곧 만료" */
export function expiryMark(c: Pick<Credential, "expiry">): string | null {
  return c.expiry === "expired" ? "만료됨" : c.expiry === "soon" ? "곧 만료" : null;
}

/** 기간 또는 취득일 한 줄 */
export function whenText(c: Pick<Credential, "acquired_on" | "started_on" | "ended_on">): string | null {
  if (c.acquired_on) return formatDate(c.acquired_on);
  if (c.started_on) return `${formatDate(c.started_on)} –${c.ended_on ? ` ${formatDate(c.ended_on)}` : ""}`;
  return null;
}

/** 점수·시간 한 칸 */
export function scoreText(c: Pick<Credential, "score" | "hours">): string | null {
  return [c.score, c.hours ? `${c.hours}시간` : null].filter(Boolean).join(" · ") || null;
}
