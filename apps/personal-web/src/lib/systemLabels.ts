import type { ApprovalStatus, RunStatus } from "@career-brain/client";
import type { BadgeTone } from "@career-brain/ui";
import { ApiError } from "./api";

export const RUN_STATUS: Record<RunStatus, { label: string; tone: BadgeTone }> = {
  queued: { label: "대기", tone: "none" },
  running: { label: "실행 중", tone: "accent" },
  waiting_approval: { label: "승인 대기", tone: "warn" },
  interrupted: { label: "멈춤", tone: "warn" },
  cancelled: { label: "취소됨", tone: "none" },
  failed: { label: "실패", tone: "danger" },
  done: { label: "완료", tone: "ok" },
};

export const APPROVAL_STATUS: Record<ApprovalStatus, { label: string; tone: BadgeTone }> = {
  pending: { label: "대기", tone: "warn" },
  approved: { label: "승인함", tone: "ok" },
  rejected: { label: "거절함", tone: "none" },
  used: { label: "사용됨", tone: "neutral" },
  invalidated: { label: "무효", tone: "danger" },
  expired: { label: "만료", tone: "none" },
};

export const WORKFLOW_LABEL: Record<string, string> = {
  save_job_urls: "공고 주소 저장",
  submit_application: "지원서 제출",
  suggest_document_batch: "문단별 AI 수정안",
  interview_question_batch: "AI 인터뷰 질문 묶음",
};

export const ACTION_LABEL: Record<string, string> = {
  "applications.submit": "지원서 제출",
};

export const RUN_EVENT_KIND: Record<string, string> = {
  status: "상태 바뀜",
  step: "단계 끝남",
  wait: "기다림",
  resumed: "이어서 시작",
  error: "오류",
};

/** 실행 이벤트·실패 사유 코드 */
export const RUN_CODE: Record<string, string> = {
  cancel_requested: "취소 요청 (이번 단계가 끝나면 멈춤)",
  cancelled_by_user: "사용자가 취소",
  interrupt_requested: "멈춤 요청 (이번 단계가 끝나면 멈춤)",
  interrupted_by_user: "사용자가 멈춤",
  resumed_by_user: "사용자가 다시 시작",
  approval_decided: "승인 결정됨",
  approval_invalidated: "대상이 바뀌어 승인이 무효가 됨",
  approval_expired: "승인 요청이 만료됨",
  unknown_workflow: "알 수 없는 작업",
  actor_revoked: "요청한 사용자의 권한이 없어짐",
  step_error: "단계 실행 오류",
  rate_limited: "제공자 한도: 잠시 후 다시 시도",
  timeout: "시간 초과: 잠시 후 다시 시도",
  connection_error: "연결 오류: 잠시 후 다시 시도",
  quota_exceeded: "제공자 사용 한도 초과",
  budget_exceeded: "월 예산 초과",
  price_unknown: "단가를 몰라 호출하지 않음",
  // 일괄 실행(c0023 웹에서 시작) 실패 사유
  egress_denied: "자료 등급 때문에 모델로 보내지 않음",
  no_model_gateway: "모델 설정이 없음",
  revision_conflict: "시작한 뒤 문서가 바뀜 (다시 시작)",
  application_locked: "제출본을 고정한 지원",
  target_not_found: "대상이 없어짐",
  nothing_to_suggest: "수정안을 받을 문단이 없음",
  unknown_block: "없는 문단",
  forbidden: "권한 없음",
};

export const INVALID_REASON: Record<string, string> = {
  target_changed: "요청 뒤 대상(문서·공고·상태)이 바뀜",
  target_missing: "대상이 지워짐",
  payload_changed: "요청 내용이 바뀜",
};

const SYSTEM_ERROR: Record<string, string> = {
  approval_not_pending: "이미 결정했거나 닫힌 승인입니다.",
  approval_expired: "만료된 승인입니다.",
  payload_mismatch: "화면에 보인 내용과 저장된 요청이 다릅니다. 새로고침하세요.",
  target_changed: "요청 뒤 대상이 바뀌어 승인할 수 없습니다.",
  human_approval_required: "승인은 사람만 할 수 있습니다 (에이전트 토큰 불가).",
  run_finished: "이미 끝난 실행입니다.",
  run_not_active: "대기·실행 중일 때만 멈출 수 있습니다.",
  run_not_interrupted: "멈춘 실행만 다시 시작할 수 있습니다.",
  invalid_recipient: "age 공개 키 형식이 아닙니다 (age1…).",
  forbidden: "이 토큰에는 권한이 없습니다 (공간 소유자만).",
};

/** 시스템 화면 전용 오류 문구. 없으면 null (공통 errorText로). */
export function systemErrorText(error: unknown): string | null {
  return error instanceof ApiError && error.code ? (SYSTEM_ERROR[error.code] ?? null) : null;
}

const FIELD: Record<string, string> = {
  revision: "문서 버전",
  content_hash: "본문",
  char_limit: "글자 수 제한",
  byte_limit: "바이트 제한",
  status: "지원 상태",
  title: "제목",
  posting_id: "공고",
  posting_version_id: "공고 확정 버전",
  posting_fields: "공고 내용",
  posting_pending_changes: "공고 변경 대기",
  documents: "문서",
};

/** "documents[<id>].revision" → "문서 버전" */
export function changeField(path: string): string {
  const leaf = path.replace(/\[[^\]]*\]/g, "").split(".").filter(Boolean).pop() ?? path;
  return FIELD[leaf] ?? leaf;
}

/** 값 표시: 해시는 짧게, 목록은 쉼표로. */
export function changeValue(v: unknown): string {
  if (v === null || v === undefined) return "없음";
  if (typeof v === "string") return /^[0-9a-f]{64}$/.test(v) ? `${v.slice(0, 8)}…` : v;
  if (Array.isArray(v)) return v.length ? v.map(changeValue).join(", ") : "없음";
  if (typeof v === "object") {
    const o = v as Record<string, unknown>;
    return typeof o.title === "string" ? o.title : JSON.stringify(o).slice(0, 80);
  }
  return String(v);
}

export function bytes(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let v = n / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v.toFixed(v >= 10 ? 0 : 1)} ${units[i]}`;
}

/** 내려받은 파일을 브라우저 저장으로 넘긴다. */
export function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
