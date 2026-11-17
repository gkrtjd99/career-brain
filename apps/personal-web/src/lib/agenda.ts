import type {
  ApplicationStage,
  Credential,
  ApplicationSummary,
  PostingSummary,
  UpcomingAssessment,
} from "@career-brain/client";
import { dateKey } from "./dates";
import { hostOf } from "./format";
import { CREDENTIAL_KIND_LABEL } from "./credentials";
import { ASSESSMENT_KIND, STAGE_KIND } from "./labels";

/** 오늘 화면 달력·일정의 한 칸. 날짜는 표시 시간대의 YYYY-MM-DD. */
export type AgendaKind = "deadline" | "opens" | "stage" | "test" | "expiry";

export interface AgendaEvent {
  key: string;
  date: string;
  /** 시각이 있으면 ISO, 날짜만 있는 마감이면 null */
  at: string | null;
  kind: AgendaKind;
  title: string;
  sub: string;
  to: string;
  params?: Record<string, string>;
  search?: Record<string, string>;
}

export const AGENDA_KIND: Record<AgendaKind, string> = {
  deadline: "마감",
  opens: "접수 시작",
  stage: "전형",
  test: "시험",
  expiry: "만료",
};

const DATE_ONLY = /^\d{4}-\d{2}-\d{2}$/;

export function postingName(p: Pick<PostingSummary, "title" | "canonical_url">) {
  return p.title ?? hostOf(p.canonical_url);
}

/**
 * 저장한 공고의 마감·접수 시작, 제출한 지원의 결과 전 전형 일정, 일정이 잡힌 시험, 자격·어학 성적의 만료일을 한 목록으로.
 * 같은 공고에 지원이 있으면 마감은 지원 화면으로 잇는다. 만료일은 적힌 것만 (서버가 계산하지 않는다).
 */
export function buildAgenda(input: {
  postings: PostingSummary[];
  applications: ApplicationSummary[];
  stages: ApplicationStage[];
  tests: UpcomingAssessment[];
  credentials?: Credential[];
}): AgendaEvent[] {
  const appByPosting = new Map<string, ApplicationSummary>();
  for (const a of input.applications)
    if (!appByPosting.has(a.posting_id) || a.status === "draft") appByPosting.set(a.posting_id, a);
  const appById = new Map(input.applications.map((a) => [a.id, a]));
  const out: AgendaEvent[] = [];

  for (const p of input.postings) {
    if (p.status !== "saved" || !p.deadline || p.deadline === "always") continue;
    const date = DATE_ONLY.test(p.deadline) ? p.deadline : dateKey(p.deadline);
    if (!date) continue;
    const a = appByPosting.get(p.id);
    out.push({
      key: `deadline-${p.id}`,
      date,
      at: DATE_ONLY.test(p.deadline) ? null : p.deadline,
      kind: "deadline",
      title: p.company ?? postingName(p),
      sub: [p.company ? postingName(p) : null, a && a.status !== "draft" ? "제출함" : null]
        .filter(Boolean)
        .join(" · "),
      ...(a
        ? { to: "/applications/$applicationId", params: { applicationId: a.id } }
        : { to: "/postings/$postingId", params: { postingId: p.id } }),
    });
  }

  for (const p of input.postings) {
    // 접수 시작은 원문에 적힌 것만 (서버가 추측하지 않는다). 이미 지원을 만들었으면 띄우지 않는다
    if (p.status !== "saved" || !p.opens_at || appByPosting.has(p.id)) continue;
    const date = DATE_ONLY.test(p.opens_at) ? p.opens_at : dateKey(p.opens_at);
    if (!date) continue;
    out.push({
      key: `opens-${p.id}`,
      date,
      at: DATE_ONLY.test(p.opens_at) ? null : p.opens_at,
      kind: "opens",
      title: p.company ?? postingName(p),
      sub: p.company ? postingName(p) : "",
      to: "/postings/$postingId",
      params: { postingId: p.id },
    });
  }

  for (const s of input.stages) {
    if (!s.scheduled_at || s.outcome !== "pending") continue;
    const a = appById.get(s.application_id);
    out.push({
      key: `stage-${s.id}`,
      date: dateKey(s.scheduled_at),
      at: s.scheduled_at,
      kind: "stage",
      title: a?.company ?? a?.title ?? "지원",
      sub: [STAGE_KIND[s.kind], s.name !== STAGE_KIND[s.kind] ? s.name : null].filter(Boolean).join(" · "),
      to: "/applications/$applicationId",
      params: { applicationId: s.application_id },
    });
  }

  for (const t of input.tests) {
    out.push({
      key: `test-${t.id}`,
      date: dateKey(t.scheduled_at),
      at: t.scheduled_at,
      kind: "test",
      title: t.title,
      sub: [ASSESSMENT_KIND[t.kind], t.requirement?.posting_title].filter(Boolean).join(" · "),
      to: "/study",
      search: { tab: "assessments" },
    });
  }

  for (const c of input.credentials ?? []) {
    if (!c.expires_on || c.status === "extracted") continue;
    out.push({
      key: `expiry-${c.id}`,
      date: c.expires_on,
      at: null,
      kind: "expiry",
      title: `${c.name} 만료`,
      sub: [CREDENTIAL_KIND_LABEL[c.kind], c.score].filter(Boolean).join(" · "),
      to: "/brain/credentials",
    });
  }

  return out.sort((x, y) => (x.date === y.date ? (x.at ?? "~").localeCompare(y.at ?? "~") : x.date.localeCompare(y.date)));
}

/** 날짜 키끼리의 날 수 차이 (b - a) */
export function daysBetween(a: string, b: string) {
  const t = (k: string) => Date.UTC(Number(k.slice(0, 4)), Number(k.slice(5, 7)) - 1, Number(k.slice(8, 10)));
  return Math.round((t(b) - t(a)) / 86_400_000);
}
