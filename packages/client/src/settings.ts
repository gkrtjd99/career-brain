// 공간 공용 설정(c0021: 표시 시간대·기술명 별칭)과 판정 모델(참고 의견) API.
// createClient 안의 요청 함수를 받아 쓴다 (인증·오류 처리는 그쪽과 같다).
import type { IsoDateTime, Uuid } from "./types";

export interface TechAliasEntry {
  /** 대표 표기 (화면 이름 그대로, 예: "FastAPI") */
  term: string;
  /** 같은 기술로 볼 다른 표기 (예: "패스트API") */
  aliases: string[];
}

export interface WorkspaceSettings {
  /** IANA 시간대. 모든 화면의 날짜 표시·복습 마감·D-day 기준 */
  timezone: string;
  timezone_is_default: boolean;
  today: string;
  day_ends_at: IsoDateTime;
  /** 사용자 별칭 (기본 표 위에 합쳐진다) */
  tech_aliases: TechAliasEntry[];
  /** 기본 표 (읽기 전용) */
  builtin_aliases: TechAliasEntry[];
  /** c0052: 넣은 내 경험 자료를 바로 AI로 정리 (기본 켜짐) */
  auto_extract: boolean;
}

/** 준 칸만 바꾼다. timezone: null → 기본값(Asia/Seoul), tech_aliases: [] → 사용자 별칭 비우기 */
export interface WorkspaceSettingsIn {
  timezone?: string | null;
  tech_aliases?: TechAliasEntry[];
  auto_extract?: boolean;
}

/** 판정 모델의 참고 의견. 연결·충족 판정이 아니다 */
export interface JudgeVerdict {
  verdict: "yes" | "no" | "unclear";
  reason: string | null;
  quote: string | null;
  note: string | null;
}

export interface JudgeRun {
  /** unavailable: 라우트 없음(no_route) 또는 egress 범위 없음 */
  status: "ok" | "unavailable" | "denied" | "error" | "partial";
  note: string | null;
  calls: number;
  model: string | null;
  cost_usd: string | null;
  cost_basis: string | null;
  basis: "judge_advisory";
  /** 응답 캐시(c0022)에서 꺼낸 수. calls·cost_usd에는 들어가지 않는다 */
  cached?: number;
}

/** 판정 응답 캐시 (c0022, GET /core/judges/cache) */
export interface JudgeCacheStats {
  entries: number;
  hits: number;
  saved_usd: string | null;
  /** 보관 일수 (CAREER_JUDGE_CACHE_DAYS, 0 = 캐시 안 씀) */
  days: number;
}

export interface VisibilityJudgeRow {
  kind: string;
  requirement: string;
  baseline_verdict: string;
  document_terms: string[];
  judge: JudgeVerdict | null;
  agrees: boolean | null;
}

export interface VisibilityJudge {
  application_id: Uuid;
  document_ids: Uuid[];
  rows: VisibilityJudgeRow[];
  judge: JudgeRun;
  blocks_withheld: number;
  basis: "judge_advisory";
}

export type TalentLevel = "absent" | "claimed" | "shown" | "unclear";

export interface TraitFit {
  document_id: Uuid;
  level: TalentLevel;
  /** 문단 중 가장 높은 "한 일로 보여 준다" 확률 (0~1) */
  shown: number;
  /** "말로 내세우거나 약속한다" 확률 (0~1) */
  said: number | null;
  /** 근거 문단(블록 ID), 확률 높은 순 */
  evidence: string[];
}

export interface TalentTrait {
  text: string;
  source: "posting" | "company";
  confirmed: boolean;
  fact_id: Uuid | null;
  fits: TraitFit[];
  best: TalentLevel | null;
}

export interface TalentFit {
  application_id: Uuid;
  document_ids: Uuid[];
  traits: TalentTrait[];
  judge: JudgeRun;
  blocks_withheld: number;
  basis: "judge_advisory";
}

type Json = <R>(method: string, path: string, opts?: { json?: unknown }) => Promise<R>;

interface SettingsDeps {
  get: <R>(path: string) => Promise<R>;
  json: Json;
}

export function settingsEndpoints({ get, json }: SettingsDeps) {
  const id = encodeURIComponent;
  return {
    workspaceSettings: () => get<WorkspaceSettings>("/core/workspace-settings"),
    saveWorkspaceSettings: (body: WorkspaceSettingsIn) =>
      json<WorkspaceSettings>("PUT", "/core/workspace-settings", { json: body }),
    visibilityJudge: (applicationId: string, documentId?: string | null) =>
      json<VisibilityJudge>("POST", `/core/applications/${id(applicationId)}/visibility-judge`, {
        json: { document_id: documentId ?? null },
      }),
    talentFit: (applicationId: string, documentId?: string | null) =>
      json<TalentFit>("POST", `/core/applications/${id(applicationId)}/talent-fit`, {
        json: { document_id: documentId ?? null },
      }),
    judgeCache: () => get<JudgeCacheStats>("/core/judges/cache"),
    clearJudgeCache: () => json<{ deleted: number }>("DELETE", "/core/judges/cache"),
  };
}
