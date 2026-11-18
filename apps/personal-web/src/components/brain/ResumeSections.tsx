import type {
  Credential,
  Education,
  ExperienceKind,
  ExperienceSummary,
  ProfileDetails,
  ProfilePhoto,
} from "@career-brain/client";
import { cn } from "@career-brain/ui";
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { ImageIcon } from "lucide-react";
import { Fragment, useEffect, useState, type ReactNode } from "react";
import { api } from "../../lib/api";
import {
  ANSWER_KIND_LABEL,
  DEGREE_LABEL,
  EDUCATION_STATUS_LABEL,
  EMPLOYMENT_LABEL,
  gpaText,
  majorText,
  MILITARY_LABEL,
  monthText,
  periodText,
} from "../../lib/brainMe";
import { CREDENTIAL_KIND_LABEL, expiryMark, scoreText } from "../../lib/credentials";
import { EXPERIENCE_KIND_LABEL, periodLabel } from "../../lib/experience";
import { CredentialSheet } from "../credentials/CredentialSheet";
import { EducationSheet } from "../education/EducationSheet";
import { useIngest } from "../GlobalActions";

/*
 * 브레인 첫 화면의 이력서 꼴 (2026-11-18, 사람인·잡코리아 이력서 항목 순서).
 * 인적사항 → 경력 → 학력 → 프로젝트·대외활동 → 교육 이수 → 자격증·어학·수상 → 기술 → 포트폴리오
 * → 취업 우대·병역 → 희망 근무 조건 → 자기소개서. 칸마다 항목을 이력서처럼 짧게, 빈 칸은 "비어 있음 · 추가".
 */

const ROW = "-mx-2 flex items-start gap-4 rounded-md px-2 py-2 transition-colors duration-150 hover:bg-subtle/60";
const ACTION =
  "inline-flex min-h-9 items-center text-[13px] text-muted transition-colors duration-150 hover:text-fg sm:min-h-7";

type Act = { label: string; to?: string; search?: Record<string, unknown>; onClick?: () => void };

function Action({ a }: { a: Act }) {
  if (a.to)
    return (
      <Link to={a.to} search={a.search} className={ACTION}>
        {a.label}
      </Link>
    );
  return (
    <button type="button" onClick={a.onClick} className={ACTION}>
      {a.label}
    </button>
  );
}

/** 이력서 한 칸: 왼쪽에 제목·요약·고칠 곳, 오른쪽에 항목. 좁은 화면은 제목 줄 아래로 */
export function ResumeSection({
  id,
  title,
  meta,
  actions = [],
  children,
}: {
  id: string;
  title: string;
  meta?: ReactNode;
  actions?: Act[];
  children: ReactNode;
}) {
  return (
    <section
      aria-labelledby={`rs-${id}`}
      className="border-t border-line py-5 sm:grid sm:grid-cols-[9rem_minmax(0,1fr)] sm:gap-x-8"
    >
      <div className="mb-1.5 flex items-start justify-between gap-3 sm:mb-0 sm:block">
        <div className="min-w-0">
          <h2 id={`rs-${id}`} className="text-[15px] leading-7 font-semibold">
            {title}
          </h2>
          {meta ? <p className="text-[13px] text-muted tabular-nums">{meta}</p> : null}
        </div>
        {actions.length ? (
          <div className="-mr-1 flex shrink-0 gap-3 sm:mt-1.5 sm:mr-0 sm:flex-col sm:items-start sm:gap-0">
            {actions.map((a) => (
              <Action key={a.label} a={a} />
            ))}
          </div>
        ) : null}
      </div>
      <div className="min-w-0">{children}</div>
    </section>
  );
}

/** 빈 칸: 비어 있음 · 추가 */
function Empty({ add, note }: { add?: Act; note?: string }) {
  return (
    <p className="flex min-h-9 flex-wrap items-center gap-x-1.5 text-sm text-muted">
      <span>{note ?? "비어 있음"}</span>
      {add ? (
        <>
          <span aria-hidden>·</span>
          {add.to ? (
            <Link to={add.to} search={add.search} className="text-fg underline underline-offset-2">
              추가
            </Link>
          ) : (
            <button type="button" onClick={add.onClick} className="min-h-9 text-fg underline underline-offset-2">
              추가
            </button>
          )}
        </>
      ) : null}
    </p>
  );
}

/** 항목 한 줄: 굵은 이름, 회색 설명, 오른쪽 기간 */
function Entry({
  to,
  params,
  onClick,
  title,
  sub,
  when,
  mark,
}: {
  to?: string;
  params?: Record<string, string>;
  onClick?: () => void;
  title: ReactNode;
  sub?: ReactNode;
  when?: ReactNode;
  mark?: ReactNode;
}) {
  const body = (
    <>
      <div className="min-w-0 flex-1">
        <div className="text-sm font-medium break-words">
          {title}
          {mark ? <span className="ml-2 text-[13px] font-semibold whitespace-nowrap">{mark}</span> : null}
        </div>
        {sub ? <div className="text-[13px] text-muted break-words">{sub}</div> : null}
      </div>
      {when ? <div className="shrink-0 pt-px text-right text-[13px] leading-5 text-muted tabular-nums">{when}</div> : null}
    </>
  );
  return (
    <li>
      {to ? (
        <Link to={to} params={params} className={ROW}>
          {body}
        </Link>
      ) : (
        <button type="button" onClick={onClick} className={cn(ROW, "w-[calc(100%+1rem)] text-left")}>
          {body}
        </button>
      )}
    </li>
  );
}

const join = (xs: (string | null | undefined | false)[]) => xs.filter(Boolean).join(" · ") || null;

// ---------------------------------------------------------------- 기간 계산 (경력 총 기간)

function months(e: Pick<ExperienceSummary, "started_on" | "ended_on" | "ongoing">, now = new Date()): [number, number] | null {
  if (!e.started_on) return null;
  const at = (ym: string) => Number(ym.slice(0, 4)) * 12 + Number(ym.slice(5, 7)) - 1;
  const start = at(e.started_on);
  const end = e.ongoing || !e.ended_on ? (e.ongoing ? now.getFullYear() * 12 + now.getMonth() : null) : at(e.ended_on);
  if (end === null || end < start) return null;
  return [start, end];
}

export function durationText(n: number): string {
  const y = Math.floor(n / 12);
  const m = n % 12;
  return y ? (m ? `${y}년 ${m}개월` : `${y}년`) : `${m}개월`;
}

/** 겹치는 기간은 한 번만 센다 */
function totalMonths(list: ExperienceSummary[]): number {
  const spans = list.map((e) => months(e)).filter((x): x is [number, number] => !!x).sort((a, b) => a[0] - b[0]);
  let total = 0;
  let cur: [number, number] | null = null;
  for (const s of spans) {
    if (!cur || s[0] > cur[1] + 1) {
      if (cur) total += cur[1] - cur[0] + 1;
      cur = [s[0], s[1]];
    } else cur[1] = Math.max(cur[1], s[1]);
  }
  if (cur) total += cur[1] - cur[0] + 1;
  return total;
}

function byStart(a: ExperienceSummary, b: ExperienceSummary) {
  if (a.ongoing !== b.ongoing) return a.ongoing ? -1 : 1;
  return (b.started_on ?? "").localeCompare(a.started_on ?? "");
}

// ---------------------------------------------------------------- 인적사항

export function PersonalSection() {
  const profile = useQuery({ queryKey: ["profile"], queryFn: api.profile });
  const photos = useQuery({ queryKey: ["profile", "photos"], queryFn: api.profilePhotos });
  const p = profile.data;
  const photo = photos.data?.find((x) => x.id === p?.default_photo_id) ?? photos.data?.[0] ?? null;
  const contact = p ? join([p.email, p.phone, p.address]) : null;
  const born = p?.birth_date ? `${p.birth_date.slice(0, 4)}년생` : null;
  const filled = !!(p?.name || p?.email || p?.phone || p?.headline);
  return (
    <ResumeSection id="personal" title="인적사항" actions={[{ label: filled ? "수정" : "추가", to: "/brain/profile" }]}>
      {profile.isPending ? null : (
        <div className="flex items-start gap-4">
          <Link
            to={photo ? "/brain/profile" : "/tools/photo"}
            aria-label={photo ? "증명사진" : "증명사진 만들기"}
            className="shrink-0 rounded-sm focus-visible:outline-2 focus-visible:outline-accent"
          >
            {photo ? (
              <Thumb photo={photo} />
            ) : (
              <span className="grid h-24 w-[4.5rem] place-items-center rounded-sm border border-dashed border-line text-muted">
                <ImageIcon size={18} aria-hidden />
              </span>
            )}
          </Link>
          <div className="min-w-0 flex-1 space-y-0.5">
            {filled ? (
              <>
                <p className="flex flex-wrap items-baseline gap-x-2">
                  <span className="text-lg leading-7 font-semibold">{p?.name ?? "이름 없음"}</span>
                  {p?.name_en ? <span className="text-[13px] text-muted">{p.name_en}</span> : null}
                  {born ? <span className="text-[13px] text-muted tabular-nums">{born}</span> : null}
                </p>
                {p?.headline ? <p className="text-sm">{p.headline}</p> : null}
                {contact ? <p className="text-[13px] break-words text-muted tabular-nums">{contact}</p> : null}
                {p?.links.length ? (
                  <p className="flex flex-wrap gap-x-3 text-[13px]">
                    {p.links.map((l) => (
                      <a
                        key={l.url}
                        href={l.url}
                        target="_blank"
                        rel="noreferrer"
                        className="inline-flex min-h-9 items-center underline underline-offset-2 sm:min-h-6"
                      >
                        {l.label || l.url}
                      </a>
                    ))}
                  </p>
                ) : null}
              </>
            ) : (
              <Empty add={{ label: "추가", to: "/brain/profile" }} note="이름·연락처가 비어 있음" />
            )}
          </div>
        </div>
      )}
    </ResumeSection>
  );
}

function Thumb({ photo }: { photo: ProfilePhoto }) {
  const file = useQuery({
    queryKey: ["profile-photo-file", photo.id],
    queryFn: () => api.profilePhotoFile(photo.id),
    staleTime: Infinity,
  });
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    if (!file.data) return;
    const u = URL.createObjectURL(file.data);
    setUrl(u);
    return () => URL.revokeObjectURL(u);
  }, [file.data]);
  return (
    <span
      className="block h-24 overflow-hidden rounded-sm bg-subtle shadow-[0_0_0_1px_var(--app-line)]"
      style={{ aspectRatio: `${photo.width_px} / ${photo.height_px}` }}
    >
      {url ? <img src={url} alt="증명사진" className="block h-full w-full object-cover" /> : null}
    </span>
  );
}

// ---------------------------------------------------------------- 경험 (경력·프로젝트·교육)

function reviewMark(e: ExperienceSummary, children: ExperienceSummary[] = []) {
  const n = e.needs_review + children.reduce((k, c) => k + c.needs_review, 0);
  return n ? `확인 ${n}` : null;
}

export function CareerSection({ experiences }: { experiences: ExperienceSummary[] | undefined }) {
  const ingest = useIngest();
  const all = experiences ?? [];
  const works = all.filter((e) => e.kind === "work" && !e.parent_id).sort(byStart);
  const kids = (id: string) => all.filter((c) => c.parent_id === id);
  const total = totalMonths(works);
  return (
    <ResumeSection
      id="career"
      title="경력"
      meta={total ? `총 ${durationText(total)}` : null}
      actions={[
        ...(works.length ? [{ label: "모두 보기", to: "/brain/experiences", search: { kind: "work" } }] : []),
        { label: "추가", onClick: () => ingest.openExperience("work") },
      ]}
    >
      {experiences === undefined ? null : works.length ? (
        <ul>
          {works.map((e) => {
            const children = kids(e.id);
            const span = months(e);
            return (
              <Entry
                key={e.id}
                to="/brain/$experienceId"
                params={{ experienceId: e.id }}
                title={e.organization ?? e.title}
                mark={reviewMark(e, children)}
                sub={join([
                  e.role,
                  // 제목이 회사 이름으로 시작하면 되풀이라 뺀다 ("가상데이터 데이터 엔지니어")
                  e.organization && !e.title.startsWith(e.organization.replace(/\s*\(.*\)$/, "")) ? e.title : null,
                  children.length ? `프로젝트 ${children.length}` : null,
                ])}
                when={
                  <>
                    {periodLabel(e)}
                    {span ? <div>{durationText(span[1] - span[0] + 1)}</div> : null}
                  </>
                }
              />
            );
          })}
        </ul>
      ) : (
        <Empty add={{ label: "추가", onClick: () => ingest.openExperience("work") }} />
      )}
    </ResumeSection>
  );
}

const PROJECT_KINDS: ExperienceKind[] = ["project", "activity", "other"];

export function ProjectSection({ experiences }: { experiences: ExperienceSummary[] | undefined }) {
  const ingest = useIngest();
  const all = experiences ?? [];
  const workIds = new Set(all.filter((e) => e.kind === "work").map((e) => e.id));
  // 경력 안의 프로젝트는 경력 줄에서 센다
  const list = all
    .filter((e) => PROJECT_KINDS.includes(e.kind) && !(e.parent_id && workIds.has(e.parent_id)))
    .sort(byStart);
  return (
    <ResumeSection
      id="projects"
      title="프로젝트·대외활동"
      actions={[
        ...(list.length ? [{ label: "모두 보기", to: "/brain/experiences", search: { kind: "project" } }] : []),
        { label: "추가", onClick: () => ingest.openExperience("project") },
      ]}
    >
      {experiences === undefined ? null : list.length ? (
        <ul>
          {list.map((e) => (
            <Entry
              key={e.id}
              to="/brain/$experienceId"
              params={{ experienceId: e.id }}
              title={e.title}
              mark={reviewMark(e)}
              sub={join([
                e.kind !== "project" ? EXPERIENCE_KIND_LABEL[e.kind] : null,
                e.organization,
                e.role,
                e.tags.slice(0, 4).join(", "),
              ])}
              when={periodLabel(e)}
            />
          ))}
        </ul>
      ) : (
        <Empty add={{ label: "추가", onClick: () => ingest.openExperience("project") }} />
      )}
    </ResumeSection>
  );
}

export function TrainingSection({
  experiences,
  credentials,
}: {
  experiences: ExperienceSummary[] | undefined;
  credentials: Credential[] | undefined;
}) {
  const ingest = useIngest();
  const [open, setOpen] = useState<Credential | null>(null);
  const courses = (experiences ?? []).filter((e) => e.kind === "education").sort(byStart);
  const done = (credentials ?? []).filter((c) => c.kind === "course");
  const loading = experiences === undefined || credentials === undefined;
  return (
    <ResumeSection
      id="training"
      title="교육 이수"
      actions={[{ label: "추가", onClick: () => ingest.openExperience("education") }]}
    >
      {loading ? null : courses.length || done.length ? (
        <ul>
          {courses.map((e) => (
            <Entry
              key={e.id}
              to="/brain/$experienceId"
              params={{ experienceId: e.id }}
              title={e.title}
              mark={reviewMark(e)}
              sub={join([e.organization, e.role])}
              when={periodLabel(e)}
            />
          ))}
          {done.map((c) => (
            <Entry
              key={c.id}
              onClick={() => setOpen(c)}
              title={c.name}
              mark={c.status === "extracted" ? "확인 전" : null}
              sub={join([c.issuer, scoreText(c)])}
              when={credWhen(c)}
            />
          ))}
        </ul>
      ) : (
        <Empty add={{ label: "추가", onClick: () => ingest.openExperience("education") }} />
      )}
      <CredentialSheet open={open !== null} credential={open} onClose={() => setOpen(null)} />
    </ResumeSection>
  );
}

// ---------------------------------------------------------------- 학력

export function EducationSection() {
  const q = useQuery({ queryKey: ["education"], queryFn: api.education });
  const [open, setOpen] = useState<{ item: Education | null } | null>(null);
  const list = q.data ?? [];
  return (
    <ResumeSection
      id="education"
      title="학력"
      actions={[
        ...(list.length ? [{ label: "모두 보기", to: "/brain/education" }] : []),
        { label: "추가", onClick: () => setOpen({ item: null }) },
      ]}
    >
      {q.isPending ? null : list.length ? (
        <ul>
          {list.map((e) => (
            <Entry
              key={e.id}
              onClick={() => setOpen({ item: e })}
              title={[e.school, e.campus].filter(Boolean).join(" ")}
              sub={join([majorText(e), DEGREE_LABEL[e.degree], EDUCATION_STATUS_LABEL[e.status], gpaText(e)])}
              when={periodText(e)}
            />
          ))}
        </ul>
      ) : (
        <Empty add={{ label: "추가", onClick: () => setOpen({ item: null }) }} />
      )}
      <EducationSheet open={open !== null} education={open?.item ?? null} onClose={() => setOpen(null)} />
    </ResumeSection>
  );
}

// ---------------------------------------------------------------- 자격증·어학·수상

/** 이력서 칸은 달까지만: "2019.06", "2022.09 – 2022.11" */
function credWhen(c: Pick<Credential, "acquired_on" | "started_on" | "ended_on">): string | null {
  if (c.acquired_on) return monthText(c.acquired_on);
  if (c.started_on) return `${monthText(c.started_on)} –${c.ended_on ? ` ${monthText(c.ended_on)}` : ""}`;
  return null;
}

const CRED_KINDS = new Set(["license", "language", "award", "other"]);

export function CredentialSection({ credentials }: { credentials: Credential[] | undefined }) {
  const [open, setOpen] = useState<{ item: Credential | null } | null>(null);
  const list = (credentials ?? []).filter((c) => CRED_KINDS.has(c.kind));
  return (
    <ResumeSection
      id="credentials"
      title="자격증·어학·수상"
      actions={[
        ...(list.length ? [{ label: "모두 보기", to: "/brain/credentials" }] : []),
        { label: "추가", onClick: () => setOpen({ item: null }) },
      ]}
    >
      {credentials === undefined ? null : list.length ? (
        <ul>
          {list.map((c) => (
            <Entry
              key={c.id}
              onClick={() => setOpen({ item: c })}
              title={c.name}
              mark={c.status === "extracted" ? "확인 전" : expiryMark(c)}
              sub={join([CREDENTIAL_KIND_LABEL[c.kind], c.issuer, scoreText(c)])}
              when={credWhen(c)}
            />
          ))}
        </ul>
      ) : (
        <Empty add={{ label: "추가", onClick: () => setOpen({ item: null }) }} />
      )}
      <CredentialSheet open={open !== null} credential={open?.item ?? null} onClose={() => setOpen(null)} />
    </ResumeSection>
  );
}

// ---------------------------------------------------------------- 기술

const SKILLS_SHOWN = 24;

export function SkillSection() {
  const q = useQuery({ queryKey: ["skills"], queryFn: api.skills });
  // 자격증 이름에서만 나온 것(경험·사실 없음)은 자격증 칸에 이미 있다
  const list = [...(q.data ?? [])]
    .filter((s) => s.experiences.length > 0 || s.claims > 0)
    .sort((a, b) => b.count - a.count || a.name.localeCompare(b.name));
  const shown = list.slice(0, SKILLS_SHOWN);
  return (
    <ResumeSection
      id="skills"
      title="기술"
      meta={list.length ? `${list.length}개` : null}
      actions={list.length ? [{ label: "모두 보기", to: "/brain/skills" }] : []}
    >
      {q.isPending ? null : list.length ? (
        <p className="py-1 text-sm leading-7">
          {shown.map((s, i) => (
            // 가운뎃점은 앞 낱말에 붙여 줄 머리에 오지 않게 한다. 줄은 낱말 사이(빈칸)에서만 바뀐다
            <Fragment key={s.key}>
              {i ? " " : null}
              <span className="whitespace-nowrap">
                {s.name}
                {i < shown.length - 1 ? (
                  <span className="pl-1.5 text-muted" aria-hidden>
                    ·
                  </span>
                ) : null}
              </span>
            </Fragment>
          ))}
          {list.length > shown.length ? (
            <span className="text-muted"> 외 {list.length - shown.length}개</span>
          ) : null}
        </p>
      ) : (
        <Empty note="비어 있음 · 경험의 기술 태그와 확인한 사실에서 모입니다" />
      )}
    </ResumeSection>
  );
}

// ---------------------------------------------------------------- 포트폴리오

export function PortfolioSection() {
  const q = useQuery({ queryKey: ["portfolio", "items"], queryFn: () => api.portfolioItems() });
  const list = q.data ?? [];
  return (
    <ResumeSection
      id="portfolio"
      title="포트폴리오"
      actions={[{ label: list.length ? "모두 보기" : "추가", to: "/brain/portfolio" }]}
    >
      {q.isPending ? null : list.length ? (
        <ul>
          {list.slice(0, 6).map((x) => (
            <Entry
              key={x.id}
              to="/brain/portfolio/items/$itemId"
              params={{ itemId: x.id }}
              title={x.title}
              sub={join([x.experience_title, x.tags.slice(0, 4).join(", ")])}
              mark={x.lines.broken ? `근거 사라짐 ${x.lines.broken}` : null}
            />
          ))}
        </ul>
      ) : (
        <Empty add={{ label: "추가", to: "/brain/portfolio" }} />
      )}
    </ResumeSection>
  );
}

// ---------------------------------------------------------------- 취업 우대·병역, 희망 근무 조건

function military(d: ProfileDetails): string | null {
  if (!d.military_status) return null;
  const served = d.military_status === "served" || d.military_status === "serving";
  const period = [monthText(d.military_started_on), monthText(d.military_ended_on)].filter(Boolean).join(" – ");
  return join([
    MILITARY_LABEL[d.military_status],
    served ? [d.military_branch, d.military_rank].filter(Boolean).join(" ") : null,
    d.military_status === "exempt" ? d.military_exemption_reason : null,
    served ? period : null,
  ]);
}

function Facts({ rows }: { rows: [string, string | null][] }) {
  const shown = rows.filter(([, v]) => v);
  return (
    <dl className="grid grid-cols-[4.5rem_minmax(0,1fr)] gap-x-4 gap-y-1.5 py-1 text-sm">
      {shown.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-muted">{k}</dt>
          <dd className="break-words tabular-nums">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

export function DetailsSections() {
  const q = useQuery({ queryKey: ["profile", "details"], queryFn: api.profileDetails });
  const d = q.data;
  const support: [string, string | null][] = d
    ? [
        ["병역", military(d)],
        ["보훈", d.veteran === null ? null : d.veteran ? "대상" : "비대상"],
        ["장애", d.disability === null ? null : d.disability ? "있음" : "없음"],
      ]
    : [];
  const wish: [string, string | null][] = d
    ? [
        ["직무", d.desired_roles.join(", ") || null],
        ["고용 형태", d.employment_types.map((x) => EMPLOYMENT_LABEL[x]).join(", ") || null],
        ["근무지", d.desired_locations.join(", ") || null],
        ["연봉", d.desired_salary],
        ["입사 가능", d.available_from],
      ]
    : [];
  const edit = { label: "수정", to: "/brain/profile", search: undefined } as Act;
  const add = { label: "추가", to: "/brain/profile" } as Act;
  return (
    <>
      <ResumeSection
        id="support"
        title="취업 우대·병역"
        meta="AI에 보내지 않음"
        actions={[support.some(([, v]) => v) ? edit : add]}
      >
        {q.isPending ? null : support.some(([, v]) => v) ? <Facts rows={support} /> : <Empty add={add} />}
      </ResumeSection>
      <ResumeSection id="wish" title="희망 근무 조건" actions={[wish.some(([, v]) => v) ? edit : add]}>
        {q.isPending ? null : wish.some(([, v]) => v) ? <Facts rows={wish} /> : <Empty add={add} />}
      </ResumeSection>
    </>
  );
}

// ---------------------------------------------------------------- 자기소개서 (공통 답변)

export function AnswersSection() {
  const q = useQuery({ queryKey: ["answer-assets"], queryFn: api.answerAssets });
  const list = q.data ?? [];
  return (
    <ResumeSection
      id="answers"
      title="자기소개서"
      meta="공통 답변"
      actions={[{ label: list.length ? "모두 보기" : "추가", to: "/brain/answers" }]}
    >
      {q.isPending ? null : list.length ? (
        <ul>
          {list.slice(0, 6).map((a) => (
            <Entry
              key={a.id}
              to="/brain/answers/$answerId"
              params={{ answerId: a.id }}
              title={a.title}
              sub={join([ANSWER_KIND_LABEL[a.kind], `${a.chars.toLocaleString()}자`])}
            />
          ))}
        </ul>
      ) : (
        <Empty add={{ label: "추가", to: "/brain/answers" }} />
      )}
    </ResumeSection>
  );
}
