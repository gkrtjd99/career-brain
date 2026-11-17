// 이력서 화면의 말 (O17). 판본 종류·템플릿·구역 이름·상태·오류.
import type {
  ResumeItem,
  ResumeKind,
  ResumeLineState,
  ResumeSectionKind,
  ResumeTemplate,
} from "@career-brain/client";
import { ApiError } from "./api";

export const RESUME_KIND_LABEL: Record<ResumeKind, string> = {
  master: "마스터",
  role: "직무별",
  posting: "공고별",
};

export const TEMPLATE_LABEL: Record<ResumeTemplate, string> = {
  one_column: "단정한 한 단",
  two_column: "두 단",
  korean_table: "한국식 표 양식",
};

export const SECTION_LABEL: Record<ResumeSectionKind, string> = {
  profile: "프로필",
  work: "경력",
  education: "학력·교육",
  project: "프로젝트",
  skills: "기술",
  credentials: "자격·수료",
  awards: "수상",
  publications: "논문·발표",
  activities: "대외활동",
};

export const LINE_STATE_LABEL: Record<ResumeLineState, string | null> = {
  none: "근거 없음",
  ok: null,
  stale: "사실이 바뀜",
  broken: "근거 끊김",
};

export const PROFILE_SHOW_LABEL: Record<string, string> = {
  photo: "사진",
  birth_date: "생년월일",
  address: "주소",
  name_en: "영문 이름",
  military: "병역",
};

export const FORMAT_LABEL = {
  html: "인쇄용 HTML",
  docx: "Word (DOCX)",
  md: "Markdown",
  json: "JSON Resume",
} as const;

const s = (v: unknown) => (typeof v === "string" && v.trim() ? v : null);

/** 편집 목록에 보이는 항목 이름과 둘째 줄 */
export function itemTitle(sectionKind: ResumeSectionKind, it: ResumeItem): { title: string; sub: string | null } {
  const f = it.fields;
  if (sectionKind === "profile") return { title: s(f.name) ?? "이름 없음", sub: s(f.headline) };
  if (sectionKind === "skills") return { title: s(f.name) ?? s(f.title) ?? "기술", sub: null };
  if (it.ref?.type === "education") {
    return {
      title: s(f.school) ?? "학교",
      sub: [s(f.major), period(f)].filter(Boolean).join(" · ") || null,
    };
  }
  if (it.ref?.type === "credential") {
    return { title: s(f.name) ?? "자격", sub: [s(f.issuer), s(f.acquired_on)?.slice(0, 7)].filter(Boolean).join(" · ") || null };
  }
  return {
    title: s(f.title) ?? "제목 없음",
    sub: [s(f.organization), s(f.role), s(f.period_text) ?? period(f)].filter(Boolean).join(" · ") || null,
  };
}

function period(f: Record<string, unknown>): string | null {
  const a = s(f.started_on) ?? s(f.entered_on);
  const b = f.ongoing ? "현재" : (s(f.ended_on) ?? s(f.graduated_on));
  const m = (x: string | null) => (x ? x.slice(0, 7).replace("-", ".") : null);
  if (!a && !b) return null;
  return [m(a), m(b) ?? (b === "현재" ? "현재" : null)].filter(Boolean).join(" – ");
}

const ERRORS: Record<string, string> = {
  revision_conflict: "그사이 이력서가 바뀌었습니다. 최신 내용을 다시 불러왔으니 한 번 더 해 주세요.",
  master_exists: "마스터 이력서는 하나입니다. 직무별·공고별 판본은 마스터에서 만듭니다.",
  version_has_children: "이 판본에서 만든 판본이 있어 삭제할 수 없습니다. 아래 판본부터 삭제합니다.",
  parent_required: "어느 판본에서 시작할지 골라 주세요.",
  invalid_parent: "직무별은 마스터에서, 공고별은 마스터나 직무별에서 만듭니다.",
  application_needs_posting: "지원에 잇는 것은 공고별 판본만 됩니다.",
  claim_not_confirmed: "확인한 사실만 줄로 넣을 수 있습니다. 브레인에서 먼저 확인합니다.",
  claim_not_in_experience: "이 경험(과 그 아래 경험)의 사실만 고를 수 있습니다.",
  claim_already_used: "이 사실은 이미 줄로 들어가 있습니다.",
  unknown_claim: "사실을 찾지 못했습니다.",
  already_added: "이미 넣은 기록입니다.",
  ref_fields_locked: "브레인 기록에서 온 칸은 여기서 바꾸지 않습니다. 브레인에서 바꾸고 다시 채웁니다.",
  not_from_brain: "직접 쓴 항목은 다시 채울 수 없습니다.",
  unknown_record: "브레인에서 그 기록을 찾지 못했습니다.",
  title_required: "제목을 적어 주세요.",
  empty_line: "빈 줄은 넣지 않습니다.",
  line_too_long: "줄이 너무 깁니다 (500자까지).",
  too_many_lines: "한 항목의 줄은 30개까지입니다.",
  too_many_items: "항목이 너무 많습니다.",
  prep_not_chosen: "지원 준비에서 고른 경험이 아직 없습니다.",
  no_application: "이 판본은 지원에 이어져 있지 않습니다.",
  nothing_to_fill: "브레인에 더 채울 기록이 없습니다.",
  nothing_to_export: "아직 내용이 없습니다.",
  no_evidence: "근거 사실이 확인된 상태가 아니라 새 문장을 받을 수 없습니다.",
  models_not_configured: "모델 연결이 없어 줄 다듬기를 쓸 수 없습니다. 연결·관리에서 모델을 연결합니다.",
  invalid_model_output: "모델 답을 읽지 못했습니다. 다시 해 주세요.",
  forbidden: "이 작업을 할 권한이 없습니다.",
};

export function resumeErrorText(error: unknown): string | null {
  if (!(error instanceof ApiError)) return null;
  const detail = error.detail as { code?: string } | null;
  const code = error.code ?? (detail && typeof detail === "object" ? detail.code : undefined);
  if (error.status === 403) return ERRORS.forbidden!;
  return (code && ERRORS[code]) || null;
}

export function isConflict(error: unknown): boolean {
  if (!(error instanceof ApiError) || error.status !== 409) return false;
  const detail = error.detail as { code?: string } | null;
  return !!detail && typeof detail === "object" && detail.code === "revision_conflict";
}
