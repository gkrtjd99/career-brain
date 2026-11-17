// 코어 HTTP 응답 타입. packages/core/src/career_core/interfaces/http 의 Pydantic 모델과 맞춘다.
// UUID·datetime은 문자열, Decimal은 문자열(JSON에서 정밀도를 잃지 않게)로 온다.

export type Uuid = string;
export type IsoDateTime = string;
export type DecimalString = string;

export interface CoreHealth {
  status: string;
  core_version: string;
}

export interface Me {
  actor_id: Uuid;
  workspace: { id: Uuid; name: string };
  scopes: string[];
}

export interface StatusOut {
  id: Uuid;
  status: string;
}

// ---------------------------------------------------------------- sources

export interface UploadOut {
  source_id: Uuid;
  version_id: Uuid;
  parse_run_id: Uuid | null;
  deduplicated: boolean;
  /** c0029: 숨김 서식 글을 블록에 넣는지·빼는지 (DOCX만 exclude). 같은 파일이면 처음 값 */
  hidden_text: HiddenTextMode;
  /** c0032: 원본의 용도·등급. 같은 파일이면 처음 원본의 용도 (증빙이면 등급은 restricted) */
  purpose: SourcePurpose;
  data_class: string;
}

export type HiddenTextMode = "keep" | "exclude";

/**
 * 원본 용도 (c0032). experience = 내 경험 자료(경험·주장 추출), proof = 증빙 서류(추출 안 함,
 * 늘 restricted, 검색·MCP에 안 나옴), reference = 참고 자료(추출 안 함, 기본 검색에서 빠짐).
 */
export type SourcePurpose = "experience" | "proof" | "reference";

/** 받은 자료함에 올라온 이유 (앞의 것이 더 급하다). null = 다 처리됨 */
export type SourceAttention =
  | "parse_failed"
  | "extraction_failed"
  | "processing"
  | "extracting"
  | "pending_claims"
  | "parse_partial"
  | "extraction_partial"
  | "not_extracted";

export interface PurposeSuggestion {
  suggested_purpose: SourcePurpose;
  /** 걸린 낱말 또는 "image". 기본값이면 null */
  matched: string | null;
}

export interface PurposeChange {
  source_id: Uuid;
  purpose: SourcePurpose;
  data_class: string;
  changed: boolean;
  discarded_claims: number;
  discarded_experiences: number;
  cancelled_extractions: number;
  /** → experience: 큐에 넣은 추출 (읽기 전이거나 권한이 없으면 null) */
  extraction_run_id: Uuid | null;
}

export interface ParseOut {
  run_id: Uuid;
  status: string;
  pages_total: number | null;
  missing_pages: number[];
  error_code: string | null;
  hidden_text: HiddenTextMode;
}

export interface SourceVersion {
  id: Uuid;
  sha256: string;
  mime_type: string;
  size_bytes: number;
  original_filename: string;
  parse: ParseOut | null;
}

export interface Source {
  id: Uuid;
  title: string;
  data_class: string;
  versions: SourceVersion[];
  purpose: SourcePurpose;
  created_at: IsoDateTime | null;
  /** 최신 읽기 결과로 요청한 마지막 경험 추출 */
  extraction: { id: Uuid; status: string; error_code: string | null; claims_created: number } | null;
  /** 이 원본에서 뽑은 확인 전 후보 / 이 원본을 근거로 단 확인된 주장 */
  pending_claims: number;
  confirmed_claims: number;
}

export interface SourceListItem {
  id: Uuid;
  title: string;
  data_class: string;
  created_at: IsoDateTime;
  versions: number;
  latest_version_id: Uuid | null;
  original_filename: string | null;
  mime_type: string | null;
  size_bytes: number | null;
  parse_run_id: Uuid | null;
  parse_status: string | null;
  pages_total: number | null;
  missing_pages: number[];
  parse_error: string | null;
  extraction_run_id: Uuid | null;
  extraction_status: string | null;
  claims_created: number | null;
  purpose: SourcePurpose;
  pending_claims: number;
  attention: SourceAttention | null;
}

export interface Block {
  id: Uuid;
  page: number | null;
  ordinal: number;
  origin: string;
  text: string;
}

// ---------------------------------------------------------------- brain

export type ClaimStatus =
  | "extracted"
  | "self_confirmed"
  | "evidence_supported"
  | "disputed"
  | "retired";

export type ClaimKind =
  | "responsibility"
  | "action"
  | "result"
  | "decision"
  | "difficulty"
  | "skill"
  | "other";

export type Contribution = "personal" | "team" | "unknown";

/** 경험 종류 (c0030, O19) */
export type ExperienceKind = "work" | "project" | "education" | "activity" | "other";
/** 월: "YYYY-MM" (그 달 1일로 저장된다) */
export type YearMonth = string;

/** 경험 구조 칸 (c0030). 날짜는 원문 기간이 분명할 때만 채워지고, 원문은 period_text 그대로다. */
export interface ExperienceStructure {
  kind: ExperienceKind;
  parent_id: Uuid | null;
  parent_title: string | null;
  started_on: YearMonth | null;
  ended_on: YearMonth | null;
  ongoing: boolean;
  tags: string[];
}

export interface ExperienceSummary extends ExperienceStructure {
  id: Uuid;
  title: string;
  organization: string | null;
  role: string | null;
  period_text: string | null;
  origin: string;
  claim_counts: Partial<Record<ClaimStatus, number>>;
  needs_review: number;
  children_count: number;
  updated_at: IsoDateTime | null;
}

export type ExperienceReviewFilter = "needs_review" | "confirmed" | "no_claims";
export type ExperienceSort = "start" | "updated" | "title";

/** GET /core/experiences 거름 (모두 선택). 전체 수는 X-Total-Count. */
export interface ExperienceQuery {
  kind?: ExperienceKind[];
  parentId?: Uuid;
  topLevel?: boolean;
  /** 하나라도 있으면 (대소문자 무시) */
  tags?: string[];
  /** 그해와 기간이 겹친다 */
  year?: number;
  org?: string;
  status?: ExperienceReviewFilter;
  q?: string;
  sort?: ExperienceSort;
  limit?: number;
  offset?: number;
}

export interface ExperiencePage {
  items: ExperienceSummary[];
  total: number;
}

export interface ChildExperience {
  id: Uuid;
  title: string;
  kind: ExperienceKind;
  role: string | null;
  started_on: YearMonth | null;
  ended_on: YearMonth | null;
  ongoing: boolean;
}

export interface BrainOverview {
  experiences: number;
  kinds: Record<ExperienceKind, number>;
  /** 확인할 주장이 있는 경험 수 */
  needs_review: number;
  pending_claims: number;
  /** 시작 달이 없는 경험 수 */
  undated: number;
  year_min: number | null;
  year_max: number | null;
  recent: { id: Uuid; title: string; kind: ExperienceKind; organization: string | null; updated_at: IsoDateTime }[];
  tags: { tag: string; count: number }[];
}

export interface Evidence {
  id: Uuid;
  kind: "source_span" | "interview_answer" | string;
  span_id: Uuid | null;
  quote: string | null;
  page: number | null;
  source_title: string | null;
  answer_id: Uuid | null;
  question: string | null;
  answer: string | null;
}

export interface Claim {
  id: Uuid;
  kind: ClaimKind;
  statement: string;
  contribution: Contribution;
  status: ClaimStatus;
  origin: string;
  confirmed_at: IsoDateTime | null;
  evidence: Evidence[];
}

export interface Experience extends ExperienceStructure {
  id: Uuid;
  title: string;
  organization: string | null;
  role: string | null;
  period_text: string | null;
  origin: string;
  status: string;
  claims: Claim[];
  children: ChildExperience[];
  updated_at: IsoDateTime | null;
}

export interface ClaimBrief {
  id: Uuid;
  statement: string;
  status: ClaimStatus;
  kind: ClaimKind;
  contribution: Contribution;
  experience_id: Uuid;
  experience_title: string;
}

export interface ExperienceIn {
  title: string;
  organization?: string | null;
  role?: string | null;
  period_text?: string | null;
  kind?: ExperienceKind;
  parent_id?: Uuid | null;
  started_on?: YearMonth | null;
  ended_on?: YearMonth | null;
  ongoing?: boolean;
  tags?: string[];
}

/** PATCH /core/experiences/{id}: 준 칸만 고친다 (null은 비우기, 제목·종류·진행 중은 null 불가) */
export type ExperiencePatch = Partial<ExperienceIn>;

export interface ClaimIn {
  statement: string;
  kind?: ClaimKind;
  contribution?: Contribution;
  span_ids?: Uuid[];
}

export interface ClaimPatch {
  statement?: string;
  kind?: ClaimKind;
  contribution?: Contribution;
}

export interface ConfirmIn {
  contribution?: Contribution;
  statement?: string;
}

export interface Span {
  id: Uuid;
  quote: string;
  start_char: number;
  end_char: number;
  block_id: Uuid;
  block_text: string;
  page: number | null;
  ordinal: number;
  parse_run_id: Uuid;
  source_id: Uuid;
  source_title: string;
  original_filename: string;
}

export interface ExtractionRun {
  id: Uuid;
  parse_run_id: Uuid;
  status: string;
  error_code: string | null;
  blocks_total: number;
  blocks_sent: number;
  experiences_created: number;
  claims_created: number;
  claims_dropped: number;
}

export interface InterviewQuestion {
  id: Uuid;
  ordinal: number;
  focus: string;
  prompt: string;
  origin: string;
  answers: string[];
  /** c0023: 이 질문을 만든 일괄 실행 (interview_question_batch). 직접 요청·정해진 질문이면 null */
  run_id?: Uuid | null;
}

export interface InterviewSummary {
  id: Uuid;
  experience_id: Uuid | null;
  experience_title: string | null;
  status: string;
  questions: number;
  answered: number;
  created_at: IsoDateTime;
  last_answer_at: IsoDateTime | null;
  /** c0023: 이 세션에 질문을 만든 일괄 실행들 (오래된 것부터) */
  run_ids?: Uuid[];
}

export interface Interview {
  id: Uuid;
  experience_id: Uuid | null;
  status: string;
  questions: InterviewQuestion[];
}

export interface AnswerIn {
  answer: string;
  make_claim?: boolean;
  statement?: string | null;
  kind?: ClaimKind;
  contribution?: Contribution;
  experience_id?: Uuid | null;
}

export interface AnswerOut {
  answer_id: Uuid;
  claim_id: Uuid | null;
  claim_status: ClaimStatus | null;
}

export interface WikiPage {
  experience_id: Uuid;
  title: string;
  organization: string | null;
  role: string | null;
  period_text: string | null;
  sections: { kind: string; title: string; claims: Claim[] }[];
  confirmed: number;
  pending: number;
}

// ---------------------------------------------------------------- search

/** study_note·study_mistake: 학습 메모·오답 (학습 기록, 경험 아님). 기본 검색에는 없고 kinds로 요청한다 */
export type SearchKind = "claim" | "block" | "answer" | "study_note" | "study_mistake";
/** reference = 참고 자료의 원문 (include_reference일 때만, 내 경험이 아님) */
export type SearchRecord = "experience" | "source" | "study" | "reference";

export interface SearchHit {
  kind: SearchKind;
  id: Uuid;
  snippet: string;
  score: number;
  keyword_rank: number | null;
  semantic_rank: number | null;
  claim_status: ClaimStatus | null;
  experience_id: Uuid | null;
  experience_title: string | null;
  source_id: Uuid | null;
  source_title: string | null;
  page: number | null;
  question: string | null;
  /** 원문(block) 결과만: 원본 보기를 열 위치. match_* 는 블록 원문 안 첫 일치 낱말의 글자 범위 */
  block_id?: Uuid | null;
  parse_run_id?: Uuid | null;
  ordinal?: number | null;
  match_start?: number | null;
  match_end?: number | null;
  /** experience(주장·인터뷰 답) | source(원문) | study(학습 메모·오답 — 경험이 아님) */
  record?: SearchRecord;
  study_item_id?: Uuid | null;
  study_title?: string | null;
  /** c0032: 원본 용도 (experience | reference) */
  purpose?: SourcePurpose;
}

/** 공간 별칭 사전으로 키워드 검색에 더한 표기 ("패스트API" → FastAPI, fast api) */
export interface ExpandedTerm {
  term: string;
  canonical: string;
  spellings: string[];
}

export interface SearchResult {
  query: string;
  mode: string;
  semantic_note: string | null;
  /** 참고 자료의 원문까지 찾았는지 (c0032) */
  include_reference?: boolean;
  hits: SearchHit[];
  expanded_terms?: ExpandedTerm[];
}

// ---------------------------------------------------------------- job postings

export type PostingStatus = "draft" | "saved" | "archived";
/** needs_confirmation: 스캔 PDF 비전 호출 예상 비용이 상한을 넘어 사람의 확인을 기다린다 (c0025) */
export type FetchStatus = "pending" | "done" | "listing" | "failed" | "needs_confirmation";
export type FetchError =
  | "source_unavailable"
  | "login_required"
  | "rate_limited"
  | "bot_blocked"
  | "maybe_bot_blocked"
  | "needs_manual_check"
  | "blocked_address"
  | "too_large"
  | "unsupported_content"
  | "invalid_url";

export const POSTING_FIELDS = [
  "title",
  "company",
  "location",
  "employment_type",
  "career_level",
  "deadline",
  "posted_at",
  "responsibilities",
  "requirements",
  "preferred",
  "questions",
  "process",
] as const;
export type PostingField = (typeof POSTING_FIELDS)[number];
export const POSTING_LIST_FIELDS: readonly PostingField[] = [
  "responsibilities",
  "requirements",
  "preferred",
  "process",
];

export interface EssayQuestion {
  prompt: string;
  char_limit: number | null;
}

/** 지원서 선택형 문항 (보기 고르기, 자기소개서 아님). 채용 시스템 구조화 데이터에서만 온다 (c0025) */
export interface ChoiceQuestion {
  prompt: string;
  required: boolean | null;
  options: string[];
  /** 채용 시스템의 원래 문항 종류 번호 (그리팅 200·400). 하나/여러 개 고르기는 구분하지 않는다 */
  source_type: number | null;
}

/** 확정 화면에서 고치지 않는(구조화 데이터에서만 오는) 항목 */
export type StructuredOnlyField = "application_questions";

export type PostingValue = string | string[] | EssayQuestion[] | ChoiceQuestion[];

/** 항목 근거. structured: JSON-LD 경로, quote: 본문 위치, quotes: 목록 항목별 인용, user: 사용자 수정 */
export type FieldEvidence =
  | { kind: "structured"; ref: string }
  | { kind: "quote"; ref: string; start: number; end: number }
  | { kind: "quotes"; ref: { kind: "quote"; ref: string; start: number; end: number }[] }
  | { kind: "user"; ref: string }
  | { kind: string; ref: unknown; start?: number; end?: number };

export interface ExtractedField {
  value: PostingValue;
  evidence: FieldEvidence;
}

export interface SaveUrlResult {
  url: string;
  posting_id: Uuid | null;
  fetch_run_id: Uuid | null;
  deduplicated: boolean;
  error: string | null;
}

export interface PostingSummary {
  id: Uuid;
  canonical_url: string;
  status: PostingStatus;
  title: string | null;
  company: string | null;
  deadline: string | null;
  pending_changes: string[];
  last_fetch_status: FetchStatus | null;
  last_fetch_error: FetchError | string | null;
  created_at: IsoDateTime | null;
  saved_at: IsoDateTime | null;
}

/** 비전 호출 비용 확인 요청 (c0025). 금액은 USD 문자열, 토큰은 추정값 */
export interface FetchConfirmation {
  id: Uuid;
  status: "pending" | "confirmed" | "declined" | "used" | "superseded";
  /** 읽을 쪽 (1부터) */
  pages: number[];
  pages_total: number;
  images: number;
  input_tokens: number;
  output_tokens: number;
  estimated_usd: string | null;
  /** 출력이 상한까지 나올 때. 상한 비교는 이 값으로 한다 */
  max_usd: string | null;
  threshold_usd: string;
  endpoint: string;
  model: string;
  created_at: IsoDateTime;
  decided_at: IsoDateTime | null;
  /** 호출별 예상: 이미지 읽기(job_extract_image) + 항목 추출(job_extract). 옛 요청은 빈 목록 */
  parts?: FetchCostPart[];
}

/** 비용 확인의 호출 하나. 합계·상한 비교에는 종량제(metered)만 들어간다 */
export interface FetchCostPart {
  purpose: string;
  endpoint: string;
  model: string;
  billing: "metered" | "subscription" | "free" | string;
  input_tokens: number;
  max_input_tokens: number;
  output_tokens: number;
  max_output_tokens: number;
  estimated_usd: string | null;
  max_usd: string | null;
  /** 출력 추정 근거: per_page | history_<n>(지난 호출 n개 평균) | default(상수) */
  basis: string;
}

export interface FetchRun {
  id: Uuid;
  status: FetchStatus;
  error_code: FetchError | string | null;
  http_status: number | null;
  unchanged: boolean;
  version_id: Uuid | null;
  candidates: { url: string; title: string }[];
  notes: string[];
  created_at: IsoDateTime;
  confirmation?: FetchConfirmation | null;
}

export interface PostingVersion {
  id: Uuid;
  version_no: number;
  method: string;
  adapter: string;
  final_url: string;
  extracted: Partial<Record<PostingField | StructuredOnlyField, ExtractedField>>;
  missing: string[];
  changed_fields: string[];
  has_screenshot: boolean;
  created_at: IsoDateTime;
  text: string | null;
}

export interface Posting {
  id: Uuid;
  canonical_url: string;
  status: PostingStatus;
  fields: Partial<Record<PostingField | StructuredOnlyField, PostingValue>>;
  evidence: Partial<Record<PostingField | StructuredOnlyField, FieldEvidence>>;
  pending_changes: string[];
  confirmed_version_id: Uuid | null;
  saved_at: IsoDateTime | null;
  latest: PostingVersion | null;
  runs: FetchRun[];
}

export interface ConfirmPostingIn {
  version_id?: Uuid | null;
  overrides?: Partial<Record<PostingField, PostingValue>>;
  clear?: PostingField[];
}

// ---------------------------------------------------------------- usage

export type UsageGroup = "day" | "model" | "purpose" | "endpoint" | "status";

export interface UsageRow {
  key: string;
  calls: number;
  ok: number;
  errors: number;
  denied: number;
  input_tokens: number;
  cached_input_tokens: number;
  cache_write_tokens: number;
  output_tokens: number;
  reasoning_tokens: number;
  spent_usd: DecimalString;
  equivalent_usd: DecimalString;
  unknown_cost_calls: number;
}

export interface Usage {
  since: IsoDateTime;
  until: IsoDateTime;
  group_by: UsageGroup;
  rows: UsageRow[];
  total: UsageRow;
  month_spent_usd: DecimalString;
  monthly_budget_usd: DecimalString | null;
  budget_remaining_usd: DecimalString | null;
  notes: string[];
}

// ---------------------------------------------------------------- activity

export type ActivityKind = "claim" | "posting" | "fetch_run" | "document_revision" | "study_attempt";

export interface ActivityItem {
  kind: ActivityKind;
  action: "created" | "confirmed" | "saved" | "fetched" | "revised" | "attempted" | string;
  id: Uuid;
  at: IsoDateTime;
  title: string;
  status: string | null;
  detail: string | null;
  refs: Partial<
    Record<
      | "experience_id"
      | "posting_id"
      | "version_id"
      | "application_id"
      | "document_id"
      | "revision_no"
      | "assessment_id"
      | "study_item_id"
      /** "true" = 공용 문서의 revision (application_id 없음, c0016) */
      | "shared",
      string
    >
  >;
}

// ---------------------------------------------------------------- applications (O07)

export type ApplicationStatus = "draft" | "submitted" | "passed" | "rejected" | "withdrawn";
export type DocumentKind = "question" | "resume" | "cover_letter" | "portfolio" | "other";

export interface CountRule {
  spaces: "include" | "exclude";
  newlines: "one" | "two" | "none";
  encoding: "utf-8" | "cp949" | "euc-kr";
}

/** 서버 코드가 센 글자·바이트 수. 모델이 센 값이 아니다. */
export interface TextCounts {
  chars: number;
  chars_with_spaces: number;
  chars_without_spaces: number;
  bytes: number;
  newlines: number;
  rule: CountRule;
  char_limit: number | null;
  byte_limit: number | null;
  over_char_limit: boolean;
  over_byte_limit: boolean;
  unencodable: string[];
}

export interface ApplicationSummary {
  id: Uuid;
  posting_id: Uuid;
  title: string;
  status: ApplicationStatus;
  company: string | null;
  job_title: string | null;
  deadline: string | null;
  documents: number;
  submitted_at: IsoDateTime | null;
  created_at: IsoDateTime;
  /** 공고 문항 문서 수와 그중 아직 글이 없는 수 */
  questions: number;
  unwritten: number;
}

export interface DocumentSummary {
  id: Uuid;
  kind: DocumentKind;
  title: string;
  question_index: number | null;
  question_prompt: string | null;
  char_limit: number | null;
  byte_limit: number | null;
  current_revision: number;
  counts: TextCounts | null;
  /** 공용 문서에서 복사한 문서면 출처 (c0012) */
  source?: DocumentSource | null;
}

/** 복사 출처: 원본 공용 문서와 복사한 revision. 원본이 그 뒤에 바뀌어도 그대로다. */
export interface DocumentSource {
  document_id: Uuid;
  revision_id: Uuid;
  revision_no?: number | null;
  title?: string | null;
  current_revision?: number | null;
}

export interface Application {
  id: Uuid;
  posting_id: Uuid;
  posting_version_id: Uuid | null;
  title: string;
  status: ApplicationStatus;
  result_note: string | null;
  submitted_at: IsoDateTime | null;
  posting: Record<string, unknown>;
  posting_pending_changes: string[];
  documents: DocumentSummary[];
  submission_id: Uuid | null;
}

export interface DocBlock {
  id: string;
  text: string;
  origin: "user" | "ai" | string;
  claim_ids: string[];
  /** 문장 단위 근거 (문단 안 글자 위치). 없으면 문단 단위 근거만 있다. */
  spans?: DocSpan[];
}

export interface DocSpan {
  start: number;
  end: number;
  claim_ids: string[];
}

export interface AppDocument {
  id: Uuid;
  /** 공용 문서면 null */
  application_id: Uuid | null;
  kind: DocumentKind;
  title: string;
  question_index: number | null;
  question_prompt: string | null;
  char_limit: number | null;
  byte_limit: number | null;
  count_rule: CountRule;
  current_revision: number;
  revision_no: number;
  revision_id: Uuid | null;
  blocks: DocBlock[];
  text: string;
  counts: TextCounts;
  ai_ratio: number;
  editable: boolean;
  source?: DocumentSource | null;
}

export interface DocumentIn {
  kind: DocumentKind;
  title: string;
  question_prompt?: string | null;
  char_limit?: number | null;
  byte_limit?: number | null;
  count_rule?: Partial<CountRule> | null;
}

export interface DocumentPatch {
  title?: string;
  char_limit?: number | null;
  byte_limit?: number | null;
  count_rule?: Partial<CountRule>;
}

export interface RevisionSummary {
  id: Uuid;
  revision_no: number;
  origin: string;
  reason: string | null;
  content_hash: string;
  counts: TextCounts;
  created_by: Uuid;
  created_at: IsoDateTime;
}

export interface SpanIn {
  start: number;
  end: number;
  claim_ids: Uuid[];
}

export type DocOperation =
  | { op: "replace"; block_id: string; text: string; claim_ids?: Uuid[]; spans?: SpanIn[] }
  | { op: "insert"; text: string; index?: number; claim_ids?: Uuid[]; spans?: SpanIn[] }
  | { op: "delete"; block_id: string }
  | { op: "link"; block_id: string; claim_ids: Uuid[] }
  /** 문단의 문장 단위 근거를 통째로 바꾼다 (빈 목록이면 지운다) */
  | { op: "link_spans"; block_id: string; spans: SpanIn[] }
  | {
      op: "set_blocks";
      blocks: { id?: string | null; text: string; claim_ids?: Uuid[]; spans?: SpanIn[] | null }[];
    };

export interface RevisionIn {
  base_revision: number;
  operations: DocOperation[];
  reason?: string | null;
  idempotency_key?: string | null;
}

export interface RevisionOut {
  id: Uuid;
  revision_no: number;
  created: boolean;
}

/** 409 `revision_conflict` 의 detail */
export interface RevisionConflictDetail {
  code: "revision_conflict";
  current_revision: number;
  stale: unknown[];
}

export interface Finding {
  code: string;
  severity: "error" | "warning" | "info" | string;
  message: string;
  block_id: string | null;
  sentence: string | null;
  detail: Record<string, unknown>;
}

export type Visibility = "met_visible" | "missing" | "overclaim_risk" | "unmet" | "not_checkable";

export interface RequirementVisibility {
  kind: "requirements" | "preferred" | string;
  requirement: string;
  terms: string[];
  verdict: Visibility;
  brain_claim_ids: string[];
  document_terms: string[];
  linked: boolean;
  suggestion: string;
}

export interface ValidateResult {
  document_id: Uuid;
  revision: number;
  counts: TextCounts;
  ai_ratio: number;
  findings: Finding[];
  requirements: RequirementVisibility[];
  summary: Record<string, number>;
}

export interface ApplicationChecks {
  application_id: Uuid;
  documents: {
    document_id: Uuid;
    title: string;
    revision: number;
    counts: TextCounts;
    summary: Record<string, number>;
  }[];
  requirements: RequirementVisibility[];
  posting_pending_changes: string[];
}

/** 수정안 거부 사유: number·role·organization·new_term·ats_manipulation */
export interface RejectReason {
  code: string;
  values?: string[];
}

export interface Suggestion {
  id: Uuid;
  block_id: string;
  base_revision_id: Uuid;
  original_text: string;
  suggested_text: string;
  reason: string | null;
  status: "proposed" | "rejected" | "applied" | "dismissed";
  reject_reasons: RejectReason[];
  applied_revision_id: Uuid | null;
  created_at: IsoDateTime;
}

export interface SuggestOut {
  suggestions: Suggestion[];
  questions: string[];
  dropped: number;
  model_call_id: Uuid | null;
  /** 같은 idempotency_key로 다시 요청해 모델을 부르지 않고 저장된 수정안을 돌려줬다 */
  replayed?: boolean;
  /** c0016 요청 기록 ID (질문 글·개수는 suggestionRequests 로 다시 본다) */
  request_id?: Uuid | null;
}

export interface SubmissionDocument {
  document_id: Uuid;
  kind: DocumentKind;
  title: string;
  question_index: number | null;
  question_prompt: string | null;
  char_limit: number | null;
  byte_limit: number | null;
  count_rule: CountRule;
  revision_id: Uuid;
  revision_no: number;
  content_hash: string;
  blocks: DocBlock[];
  text: string;
  counts: TextCounts;
  ai_ratio: number;
  /** 제출 당시 연결된 주장의 그때 문장 */
  evidence: {
    claim_id: string;
    statement: string;
    kind: string;
    status: string;
    contribution: string;
    experience: { id: string; title: string; organization: string | null; role: string | null; period_text: string | null };
  }[];
}

export interface Submission {
  id: Uuid;
  application_id: Uuid;
  posting_id: Uuid;
  posting_version_id: Uuid | null;
  posting: { fields?: Record<string, unknown>; pending_changes?: string[]; [key: string]: unknown };
  documents: SubmissionDocument[];
  checks: {
    documents?: Record<string, { summary: Record<string, number>; findings: Finding[] }>;
    requirements?: RequirementVisibility[];
    sensitive?: { hash: string | null; acknowledged: boolean; findings: SensitiveFinding[] };
  };
  content_hash: string;
  note: string | null;
  submitted_by: Uuid;
  submitted_at: IsoDateTime;
}

// ---------------------------------------------------------------- 공용 문서·설정·내보내기·전형 단계 (c0012)

export type SharedDocumentKind = Exclude<DocumentKind, "question">;

export interface SharedDocumentSummary {
  id: Uuid;
  kind: SharedDocumentKind;
  title: string;
  char_limit: number | null;
  byte_limit: number | null;
  current_revision: number;
  counts: TextCounts | null;
  /** 이 문서를 복사해 간 지원 문서 수 */
  copies: number;
  created_at: IsoDateTime;
  updated_at: IsoDateTime;
}

export interface SharedDocumentIn {
  kind: SharedDocumentKind;
  title: string;
  char_limit?: number | null;
  byte_limit?: number | null;
  count_rule?: Partial<CountRule> | null;
}

export interface OwnContacts {
  emails: string[];
  phones: string[];
}

export interface DocumentSettings {
  cliches: string[];
  /** false면 기본 목록을 쓰는 중 */
  customized: boolean;
  default_cliches: string[];
  own_contacts: OwnContacts;
  updated_at: IsoDateTime | null;
}

/** 준 칸만 바꾼다. cliches: null 이면 기본 목록으로 되돌린다. */
export interface DocumentSettingsIn {
  cliches?: string[] | null;
  own_contacts?: OwnContacts | null;
}

/** md·docx 는 파일, html 은 브라우저에서 PDF로 인쇄할 화면 */
export type ExportFormat = "md" | "docx" | "html";

export interface ExportCheck {
  filename: string;
  media_type: string;
  format: ExportFormat;
  revision: number;
  bytes: number;
  findings: Finding[];
  summary: Record<string, number>;
}

export interface ExportFile {
  filename: string;
  blob: Blob;
  /** 서버가 만든 파일을 다시 읽어 찾은 경고 수 */
  findings: number;
  codes: string[];
}

export interface SensitiveFinding extends Finding {
  document_id: Uuid;
  title: string;
}

export interface SensitiveCheck {
  application_id: Uuid;
  /** 경고가 없으면 null. 있으면 제출할 때 sensitive_ack 로 이 값을 보낸다. */
  sensitive_hash: string | null;
  requires_ack: boolean;
  findings: SensitiveFinding[];
  summary: Record<string, number>;
}

/** 409 `sensitive_unacknowledged` 의 detail */
export interface SensitiveUnacknowledgedDetail {
  code: "sensitive_unacknowledged";
  sensitive_hash: string;
  findings: SensitiveFinding[];
}

export type StageKind = "document" | "test" | "assignment" | "interview" | "final" | "other";
export type StageOutcome = "pending" | "passed" | "failed" | "withdrawn" | "cancelled";

export interface ApplicationStage {
  id: Uuid;
  application_id: Uuid;
  submission_id: Uuid;
  position: number;
  kind: StageKind;
  name: string;
  scheduled_at: IsoDateTime | null;
  outcome: StageOutcome;
  decided_at: IsoDateTime | null;
  note: string | null;
  created_at: IsoDateTime;
  updated_at: IsoDateTime;
}

export interface StageIn {
  kind: StageKind;
  name?: string | null;
  scheduled_at?: IsoDateTime | null;
  outcome?: StageOutcome;
  note?: string | null;
}

export interface StagePatch {
  kind?: StageKind;
  name?: string | null;
  scheduled_at?: IsoDateTime | null;
  outcome?: StageOutcome;
  note?: string | null;
  position?: number;
}

// ---------------------------------------------------------------- study (O09)

export type RequirementField = "requirements" | "preferred" | "process";
export type StudyItemKind = "skill" | "topic" | "other";
export type StudyItemStatus = "planned" | "in_progress" | "done" | "dropped" | "merged";
export type AssessmentKind =
  | "coding_test"
  | "written_test"
  | "assignment"
  | "interview"
  | "certification"
  | "other";
export type AssessmentStatus = "planned" | "done" | "cancelled";
export type AttemptResult = "passed" | "failed" | "unknown";
export type MistakeReason = "concept" | "careless" | "time" | "misread" | "unknown";
export type ReviewGrade = "again" | "hard" | "good" | "easy";
export type CoverageStatus = "evidenced" | "claim_pending" | "studied" | "studying" | "uncovered";

export interface RequirementRef {
  id: Uuid;
  posting_id: Uuid;
  field: RequirementField;
  text: string;
  posting_title: string | null;
}

export interface StudyItemSummary {
  id: Uuid;
  title: string;
  kind: StudyItemKind;
  status: StudyItemStatus;
  requirement: RequirementRef | null;
  assessment_id: Uuid | null;
  total_minutes: number;
  open_mistakes: number;
  completed_at: IsoDateTime | null;
  updated_at: IsoDateTime;
}

export interface StudyItemIn {
  title?: string | null;
  kind?: StudyItemKind;
  notes?: string | null;
  posting_id?: Uuid | null;
  field?: RequirementField | null;
  text?: string | null;
  assessment_id?: Uuid | null;
}

export interface StudySession {
  id: Uuid;
  started_at: IsoDateTime;
  minutes: number;
  notes: string | null;
  resources: { title: string; url?: string | null }[];
}

export interface Attempt {
  id: Uuid;
  assessment_id: Uuid | null;
  study_item_id: Uuid | null;
  taken_at: IsoDateTime;
  result: AttemptResult;
  score: DecimalString | null;
  max_score: DecimalString | null;
  minutes: number | null;
  notes: string | null;
}

export interface Mistake {
  id: Uuid;
  attempt_id: Uuid | null;
  study_item_id: Uuid | null;
  question: string;
  wrong_answer: string | null;
  correct_answer: string | null;
  reason: MistakeReason;
  reason_note: string | null;
  status: "open" | "resolved";
  repetitions: number;
  interval_days: number;
  due_at: IsoDateTime;
  last_reviewed_at: IsoDateTime | null;
}

export interface MistakeListItem extends Mistake {
  study_item_title: string | null;
  created_at: IsoDateTime;
}

export interface StudyItem {
  id: Uuid;
  title: string;
  kind: StudyItemKind;
  status: StudyItemStatus;
  notes: string | null;
  requirement: RequirementRef | null;
  assessment_id: Uuid | null;
  completed_at: IsoDateTime | null;
  total_minutes: number;
  sessions: StudySession[];
  attempts: Attempt[];
  mistakes: Mistake[];
  claim_candidates: { claim_id: Uuid; attempt_id: Uuid | null; statement: string; status: string }[];
  /** status = merged면 합친 항목 */
  merged_into_id?: Uuid | null;
  /** 이 항목으로 합쳐진 항목 (응시·후보 기록은 원래 항목에 남고 여기서 함께 보인다) */
  merged_from?: { id: Uuid; title: string }[];
}

export interface Assessment {
  id: Uuid;
  title: string;
  kind: AssessmentKind;
  status: AssessmentStatus;
  scheduled_at: IsoDateTime | null;
  notes: string | null;
  requirement: RequirementRef | null;
  attempts: Attempt[];
  items: Uuid[];
  mistakes: Mistake[];
  last_attempt: Attempt | null;
}

export interface AssessmentIn {
  title: string;
  kind: AssessmentKind;
  scheduled_at?: IsoDateTime | null;
  notes?: string | null;
  posting_id?: Uuid | null;
  process_text?: string | null;
}

export interface AttemptIn {
  result: AttemptResult;
  assessment_id?: Uuid | null;
  study_item_id?: Uuid | null;
  score?: number | string | null;
  max_score?: number | string | null;
  taken_at?: IsoDateTime | null;
  minutes?: number | null;
  notes?: string | null;
}

export interface MistakeIn {
  question: string;
  reason?: MistakeReason;
  attempt_id?: Uuid | null;
  study_item_id?: Uuid | null;
  wrong_answer?: string | null;
  correct_answer?: string | null;
  reason_note?: string | null;
}

export interface ReviewOut {
  mistake_id: Uuid;
  grade: ReviewGrade;
  repetitions: number;
  interval_days: number;
  ease: number;
  due_at: IsoDateTime;
}

export interface DueReview {
  id: Uuid;
  question: string;
  wrong_answer: string | null;
  correct_answer: string | null;
  reason: MistakeReason;
  reason_note: string | null;
  due_at: IsoDateTime;
  repetitions: number;
  interval_days: number;
  study_item_id: Uuid | null;
  study_item_title: string | null;
  attempt_id: Uuid | null;
}

export interface RequirementCoverage {
  field: RequirementField;
  text: string;
  requirement_id: Uuid | null;
  current: boolean;
  coverage: CoverageStatus;
  claims: { id: Uuid; statement: string; status: string }[];
  items: { id: Uuid; title: string; status: StudyItemStatus; last_attempt: Attempt | null }[];
  assessments: {
    id: Uuid;
    title: string;
    kind: AssessmentKind;
    status: AssessmentStatus;
    scheduled_at: IsoDateTime | null;
    last_attempt: Attempt | null;
  }[];
  steps: { step: string; kind: AssessmentKind | null }[];
}

export interface Coverage {
  posting_id: Uuid;
  title: string | null;
  company: string | null;
  status: PostingStatus;
  requirements: RequirementCoverage[];
  counts: Record<string, number>;
  basis: string;
}

export interface PlanOut {
  items_created: Uuid[];
  assessments_created: Uuid[];
  skipped: number;
}

// ---------------------------------------------------------------- study backlog (c0013)

export interface StudySettings {
  /** IANA 시간대. 복습 마감 = 이 시간대의 오늘 끝, D-day 기준 */
  timezone: string;
  is_default: boolean;
  today: string;
  day_ends_at: IsoDateTime;
}

export interface UpcomingAssessment {
  id: Uuid;
  title: string;
  kind: AssessmentKind;
  scheduled_at: IsoDateTime;
  /** 0 = 오늘, 1 = 내일 (표시 시간대 날짜 기준) */
  d_day: number;
  requirement: RequirementRef | null;
}

export interface MergeOut {
  target_id: Uuid;
  sessions_moved: number;
  mistakes_moved: number;
  attempts_kept: number;
  candidates_kept: number;
}

export interface DeleteItemOut {
  item_id: Uuid;
  sessions_deleted: number;
  mistakes_deleted: number;
  merged_items_deleted: number;
}

export interface CleanupOut {
  removed: Uuid[];
  items_unlinked: number;
  assessments_unlinked: number;
  items_dropped: number;
  assessments_cancelled: number;
  claim_links_removed: number;
}

export interface ClaimSuggestion {
  claim_id: Uuid;
  statement: string;
  status: ClaimStatus;
  experience_id: Uuid | null;
  experience_title: string | null;
  score: number;
  keyword_rank: number | null;
  semantic_rank: number | null;
  /** judge=true로 물었을 때만: 판정 모델의 참고 의견 (연결·충족 판정이 아니다) */
  judge?: import("./settings").JudgeVerdict | null;
  /** restricted_not_sent: restricted 근거라 판정 모델에 보내지 않음 */
  judge_note?: string | null;
}

/** 요건 → 주장 후보 (검색 제안). 사용자가 하나씩 연결해야 반영되고 충족 판정에 쓰지 않는다 */
export interface ClaimSuggestions {
  posting_id: Uuid;
  lines: {
    field: RequirementField;
    text: string;
    requirement_id: Uuid | null;
    query: string;
    candidates: ClaimSuggestion[];
  }[];
  mode: string;
  semantic_note: string | null;
  basis: "search_suggestion";
  judge?: import("./settings").JudgeRun | null;
}

export interface FrequencyGroup {
  key: string;
  label: string;
  postings: number;
  required: number;
  preferred: number;
  examples: string[];
  posting_ids: Uuid[];
}

/** 저장 공고 요건 빈도. 사용자가 저장한 공고만 센다 (시장 통계 아님) */
export interface RequirementFrequency {
  total_postings: number;
  groups: FrequencyGroup[];
  statuses: PostingStatus[];
  basis: "saved_postings_only";
}

// ---------------------------------------------------------------- c0016 요청 기록·파일 검사

/** 수정안 요청 1회 기록. questions 는 모델이 사실 대신 물어본 것 (근거가 아니다) */
export interface SuggestionRequest {
  id: Uuid;
  document_id: Uuid;
  block_ids: string[];
  questions: string[];
  proposed: number;
  rejected: number;
  dropped: number;
  /** 일괄 실행(suggest_document_batch)에서 나온 요청이면 그 실행 */
  run_id: Uuid | null;
  model_call_id: Uuid | null;
  created_at: IsoDateTime;
}

export type HiddenTextCode =
  | "hidden_text_vanish"
  | "hidden_text_invisible"
  | "hidden_text_white"
  | "hidden_text_tiny"
  | "hidden_text_outside_page"
  // c0023 PDF: 배경과 같은 색, 숨긴 주석·양식 칸, 인쇄되지 않는 주석 글(참고)
  | "hidden_text_same_color"
  | "hidden_text_annotation"
  | "annotation_text"
  // PDF 글 뒤에 칠한 불투명 도형·이미지가 글을 덮음
  | "hidden_text_covered";

/** 올린 DOCX·PDF 숨김 글 검사 결과. 경고만 한다 (파일을 고치거나 저장하지 않는다) */
export interface FileCheck {
  format: "docx" | "pdf";
  filename: string | null;
  bytes: number;
  pages: number | null;
  pages_checked: number | null;
  chars: number;
  /** detail.count = 곳 수, detail.samples = [{page?, part?, text}] */
  findings: Finding[];
  summary: Record<string, number>;
  source_id?: Uuid | null;
  version_id?: Uuid | null;
}
