// 지원서 초안 API (plan §22). 문항마다 브레인에서 경험을 고르고 초안을 쓴다 (내 자료만, 문장마다 근거).
// 시작하면 실행(workflow draft_application)이 생긴다. 진행은 run(runId)으로 본다.
import type { Run, RunEstimate } from "./system";
import type { Uuid } from "./types";

export const DRAFT_WORKFLOW = "draft_application";

export interface DraftIn {
  /** 없으면 모든 문항 */
  document_ids?: Uuid[] | null;
  instruction?: string | null;
  /** false(기본): 글이 없는 문항만. true: 고른 문항을 다시 쓴다 (이전 글은 기록에 남는다) */
  overwrite?: boolean;
  idempotency_key?: string | null;
}

export interface DraftClaim {
  claim_id: Uuid;
  statement: string;
  status: string;
  confirmed: boolean;
  experience_id: Uuid;
  experience_title: string;
  /** 원문 인용 (restricted 원본이면 null) */
  quote: string | null;
  source:
    | { kind: "span"; title: string | null; page: number | null }
    | { kind: "answer"; question: string | null }
    | null;
  restricted: boolean;
}

export interface DraftDocument {
  document_id: Uuid;
  revision_no: number;
  /** 지금 글이 AI 초안 */
  drafted: boolean;
  claims: DraftClaim[];
  experiences: { experience_id: Uuid; title: string; claims: number }[];
  /** 확인 전 사실에만 기댄 문장 (블록 글의 [start, end)) */
  unconfirmed_sentences: { block_id: string; start: number; end: number }[];
}

export interface ApplicationDraft {
  application_id: Uuid;
  documents: DraftDocument[];
  unconfirmed_total: number;
  run_id: Uuid | null;
}

/** 실행 위치(position)·결과의 문항별 결과 */
export interface DraftResult {
  document_id: Uuid;
  /** written · not_empty · changed · no_material · model_empty */
  status: string;
  revision_no: number | null;
  unconfirmed: number;
  experiences: Uuid[];
  dropped_sentences: number;
  trimmed_sentences: number;
}

interface DraftDeps {
  get: <T>(path: string) => Promise<T>;
  post: <T>(path: string, json?: unknown) => Promise<T>;
}

export function draftEndpoints({ get, post }: DraftDeps) {
  const base = (applicationId: string) => `/core/applications/${encodeURIComponent(applicationId)}/draft`;
  return {
    applicationDraft: (applicationId: string) => get<ApplicationDraft>(base(applicationId)),
    startDraft: (applicationId: string, body: DraftIn = {}) => post<Run>(base(applicationId), body),
    draftEstimate: (applicationId: string, body: DraftIn = {}) =>
      post<RunEstimate>(`${base(applicationId)}/estimate`, body),
  };
}
