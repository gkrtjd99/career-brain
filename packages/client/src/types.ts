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
}

export interface ParseOut {
  run_id: Uuid;
  status: string;
  pages_total: number | null;
  missing_pages: number[];
  error_code: string | null;
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

export interface ExperienceSummary {
  id: Uuid;
  title: string;
  organization: string | null;
  role: string | null;
  period_text: string | null;
  origin: string;
  claim_counts: Partial<Record<ClaimStatus, number>>;
  needs_review: number;
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

export interface Experience {
  id: Uuid;
  title: string;
  organization: string | null;
  role: string | null;
  period_text: string | null;
  origin: string;
  status: string;
  claims: Claim[];
}

export interface ExperienceIn {
  title: string;
  organization?: string | null;
  role?: string | null;
  period_text?: string | null;
}

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

export type SearchKind = "claim" | "block" | "answer";

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
}

export interface SearchResult {
  query: string;
  mode: string;
  semantic_note: string | null;
  hits: SearchHit[];
}

// ---------------------------------------------------------------- job postings

export type PostingStatus = "draft" | "saved" | "archived";
export type FetchStatus = "pending" | "done" | "listing" | "failed";
export type FetchError =
  | "source_unavailable"
  | "login_required"
  | "rate_limited"
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

export type PostingValue = string | string[] | EssayQuestion[];

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
}

export interface PostingVersion {
  id: Uuid;
  version_no: number;
  method: string;
  adapter: string;
  final_url: string;
  extracted: Partial<Record<PostingField, ExtractedField>>;
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
  fields: Partial<Record<PostingField, PostingValue>>;
  evidence: Partial<Record<PostingField, FieldEvidence>>;
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
