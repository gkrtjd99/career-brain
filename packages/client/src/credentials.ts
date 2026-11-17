// 자격·이수 기록과 증빙 서류함 API (c0034, O20, plan §20).
// createClient 안의 요청 함수를 받아 쓴다 (인증·오류 처리는 그쪽과 같다).
import type { IsoDateTime, Uuid } from "./types";

/** license 자격증 · language 어학 · course 수료 · award 수상 · degree 학력 · other 그 밖의 증명 */
export type CredentialKind = "license" | "language" | "course" | "award" | "degree" | "other";
export const CREDENTIAL_KINDS: CredentialKind[] = ["license", "language", "course", "award", "degree", "other"];

/** extracted 증빙에서 뽑음(확인 전) · self_confirmed 직접 확인 · evidence_supported 확인 + 증빙 연결 */
export type CredentialStatus = "extracted" | "self_confirmed" | "evidence_supported";

/** 날짜 "YYYY-MM-DD" */
export type IsoDay = string;

/** 증빙에서 뽑은 칸의 원문 위치 (파싱 실행의 블록 순번과 그 글 안의 [start, end)). 글은 싣지 않는다 */
export interface CredentialFieldSpan {
  block: number;
  start: number;
  end: number;
  page?: number | null;
}

export type CredentialField =
  | "name"
  | "issuer"
  | "number"
  | "score"
  | "acquired_on"
  | "expires_on"
  | "started_on"
  | "ended_on"
  | "hours";

export interface Credential {
  id: Uuid;
  kind: CredentialKind;
  name: string;
  issuer: string | null;
  /** 끝 4자리만 "••••1234" (reveal이면 원래 값) */
  number: string | null;
  number_masked: boolean;
  acquired_on: IsoDay | null;
  expires_on: IsoDay | null;
  started_on: IsoDay | null;
  ended_on: IsoDay | null;
  score: string | null;
  hours: number | null;
  detail: string | null;
  status: CredentialStatus;
  source_id: Uuid | null;
  source_title: string | null;
  experience_id: Uuid | null;
  experience_title: string | null;
  parse_run_id: Uuid | null;
  field_spans: Partial<Record<CredentialField, CredentialFieldSpan>>;
  /** expired 지남 · soon 90일 안 · null */
  expiry: "expired" | "soon" | null;
  days_left: number | null;
  confirmed_at: IsoDateTime | null;
  created_at: IsoDateTime;
  updated_at: IsoDateTime;
}

export interface CredentialIn {
  kind: CredentialKind;
  name: string;
  issuer?: string | null;
  number?: string | null;
  acquired_on?: IsoDay | null;
  expires_on?: IsoDay | null;
  started_on?: IsoDay | null;
  ended_on?: IsoDay | null;
  score?: string | null;
  hours?: number | null;
  detail?: string | null;
  source_id?: Uuid | null;
  experience_id?: Uuid | null;
}

export type CredentialPatch = Partial<CredentialIn>;

export interface CredentialQuery {
  kind?: CredentialKind;
  status?: CredentialStatus;
  /** 오늘부터 이 날 수 안에 만료되는 것만 (가까운 순) */
  expiringWithin?: number;
  sourceId?: Uuid;
  reveal?: boolean;
}

export interface CredentialExtractResult {
  source_id: Uuid;
  credential_id: Uuid | null;
  /** not_parsed 읽기 전·실패 · no_text 읽은 글 없음(사진 등) · nothing_found · already_recorded */
  reason: "not_parsed" | "no_text" | "nothing_found" | "already_recorded" | null;
  replaced: number;
  fields: CredentialField[];
}

export interface ProofItem {
  id: Uuid;
  title: string;
  created_at: IsoDateTime;
  original_filename: string | null;
  mime_type: string | null;
  size_bytes: number | null;
  sha256: string | null;
  parse_status: string | null;
  parse_error: string | null;
  /** 주민등록번호로 보이는 값이 있다 (값은 싣지 않는다) */
  resident_number: boolean;
  credentials: { id: Uuid; kind: CredentialKind; name: string; status: CredentialStatus; expires_on: IsoDay | null }[];
  candidates: number;
}

export interface OriginalFile {
  blob: Blob;
  filename: string;
  sha256: string | null;
}

interface CredentialDeps {
  request: (method: string, path: string, opts?: { query?: Record<string, string | undefined> }) => Promise<Response>;
  get: <R>(path: string, query?: Record<string, string | number | boolean | undefined>) => Promise<R>;
  post: <R>(path: string, body?: unknown) => Promise<R>;
  patch: <R>(path: string, body: unknown) => Promise<R>;
}

function filenameOf(res: Response, fallback: string) {
  const cd = res.headers.get("content-disposition") ?? "";
  const star = /filename\*=(?:UTF-8'')?([^;]+)/i.exec(cd);
  if (star?.[1]) return decodeURIComponent(star[1].replace(/"/g, ""));
  return fallback;
}

export function credentialEndpoints({ request, get, post, patch }: CredentialDeps) {
  const id = encodeURIComponent;
  const base = (credentialId: string) => `/core/credentials/${id(credentialId)}`;
  return {
    credentials: (q: CredentialQuery = {}) =>
      get<Credential[]>("/core/credentials", {
        kind: q.kind,
        status: q.status,
        expiring_within: q.expiringWithin,
        source_id: q.sourceId,
        reveal: q.reveal || undefined,
      }),
    /** reveal = 번호 원래 값 (쓰기 권한, 감사 기록에 남는다) */
    credential: (credentialId: string, reveal = false) =>
      get<Credential>(base(credentialId), { reveal: reveal || undefined }),
    createCredential: (body: CredentialIn) => post<{ id: Uuid }>("/core/credentials", body),
    updateCredential: (credentialId: string, body: CredentialPatch) => patch<{ id: Uuid }>(base(credentialId), body),
    confirmCredential: (credentialId: string) =>
      post<{ id: Uuid; status: CredentialStatus }>(`${base(credentialId)}/confirm`),
    deleteCredential: async (credentialId: string) => {
      await request("DELETE", base(credentialId));
    },
    proofs: (sourceId?: string) => get<ProofItem[]>("/core/proofs", { source_id: sourceId }),
    /** 증빙 글에서 항목 다시 뽑기 (로컬 규칙, 모델 호출 없음). 409 source_not_proof */
    extractCredentials: (sourceId: string) =>
      post<CredentialExtractResult>(`/core/sources/${id(sourceId)}/extract-credentials`),
    /** 원본 바이트 그대로. 409 original_changed = 저장한 해시와 다름 */
    downloadOriginal: async (sourceId: string, fallback = "original"): Promise<OriginalFile> => {
      const res = await request("GET", `/core/sources/${id(sourceId)}/original`);
      return {
        blob: await res.blob(),
        filename: filenameOf(res, fallback),
        sha256: res.headers.get("x-content-sha256"),
      };
    },
  };
}
