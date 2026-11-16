// 시스템 화면용 API: 실행(run)·승인·유지보수(내보내기)·MCP 감사 기록.
// createClient 안의 요청 함수를 받아 쓴다 (인증·오류 처리는 그쪽과 같다).
import type { IsoDateTime, Uuid } from "./types";

export type RunStatus =
  | "queued"
  | "running"
  | "waiting_approval"
  | "interrupted"
  | "cancelled"
  | "failed"
  | "done";

export interface Run {
  id: Uuid;
  workflow: string;
  status: RunStatus;
  step: number;
  steps_total: number | null;
  position: Record<string, unknown>;
  result: Record<string, unknown> | null;
  stop_requested: "cancel" | "interrupt" | null;
  wait_reason: string | null;
  retry_at: IsoDateTime | null;
  approval_id: Uuid | null;
  error_code: string | null;
  cost_usd: string;
  cost_unknown: boolean;
  model_calls: number;
  resumes: number;
  actor_kind: string;
  created_by: Uuid;
  created_at: IsoDateTime;
  started_at: IsoDateTime | null;
  finished_at: IsoDateTime | null;
  updated_at: IsoDateTime;
}

export interface RunEvent {
  seq: number;
  kind: "status" | "step" | "wait" | "resumed" | "error" | string;
  status: string | null;
  step: number | null;
  code: string | null;
  data: Record<string, unknown>;
  created_at: IsoDateTime;
}

export type ApprovalStatus = "pending" | "approved" | "rejected" | "used" | "invalidated" | "expired";

export interface Approval {
  id: Uuid;
  action: string;
  target_kind: string;
  target_id: Uuid;
  target_version: string;
  payload_hash: string;
  payload: Record<string, unknown>;
  summary: Record<string, unknown>;
  status: ApprovalStatus;
  reason: string | null;
  requested_by: Uuid;
  requested_via: "human" | "agent" | string;
  run_id: Uuid | null;
  expires_at: IsoDateTime;
  decided_by: Uuid | null;
  decided_at: IsoDateTime | null;
  decision_note: string | null;
  used_at: IsoDateTime | null;
  used_result_id: Uuid | null;
  invalid_reason: string | null;
  created_at: IsoDateTime;
}

export interface ApprovalChange {
  /** 예: "documents[<id>].revision", "status" */
  path: string;
  kind: "changed" | "added" | "removed";
  before: unknown;
  after: unknown;
  /** 목록 항목의 이름 (문서 제목 등) */
  label: string | null;
}

export interface ApprovalDiff {
  approval: Approval;
  requested: Record<string, unknown>;
  /** 대상이 없어졌으면 null */
  current: Record<string, unknown> | null;
  target_changed: boolean;
  changes: ApprovalChange[];
}

export interface MaintenanceRun {
  kind: "export" | "restore";
  via: string;
  created_at: IsoDateTime;
  archive_sha256: string;
  archive_bytes: number;
  rows_total: number;
  blobs_total: number;
}

export interface Maintenance {
  tables: Record<string, number>;
  excluded: Record<string, number>;
  rows_total: number;
  blobs: { referenced: number; files: number | null; bytes: number | null };
  last_export: MaintenanceRun | null;
  last_restore: MaintenanceRun | null;
  drafts_purgeable: number;
  draft_retention_days: number;
  export_available: boolean;
}

export interface AuditEvent {
  id: Uuid;
  created_at: IsoDateTime;
  actor_id: Uuid | null;
  actor_kind: string;
  channel: string;
  event: string;
  tool: string | null;
  status: "ok" | "error";
  error_code: string | null;
  duration_ms: number;
  args_sha256: string | null;
}

export interface AuditPage {
  events: AuditEvent[];
  last_24h: { tool: string; calls: number; errors: number }[];
  /** 보존 기간(일). 0 = 계속 보관 (c0019) */
  retention_days?: number;
}

export interface DownloadedFile {
  blob: Blob;
  filename: string;
  sha256: string | null;
}

type SysQuery = Record<string, string | number | undefined | null>;

export interface SystemDeps {
  request: (method: string, path: string, opts?: { query?: SysQuery }) => Promise<Response>;
  get: <R>(path: string, query?: SysQuery) => Promise<R>;
  post: <R>(path: string, body?: unknown) => Promise<R>;
}

function filenameOf(res: Response, fallback: string) {
  const cd = res.headers.get("content-disposition") ?? "";
  const star = /filename\*=(?:UTF-8'')?([^;]+)/i.exec(cd);
  if (star?.[1]) return decodeURIComponent(star[1].replace(/"/g, ""));
  const plain = /filename="?([^";]+)"?/i.exec(cd);
  return plain?.[1] ?? fallback;
}

export function systemEndpoints({ request, get, post }: SystemDeps) {
  const id = encodeURIComponent;
  async function download(path: string, fallback: string, query?: SysQuery): Promise<DownloadedFile> {
    const res = await request("GET", path, { query });
    return {
      blob: await res.blob(),
      filename: filenameOf(res, fallback),
      sha256: res.headers.get("x-archive-sha256"),
    };
  }

  return {
    // 실행
    runs: (opts: { status?: RunStatus; limit?: number } = {}) =>
      get<Run[]>("/core/runs", { status: opts.status, limit: opts.limit }),
    run: (runId: string) => get<Run>(`/core/runs/${id(runId)}`),
    runEvents: (runId: string, after = 0) => get<RunEvent[]>(`/core/runs/${id(runId)}/events`, { after }),
    cancelRun: (runId: string) => post<Run>(`/core/runs/${id(runId)}/cancel`),
    interruptRun: (runId: string) => post<Run>(`/core/runs/${id(runId)}/interrupt`),
    resumeRun: (runId: string) => post<Run>(`/core/runs/${id(runId)}/resume`),

    // 승인 (결정은 사람만: 보여준 내용의 해시를 같이 보낸다)
    approvals: (opts: { status?: ApprovalStatus; limit?: number } = {}) =>
      get<Approval[]>("/core/approvals", { status: opts.status, limit: opts.limit }),
    approval: (approvalId: string) => get<Approval>(`/core/approvals/${id(approvalId)}`),
    approvalDiff: (approvalId: string) => get<ApprovalDiff>(`/core/approvals/${id(approvalId)}/diff`),
    approve: (approvalId: string, body: { payload_hash: string; note?: string }) =>
      post<Approval>(`/core/approvals/${id(approvalId)}/approve`, body),
    reject: (approvalId: string, body: { payload_hash: string; note?: string }) =>
      post<Approval>(`/core/approvals/${id(approvalId)}/reject`, body),

    // 유지보수·내보내기 (소유자만)
    maintenance: () => get<Maintenance>("/core/maintenance"),
    purgeDrafts: () => post<{ deleted: number }>("/core/maintenance/purge-drafts"),
    /** 공간 묶음(tar.gz). age 공개 키를 주면 암호화한 묶음(.age). */
    downloadExport: (recipient?: string) =>
      download("/core/maintenance/export", "career-export.tar.gz", { recipient }),
    downloadDeletions: () => download("/core/maintenance/deletions", "career-deletions.jsonl"),

    // MCP 도구 호출 기록 (원문 인자 없음)
    audit: (opts: { limit?: number; before?: string; tool?: string; status?: "ok" | "error" } = {}) =>
      get<AuditPage>("/core/audit", opts),
  };
}
