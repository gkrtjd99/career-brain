// 이력서 판본 API (c0047, O17, plan §19.5). createClient 안의 요청 함수를 받아 쓴다.
import type { IsoDateTime, Uuid } from "./types";

export type ResumeKind = "master" | "role" | "posting";
export type ResumeTemplate = "one_column" | "two_column" | "korean_table";
export const RESUME_TEMPLATES: ResumeTemplate[] = ["one_column", "two_column", "korean_table"];
export type ResumeSectionKind =
  | "profile"
  | "work"
  | "education"
  | "project"
  | "skills"
  | "credentials"
  | "awards"
  | "publications"
  | "activities";
export type ResumeRefType = "experience" | "education" | "credential" | "skill" | "profile";
export type ResumeFormat = "html" | "docx" | "md" | "json";

export interface ResumeSummary {
  id: Uuid;
  kind: ResumeKind;
  title: string;
  target_role: string | null;
  parent_id: Uuid | null;
  parent_title: string | null;
  /** 복사해 온 위 판본 revision과 위 판본의 지금 revision (다르면 위 판본이 그 뒤에 바뀌었다) */
  source_revision_no: number | null;
  parent_revision: number | null;
  application_id: Uuid | null;
  application_title: string | null;
  template: ResumeTemplate;
  blind: boolean;
  max_pages: number;
  current_revision: number;
  items: number;
  lines: number;
  unsupported_lines: number;
  children: number;
  created_at: IsoDateTime;
  updated_at: IsoDateTime;
}

/** none = 근거 없음 · ok · stale = 사실이 그 뒤에 바뀜 · broken = 확인이 풀림 */
export type ResumeLineState = "none" | "ok" | "stale" | "broken";
/** manual = 직접 쓴 항목 · ok · stale = 기록이 바뀜 · missing = 기록이 없음 */
export type ResumeItemState = "manual" | "ok" | "stale" | "missing";

export interface ResumeLine {
  id: string;
  text: string;
  claim_id: Uuid | null;
  basis: string | null;
  origin: "claim" | "user" | "ai";
  state: ResumeLineState;
}

export interface ResumeItem {
  id: string;
  ref: { type: ResumeRefType; id: string } | null;
  fields: Record<string, unknown>;
  basis: string | null;
  hidden?: boolean;
  /** 프로필: 사진·생년월일·주소·영문 이름·병역 표시 */
  show?: Record<string, boolean>;
  lines: ResumeLine[];
  state: ResumeItemState;
}

export interface ResumeSection {
  kind: ResumeSectionKind;
  title: string | null;
  visible: boolean;
  items: ResumeItem[];
}

export interface ResumeVersion {
  summary: ResumeSummary;
  revision_no: number;
  revision_id: Uuid | null;
  revision_origin: string | null;
  content: { v: number; sections: ResumeSection[] };
  lineage: { id: Uuid; kind: ResumeKind; title: string }[];
  blind_suggested: boolean;
  prep_choices: number;
}

export interface ResumeCreateIn {
  kind: ResumeKind;
  title?: string | null;
  parent_id?: Uuid | null;
  parent_revision?: number | null;
  target_role?: string | null;
  application_id?: Uuid | null;
  template?: ResumeTemplate | null;
  fill?: boolean;
  fill_claims?: boolean;
}

export interface ResumeSettingsPatch {
  title?: string;
  target_role?: string | null;
  template?: ResumeTemplate;
  blind?: boolean;
  max_pages?: number;
  application_id?: Uuid | null;
}

/** 수정 연산 (domain.resume.apply_operations). 연산 이름과 인자는 서버와 같다 */
export type ResumeOperation = { op: string } & Record<string, unknown>;

export interface ResumeSaveOut {
  id: Uuid;
  revision_no: number;
  created: boolean;
}

export interface ResumeCheckItem {
  code: string;
  message: string;
  count: number;
  severity: "warning" | "info" | "error";
  targets: string[];
}

export interface ResumeCheck {
  version_id: Uuid;
  revision: number;
  estimated_pages: number;
  /** Always true: the server count is an estimate; the preview measures real pages. */
  pages_estimated: true;
  max_pages: number;
  blind: boolean;
  blind_suggested: boolean;
  checklist: ResumeCheckItem[];
  findings: {
    code: string;
    severity: string;
    message: string;
    block_id: string | null;
    sentence: string | null;
  }[];
  summary: Record<string, number>;
}

export interface ResumePicker {
  profile: { id: Uuid; name: string | null; added: boolean } | null;
  experiences: {
    id: Uuid;
    kind: string;
    section: ResumeSectionKind;
    title: string;
    organization: string | null;
    role: string | null;
    period: string | null;
    added: boolean;
    claims: { id: Uuid; statement: string; kind: string }[];
  }[];
  education: {
    id: Uuid;
    school: string;
    major: string | null;
    degree: string;
    period: string | null;
    added: boolean;
  }[];
  credentials: {
    id: Uuid;
    kind: string;
    section: ResumeSectionKind;
    name: string;
    issuer: string | null;
    acquired_on: string | null;
    added: boolean;
  }[];
  skills: { key: string; name: string; added: boolean }[];
}

export interface ResumeExportCheck {
  filename: string;
  media_type: string;
  format: ResumeFormat;
  revision: number;
  bytes: number;
  findings: { code: string; severity: string; message: string }[];
  summary: Record<string, number>;
}

export interface ResumeLineSuggestion {
  line_id: string;
  original_text: string;
  suggested_text: string;
  reason: string | null;
  status: "proposed" | "rejected";
  reject_reasons: { code: string; message?: string }[];
}

export interface ResumeSuggestOut {
  revision: number;
  suggestions: ResumeLineSuggestion[];
  questions: string[];
  dropped: number;
  model_call_id: Uuid | null;
}

interface ResumeDeps {
  request: (
    method: string,
    path: string,
    opts?: { json?: unknown; query?: Record<string, string | number | undefined> },
  ) => Promise<Response>;
  get: <R>(path: string, query?: Record<string, string | number | undefined>) => Promise<R>;
  post: <R>(path: string, body?: unknown) => Promise<R>;
  patch: <R>(path: string, body: unknown) => Promise<R>;
}

export function resumeEndpoints({ request, get, post, patch }: ResumeDeps) {
  const id = encodeURIComponent;
  const base = (versionId: string) => `/core/resumes/${id(versionId)}`;
  return {
    resumes: (applicationId?: string) =>
      get<ResumeSummary[]>("/core/resumes", { application_id: applicationId }),
    resume: (versionId: string, revision?: number) =>
      get<ResumeVersion>(base(versionId), { revision }),
    createResume: (body: ResumeCreateIn) => post<{ id: Uuid }>("/core/resumes", body),
    patchResume: (versionId: string, body: ResumeSettingsPatch) =>
      patch<ResumeVersion>(base(versionId), body),
    deleteResume: async (versionId: string) => {
      await request("DELETE", base(versionId));
    },
    /** 409 {code: revision_conflict, current_revision} */
    saveResume: (
      versionId: string,
      body: {
        base_revision: number;
        operations: ResumeOperation[];
        reason?: string;
        idempotency_key?: string;
      },
    ) => post<ResumeSaveOut>(`${base(versionId)}/revisions`, body),
    resumePicker: (versionId: string) => get<ResumePicker>(`${base(versionId)}/picker`),
    resumeCheck: (versionId: string) => get<ResumeCheck>(`${base(versionId)}/check`),
    /** 미리보기 HTML (내보낸 HTML 파일과 같은 바이트) */
    resumePreview: async (versionId: string): Promise<string> =>
      (await request("GET", `${base(versionId)}/preview`)).text(),
    resumeExportCheck: (versionId: string, format: ResumeFormat) =>
      get<ResumeExportCheck>(`${base(versionId)}/export/check`, { format }),
    resumeExportFile: async (versionId: string, format: ResumeFormat) => {
      const res = await request("GET", `${base(versionId)}/export`, { query: { format } });
      const disposition = res.headers.get("Content-Disposition") ?? "";
      const m = /filename\*=UTF-8''([^;]+)/.exec(disposition);
      const codes = res.headers.get("X-Export-Codes") ?? "";
      return {
        filename: m?.[1] ? decodeURIComponent(m[1]) : `resume.${format}`,
        blob: await res.blob(),
        findings: Number(res.headers.get("X-Export-Findings") ?? "0"),
        codes: codes ? codes.split(",") : [],
      };
    },
    /** 줄 다듬기 수정안 (저장하지 않는다). 적용은 update_line 연산으로 */
    suggestResumeLines: (
      versionId: string,
      body: { base_revision: number; line_ids?: string[]; instruction?: string },
    ) => post<ResumeSuggestOut>(`${base(versionId)}/suggestions`, body),
  };
}
