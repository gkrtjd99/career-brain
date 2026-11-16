import type {
  ClaimKind,
  Contribution,
  FetchStatus,
  PostingField,
  PostingStatus,
} from "@career-brain/client";
import type { BadgeTone } from "@career-brain/ui";

export const KIND_LABEL: Record<ClaimKind, string> = {
  responsibility: "맡은 일",
  action: "직접 한 일",
  result: "결과",
  decision: "선택과 이유",
  difficulty: "어려움과 해결",
  skill: "기술",
  other: "기타",
};

export const CONTRIBUTION_LABEL: Record<Contribution, string> = {
  personal: "본인",
  team: "팀",
  unknown: "기여 미상",
};

export const ORIGIN_LABEL: Record<string, string> = {
  ai_extraction: "AI 추출",
  interview: "인터뷰",
  user: "직접 입력",
};

export const FOCUS_LABEL: Record<string, string> = {
  context: "배경",
  personal_contribution: "본인 기여",
  metric: "수치",
  decision: "판단",
  difficulty: "어려움",
  team_vs_personal: "팀과 본인 구분",
};

export const POSTING_STATUS: Record<PostingStatus, { label: string; tone: BadgeTone }> = {
  draft: { label: "확인 필요", tone: "warn" },
  saved: { label: "저장됨", tone: "ok" },
  archived: { label: "보관", tone: "none" },
};

export const FETCH_STATUS: Record<FetchStatus, { label: string; tone: BadgeTone }> = {
  pending: { label: "가져오는 중", tone: "accent" },
  done: { label: "가져옴", tone: "ok" },
  listing: { label: "목록 페이지", tone: "warn" },
  failed: { label: "가져오기 실패", tone: "danger" },
};

/** 가져오기 실패 사유. 실패는 마감이 아니다. */
export const FETCH_ERROR: Record<string, string> = {
  source_unavailable: "페이지를 열 수 없음",
  login_required: "로그인이 필요한 페이지",
  rate_limited: "요청이 너무 많아 거절됨",
  needs_manual_check: "직접 확인 필요",
  blocked_address: "내부 주소라 차단함",
  too_large: "페이지가 너무 큼",
  unsupported_content: "PDF·이미지 공고 (본문 붙여넣기로)",
  invalid_url: "URL 형식 오류",
};

export const FIELD_LABEL: Record<PostingField, string> = {
  title: "공고명",
  company: "회사",
  location: "근무지",
  employment_type: "고용 형태",
  career_level: "경력 조건",
  deadline: "마감",
  posted_at: "게시일",
  responsibilities: "주요 업무",
  requirements: "자격 요건",
  preferred: "우대 사항",
  questions: "자기소개서 문항",
  process: "전형 절차",
};

export const METHOD_LABEL: Record<string, string> = {
  http: "HTTP",
  browser: "브라우저 렌더링",
  manual_text: "직접 붙여넣기",
};

export const ADAPTER_LABEL: Record<string, string> = {
  json_ld: "구조화 데이터(schema.org)",
  ninehire: "채용 시스템(NineHire)",
  text: "본문",
};

/** 가져오기 메모 (`llm_dropped:3` 처럼 값이 붙는다) */
export function noteLabel(note: string): string {
  const [code, arg] = note.split(":", 2);
  switch (code) {
    case "llm_denied":
      return `AI 추출 안 함: ${ERROR_TEXT[arg ?? ""] ?? arg}`;
    case "llm_error":
      return `AI 추출 오류: ${arg}`;
    case "llm_invalid_output":
      return "AI 출력 형식이 맞지 않아 버림";
    case "llm_dropped":
      return `원문에서 인용을 찾지 못한 값 ${arg}개를 버림`;
    case "llm_says_listing":
      return "AI가 목록 페이지로 판단함";
    default:
      return note;
  }
}

/** 서버 오류 코드 → 사람이 읽는 문장 */
export const ERROR_TEXT: Record<string, string> = {
  contribution_required: "본인 기여인지 팀 성과인지 먼저 골라 주세요.",
  confirm_requires_approve: "확정 권한(approve)이 없는 토큰입니다.",
  claim_retired: "보관된 주장은 바꿀 수 없습니다.",
  interview_closed: "닫힌 인터뷰입니다.",
  parse_not_finished: "아직 원본 읽기가 끝나지 않았습니다.",
  title_required: "공고명은 비울 수 없습니다.",
  nothing_to_confirm: "확정할 추출 결과가 없습니다. 본문을 붙여넣거나 값을 직접 입력하세요.",
  unknown_field: "알 수 없는 항목입니다.",
  invalid_deadline: "마감은 2026-12-31 또는 2026-12-31T18:00 꼴이어야 합니다.",
  invalid_posted_at: "게시일은 2026-12-31 꼴이어야 합니다.",
  invalid_url: "URL 형식이 아닙니다.",
  models_not_configured: "모델이 설정되지 않았습니다 (models.toml).",
  budget_exceeded: "월 예산을 넘어 호출하지 않았습니다.",
  no_route: "이 작업에 연결된 모델이 없습니다.",
  api_key_missing: "모델 API 키가 없습니다.",
  forbidden: "이 토큰에는 권한이 없습니다.",
  "not found": "찾을 수 없습니다.",
  authentication_required: "토큰이 필요합니다.",
};
