// 공고별 지원 준비 문서 API (O15, plan §19.3). 글을 쓰지 않는다 — 문항마다 쓸 경험 후보와 이유를 보여 준다.
// 후보는 내 경험의 확인한 주장과 그 원문 근거뿐이다. 회사 항목·참고 구성은 참고 지식이고 근거가 아니다.
import type { IsoDateTime, Uuid } from "./types";

/** model = 모델이 검색 후보 안에서 고르고 이유를 씀 · search = 모델 없이 검색 순위 */
export type PrepMode = "model" | "search";
/** no_metric 수치 없음 · role_unclear 본인 역할 불분명 · period_unknown 기간 모름 */
export type PrepGap = "no_metric" | "role_unclear" | "period_unknown";
/** strong 근거 있음 · weak 근거 약함 · none 근거 없음 */
export type CoverageLevel = "strong" | "weak" | "none";
export type PrepStaleReason = "posting_changed" | "questions_changed" | "experience_changed";

export interface PrepEvidence {
  claim_id: Uuid;
  statement: string;
  kind: string;
  contribution: string;
  /** 원문 근거: 원본 인용 또는 인터뷰 답. MCP에서는 restricted 원본이면 null */
  quote: string | null;
  source: { kind: "span"; title: string | null; page: number | null } | { kind: "answer"; question: string | null };
  restricted: boolean;
}

export interface PrepCandidate {
  experience_id: Uuid;
  title: string;
  organization: string | null;
  role: string | null;
  period: string | null;
  evidence: PrepEvidence[];
  /** 모델이 쓴 이유. 검색 순위면 null */
  reason: string | null;
  reason_source: PrepMode;
  /** 경험의 주장·근거에 보이는 공고 요건 낱말 */
  matched: { requirement: string; terms: string[] }[];
  gaps: PrepGap[];
  /** 이유가 기대는 회사 항목 */
  fact_ids: Uuid[];
  score: number;
}

/** 참고 글의 구조 메모 (문장은 없다) */
export interface PrepReferenceNote {
  note_id: Uuid;
  reference_id: Uuid;
  reference_title: string;
  reference_kind: string;
  same_company: boolean;
  question: string | null;
  structure: string[];
  materials: string[];
  competencies: string[];
}

export interface PrepQuestion {
  document_id: Uuid;
  question_index: number | null;
  title: string;
  prompt: string | null;
  char_limit: number | null;
  /** 문항이 묻는 것 (모델, 없으면 null) */
  focus: string | null;
  candidates: PrepCandidate[];
  /** 검색으로 찾은 후보 수 */
  searched: number;
  references: PrepReferenceNote[];
}

export interface PrepCoverage {
  kind: "requirements" | "preferred";
  requirement: string;
  level: CoverageLevel;
  claims: { claim_id: Uuid; statement: string; experience_id: Uuid; experience_title: string }[];
  experience_ids: Uuid[];
}

export interface PrepCompanyFact {
  id: Uuid;
  kind: string;
  body: string;
  quote: string;
  source_url: string | null;
  fetched_at: IsoDateTime;
  as_of: string | null;
  as_of_precision: "year" | "month" | "day" | null;
  stale: boolean;
}

export interface PrepChoice {
  experience_id: Uuid;
  claim_ids: Uuid[];
  chosen_at: IsoDateTime;
}

export interface ApplicationPrep {
  application_id: Uuid;
  /** none = 아직 만들지 않음 */
  status: "none" | "ready";
  mode: PrepMode | null;
  generated_at: IsoDateTime | null;
  stale: boolean;
  stale_reasons: PrepStaleReason[];
  stale_experiences: Uuid[];
  questions: PrepQuestion[];
  requirements: PrepCoverage[];
  company: { id: Uuid; name: string; facts: PrepCompanyFact[] } | null;
  /** 문항(document_id)마다 사람이 고른 경험 */
  choices: Record<Uuid, PrepChoice[]>;
  /** 두 문항 이상에 쓰는 경험 */
  overlaps: { experience_id: Uuid; title: string | null; document_ids: Uuid[] }[];
  notes: string[];
  model_call_id: Uuid | null;
  /** 준비 문서를 만든 뒤 생긴 문항 */
  new_questions: Uuid[];
}

export interface PrepChoiceOut {
  document_id: Uuid;
  experience_id: Uuid;
  /** 빈 문단에 근거로 연결할 주장 (글은 사람이 쓴다) */
  claim_ids: Uuid[];
}

interface PrepDeps {
  request: (method: string, path: string, opts?: { json?: unknown }) => Promise<Response>;
  get: <T>(path: string) => Promise<T>;
  post: <T>(path: string, json?: unknown) => Promise<T>;
}

export function prepEndpoints({ request, get, post }: PrepDeps) {
  const id = encodeURIComponent;
  const base = (applicationId: string) => `/core/applications/${id(applicationId)}/prep`;
  return {
    applicationPrep: (applicationId: string) => get<ApplicationPrep>(base(applicationId)),
    /** (다시) 만들기. use_model=false면 모델 없이 검색 순위로 */
    generateApplicationPrep: (applicationId: string, opts: { use_model?: boolean } = {}) =>
      post<ApplicationPrep>(base(applicationId), { use_model: opts.use_model ?? true }),
    /** 이 경험으로 쓰기: 고른 것을 기록한다. 문서에 글을 넣지 않는다 */
    choosePrepExperience: (
      applicationId: string,
      documentId: string,
      body: { experience_id: string; claim_ids?: string[] | null },
    ) => post<PrepChoiceOut>(`${base(applicationId)}/questions/${id(documentId)}/choose`, body),
    unchoosePrepExperience: async (applicationId: string, documentId: string, experienceId: string) => {
      await request("DELETE", `${base(applicationId)}/questions/${id(documentId)}/choose/${id(experienceId)}`);
    },
  };
}
