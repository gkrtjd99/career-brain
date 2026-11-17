// 연결·관리 화면(O22)의 모델 연결 문구. 키 값은 다루지 않는다 (상태·끝 4자리만).
import type { ModelBilling, ModelConnection, ModelKeyState } from "@career-brain/client";

/** 연결 추가의 첫 단계: 종류를 고르면 묻는 칸이 정해진다 (plan §21 표) */
export type ConnectionKind = "official" | "subscription" | "litellm" | "ollama" | "custom";

export const KIND_CHOICES: { kind: ConnectionKind; label: string; hint: string }[] = [
  { kind: "official", label: "공식 API", hint: "Anthropic·OpenAI·Gemini·xAI·OpenRouter의 API 키. 쓴 만큼 청구되고 월 예산에 듭니다." },
  {
    kind: "subscription",
    label: "구독 계정 (CLIProxyAPI)",
    hint: "ChatGPT·Claude·Gemini 구독을 CLIProxyAPI로 잇습니다. 실지출 0, 종량제였다면 얼마인지만 환산합니다.",
  },
  { kind: "litellm", label: "LiteLLM 프록시", hint: "이미 돌리는 LiteLLM 서버. 과금 방식은 직접 고릅니다." },
  { kind: "ollama", label: "로컬 모델 (Ollama)", hint: "이 기계에서 도는 모델. 자료가 밖으로 나가지 않고 무료입니다." },
  { kind: "custom", label: "직접 (OpenAI 호환)", hint: "OpenAI 호환 /v1 주소가 있는 그 밖의 서버." },
];

export const OFFICIAL_PRESETS: [string, string][] = [
  ["anthropic", "Anthropic"],
  ["openai", "OpenAI"],
  ["gemini", "Google Gemini"],
  ["xai", "xAI"],
  ["openrouter", "OpenRouter"],
];

export const DEFAULT_BASE_URL: Partial<Record<ConnectionKind, string>> = {
  subscription: "http://127.0.0.1:8317/v1",
  litellm: "http://127.0.0.1:4000/v1",
  ollama: "http://127.0.0.1:11434/v1",
};

export const KIND_PRESET: Record<Exclude<ConnectionKind, "official">, string> = {
  subscription: "cliproxyapi",
  litellm: "openai-compatible",
  ollama: "ollama",
  custom: "openai-compatible",
};

const PRESET_LABEL: Record<string, string> = {
  anthropic: "공식 API · Anthropic",
  openai: "공식 API · OpenAI",
  gemini: "공식 API · Gemini",
  xai: "공식 API · xAI",
  openrouter: "공식 API · OpenRouter",
  cliproxyapi: "구독 계정 (CLIProxyAPI)",
  ollama: "로컬 모델 (Ollama)",
  local: "로컬 임베딩",
  "openai-compatible": "OpenAI 호환",
};

/** 목록에 보일 종류 이름. preset이 더 구체적이므로 그것을 먼저 본다 */
export function connectionKindLabel(c: Pick<ModelConnection, "preset" | "kind">) {
  return PRESET_LABEL[c.preset] ?? PRESET_LABEL[c.kind] ?? c.preset;
}

/** 고치기 시트가 쓰는 종류 (openai-compatible은 LiteLLM 기본 주소면 LiteLLM으로 본다) */
export function connectionKindOf(c: Pick<ModelConnection, "preset" | "base_url">): ConnectionKind {
  if (OFFICIAL_PRESETS.some(([p]) => p === c.preset)) return "official";
  if (c.preset === "cliproxyapi") return "subscription";
  if (c.preset === "ollama") return "ollama";
  if (c.preset === "openai-compatible" && c.base_url === DEFAULT_BASE_URL.litellm) return "litellm";
  return "custom";
}

export const BILLING_LABEL: Record<ModelBilling, string> = {
  metered: "종량제",
  subscription: "구독",
  free: "무료",
};

export function keyStateText(c: Pick<ModelConnection, "key" | "key_last4">) {
  const k: ModelKeyState = c.key;
  if (k === "set") return c.key_last4 ? `저장됨 ····${c.key_last4}` : "저장됨";
  if (k === "missing") return "키 없음";
  if (k === "needs_reentry") return "다시 넣어야 함";
  return "필요 없음";
}

const DATA_CLASS_LABEL: Record<string, string> = { public: "공개", private: "개인", restricted: "민감" };

export function dataClassText(classes: string[]) {
  if (!classes.length) return "없음 — 호출 거부";
  return classes.map((c) => DATA_CLASS_LABEL[c] ?? c).join("·");
}

/** 용도(purpose) 이름. 모르는 것은 원래 이름 그대로 */
export const ROUTE_PURPOSE_LABEL: Record<string, string> = {
  default: "기본 (따로 정하지 않은 용도)",
  embed: "검색 색인 (임베딩)",
  rerank: "검색 결과 다시 정렬 (선택)",
  extract: "경험 후보 추출",
  brain_extract: "경험 후보 추출",
  interview: "인터뷰 질문",
  interview_questions: "인터뷰 질문",
  job_extract: "공고 항목 추출",
  job_extract_image: "공고 이미지 읽기",
  company_facts: "회사 항목 추출 (참고)",
  reference_notes: "참고 글 구조 메모 (참고)",
  doc_suggest: "문서 수정안",
  judge_claim: "학습 연결 판정 (참고)",
  judge_visibility: "요건 가시성 판정 (참고)",
  application_prep: "지원 준비 (쓸 경험 고르기)",
  interview_prep: "면접 준비 (예상 질문 다듬기)",
  interview_feedback: "면접 답 의견 (요청할 때만)",
};

/** default로 넘어가지 않는 용도 (models.example.toml): 비어 있으면 그 일을 하지 않는다 */
export const NO_FALLBACK = new Set(["embed", "rerank", "job_extract_image", "judge_claim", "judge_visibility"]);
