// 연결·관리 화면의 모델 연결 API (O22, plan §21). 개인 앱이 켤 때만 동작한다 (아니면 not_enabled).
// 키 값은 어떤 응답에도 오지 않는다. 화면에는 key 상태와 끝 4자리만.
// createClient 안의 요청 함수를 받아 쓴다 (인증·오류 처리는 그쪽과 같다).
import type { DecimalString, IsoDateTime } from "./types";

/** file: models.toml에서 읽음(화면에서 읽기 전용) · ui: 화면에서 만들거나 덮음(DB) */
export type ModelConfigSource = "file" | "ui";
/** metered: 종량제(월 예산에 듦) · subscription: 구독(실지출 0, 환산만) · free: 무료(로컬) */
export type ModelBilling = "metered" | "subscription" | "free";
export type ModelKeyState = "set" | "missing" | "not_needed" | "needs_reentry";
/** 연결로 보낼 수 있는 자료 등급. restricted는 고를 수 없다 */
export type ModelDataClass = "public" | "private" | "restricted";

export interface ModelConnection {
  name: string;
  source: ModelConfigSource;
  /** anthropic · openai · gemini · xai · openrouter · cliproxyapi · ollama · openai-compatible · local … */
  preset: string;
  kind: string;
  base_url: string;
  billing: ModelBilling;
  /** 구독·프록시일 때 "API였다면 얼마"를 환산할 단가 기준 (preset 이름) */
  price_as: string | null;
  /** 비어 있으면 모든 호출을 거부한다 */
  data_classes: string[];
  key: ModelKeyState;
  key_last4: string | null;
  last_test_at: IsoDateTime | null;
  last_test_ok: boolean | null;
  /** false = 자료가 이 기계 밖으로 나가지 않음 (로컬 모델) */
  leaves_host: boolean;
}

export interface ModelRoute {
  purpose: string;
  endpoint: string;
  model: string;
  /** ui = 화면 값이 파일 값을 덮음 */
  source: ModelConfigSource;
}

export interface ModelBudget {
  monthly_usd: DecimalString | null;
  source: ModelConfigSource | null;
  spent_usd_this_month: DecimalString;
}

export interface ModelPreset {
  name: string;
  kind: string;
  base_url: string | null;
  billing: ModelBilling;
  needs_key: boolean;
}

export interface ModelsStatus {
  connections: ModelConnection[];
  routes: ModelRoute[];
  /** 라우트를 고를 수 있는 용도 */
  purposes: string[];
  budget: ModelBudget;
  presets: ModelPreset[];
}

export interface ModelConnectionIn {
  name: string;
  preset: string;
  base_url?: string;
  /** 보낼 때만 저장한다. 응답·목록에는 오지 않는다 */
  api_key?: string;
  billing?: ModelBilling;
  price_as?: string | null;
  data_classes: string[];
  /** max_tokens · max_completion_tokens (직접 연결) */
  token_param?: string | null;
}

/** 준 칸만 바꾼다. api_key: 문자열 = 바꾸기, null = 지우기, 없음 = 그대로 */
export interface ModelConnectionPatch {
  base_url?: string;
  api_key?: string | null;
  billing?: ModelBilling;
  price_as?: string | null;
  data_classes?: string[];
  token_param?: string | null;
}

/** 과금되지 않는 GET {base_url}/models 결과 */
export interface ModelConnectionTest {
  ok: boolean;
  models: string[];
  /** unreachable · unauthorized · bad_response · restricted_not_allowed … */
  error_code?: string | null;
}

interface ModelsDeps {
  request: (method: string, path: string, opts?: { json?: unknown }) => Promise<Response>;
  get: <R>(path: string) => Promise<R>;
}

export function modelEndpoints({ request, get }: ModelsDeps) {
  const id = encodeURIComponent;
  /** 바꾸기 요청. 응답 본문이 없어도(204) 된다 — 화면은 끝나면 status를 다시 읽는다 */
  async function send(method: string, path: string, body?: unknown): Promise<unknown> {
    const res = await request(method, path, body === undefined ? {} : { json: body });
    if (res.status === 204) return null;
    try {
      return await res.json();
    } catch {
      return null;
    }
  }
  return {
    modelsStatus: () => get<ModelsStatus>("/core/models/status"),
    /** 409 connection_name_taken (파일 연결과 이름이 겹쳐도) · 422 invalid_preset · restricted_not_allowed */
    createModelConnection: (body: ModelConnectionIn) => send("POST", "/core/models/connections", body),
    updateModelConnection: (name: string, body: ModelConnectionPatch) =>
      send("PATCH", `/core/models/connections/${id(name)}`, body),
    /** 409 connection_in_use: 이 연결을 쓰는 라우트가 있음 */
    deleteModelConnection: (name: string) => send("DELETE", `/core/models/connections/${id(name)}`),
    testModelConnection: async (name: string) =>
      (await (await request("POST", `/core/models/connections/${id(name)}/test`, { json: {} })).json()) as ModelConnectionTest,
    /** "연결/모델"로 덮고, null이면 파일 값으로 되돌린다 */
    putModelRoutes: (routes: Record<string, string | null>) => send("PUT", "/core/models/routes", { routes }),
    /** "20.00" 또는 null(화면 값 지우기 → 파일 값) */
    putModelBudget: (monthlyUsd: string | null) => send("PUT", "/core/models/budget", { monthly_usd: monthlyUsd }),
  };
}
