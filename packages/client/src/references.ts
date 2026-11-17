// 참고 자료 보관함 API (O14, plan §19.2). 다른 사람의 글: 내 경험이 아니고 문서 근거로 쓰지 않는다.
// 뒤의 기능(지원 준비·초안)은 원문 문장이 아니라 사람이 확인한 구조 메모만 쓴다.
import type { IsoDateTime, Uuid } from "./types";

/** accepted_essay 합격 자기소개서 · essay 다른 사람 자기소개서 · resume 다른 사람 이력서 · interview_review 면접 후기 · job_article 직무 글 · other */
export type ReferenceKind = "accepted_essay" | "essay" | "resume" | "interview_review" | "job_article" | "other";
export const REFERENCE_KINDS: ReferenceKind[] = [
  "accepted_essay",
  "essay",
  "resume",
  "interview_review",
  "job_article",
  "other",
];

export interface ReferenceSummary {
  id: Uuid;
  kind: ReferenceKind;
  title: string;
  company_id: Uuid | null;
  company_name: string | null;
  role: string | null;
  year: number | null;
  source_id: Uuid | null;
  data_class: string;
  text_chars: number;
  /** 저장할 때 가린 개인정보 수 */
  pii_masked: number;
  notes_confirmed: number;
  notes_candidate: number;
  created_at: IsoDateTime;
  updated_at: IsoDateTime;
}

/** 구조 메모: 원문 문장이 아니라 문항·문단 구성·소재 종류·강조 역량 */
export interface ReferenceNote {
  id: Uuid;
  reference_id: Uuid;
  question: string | null;
  structure: string[];
  materials: string[];
  competencies: string[];
  status: "candidate" | "confirmed";
  origin: "model" | "user";
  confirmed_at: IsoDateTime | null;
  created_at: IsoDateTime;
  updated_at: IsoDateTime;
}

export interface Reference {
  id: Uuid;
  kind: ReferenceKind;
  title: string;
  company_id: Uuid | null;
  company_name: string | null;
  role: string | null;
  year: number | null;
  source_note: string | null;
  source_id: Uuid | null;
  source_title: string | null;
  data_class: string;
  /** 저장한 글 (가리기로 했으면 가린 글) */
  text: string;
  pii_masked: number;
  created_at: IsoDateTime;
  updated_at: IsoDateTime;
  notes: ReferenceNote[];
}

/** text(붙여넣은 글) 또는 source_id(용도가 참고인 원본) 중 하나 */
export interface ReferenceIn {
  kind: ReferenceKind;
  title: string;
  text?: string | null;
  source_id?: Uuid | null;
  company_id?: Uuid | null;
  role?: string | null;
  year?: number | null;
  source_note?: string | null;
  data_class?: "public" | "private" | "restricted" | null;
  /** 개인정보를 ○○○로 가려 저장 */
  mask_pii?: boolean;
}

/** 준 칸만 바꾼다. null = 비우기. mask_pii: 저장된 글을 지금 가린다 */
export type ReferencePatch = Partial<Omit<ReferenceIn, "text" | "source_id">>;

export type PiiKind = "resident_registration_number" | "phone_number" | "email_address" | "name" | "school";

export interface PiiHit {
  kind: PiiKind;
  start: number;
  end: number;
  value: string;
  replacement: string;
}

export interface PiiReport {
  hits: PiiHit[];
  total: number;
  by_kind: Partial<Record<PiiKind, number>>;
}

export interface ReferenceNoteIn {
  question?: string | null;
  structure?: string[];
  materials?: string[];
  competencies?: string[];
}

export interface ReferenceFilter {
  kind?: ReferenceKind;
  company_id?: string;
  year?: number;
  q?: string;
}

interface ReferenceDeps {
  request: (method: string, path: string, opts?: { json?: unknown }) => Promise<Response>;
  get: <R>(path: string, query?: Record<string, string | number | undefined>) => Promise<R>;
  post: <R>(path: string, body?: unknown) => Promise<R>;
  patch: <R>(path: string, body: unknown) => Promise<R>;
}

export function referenceEndpoints({ request, get, post, patch }: ReferenceDeps) {
  const id = encodeURIComponent;
  const base = (referenceId: string) => `/core/references/${id(referenceId)}`;
  return {
    references: (f: ReferenceFilter = {}) =>
      get<ReferenceSummary[]>("/core/references", {
        kind: f.kind,
        company_id: f.company_id,
        year: f.year,
        q: f.q || undefined,
      }),
    reference: (referenceId: string) => get<Reference>(base(referenceId)),
    /** 저장 전 개인정보 찾기 (글 또는 참고 원본) */
    referencePiiCheck: (body: { text?: string; source_id?: string }) =>
      post<PiiReport>("/core/references/pii-check", body),
    createReference: (body: ReferenceIn) => post<{ id: Uuid }>("/core/references", body),
    updateReference: (referenceId: string, body: ReferencePatch) => patch<{ id: Uuid }>(base(referenceId), body),
    deleteReference: async (referenceId: string) => {
      await request("DELETE", base(referenceId));
    },
    addReferenceNote: (referenceId: string, body: ReferenceNoteIn) =>
      post<{ id: Uuid }>(`${base(referenceId)}/notes`, body),
    updateReferenceNote: (referenceId: string, noteId: string, body: ReferenceNoteIn) =>
      patch<{ id: Uuid }>(`${base(referenceId)}/notes/${id(noteId)}`, body),
    confirmReferenceNote: (referenceId: string, noteId: string) =>
      post<{ id: Uuid; status: string }>(`${base(referenceId)}/notes/${id(noteId)}/confirm`),
    deleteReferenceNote: async (referenceId: string, noteId: string) => {
      await request("DELETE", `${base(referenceId)}/notes/${id(noteId)}`);
    },
    /** 모델이 구조 메모 후보를 만든다. notes: no_model · llm_denied:… · llm_dropped:n … */
    suggestReferenceNotes: (referenceId: string) =>
      post<{ created: number; notes: string[] }>(`${base(referenceId)}/suggest-notes`),
  };
}
