// 브레인 "나" 보강 화면의 말 (학력·프로필 더하기·기술 보기·공통 답변·지난 제출본)
import type {
  AnswerAssetKind,
  Degree,
  Education,
  EducationStatus,
  EmploymentType,
  ExtraMajorKind,
  MilitaryStatus,
} from "@career-brain/client";
import { ApiError } from "./api";

export const DEGREE_LABEL: Record<Degree, string> = {
  high_school: "고등학교",
  associate: "전문학사",
  bachelor: "학사",
  master: "석사",
  doctor: "박사",
};

export const EDUCATION_STATUS_LABEL: Record<EducationStatus, string> = {
  graduated: "졸업",
  expected: "졸업 예정",
  enrolled: "재학",
  on_leave: "휴학",
  completed: "수료",
  dropped: "중퇴",
};

export const EXTRA_MAJOR_LABEL: Record<ExtraMajorKind, string> = { double: "복수전공", minor: "부전공" };

export const MILITARY_LABEL: Record<MilitaryStatus, string> = {
  served: "군필",
  serving: "복무 중",
  not_served: "미필",
  exempt: "면제",
  not_applicable: "비대상",
};

export const EMPLOYMENT_LABEL: Record<EmploymentType, string> = {
  regular: "정규직",
  contract: "계약직",
  intern: "인턴",
};

export const ANSWER_KIND_LABEL: Record<AnswerAssetKind, string> = {
  intro: "1분 자기소개",
  motivation: "지원 동기 틀",
  personality: "성격 장단점",
  conflict: "갈등 경험",
  failure: "실패 경험",
  aspiration: "입사 후 포부",
  other: "기타",
};

const ERRORS: Record<string, string> = {
  school_required: "학교 이름을 적어 주세요.",
  gpa_over_scale: "학점이 만점보다 큽니다.",
  gpa_needs_scale: "학점과 만점을 함께 적어 주세요.",
  invalid_gpa: "학점은 0 이상의 숫자로 적어 주세요.",
  invalid_gpa_scale: "만점은 4.5 · 4.3 · 4.0 · 100 가운데 하나입니다.",
  graduated_before_entered: "졸업 달이 입학 달보다 빠릅니다.",
  extra_major_needs_kind: "복수전공인지 부전공인지 골라 주세요.",
  source_not_proof: "증빙 서류로 올린 자료만 이을 수 있습니다.",
  source_not_found: "이을 증빙 서류를 찾을 수 없습니다.",
  experience_not_found: "이을 경험을 찾을 수 없습니다.",
  invalid_month: "달은 YYYY-MM으로 적어 주세요.",
  credential_not_degree: "학력 기록만 옮길 수 있습니다.",
  invalid_military_period: "복무 기간을 확인해 주세요. 끝난 달이 시작한 달보다 빠릅니다.",
  invalid_desired_roles: "희망 직무는 50자 이내로 10개까지 적습니다.",
  invalid_desired_locations: "희망 근무지는 50자 이내로 10개까지 적습니다.",
  claim_not_confirmed: "확인한 사실만 근거로 이을 수 있습니다. 브레인에서 먼저 확인하세요.",
  unknown_claim: "없는 사실을 근거로 이었습니다.",
  too_many_claims: "근거는 30개까지 이을 수 있습니다.",
  invalid_skill_name: "기술 이름을 확인해 주세요.",
  nothing_to_merge: "합칠 다른 표기가 없습니다.",
  not_merged: "합친 표기가 아닙니다.",
  revision_conflict: "그사이 문서가 바뀌었습니다. 다시 열어 최신 글에 넣어 주세요.",
};

/** 이 화면들의 오류 코드 → 문장. 모르는 코드는 null (공통 errorText로) */
export function meErrorText(error: unknown): string | null {
  if (!(error instanceof ApiError)) return null;
  const code =
    error.code ??
    (error.detail && typeof error.detail === "object" && "code" in error.detail
      ? String((error.detail as { code: unknown }).code)
      : null);
  return code ? (ERRORS[code] ?? null) : null;
}

/** "2021-02-01" → "2021.02" */
export function monthText(d: string | null): string | null {
  return d ? `${d.slice(0, 4)}.${d.slice(5, 7)}` : null;
}

/** 학력 기간 한 줄: "2015.03 – 2021.02" */
export function periodText(e: Pick<Education, "entered_on" | "graduated_on">): string | null {
  const a = monthText(e.entered_on);
  const b = monthText(e.graduated_on);
  if (!a || !b) return a ?? b;
  return `${a} – ${b}`;
}

/** 전공 한 줄: "컴퓨터공학 · 경영학 복수전공" */
export function majorText(e: Pick<Education, "major" | "extra_major" | "extra_major_kind">): string | null {
  const extra = e.extra_major
    ? `${e.extra_major}${e.extra_major_kind ? ` ${EXTRA_MAJOR_LABEL[e.extra_major_kind]}` : ""}`
    : null;
  return [e.major, extra].filter(Boolean).join(" · ") || null;
}

export function gpaText(e: Pick<Education, "gpa" | "gpa_scale">): string | null {
  if (e.gpa === null || e.gpa_scale === null) return null;
  const f = (n: number) => (Number.isInteger(n) && n >= 10 ? String(n) : n.toFixed(2).replace(/0$/, ""));
  return `${f(e.gpa)} / ${f(e.gpa_scale)}`;
}

/** 글자 수 (공백 포함), 제한이 있으면 "238 / 700자" */
export function charsText(chars: number | null, limit?: number | null): string {
  if (chars === null) return "–";
  return limit ? `${chars.toLocaleString()} / ${limit.toLocaleString()}자` : `${chars.toLocaleString()}자`;
}
