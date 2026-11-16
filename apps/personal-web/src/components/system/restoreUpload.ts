// 웹 복원 업로드의 진행 표시 (MaintenanceTab 전용).
// fetch 본문은 올리는 동안 진행률을 알 수 없어서, 묶음 올리기만 XHR(upload.onprogress)로 보낸다.
// 서버 경로·상한(CAREER_MAX_RESTORE_MB)·머리말은 client.uploadRestore와 같다.
import { ApiError, type MergeReport, type RestoreJob } from "@career-brain/client";
import { getToken } from "../../lib/api";

const BASE = import.meta.env.VITE_API_BASE ?? "/api";
const PATH = "/core/maintenance/restore";

/** c0024에서 더한 칸 (client 타입에 아직 없는 것) */
export type MergeReportMore = MergeReport & {
  schema_converted_from?: string[] | null;
  approvals_invalidated?: number;
};
export type RestoreJobMore = Omit<RestoreJob, "report"> & {
  report: MergeReportMore | null;
  heartbeat_at?: string | null;
};

export interface UploadProgress {
  loaded: number;
  total: number;
}

function headers(): Record<string, string> {
  const h: Record<string, string> = { Accept: "application/json" };
  const token = getToken();
  if (token) h.Authorization = `Bearer ${token}`;
  return h;
}

function apiError(status: number, body: string): ApiError {
  let detail: unknown = null;
  try {
    detail = (JSON.parse(body) as { detail?: unknown }).detail ?? null;
  } catch {
    // 본문이 JSON이 아니면 상태 코드만 쓴다 (프록시의 413 등).
  }
  if (status === 413 && detail === null) detail = "too_large";
  return new ApiError(status, typeof detail === "string" ? detail : null, detail);
}

/** 묶음을 본문 그대로 올린다. 올라간 바이트를 `onProgress`로 알린다. `signal`로 멈춘다. */
export function uploadRestoreWithProgress(
  file: Blob,
  filename: string | undefined,
  onProgress: (p: UploadProgress) => void,
  signal?: AbortSignal,
): Promise<RestoreJobMore> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${BASE}${PATH}`);
    for (const [k, v] of Object.entries(headers())) xhr.setRequestHeader(k, v);
    xhr.setRequestHeader("Content-Type", "application/octet-stream");
    // 머리말은 ASCII만: 이름은 표시용이라 다른 글자는 뺀다
    const ascii = filename?.replace(/[^\x20-\x7e]/g, "_");
    if (ascii) xhr.setRequestHeader("X-Filename", ascii);
    xhr.upload.onprogress = (e) => onProgress({ loaded: e.loaded, total: e.lengthComputable ? e.total : file.size });
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(JSON.parse(xhr.responseText) as RestoreJobMore);
        } catch {
          reject(apiError(xhr.status, ""));
        }
      } else reject(apiError(xhr.status, xhr.responseText));
    };
    xhr.onerror = () => reject(new ApiError(0, "upload_failed", null));
    xhr.onabort = () => reject(new ApiError(0, "upload_cancelled", null));
    if (signal) {
      if (signal.aborted) {
        reject(new ApiError(0, "upload_cancelled", null));
        return;
      }
      signal.addEventListener("abort", () => xhr.abort(), { once: true });
    }
    onProgress({ loaded: 0, total: file.size });
    xhr.send(file);
  });
}

/** 이 공간의 지금 복원 작업 (없으면 null). 화면을 다시 열었을 때 이어 보기. */
export async function currentRestoreJob(): Promise<RestoreJobMore | null> {
  const res = await fetch(`${BASE}${PATH}`, { headers: headers(), credentials: "same-origin" });
  if (res.status === 404) return null;
  if (!res.ok) throw apiError(res.status, await res.text());
  return (await res.json()) as RestoreJobMore;
}
