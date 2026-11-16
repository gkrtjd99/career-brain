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

export interface Workflow {
  name: string;
  action: string;
  steps: number;
  description: string;
}

/** c0023 시작 전 참고 비용: 같은 workflow 지난 실행의 호출 1회 평균 × 계획한 호출 수. 지난 실행이 없으면 null */
export interface RunEstimate {
  workflow: string;
  units: number;
  calls: number;
  per_call_usd: string | null;
  estimate_usd: string | null;
  based_on_runs: number;
  based_on_calls: number;
  /** 지난 실행에 단가 미상 호출이 있었다 (예상은 아는 비용만으로, 하한) */
  cost_unknown: boolean;
  basis: "past_runs" | string;
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
  /** merge: 묶음을 이 공간에 합침 (c0018, 웹 복원) */
  kind: "export" | "restore" | "merge";
  via: string;
  created_at: IsoDateTime;
  archive_sha256: string;
  archive_bytes: number;
  rows_total: number;
  blobs_total: number;
}

/** 주기 유지보수 (c0020). 일정은 설치 전체, 결과 수는 이 공간 것만. */
export interface AutoMaintenance {
  last_started_at: IsoDateTime | null;
  last_finished_at: IsoDateTime | null;
  interval_seconds: number | null;
  next_at: IsoDateTime | null;
  running: boolean;
  counts: Record<string, number>;
  errors: number;
  last_changed_at: IsoDateTime | null;
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
  /** 웹에서 묶음을 올려 합치기 (c0018) */
  restore_available: boolean;
  max_restore_bytes: number;
  /** 주기 유지보수 (c0020). 없으면 서버가 예전 버전 */
  auto?: AutoMaintenance | null;
}

export type DuplicatePolicy = "skip" | "fail";

export interface MergeTable {
  name: string;
  archive_rows: number;
  added: number;
  skipped: number;
  /** 겹친 행: 찾은 키 → 수 ("id", "content", UNIQUE 제약 이름…) */
  skipped_by: Record<string, number>;
  sha256: string;
}

export interface SkippedDeletion {
  target_table: string;
  target_id: Uuid;
  /** still_referenced | not_permitted | rejected | kept_existing | error */
  reason: string;
  detail: string;
}

export interface MergeReport {
  dry_run: boolean;
  workspace_id: Uuid;
  workspace_name: string;
  source_workspace_id: Uuid;
  source_workspace_name: string;
  on_duplicate: DuplicatePolicy;
  archive_sha256: string;
  encrypted: boolean;
  format_upgraded_from: number | null;
  rows_added: number;
  rows_skipped: number;
  blobs: number;
  actors_mapped: number;
  tables: MergeTable[];
  not_merged: Record<string, string>;
  requeued: Record<string, number>;
  deletions_applied: number;
  deletions_skipped: number;
  skipped_deletions: SkippedDeletion[];
  notes: string[];
}

export type RestoreStatus = "uploaded" | "verifying" | "verified" | "applying" | "done" | "failed";

export interface RestoreJob {
  id: Uuid;
  status: RestoreStatus;
  /** read · decrypt · match · insert · verify · deletions · blobs · commit */
  phase: string | null;
  done: number;
  total: number;
  bytes: number;
  sha256: string;
  encrypted: boolean;
  filename: string | null;
  deletions: number;
  on_duplicate: DuplicatePolicy | null;
  report: MergeReport | null;
  error: string | null;
  error_detail: string | null;
  created_at: IsoDateTime;
  updated_at: IsoDateTime;
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
  request: (
    method: string,
    path: string,
    opts?: { query?: SysQuery; json?: unknown; blob?: Blob; headers?: Record<string, string> },
  ) => Promise<Response>;
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
    workflows: () => get<Workflow[]>("/core/runs/workflows"),
    /** 일괄 실행 시작 (c0023 웹). 같은 멱등 키면 처음 만든 실행을 돌려준다 */
    startRun: (workflow: string, input: Record<string, unknown>, idempotencyKey?: string) =>
      post<Run>("/core/runs", { workflow, input, idempotency_key: idempotencyKey ?? null }),
    runEstimate: (workflow: string, units: number) =>
      get<RunEstimate>(`/core/runs/workflows/${id(workflow)}/estimate`, { units }),
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

    // 웹 복원: 묶음을 올려 이 공간에 합치기 (소유자만). 확인(dry-run) → 합치기, 둘 다 배경 작업.
    uploadRestore: async (file: Blob, filename?: string) => {
      const headers: Record<string, string> = {};
      // 머리말은 ASCII만: 이름은 표시용이라 다른 글자는 뺀다
      const ascii = filename?.replace(/[^\x20-\x7e]/g, "_");
      if (ascii) headers["X-Filename"] = ascii;
      const res = await request("POST", "/core/maintenance/restore", { blob: file, headers });
      return (await res.json()) as RestoreJob;
    },
    uploadRestoreDeletions: async (jobId: string, file: Blob) => {
      const res = await request("POST", `/core/maintenance/restore/${id(jobId)}/deletions`, { blob: file });
      return (await res.json()) as RestoreJob;
    },
    restoreJob: (jobId: string) => get<RestoreJob>(`/core/maintenance/restore/${id(jobId)}`),
    verifyRestore: (jobId: string, body: { on_duplicate: DuplicatePolicy; identity?: string }) =>
      post<RestoreJob>(`/core/maintenance/restore/${id(jobId)}/verify`, body),
    applyRestore: (
      jobId: string,
      body: { on_duplicate: DuplicatePolicy; archive_sha256: string; identity?: string },
    ) => post<RestoreJob>(`/core/maintenance/restore/${id(jobId)}/apply`, body),
    discardRestore: async (jobId: string) => {
      await request("DELETE", `/core/maintenance/restore/${id(jobId)}`);
    },

    // MCP 도구 호출 기록 (원문 인자 없음)
    audit: (opts: { limit?: number; before?: string; tool?: string; status?: "ok" | "error" } = {}) =>
      get<AuditPage>("/core/audit", opts),
  };
}
