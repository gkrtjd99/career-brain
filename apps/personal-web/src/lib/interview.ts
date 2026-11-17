// 면접 준비 (O16) 화면 문구: 출처 종류·오래됨 이유·만들 때 메모.
import type {
  ApplicationSummary,
  InterviewPrepQuestion,
  InterviewSource,
  InterviewSourceKind,
  InterviewStaleReason,
} from "@career-brain/client";
import { ANSWER_KIND_LABEL } from "./brainMe";
import { FACT_KIND_LABEL } from "./companies";
import { formatDate } from "./dates";

/** 목록 묶음 이름 (출처 칩과 같은 순서) */
export const SOURCE_GROUP: Record<InterviewSourceKind, string> = {
  submission: "제출한 글",
  posting: "공고",
  company: "회사",
  reference: "면접 후기",
  common: "공통 질문",
  user: "직접 더함",
};

export const SOURCE_ORDER: InterviewSourceKind[] = ["submission", "posting", "company", "reference", "common", "user"];

export const STALE_TEXT: Record<InterviewStaleReason, string> = {
  posting_changed: "공고를 다시 저장했습니다",
  submission_changed: "만든 뒤 제출본이 바뀌었습니다",
  submission_replaced: "이 질문이 나온 제출본이 지금 제출본이 아닙니다",
  company_changed: "회사 항목이 바뀌었습니다",
  reference_changed: "면접 후기 메모가 바뀌었습니다",
  experience_changed: "쓸 경험의 내용이 바뀌었습니다",
};

const POSTING_FIELD: Record<string, string> = {
  requirements: "자격 요건",
  preferred: "우대 사항",
  responsibilities: "담당 업무",
};

/** 기준일을 적힌 정밀도대로 (연·월·일) */
export function asOfText(asOf: string | null, precision: "year" | "month" | "day" | null): string | null {
  if (!asOf) return null;
  const [y, m] = asOf.split("-");
  if (precision === "year") return `${y}년`;
  if (precision === "month") return `${y}년 ${Number(m)}월`;
  return formatDate(asOf);
}

/** 출처 한 줄: "공고 · 자격 요건", "제출한 글 · 문항 1", "회사 · 인재상 · 2026년 3월 기준" */
export function sourceLine(s: InterviewSource): string {
  switch (s.kind) {
    case "posting":
      return `공고 · ${POSTING_FIELD[s.field] ?? s.field}`;
    case "submission":
      return ["제출한 글", s.document_title, `${formatDate(s.submitted_at)} 제출`].filter(Boolean).join(" · ");
    case "company": {
      const asOf = asOfText(s.as_of, s.as_of_precision);
      return [
        `회사 · ${FACT_KIND_LABEL[s.fact_kind as keyof typeof FACT_KIND_LABEL] ?? s.fact_kind}`,
        asOf ? `${asOf} 기준` : `${formatDate(s.fetched_at)} 가져옴`,
      ].join(" · ");
    }
    case "reference":
      return `면접 후기 · ${s.reference_title} · 참고`;
    case "common":
      return `공통 질문 · ${ANSWER_KIND_LABEL[s.common_kind]}`;
    case "user":
      return "직접 더한 질문";
  }
}

/** 답이 있는가 (메모 또는 이은 공통 답변) */
export function hasAnswer(q: InterviewPrepQuestion): boolean {
  return !!q.answer.trim() || !!q.answer_asset?.body.trim();
}

/** 만들 때 남긴 메모 → 사람 말. 모르는 것은 보이지 않는다. */
export function interviewNote(note: string): string | null {
  const [code, arg] = note.split(":", 2);
  switch (code) {
    case "no_submission":
      return "제출본이 없어 제출한 글에서 나온 질문은 없습니다.";
    case "model_not_allowed":
      return "모델 없이 정해진 질문과 검색 순위로 만들었습니다.";
    case "model_denied":
      return "모델 연결이 없어 정해진 질문과 검색 순위로 만들었습니다.";
    case "model_error":
      return "모델이 답하지 않아 정해진 질문으로 만들었습니다.";
    case "model_invalid_output":
      return "모델 답을 읽지 못해 정해진 질문으로 만들었습니다.";
    case "model_dropped":
      return `모델이 후보 밖에서 고른 ${arg}개는 뺐습니다.`;
    case "restricted_withheld":
      return `이 기기 밖으로 보내지 않는 사실 ${arg}개는 모델에 보내지 않았습니다.`;
    case "search":
      return "의미 검색 없이 낱말로만 찾았습니다.";
    case "no_candidates":
      return "쓸 경험을 찾지 못했습니다. 원문 근거가 있는 확인한 사실이 있어야 후보가 됩니다.";
    default:
      return null;
  }
}

/** 서류를 넘겼거나 면접 단계인 지원: 면접 준비를 앞에 보인다 */
export function interviewReady(a: Pick<ApplicationSummary, "status" | "stage">): boolean {
  if (a.status !== "submitted" && a.status !== "passed") return false;
  const st = a.stage;
  if (!st) return a.status === "passed";
  if (st.kind === "interview" || st.kind === "final") return true;
  if (st.kind === "document") return st.outcome === "passed";
  return st.kind === "test" || st.kind === "assignment";
}
