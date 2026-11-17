import type {
  ApplicationStatus,
  AssessmentKind,
  AssessmentStatus,
  AttemptResult,
  ClaimKind,
  Contribution,
  CoverageStatus,
  DocumentKind,
  ExportFormat,
  FetchStatus,
  MistakeReason,
  PostingField,
  PostingStatus,
  ReviewGrade,
  SourceAttention,
  SourcePurpose,
  StageKind,
  StageOutcome,
  StudyItemKind,
  StudyItemStatus,
  Visibility,
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
  needs_confirmation: { label: "비용 확인 필요", tone: "warn" },
};

/** 가져오기 실패 사유. 실패는 마감이 아니다. */
export const FETCH_ERROR: Record<string, string> = {
  source_unavailable: "페이지를 열 수 없음",
  login_required: "로그인이 필요한 페이지",
  rate_limited: "요청이 너무 많아 거절됨",
  bot_blocked: "사이트가 자동 요청을 막음 — 직접 붙여넣기",
  maybe_bot_blocked: "자동 요청이 막혔을 수 있음 — 직접 붙여넣기",
  needs_manual_check: "직접 확인 필요",
  blocked_address: "내부 주소라 차단함",
  too_large: "페이지가 너무 큼",
  unsupported_content: "지원하지 않는 형식 (본문 붙여넣기로)",
  invalid_url: "URL 형식 오류",
};

export const FIELD_LABEL: Record<PostingField, string> = {
  title: "공고명",
  company: "회사",
  location: "근무지",
  employment_type: "고용 형태",
  career_level: "경력 조건",
  deadline: "마감",
  opens_at: "접수 시작",
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
  browser_capture: "내 브라우저에서 가져옴",
};

export const ADAPTER_LABEL: Record<string, string> = {
  json_ld: "구조화 데이터(schema.org)",
  ninehire: "채용 시스템(NineHire)",
  greeting: "채용 시스템(그리팅)",
  greenhouse: "채용 시스템(Greenhouse)",
  lever: "채용 시스템(Lever)",
  workday: "채용 시스템(Workday)",
  pdf: "PDF 본문",
  image: "이미지 (AI가 글자 읽음)",
  text: "본문",
};

const VISION_NOTE: Record<string, string> = {
  vision_route_missing: "이미지 공고: 이미지 읽기 모델(job_extract_image 라우트)이 설정되지 않아 건너뜀",
  image_too_large: "이미지 공고: 파일이 커서(5MB 초과) 읽지 않음",
  image_no_text: "이미지 공고: 읽을 수 있는 글자가 없음",
  ocr_by_model: "이미지의 글자를 AI가 읽어 본문으로 썼습니다. 원본 이미지와 대조해 확인하세요.",
  pdf_no_text: "PDF에 글자가 없음 (스캔본으로 보임)",
  pdf_render_missing: "스캔 PDF: 쪽 이미지 렌더러(pdf-render 선택 설치)가 없어 읽지 않음",
  ocr_pdf_by_model: "스캔 PDF의 쪽 이미지를 AI가 읽어 본문으로 썼습니다. 원본 PDF와 대조해 확인하세요.",
  vision_confirmed: "예상 비용을 확인한 뒤 읽음",
  vision_declined: "예상 비용 확인에서 읽지 않기로 함",
  browser_capture: "내 브라우저에서 보낸 페이지 · 서버는 이 주소를 가져오지 않음",
};

/** 모르는 봇 확인 화면의 흔적 (bot_block_signs:captcha,js_challenge) */
const BOT_SIGN: Record<string, string> = {
  captcha: "캡차",
  phrase: "사람 확인 문구",
  js_challenge: "확인 스크립트",
  tiny_body_script: "빈 본문 + 스크립트",
};

/** 가져오기 메모 (`llm_dropped:3` 처럼 값이 붙는다) */
export function noteLabel(note: string): string {
  const [code, ...rest] = note.split(":");
  const arg = rest[0];
  if (VISION_NOTE[note]) return VISION_NOTE[note];
  switch (code) {
    case "pdf_pages":
      return `PDF에서 글자를 읽은 쪽: ${rest.join(":")}`;
    case "pdf_rendered":
      return `스캔 PDF 쪽 이미지로 읽음: ${rest.join(":")}`;
    case "bot_blocked_by":
      return `자동 요청 확인 화면(${arg})이 막음 · 우회하지 않음`;
    case "bot_block_signs":
      return `자동 요청 확인 화면일 수 있음 (${(arg ?? "")
        .split(",")
        .map((s) => BOT_SIGN[s] ?? s)
        .join(", ")}) · 우회하지 않음`;
    case "pdf_pages_read":
      return `읽은 쪽: ${arg}`;
    case "pdf_blank_pages":
      return `빈 쪽 ${arg}개는 건너뜀`;
    case "vision_estimate_usd":
      return arg === "unknown" ? "이미지 읽기 예상 비용: 단가 모름" : `이미지 읽기 예상 비용(최대): $${arg}`;
    case "estimate_part": {
      // estimate_part:<목적>:<과금>:<입력>:<최대 입력>:<출력>:<최대 출력>:<예상 USD>:<최대 USD>:<근거>:<엔드포인트>:<모델>
      const [purpose, billing, input, , output, , , maxUsd] = rest;
      const name = purpose === "job_extract_image" ? "이미지 읽기" : purpose === "job_extract" ? "항목 추출" : purpose;
      const kind = billing === "metered" ? "종량제" : billing === "subscription" ? "구독, 정가 환산" : "무료·로컬";
      const money = maxUsd === "unknown" ? "단가 모름" : `최대 $${Number(maxUsd)}`;
      return `예상(${name}, ${kind}): 입력 ${Number(input).toLocaleString()} · 출력 약 ${Number(output).toLocaleString()} 토큰, ${money}`;
    }
    case "pdf_unreadable":
      return `PDF를 읽지 못함: ${arg}`;
    case "vision_denied":
      return `이미지 읽기 안 함: ${ERROR_TEXT[arg ?? ""] ?? arg}`;
    case "vision_error":
      return `이미지 읽기 오류: ${arg}`;
    case "ats_api_failed":
      return `채용 시스템 API(${arg}) 응답 없음: ${rest[1] ?? ""} · 페이지 데이터로 계속`;
    case "ats_api_blocked":
    case "ats_api_unrecognized":
      return `채용 시스템 API(${arg})를 쓰지 못함 · 페이지 데이터로 계속`;
    case "llm_deduped":
      return `여러 목록에 겹친 줄 ${arg}개를 한 곳에만 남김`;
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
  // 면접 준비 (c0048)
  answer_required: "답이 비어 있습니다. 먼저 답을 씁니다.",
  answer_too_long: "답변 메모는 5,000자까지입니다.",
  question_empty: "질문이 비어 있습니다.",
  interview_question_too_long: "질문은 300자까지입니다.",
  prep_not_ready: "먼저 예상 질문을 만들어야 합니다.",
  too_many_questions: "직접 더한 질문이 너무 많습니다.",
  // 보고 있는 페이지 보내기 (c0043)
  capture_too_short: "본문을 찾지 못했습니다. 공고 본문을 선택한 뒤 다시 보내 주세요.",
  html_too_large: "페이지가 너무 큽니다. 공고 본문을 선택한 뒤 다시 보내 주세요.",
  content_required: "보낼 본문이 없습니다.",
  model_unavailable: "모델 연결이 잠시 응답하지 않습니다. 잠시 뒤 다시 저장해 주세요.",
  capture_proof_not_allowed: "웹 페이지는 증빙 서류로 넣지 않습니다. 증빙은 원본 파일로 넣습니다.",
  // 자격·이수 (c0034)
  credential_name_required: "이름은 비울 수 없습니다.",
  credential_kind_required: "종류를 골라 주세요.",
  credential_name_too_long: "이름이 너무 깁니다.",
  credential_issuer_too_long: "발급 기관이 너무 깁니다.",
  credential_number_too_long: "번호가 너무 깁니다.",
  credential_score_too_long: "점수·등급이 너무 깁니다.",
  credential_detail_too_long: "메모가 너무 깁니다.",
  invalid_credential_acquired_on: "취득일은 날짜여야 합니다.",
  invalid_credential_expires_on: "만료일은 날짜여야 합니다.",
  invalid_credential_started_on: "시작일은 날짜여야 합니다.",
  invalid_credential_ended_on: "종료일은 날짜여야 합니다.",
  invalid_hours: "시간은 1 이상의 정수여야 합니다.",
  expires_before_acquired: "만료일이 취득일보다 앞섭니다.",
  ended_before_started: "종료일이 시작일보다 앞섭니다.",
  source_not_found: "자료를 찾을 수 없습니다.",
  source_not_proof: "증빙 서류로 넣은 자료가 아닙니다.",
  experience_not_found: "경험을 찾을 수 없습니다.",
  original_missing: "원본 파일이 없습니다.",
  original_changed: "저장한 원본과 내용이 달라 내려보내지 않았습니다. 원본을 다시 올려 주세요.",
  // 원본 용도 (c0032)
  source_has_confirmed_claims:
    "확인한 사실이 이 자료를 근거로 쓰고 있어 용도를 바꿀 수 없습니다. 그 사실을 먼저 보관하세요.",
  source_not_experience: "내 경험 자료가 아니라서 경험 추출과 근거 연결을 하지 않습니다.",
  proof_requires_restricted: "증빙 서류는 이 기기 밖으로 보내지 않는 자료로만 둘 수 있습니다.",
  purpose_changed: "용도를 바꿔 추출을 멈췄습니다.",
  // 경험 구조 (c0030)
  invalid_experience_kind: "알 수 없는 경험 종류입니다.",
  invalid_month: "달은 YYYY-MM 형식이어야 합니다.",
  end_before_start: "끝 달이 시작 달보다 앞섭니다.",
  end_without_start: "끝 달을 적으려면 시작 달도 적어 주세요.",
  ongoing_with_end: "진행 중이면 끝 달을 비워 주세요.",
  tag_too_long: "태그는 하나에 40자까지입니다.",
  too_many_tags: "태그는 30개까지입니다.",
  parent_not_found: "상위 경험을 찾을 수 없습니다.",
  parent_self: "자기 자신을 상위 경험으로 고를 수 없습니다.",
  parent_is_child: "다른 경험 아래에 있는 경험은 상위 경험이 될 수 없습니다 (한 단계만).",
  experience_has_children: "안에 경험이 있는 경험은 다른 경험 아래로 옮길 수 없습니다 (한 단계만).",
  contribution_required: "본인 기여인지 팀 성과인지 먼저 골라 주세요.",
  confirm_requires_approve: "확정 권한(approve)이 없는 토큰입니다.",
  claim_retired: "보관한 사실은 바꿀 수 없습니다.",
  interview_closed: "닫힌 인터뷰입니다.",
  parse_not_finished: "아직 원본 읽기가 끝나지 않았습니다.",
  title_required: "공고명은 비울 수 없습니다.",
  nothing_to_confirm: "확정할 추출 결과가 없습니다. 본문을 붙여넣거나 값을 직접 입력하세요.",
  unknown_field: "알 수 없는 항목입니다.",
  invalid_deadline: "마감은 2026-12-31 또는 2026-12-31T18:00 꼴이어야 합니다.",
  invalid_posted_at: "게시일은 2026-12-31 꼴이어야 합니다.",
  invalid_url: "URL 형식이 아닙니다.",
  confirmation_not_pending: "이미 결정한 확인 요청입니다.",
  human_confirmation_required: "비용 확인은 사람만 할 수 있습니다.",
  models_not_configured: "모델이 설정되지 않았습니다 (models.toml).",
  budget_exceeded: "월 예산을 넘어 호출하지 않았습니다.",
  no_route: "이 작업에 연결된 모델이 없습니다.",
  api_key_missing: "모델 API 키가 없습니다.",
  forbidden: "이 토큰에는 권한이 없습니다.",
  "not found": "찾을 수 없습니다.",
  authentication_required: "토큰이 필요합니다.",
  // O07 지원·문서
  posting_not_saved: "저장(확정)한 공고에서만 만들 수 있습니다.",
  application_locked: "제출한 지원의 문서는 수정할 수 없습니다. 다음 지원은 새로 만듭니다.",
  already_submitted: "이미 제출본을 고정했습니다.",
  suggestion_stale: "그사이 문단이 바뀌어 이 수정안은 적용할 수 없습니다.",
  suggestion_not_open: "이미 처리한 수정안입니다.",
  invalid_transition: "이 상태로 바꿀 수 없습니다.",
  submit_requires_approve: "제출 권한(approve)이 없는 토큰입니다.",
  invalid_model_output: "모델 출력 형식이 맞지 않았습니다.",
  unknown_block: "없는 문단입니다. 새로고침 후 다시 시도하세요.",
  unknown_claim: "없는 사실을 근거로 연결했습니다.",
  empty_block: "빈 문단은 저장하지 않습니다.",
  block_too_long: "문단 하나가 너무 깁니다 (최대 1만 자).",
  document_too_long: "문서가 너무 깁니다 (최대 6만 자).",
  too_many_blocks: "문단이 너무 많습니다 (최대 200개).",
  too_many_claims: "문단 하나에 근거는 20개까지입니다.",
  invalid_count_rule: "글자 수 기준 값이 맞지 않습니다.",
  nothing_to_submit: "제출할 글이 없습니다.",
  unknown_document: "지원에 없는 문서입니다.",
  // c0012 공용 문서·내보내기·전형 단계·민감 정보
  question_not_shared: "공고 문항은 기본 문서로 만들 수 없습니다.",
  nothing_to_copy: "복사할 글이 없습니다. 기본 문서를 먼저 저장하세요.",
  nothing_to_export: "내보낼 글이 없습니다. 먼저 저장하세요.",
  invalid_format: "지원하지 않는 내보내기 형식입니다.",
  invalid_spans: "문장 근거 위치가 문단 글과 맞지 않습니다.",
  invalid_cliches: "상투어는 한 줄에 40자까지 적습니다.",
  too_many_cliches: "상투어는 300개까지입니다.",
  invalid_contacts: "연락처 형식이 맞지 않습니다 (각 10개까지).",
  not_submitted: "제출본을 고정한 뒤에 전형 단계를 기록합니다.",
  too_many_stages: "전형 단계는 30개까지입니다.",
  invalid_stage_kind: "전형 종류 값이 맞지 않습니다.",
  invalid_outcome: "결과 값이 맞지 않습니다.",
  sensitive_unacknowledged: "민감 정보 경고를 확인해야 고정할 수 있습니다.",
  // O09 학습
  mistake_resolved: "다 익힌 오답입니다. 다시 열어야 복습할 수 있습니다.",
  no_study_result: "학습을 마쳤거나 응시 기록이 있어야 경험 후보로 올릴 수 있습니다.",
  mistake_target_required: "오답은 학습 항목이나 응시 기록에 붙여야 합니다.",
  attempt_target_required: "응시는 시험 준비나 학습 항목에 붙여야 합니다.",
  invalid_status: "상태 값이 맞지 않습니다.",
  invalid_score: "점수 값이 맞지 않습니다 (만점은 0보다 커야 합니다).",
  score_above_max: "점수가 만점보다 큽니다.",
  invalid_minutes: "시간(분) 값이 맞지 않습니다.",
  minutes_required: "공부한 시간(분)을 넣어 주세요.",
  question_required: "문제(무엇을 틀렸는지)를 적어 주세요.",
  statement_required: "사실 문장을 직접 적어 주세요.",
  requirement_not_in_posting: "공고에 없는 요건 문장입니다.",
  attempt_not_for_item: "이 학습 항목의 응시 기록이 아닙니다.",
  text_too_long: "글이 너무 깁니다.",
  // 학습 백로그 (c0013)
  item_merged: "다른 항목에 합쳐진 학습 항목입니다. 합친 항목에서 기록하세요.",
  item_has_records: "응시·경험 후보·복습 기록이 있어 지울 수 없습니다. 그만둠으로 두거나 다른 항목에 합치세요.",
  merge_same_item: "같은 항목끼리는 합칠 수 없습니다.",
  requirement_current: "지금 공고에 있는 요건은 정리하지 않습니다.",
  invalid_timezone: "알 수 없는 시간대입니다 (예: Asia/Seoul).",
  // 공간 설정 (c0021)
  invalid_aliases: "별칭 형식이 맞지 않습니다.",
  too_many_aliases: "별칭이 너무 많습니다 (항목 200개, 항목당 20개까지).",
  invalid_alias_term: "대표 표기가 비었거나 40자를 넘습니다.",
  invalid_alias: "다른 표기가 비었거나 40자를 넘습니다.",
  alias_conflict: "같은 표기가 두 기술에 들어 있습니다.",
  // 연결·관리 (O22)
  connection_name_taken: "같은 이름의 연결이 이미 있습니다 (models.toml의 연결 이름과도 겹치면 안 됩니다).",
  connection_in_use: "이 연결을 쓰는 용도별 모델이 있어 해제할 수 없습니다. 용도별 모델에서 먼저 바꾸세요.",
  invalid_preset: "알 수 없는 연결 종류입니다.",
  unknown_preset: "알 수 없는 연결 종류입니다.",
  connection_not_found: "연결을 찾을 수 없습니다.",
  connection_read_only: "models.toml에 적은 연결은 화면에서 수정할 수 없습니다. 파일에서 수정하세요.",
  invalid_name: "이름은 영문 소문자로 시작하고 소문자·숫자·-·_ 만 씁니다 (40자까지).",
  base_url_required: "주소를 넣어 주세요.",
  invalid_base_url: "주소 형식이 맞지 않습니다 (http:// 또는 https://).",
  invalid_billing: "과금 방식이 맞지 않습니다.",
  invalid_price_as: "환산 기준이 맞지 않습니다.",
  invalid_token_param: "토큰 파라미터가 맞지 않습니다.",
  invalid_data_class: "보낼 수 있는 자료 등급이 맞지 않습니다.",
  invalid_api_key: "키 형식이 맞지 않습니다.",
  key_not_used: "이 연결 종류는 키를 쓰지 않습니다.",
  key_missing: "저장된 키가 없어 연결을 확인하지 않았습니다.",
  invalid_route: "용도별 모델은 '연결/모델' 형식이어야 합니다.",
  invalid_purpose: "알 수 없는 용도입니다.",
  unknown_endpoint: "없는 연결입니다.",
  local_only_embed: "로컬 모델은 검색 색인과 검색 결과 다시 정렬에만 쓸 수 있습니다.",
  embed_needs_openai: "임베딩에는 OpenAI 호환 또는 로컬 연결이 필요합니다.",
  invalid_budget: "예산은 0 이상의 금액이어야 합니다.",
  name_immutable: "연결 이름은 바꿀 수 없습니다. 새로 만들고 예전 것을 해제하세요.",
  restricted_not_allowed: "민감 자료(restricted)는 모델 연결로 보낼 수 없습니다.",
  not_enabled: "이 설치에서는 화면에서 모델 연결을 바꿀 수 없습니다.",
  unreachable: "주소에 닿지 않습니다. 서버가 켜져 있는지, 주소가 맞는지 확인하세요.",
  unauthorized: "키가 거절되었습니다. 키를 확인하세요.",
  bad_response: "응답이 모델 목록 형식이 아닙니다. 주소 끝이 /v1 인지 확인하세요.",
  // c0035 참고 자료 보관함 (다른 사람의 글)
  reference_title_required: "제목을 적어 주세요.",
  reference_kind_required: "종류를 골라 주세요.",
  title_too_long: "제목은 300자까지입니다.",
  role_too_long: "직무는 100자까지입니다.",
  source_note_too_long: "출처 메모는 1,000자까지입니다.",
  invalid_year: "연도는 1950~2100 사이 숫자입니다.",
  text_or_source_required: "글을 붙여넣거나 참고 자료를 하나 골라 주세요.",
  source_not_reference: "참고 자료로 넣은 자료만 참고 글로 만들 수 있습니다.",
  source_not_ready: "아직 자료를 읽는 중입니다. 읽기가 끝나면 다시 시도합니다.",
  source_empty: "자료에서 읽은 글이 없습니다.",
  source_has_reference_items: "참고 글이 이 자료를 쓰고 있어 용도를 바꿀 수 없습니다. 참고 글을 먼저 삭제하세요.",
  note_empty: "메모 칸을 하나 이상 채워 주세요.",
  question_too_long: "문항은 1,000자까지입니다.",
  structure_item_too_long: "문단 구성은 한 줄에 200자까지입니다.",
  materials_item_too_long: "소재 종류는 한 줄에 200자까지입니다.",
  competencies_item_too_long: "강조 역량은 한 줄에 200자까지입니다.",
  too_many_structure: "문단 구성은 20줄까지입니다.",
  too_many_materials: "소재 종류는 20줄까지입니다.",
  too_many_competencies: "강조 역량은 20줄까지입니다.",
  // c0033 회사 조사 (참고 지식)
  company_exists: "같은 이름의 회사가 이미 있습니다.",
  name_required: "회사 이름을 적어 주세요.",
  name_too_long: "회사 이름은 200자까지입니다.",
  company_alias_too_long: "다른 이름은 하나에 200자까지입니다.",
  too_many_company_aliases: "다른 이름은 20개까지입니다.",
  invalid_homepage: "홈페이지 주소 형식이 아닙니다.",
  invalid_corp_code: "고유번호는 숫자 8자리입니다.",
  invalid_business_no: "사업자등록번호는 숫자 10자리입니다.",
  invalid_kind: "알 수 없는 종류입니다.",
  body_required: "내용을 적어 주세요.",
  body_too_long: "내용은 2,000자까지입니다.",
  quote_required: "원문에서 인용할 글을 붙여넣어 주세요.",
  quote_too_long: "인용은 2,000자까지입니다.",
  quote_not_found: "인용한 글을 원문에서 찾지 못했습니다. 원문에서 그대로 복사해 붙여넣으세요.",
  page_not_ready: "아직 원문을 가져오는 중이거나 가져오지 못한 원문입니다.",
  finance_needs_as_of: "재무 항목은 기준일(연도 또는 날짜)이 있어야 합니다.",
  invalid_as_of: "기준일은 2024, 2024-06, 2024-06-30 꼴이어야 합니다.",
  text_required: "붙여넣을 글이 비어 있습니다.",
  posting_ids_required: "연결할 공고를 골라 주세요.",
  unknown_posting: "찾을 수 없는 공고가 있습니다.",
  too_many_postings: "한 번에 200개까지 연결할 수 있습니다.",
};

// ---------------------------------------------------------------- O07 지원·문서

export const APP_STATUS: Record<ApplicationStatus, { label: string; tone: BadgeTone }> = {
  draft: { label: "준비 중", tone: "accent" },
  submitted: { label: "제출함", tone: "ok" },
  passed: { label: "통과", tone: "ok" },
  rejected: { label: "불합격", tone: "none" },
  withdrawn: { label: "철회", tone: "none" },
};

export const DOC_KIND: Record<DocumentKind, string> = {
  question: "자기소개서 문항",
  resume: "이력서",
  cover_letter: "자기소개서",
  portfolio: "포트폴리오",
  other: "기타 문서",
};

export const SPACES_RULE: Record<string, string> = { include: "공백 포함", exclude: "공백 제외" };
export const NEWLINE_RULE: Record<string, string> = {
  one: "줄바꿈 1자",
  two: "줄바꿈 2자",
  none: "줄바꿈 안 셈",
};

/** 검사 경고 분류: 위험(다른 회사명·범용 문장) / 근거(근거 없는 문장) / 조작 / 분량 / 참고 */
export type FindingGroup = "limit" | "risk" | "evidence" | "manipulation" | "sensitive" | "info";

export const FINDING: Record<string, { label: string; group: FindingGroup }> = {
  over_char_limit: { label: "글자 수 초과", group: "limit" },
  over_byte_limit: { label: "바이트 수 초과", group: "limit" },
  unencodable_characters: { label: "양식 인코딩에 없는 문자", group: "limit" },
  other_company_name: { label: "다른 회사 이름", group: "risk" },
  // c0035 참고 글(다른 사람의 글)과 많이 겹치는 문장
  similar_to_reference: { label: "참고 글과 비슷함", group: "risk" },
  generic_company_swap: { label: "회사만 바꿔도 되는 문장", group: "risk" },
  unsupported_statement: { label: "근거 없는 경험·수치", group: "evidence" },
  number_not_in_evidence: { label: "근거에 없는 숫자", group: "evidence" },
  role_not_in_evidence: { label: "근거에 없는 역할 표현", group: "evidence" },
  evidence_unconfirmed: { label: "확인 전 근거", group: "evidence" },
  hidden_characters: { label: "보이지 않는 문자", group: "manipulation" },
  hidden_text_markup: { label: "숨김 서식", group: "manipulation" },
  screening_instruction: { label: "심사 프로그램에 지시하는 문장", group: "manipulation" },
  keyword_repetition: { label: "같은 단어 반복", group: "manipulation" },
  keyword_list_unsupported: { label: "근거 없는 키워드 나열", group: "manipulation" },
  resident_registration_number: { label: "주민등록번호", group: "sensitive" },
  phone_number: { label: "본인 것이 아닌 전화번호", group: "sensitive" },
  email_address: { label: "본인 것이 아닌 이메일", group: "sensitive" },
  company_confidential: { label: "대외비·기밀 표시", group: "sensitive" },
  cliche: { label: "상투어", group: "info" },
  paragraph_not_specific: { label: "구체성 부족", group: "info" },
  // c0016 올린 DOCX·PDF 숨김 글 검사
  hidden_text_vanish: { label: "숨김 서식 글", group: "manipulation" },
  hidden_text_invisible: { label: "보이지 않게 그린 글", group: "manipulation" },
  hidden_text_white: { label: "흰 글씨", group: "manipulation" },
  hidden_text_tiny: { label: "1pt 미만 글자", group: "manipulation" },
  hidden_text_outside_page: { label: "쪽 밖 글", group: "manipulation" },
  // c0023 PDF 배경 색·주석·양식 칸
  hidden_text_same_color: { label: "배경과 같은 색 글", group: "manipulation" },
  hidden_text_annotation: { label: "숨긴 주석·양식 칸 글", group: "manipulation" },
  annotation_text: { label: "인쇄되지 않는 주석 글", group: "info" },
  // PDF 글 뒤에 덮은 불투명 도형·이미지
  hidden_text_covered: { label: "덮어 가린 글", group: "manipulation" },
  pages_unreadable: { label: "읽지 못한 쪽", group: "info" },
  pages_not_checked: { label: "검사하지 않은 쪽", group: "info" },
};

export const FINDING_GROUP: Record<FindingGroup, { label: string; tone: BadgeTone }> = {
  limit: { label: "분량", tone: "danger" },
  risk: { label: "위험", tone: "danger" },
  manipulation: { label: "조작 의심", tone: "danger" },
  evidence: { label: "근거", tone: "warn" },
  sensitive: { label: "민감 정보", tone: "danger" },
  info: { label: "참고", tone: "neutral" },
};

export const VISIBILITY: Record<Visibility, { label: string; tone: BadgeTone }> = {
  met_visible: { label: "글에 보임", tone: "ok" },
  missing: { label: "경험은 있는데 글에 없음", tone: "warn" },
  overclaim_risk: { label: "과장 위험", tone: "danger" },
  unmet: { label: "경험 없음", tone: "none" },
  not_checkable: { label: "직접 확인", tone: "neutral" },
};

/** AI 수정안 거부 사유 (코드가 원문·근거와 비교해 판정) */
export const REJECT_REASON: Record<string, string> = {
  number: "새 숫자",
  role: "새 역할 표현 (예: 참여 → 주도)",
  organization: "새 조직명",
  new_term: "새 기술명·고유명사",
  ats_manipulation: "조작 문구",
};

export const SUGGESTION_STATUS: Record<string, { label: string; tone: BadgeTone }> = {
  proposed: { label: "제안", tone: "accent" },
  rejected: { label: "사실 변경으로 거부", tone: "danger" },
  applied: { label: "적용함", tone: "ok" },
  dismissed: { label: "닫음", tone: "none" },
};

export const REVISION_ORIGIN: Record<string, string> = {
  user: "직접 저장",
  ai_suggestion: "AI 수정안 적용",
};

export const STAGE_KIND: Record<StageKind, string> = {
  document: "서류",
  test: "테스트",
  assignment: "과제",
  interview: "면접",
  final: "최종",
  other: "기타",
};

export const STAGE_OUTCOME: Record<StageOutcome, { label: string; tone: BadgeTone }> = {
  pending: { label: "결과 전", tone: "accent" },
  passed: { label: "통과", tone: "ok" },
  failed: { label: "탈락", tone: "none" },
  withdrawn: { label: "포기", tone: "none" },
  cancelled: { label: "취소됨", tone: "neutral" },
};

export const EXPORT_FORMAT: Record<ExportFormat, string> = {
  md: "Markdown (.md)",
  docx: "Word (.docx)",
  html: "PDF (인쇄 화면)",
};

// ---------------------------------------------------------------- O09 학습

export const COVERAGE: Record<CoverageStatus, { label: string; tone: BadgeTone }> = {
  evidenced: { label: "입증함", tone: "ok" },
  claim_pending: { label: "사실 확인 대기", tone: "warn" },
  studied: { label: "학습함", tone: "accent" },
  studying: { label: "학습 중", tone: "accent" },
  uncovered: { label: "근거 없음", tone: "none" },
};

export const REQ_FIELD: Record<string, string> = {
  requirements: "자격 요건",
  preferred: "우대 사항",
  process: "전형 절차",
};

export const ITEM_STATUS: Record<StudyItemStatus, { label: string; tone: BadgeTone }> = {
  planned: { label: "계획", tone: "neutral" },
  in_progress: { label: "진행 중", tone: "accent" },
  done: { label: "완료", tone: "ok" },
  dropped: { label: "그만둠", tone: "none" },
  merged: { label: "합쳐짐", tone: "none" },
};

export const ITEM_KIND: Record<StudyItemKind, string> = {
  skill: "기술·도구",
  topic: "개념·지식",
  other: "기타",
};

export const ASSESSMENT_KIND: Record<AssessmentKind, string> = {
  coding_test: "코딩테스트",
  written_test: "필기·인적성",
  assignment: "과제",
  interview: "면접",
  certification: "자격증",
  other: "기타",
};

export const ASSESSMENT_STATUS: Record<AssessmentStatus, { label: string; tone: BadgeTone }> = {
  planned: { label: "예정", tone: "accent" },
  done: { label: "마침", tone: "ok" },
  cancelled: { label: "취소", tone: "none" },
};

export const ATTEMPT_RESULT: Record<AttemptResult, { label: string; tone: BadgeTone }> = {
  passed: { label: "통과", tone: "ok" },
  failed: { label: "못 넘음", tone: "danger" },
  unknown: { label: "결과 모름", tone: "neutral" },
};

export const MISTAKE_REASON: Record<MistakeReason, string> = {
  concept: "개념 부족",
  careless: "실수",
  time: "시간 부족",
  misread: "문제 잘못 읽음",
  unknown: "모름",
};

export const GRADE: Record<ReviewGrade, { label: string; hint: string }> = {
  again: { label: "다시", hint: "1일 뒤" },
  hard: { label: "어려움", hint: "조금 뒤" },
  good: { label: "보통", hint: "간격 늘림" },
  easy: { label: "쉬움", hint: "더 늘림" },
};

// ---------------------------------------------------------------- 원본·활동

/** 원본 용도 (c0032). 올릴 때와 원본 화면에서 같은 말로 묻는다 */
export const PURPOSE_OPTIONS: { value: SourcePurpose; label: string; text: string }[] = [
  { value: "experience", label: "내 경험 자료", text: "경험과 성과를 뽑아 브레인에 넣습니다." },
  {
    value: "proof",
    label: "증빙 서류",
    text: "자격증·성적표·증명서 사본. 원본을 보관하고 외부 모델에 보내지 않습니다.",
  },
  {
    value: "reference",
    label: "참고 자료",
    text: "회사 자료·다른 사람의 자기소개서·후기. 내 경험으로 쓰지 않습니다.",
  },
];

export const PURPOSE_LABEL: Record<SourcePurpose, string> = {
  experience: "내 경험 자료",
  proof: "증빙 서류",
  reference: "참고 자료",
};

/** 받은 자료함에 올라온 이유. warn은 진하게, danger는 빨강, 나머지는 회색 */
export const ATTENTION_LABEL: Record<SourceAttention, { label: string; tone: BadgeTone }> = {
  purpose_unconfirmed: { label: "확인 필요", tone: "warn" },
  parse_failed: { label: "읽기 실패", tone: "danger" },
  extraction_failed: { label: "추출 실패", tone: "danger" },
  processing: { label: "읽는 중", tone: "neutral" },
  extracting: { label: "추출 중", tone: "neutral" },
  pending_claims: { label: "확인할 사실", tone: "warn" },
  parse_partial: { label: "일부만 읽음", tone: "warn" },
  extraction_partial: { label: "일부만 추출", tone: "warn" },
  not_extracted: { label: "추출 전", tone: "warn" },
};

/** 자료를 어디까지 보내는지 (data_class). 화면에는 등급 이름 대신 이 문장으로 */
export const DATA_CLASS_LABEL: Record<string, string> = {
  public: "공개 자료",
  private: "내 연결에만 보냄",
  restricted: "이 기기 밖으로 보내지 않음",
};

export const PARSE_STATUS: Record<string, { label: string; tone: BadgeTone }> = {
  pending: { label: "읽는 중", tone: "accent" },
  waiting_provider: { label: "외부 처리 대기", tone: "accent" },
  complete: { label: "읽음", tone: "ok" },
  partial: { label: "일부만 읽음", tone: "warn" },
  failed: { label: "읽기 실패", tone: "danger" },
};

export const EXTRACTION_STATUS: Record<string, { label: string; tone: BadgeTone }> = {
  pending: { label: "경험 추출 중", tone: "accent" },
  complete: { label: "추출함", tone: "ok" },
  partial: { label: "일부 추출", tone: "warn" },
  failed: { label: "추출 실패", tone: "danger" },
};

export const ACTIVITY_LABEL: Record<string, string> = {
  "claim:created": "사실 추가",
  "claim:confirmed": "사실 확인",
  "posting:created": "공고 추가",
  "posting:saved": "공고 저장",
  "fetch_run:fetched": "공고 가져오기",
  "document_revision:revised": "문서 저장",
  "study_attempt:attempted": "응시 기록",
};
