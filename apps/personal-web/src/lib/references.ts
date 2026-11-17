// 참고 자료 보관함 (O14) 화면 문구. 참고 글은 다른 사람의 글이다: 내 경험·근거와 말을 섞지 않는다.
import type { PiiKind, ReferenceKind, ReferenceSummary } from "@career-brain/client";
import { ERROR_TEXT } from "./labels";

export const REFERENCE_KIND_ORDER: ReferenceKind[] = [
  "accepted_essay",
  "essay",
  "resume",
  "interview_review",
  "job_article",
  "other",
];

export const REFERENCE_KIND_LABEL: Record<ReferenceKind, string> = {
  accepted_essay: "합격 자기소개서",
  essay: "다른 사람 자기소개서",
  resume: "다른 사람 이력서",
  interview_review: "면접 후기",
  job_article: "직무 글",
  other: "기타",
};

export const PII_KIND_LABEL: Record<PiiKind, string> = {
  resident_registration_number: "주민등록번호",
  phone_number: "전화번호",
  email_address: "이메일",
  name: "이름",
  school: "학교",
};

/** 화면 맨 위에 늘 두는 한 줄 */
export const OTHERS_WRITING = "다른 사람의 글 · 내 경험 아님 · 문장을 옮겨 쓰지 마세요";

/** 목록 한 줄의 부제: 종류 · 회사 · 직무 · 연도 */
export function referenceMeta(
  r: Pick<ReferenceSummary, "kind" | "company_name" | "role" | "year">,
  withKind = true,
): string {
  return [withKind ? REFERENCE_KIND_LABEL[r.kind] : null, r.company_name, r.role, r.year ? `${r.year}년` : null]
    .filter(Boolean)
    .join(" · ");
}

/** 메모 후보 만들기 결과 메모 */
export function suggestNoteLabel(note: string): string {
  const [code, arg] = note.split(":");
  switch (code) {
    case "no_model":
      return "연결된 모델이 없습니다. 메모를 직접 씁니다.";
    case "llm_denied":
      return `모델에 보내지 않았습니다. ${ERROR_TEXT[arg ?? ""] ?? arg}`;
    case "llm_error":
      return `모델 호출에 실패했습니다 (${arg}). 잠시 뒤 다시 시도합니다.`;
    case "llm_invalid_output":
      return "모델 출력 형식이 맞지 않아 버렸습니다.";
    case "llm_dropped":
      return `원문 문장을 옮겼거나 형식이 맞지 않는 줄 ${arg}개를 버렸습니다.`;
    default:
      return note;
  }
}

/** 메모 칸 한 줄에 하나 → 목록 */
export function lines(value: string): string[] {
  return value
    .split("\n")
    .map((s) => s.trim())
    .filter(Boolean);
}
