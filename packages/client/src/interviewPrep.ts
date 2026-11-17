// 면접 준비 API (O16, plan §19.4). 지원 하나의 예상 질문 — 질문마다 출처가 있고, 답변 메모는 사람이 쓴다.
// 쓸 경험 후보는 내 경험의 확인한 사실과 그 원문 근거뿐이다. 회사 항목·참고 면접 후기는 참고 지식이고 근거가 아니다.
import type { AnswerAssetKind } from "./brainMe";
import type { PrepEvidence, PrepGap } from "./prep";
import type { IsoDateTime, Uuid } from "./types";

/** model = 모델이 질문을 다듬고 후보 안에서 고름 · search = 규칙 질문과 검색 순위 */
export type InterviewPrepMode = "model" | "search";
export type InterviewSourceKind = "posting" | "submission" | "company" | "reference" | "common" | "user";
/** 질문 문구를 누가 썼나 */
export type InterviewQuestionOrigin = "rule" | "model" | "user";
export type InterviewStaleReason =
  | "posting_changed"
  | "submission_changed"
  | "submission_replaced"
  | "company_changed"
  | "reference_changed"
  | "experience_changed";

export type InterviewSource =
  | {
      kind: "posting";
      posting_id: Uuid;
      posting_version_id: Uuid | null;
      field: "requirements" | "preferred" | "responsibilities";
      index: number;
      text: string;
    }
  | {
      kind: "submission";
      submission_id: Uuid;
      submitted_at: IsoDateTime;
      document_id: Uuid;
      document_title: string | null;
      question_prompt: string | null;
      revision_id: Uuid;
      revision_no: number;
      block_id: string;
      /** 문단 글 안의 위치 [start, end) */
      start: number;
      end: number;
      /** 제출한 글 그대로 */
      quote: string;
      claim_ids: Uuid[];
    }
  | {
      kind: "company";
      company_id: Uuid;
      company_name: string;
      fact_id: Uuid;
      fact_kind: string;
      body: string;
      quote: string;
      source_url: string | null;
      fetched_at: IsoDateTime;
      as_of: string | null;
      as_of_precision: "year" | "month" | "day" | null;
    }
  | {
      kind: "reference";
      reference_id: Uuid;
      reference_title: string;
      reference_kind: string;
      note_id: Uuid;
      note_question: string | null;
      competencies: string[];
      same_company: boolean;
    }
  | { kind: "common"; common_kind: AnswerAssetKind }
  | { kind: "user" };

export interface InterviewSuggestion {
  experience_id: Uuid;
  title: string;
  organization: string | null;
  role: string | null;
  period: string | null;
  evidence: PrepEvidence[];
  matched: { requirement: string; terms: string[] }[];
  gaps: PrepGap[];
  score: number;
  picked_by: InterviewPrepMode;
}

export interface InterviewLinkedAnswer {
  id: Uuid;
  kind: AnswerAssetKind;
  title: string;
  body: string;
  chars: number;
  chars_without_spaces: number;
}

export interface InterviewPrepQuestion {
  id: Uuid;
  position: number;
  source_kind: InterviewSourceKind;
  origin: InterviewQuestionOrigin;
  question: string;
  source: InterviewSource;
  common_kind: AnswerAssetKind | null;
  suggestions: InterviewSuggestion[];
  /** 사람이 쓴 답변 메모 */
  answer: string;
  chars: number;
  chars_without_spaces: number;
  answer_updated_at: IsoDateTime | null;
  /** 이은 공통 답변 (글은 공통 답변 쪽에 있다) */
  answer_asset: InterviewLinkedAnswer | null;
  stale: boolean;
  stale_reasons: InterviewStaleReason[];
}

export interface InterviewPrep {
  application_id: Uuid;
  /** none = 아직 만들지 않음 */
  status: "none" | "ready";
  mode: InterviewPrepMode | null;
  generated_at: IsoDateTime | null;
  /** 만들 때의 제출본 */
  submission_id: Uuid | null;
  /** 지금 지원의 제출본 */
  current_submission_id: Uuid | null;
  stale: boolean;
  stale_reasons: InterviewStaleReason[];
  questions: InterviewPrepQuestion[];
  /** 답(메모 또는 이은 공통 답변)이 있는 질문 수 */
  answered: number;
  notes: string[];
  model_call_id: Uuid | null;
}

export interface InterviewFeedback {
  question_id: Uuid;
  points: { kind: "missing" | "unsupported" | "role" | "structure" | "other"; text: string }[];
  dropped: number;
  model_call_id: Uuid | null;
}

interface InterviewPrepDeps {
  request: (method: string, path: string, opts?: { json?: unknown }) => Promise<Response>;
  get: <T>(path: string) => Promise<T>;
  post: <T>(path: string, json?: unknown) => Promise<T>;
}

export function interviewPrepEndpoints({ request, get, post }: InterviewPrepDeps) {
  const id = encodeURIComponent;
  const base = (applicationId: string) => `/core/applications/${id(applicationId)}/interview-prep`;
  const one = (applicationId: string, questionId: string) => `${base(applicationId)}/questions/${id(questionId)}`;
  return {
    interviewPrep: (applicationId: string) => get<InterviewPrep>(base(applicationId)),
    /** (다시) 만들기. 답변 메모는 건드리지 않는다. use_model=false면 모델 없이 규칙 질문으로 */
    generateInterviewPrep: (applicationId: string, opts: { use_model?: boolean } = {}) =>
      post<InterviewPrep>(base(applicationId), { use_model: opts.use_model ?? true }),
    addInterviewQuestion: (applicationId: string, question: string) =>
      post<{ id: Uuid }>(`${base(applicationId)}/questions`, { question }),
    deleteInterviewQuestion: async (applicationId: string, questionId: string) => {
      await request("DELETE", one(applicationId, questionId));
    },
    /** 사람이 쓴 답변 메모 저장 (빈 글이면 지움) */
    setInterviewAnswer: async (applicationId: string, questionId: string, answer: string) => {
      await request("PUT", `${one(applicationId, questionId)}/answer`, { json: { answer } });
    },
    /** 공통 답변에서 가져오기 (잇기, 글은 복사하지 않는다) */
    linkInterviewAnswer: async (applicationId: string, questionId: string, answerId: string) => {
      await request("PUT", `${one(applicationId, questionId)}/answer-asset`, { json: { answer_id: answerId } });
    },
    unlinkInterviewAnswer: async (applicationId: string, questionId: string) => {
      await request("DELETE", `${one(applicationId, questionId)}/answer-asset`);
    },
    /** 공통 답변으로 저장: 메모로 새 공통 답변을 만들고 잇는다 (메모는 그쪽으로 옮긴다) */
    saveInterviewAnswerAsAsset: (
      applicationId: string,
      questionId: string,
      body: { title?: string | null; kind?: AnswerAssetKind | null } = {},
    ) => post<{ id: Uuid }>(`${one(applicationId, questionId)}/save-answer`, body),
    /** 사람이 쓴 답에 모델 의견 (요청할 때만, 저장하지 않음) */
    interviewAnswerFeedback: (applicationId: string, questionId: string, instruction?: string | null) =>
      post<InterviewFeedback>(`${one(applicationId, questionId)}/feedback`, { instruction: instruction ?? null }),
  };
}
