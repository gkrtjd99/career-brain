// 브레인 "나" 보강 API (plan §20): 학력(c0040) · 프로필 더하기(c0041) · 기술 보기 · 공통 답변(c0042) · 지난 제출본.
// createClient 안의 요청 함수를 받아 쓴다 (인증·오류 처리는 그쪽과 같다).
import type { IsoDateTime, Uuid } from "./types";

// ---------------------------------------------------------------- 학력

export type Degree = "high_school" | "associate" | "bachelor" | "master" | "doctor";
export const DEGREES: Degree[] = ["high_school", "associate", "bachelor", "master", "doctor"];
export type EducationStatus = "enrolled" | "on_leave" | "expected" | "graduated" | "completed" | "dropped";
export const EDUCATION_STATUSES: EducationStatus[] = [
  "graduated",
  "expected",
  "enrolled",
  "on_leave",
  "completed",
  "dropped",
];
export type ExtraMajorKind = "double" | "minor";
/** 학점 만점으로 받는 값 */
export const GPA_SCALES = [4.5, 4.3, 4.0, 100] as const;

export interface Education {
  id: Uuid;
  school: string;
  campus: string | null;
  major: string | null;
  extra_major: string | null;
  extra_major_kind: ExtraMajorKind | null;
  degree: Degree;
  status: EducationStatus;
  /** 편입 */
  transferred: boolean;
  /** 그 달 1일 (YYYY-MM-01) */
  entered_on: string | null;
  graduated_on: string | null;
  gpa: number | null;
  gpa_scale: number | null;
  note: string | null;
  source_id: Uuid | null;
  source_title: string | null;
  experience_id: Uuid | null;
  experience_title: string | null;
  created_at: IsoDateTime;
  updated_at: IsoDateTime;
}

/** 입학·졸업은 "YYYY-MM". 고칠 때는 준 칸만 바뀌고 null은 비우기 */
export type EducationIn = Partial<
  Omit<Education, "id" | "source_title" | "experience_title" | "created_at" | "updated_at">
> & { school?: string };

// ---------------------------------------------------------------- 프로필 더하기 (restricted)

export type MilitaryStatus = "served" | "not_served" | "exempt" | "not_applicable" | "serving";
export const MILITARY_STATUSES: MilitaryStatus[] = ["served", "serving", "not_served", "exempt", "not_applicable"];
export type EmploymentType = "regular" | "contract" | "intern";
export const EMPLOYMENT_TYPES: EmploymentType[] = ["regular", "contract", "intern"];

/** 병역·보훈·장애·희망 조건. 모델·MCP로 보내지 않는다 */
export interface ProfileDetails {
  military_status: MilitaryStatus | null;
  military_branch: string | null;
  military_rank: string | null;
  /** 그 달 1일 */
  military_started_on: string | null;
  military_ended_on: string | null;
  military_exemption_reason: string | null;
  veteran: boolean | null;
  disability: boolean | null;
  desired_roles: string[];
  desired_locations: string[];
  desired_salary: string | null;
  available_from: string | null;
  employment_types: EmploymentType[];
}

/** 준 칸만 바꾼다. 복무 기간은 "YYYY-MM" */
export type ProfileDetailsPatch = Partial<ProfileDetails>;

// ---------------------------------------------------------------- 기술 보기

export interface SkillExperience {
  id: Uuid;
  title: string;
  kind: string;
  started_on: string | null;
  ended_on: string | null;
  ongoing: boolean;
  /** tag 태그 · claim 확인한 사실 */
  via: ("tag" | "claim")[];
}

export interface Skill {
  key: string;
  name: string;
  /** 이 기술로 묶인 표기 */
  spellings: string[];
  /** 내가 합친 표기 (나누기 가능) */
  merged: string[];
  /** 쓴 경험 수 */
  count: number;
  claims: number;
  first_year: number | null;
  last_year: number | null;
  experiences: SkillExperience[];
  credentials: { id: Uuid; name: string; kind: string; acquired_on: string | null }[];
}

// ---------------------------------------------------------------- 공통 답변

export type AnswerAssetKind = "intro" | "motivation" | "personality" | "conflict" | "failure" | "aspiration" | "other";
export const ANSWER_ASSET_KINDS: AnswerAssetKind[] = [
  "intro",
  "motivation",
  "personality",
  "conflict",
  "failure",
  "aspiration",
  "other",
];

export interface AnswerAssetClaim {
  id: Uuid;
  statement: string;
  kind: string;
  status: string;
  /** 지금도 확인한 사실인가 (근거로 이은 뒤 상태가 바뀔 수 있다) */
  confirmed: boolean;
  experience_id: Uuid;
  experience_title: string;
}

export interface AnswerAssetUse {
  application_id: Uuid;
  application_title: string;
  document_id: Uuid;
  document_title: string;
  created_at: IsoDateTime;
}

export interface AnswerAssetSummary {
  id: Uuid;
  kind: AnswerAssetKind;
  title: string;
  excerpt: string;
  chars: number;
  chars_without_spaces: number;
  claims: number;
  uses: number;
  updated_at: IsoDateTime;
}

export interface AnswerAsset {
  id: Uuid;
  kind: AnswerAssetKind;
  title: string;
  body: string;
  chars: number;
  chars_without_spaces: number;
  claims: AnswerAssetClaim[];
  uses: AnswerAssetUse[];
  source_submission_id: Uuid | null;
  source_document_id: Uuid | null;
  created_at: IsoDateTime;
  updated_at: IsoDateTime;
}

export interface AnswerAssetIn {
  kind?: AnswerAssetKind;
  title: string;
  body?: string;
  /** 확인한 사실만 (422 claim_not_confirmed · unknown_claim) */
  claim_ids?: Uuid[];
}

/**
 * 지원서에 넣기. document_id가 있으면 그 문서 끝에 더한다 (base_revision 필수, 오래되면 409).
 * 없으면 새 문서 (question_prompt가 있으면 공고 문항 문서). dry_run이면 쓰지 않고 경고만 돌려준다.
 */
export interface PlaceIn {
  application_id: Uuid;
  document_id?: Uuid | null;
  base_revision?: number | null;
  question_prompt?: string | null;
  char_limit?: number | null;
  dry_run?: boolean;
}

export interface PlaceResult {
  application_id: Uuid;
  document_id: Uuid | null;
  revision_no: number | null;
  created_document: boolean;
  /** 글에 보이는 다른 회사 이름 (이 지원의 회사는 뺀다). 막지 않는다 */
  company_mentions: string[];
  /** 더는 확인한 사실이 아니라 붙이지 않은 근거 수 */
  claims_dropped: number;
}

// ---------------------------------------------------------------- 지난 제출본

export interface SubmittedDocument {
  submission_id: Uuid;
  application_id: Uuid;
  application_title: string;
  company: string | null;
  role: string | null;
  document_id: Uuid;
  kind: string;
  title: string;
  question: string | null;
  char_limit: number | null;
  chars: number | null;
  submitted_at: IsoDateTime;
}

export interface SubmittedEvidence {
  claim_id: string;
  statement: string;
  kind: string;
  status: string;
  experience?: { id: string; title: string } | null;
}

export interface SubmittedDocumentView extends SubmittedDocument {
  text: string;
  blocks: { id: string | null; text: string; origin: string; claim_ids: string[] }[];
  evidence: SubmittedEvidence[];
  /** 글에 보이는 회사 이름 (다른 지원에 쓸 때 고칠 곳) */
  company_mentions: string[];
  /** 이 글에서 시작한 공통 답변 */
  answers: { id: Uuid; title: string }[];
}

export interface StartedAnswer {
  answer_id: Uuid;
  company_mentions: string[];
  claims_dropped: number;
}

interface BrainMeDeps {
  request: (method: string, path: string, opts?: { json?: unknown }) => Promise<Response>;
  get: <R>(path: string) => Promise<R>;
  post: <R>(path: string, body?: unknown) => Promise<R>;
  patch: <R>(path: string, body: unknown) => Promise<R>;
}

export function brainMeEndpoints({ request, get, post, patch }: BrainMeDeps) {
  const id = encodeURIComponent;
  const sub = (submissionId: string, documentId: string) =>
    `/core/submitted-documents/${id(submissionId)}/${id(documentId)}`;
  return {
    // 학력
    education: () => get<Education[]>("/core/education"),
    educationItem: (educationId: string) => get<Education>(`/core/education/${id(educationId)}`),
    createEducation: (body: EducationIn & { school: string }) => post<{ id: Uuid }>("/core/education", body),
    patchEducation: (educationId: string, body: EducationIn) =>
      patch<Education>(`/core/education/${id(educationId)}`, body),
    deleteEducation: async (educationId: string) => {
      await request("DELETE", `/core/education/${id(educationId)}`);
    },
    /** 자격·이수의 학력 기록을 학력으로 옮긴다 (원래 기록은 지운다). 409 credential_not_degree */
    moveCredentialToEducation: (credentialId: string) =>
      post<Education>(`/core/credentials/${id(credentialId)}/to-education`),

    // 프로필 더하기
    profileDetails: () => get<ProfileDetails>("/core/profile/details"),
    patchProfileDetails: (body: ProfileDetailsPatch) => patch<ProfileDetails>("/core/profile/details", body),

    // 기술 보기
    skills: () => get<Skill[]>("/core/skills"),
    /** names를 into 기술의 다른 표기로 묶는다 (워크스페이스 기술명 별칭 표) */
    mergeSkills: (into: string, names: string[]) => post<{ name: string }>("/core/skills/merge", { into, names }),
    splitSkill: async (name: string) => {
      await request("POST", "/core/skills/split", { json: { name } });
    },

    // 공통 답변
    answerAssets: () => get<AnswerAssetSummary[]>("/core/answer-assets"),
    answerAsset: (answerId: string) => get<AnswerAsset>(`/core/answer-assets/${id(answerId)}`),
    createAnswerAsset: (body: AnswerAssetIn) => post<{ id: Uuid }>("/core/answer-assets", body),
    patchAnswerAsset: (answerId: string, body: Partial<AnswerAssetIn>) =>
      patch<AnswerAsset>(`/core/answer-assets/${id(answerId)}`, body),
    deleteAnswerAsset: async (answerId: string) => {
      await request("DELETE", `/core/answer-assets/${id(answerId)}`);
    },
    /** 공통 답변을 지원 문서에 넣고 쓴 곳을 남긴다 */
    useAnswerAsset: (answerId: string, body: PlaceIn) =>
      post<PlaceResult>(`/core/answer-assets/${id(answerId)}/use`, body),

    // 지난 제출본 (읽기 전용)
    submittedDocuments: () => get<SubmittedDocument[]>("/core/submitted-documents"),
    submittedDocument: (submissionId: string, documentId: string) =>
      get<SubmittedDocumentView>(sub(submissionId, documentId)),
    /** 이 글로 시작: 새 공통 답변 */
    startAnswerFromSubmission: (
      submissionId: string,
      documentId: string,
      body: { kind?: AnswerAssetKind; title?: string | null } = {},
    ) => post<StartedAnswer>(`${sub(submissionId, documentId)}/to-answer`, body),
    /** 이 글로 시작: 지원 문서에 넣기 */
    useSubmittedDocument: (submissionId: string, documentId: string, body: PlaceIn) =>
      post<PlaceResult>(`${sub(submissionId, documentId)}/use`, body),
  };
}
