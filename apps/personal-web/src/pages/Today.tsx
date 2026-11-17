import type { ActivityItem, ApplicationStage, ApplicationSummary, PostingSummary } from "@career-brain/client";
import { cn, Spinner } from "@career-brain/ui";
import { useQueries, useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useMemo, useState, type ReactNode } from "react";
import { ApprovalRequestsCard } from "../components/ApprovalRequestsCard";
import { ErrorText, PageHeader } from "../components/common";
import { TokenExpiryNotice } from "../components/TokenExpiryNotice";
import { KindMark, MiniMonth, WeekStrip } from "../components/today/MonthCalendar";
import { buildAgenda, daysBetween, postingName, type AgendaEvent } from "../lib/agenda";
import { api } from "../lib/api";
import { dateKey, daysLeft, formatDateTime, formatToday } from "../lib/dates";
import { usd } from "../lib/format";
import { ACTIVITY_LABEL, STAGE_KIND } from "../lib/labels";

/** 일정 목록이 보여 주는 기간 (날짜를 고르지 않았을 때) */
const AGENDA_DAYS = 14;
/** 최근 저장에 보이는 종류: 사람이 남긴 것만 (가져오기 시도·자동 추가는 뺌) */
const SAVED_KINDS = new Set([
  "claim:confirmed",
  "posting:saved",
  "document_revision:revised",
  "study_attempt:attempted",
]);
const WEEKDAY = ["일", "월", "화", "수", "목", "금", "토"];
const DATE_ONLY = /^\d{4}-\d{2}-\d{2}$/;

export function TodayPage() {
  const postings = useQuery({ queryKey: ["postings"], queryFn: api.postings });
  const experiences = useQuery({
    queryKey: ["experiences"],
    queryFn: () => api.experiences(),
  });
  const apps = useQuery({
    queryKey: ["applications"],
    queryFn: api.applications,
  });
  const due = useQuery({
    queryKey: ["due-reviews"],
    queryFn: () => api.dueReviews({ limit: 100 }),
  });
  const tests = useQuery({
    queryKey: ["upcoming-assessments", 62],
    queryFn: () => api.upcomingAssessments({ days: 62 }),
  });
  const activity = useQuery({
    queryKey: ["activity", 30],
    queryFn: () => api.activity({ limit: 30 }),
  });
  const usage = useQuery({
    queryKey: ["usage", 30, "status"],
    queryFn: () => api.usage({ days: 30, groupBy: "status" }),
  });

  const appList = apps.data ?? [];
  const submitted = appList.filter((a) => a.status === "submitted");
  // 전형 일정은 제출한 지원에만 있다. 개인 규모라 지원마다 한 번씩 읽는다.
  const stageQueries = useQueries({
    queries: submitted.map((a) => ({
      queryKey: ["stages", a.id],
      queryFn: () => api.stages(a.id),
    })),
  });
  const stages = stageQueries.flatMap((q) => q.data ?? []);
  const stagesKey = stageQueries.map((q) => q.dataUpdatedAt).join(",");

  const list = postings.data ?? [];
  const today = dateKey(new Date());
  const events = useMemo(
    () =>
      buildAgenda({
        postings: list,
        applications: appList,
        stages,
        tests: tests.data ?? [],
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [postings.data, apps.data, tests.data, stagesKey],
  );
  const [selected, setSelected] = useState<string | null>(null);

  const appById = new Map(appList.map((a) => [a.id, a]));
  const appByPosting = new Map<string, ApplicationSummary>();
  for (const a of appList)
    if (!appByPosting.has(a.posting_id) || a.status === "draft") appByPosting.set(a.posting_id, a);
  const nextStage = new Map<string, ApplicationStage>();
  for (const s of stages)
    if (s.outcome === "pending" && s.scheduled_at && dateKey(s.scheduled_at) >= today) {
      const cur = nextStage.get(s.application_id);
      if (!cur || s.scheduled_at < (cur.scheduled_at ?? "")) nextStage.set(s.application_id, s);
    }

  const drafts = appList
    .filter((a) => a.status === "draft")
    .sort((a, b) => (a.deadline ?? "~").localeCompare(b.deadline ?? "~"));
  const active = [
    ...drafts,
    ...[...submitted].sort((a, b) =>
      (nextStage.get(a.id)?.scheduled_at ?? "~").localeCompare(nextStage.get(b.id)?.scheduled_at ?? "~"),
    ),
  ];
  const notApplied = list
    .filter((p) => p.status === "saved" && !appByPosting.has(p.id) && (daysLeft(p.deadline) ?? 0) >= 0)
    .sort((a, b) => (a.deadline ?? "~").localeCompare(b.deadline ?? "~"));

  const reviewExperiences = (experiences.data ?? []).filter((e) => e.needs_review > 0);
  const needsReview = reviewExperiences.reduce((n, e) => n + e.needs_review, 0);
  const postingDrafts = list.filter((p) => p.status === "draft" && p.last_fetch_status === "done");
  const changed = list.filter((p) => p.status === "saved" && p.pending_changes.length > 0);
  const failed = list.filter(
    (p) =>
      p.status !== "archived" &&
      (p.last_fetch_status === "failed" ||
        p.last_fetch_status === "listing" ||
        p.last_fetch_status === "needs_confirmation"),
  );
  const fetching = list.filter((p) => p.last_fetch_status === "pending");
  const todoRows: { to: string; label: string; count: number }[] = [
    { to: "/brain", label: "추출한 경험 확인", count: needsReview },
    { to: "/postings", label: "공고 초안 저장", count: postingDrafts.length },
    { to: "/postings", label: "바뀐 공고 확인", count: changed.length },
    { to: "/postings", label: "공고 직접 확인", count: failed.length },
    { to: "/study", label: "오늘 복습", count: due.data?.length ?? 0 },
  ].filter((r) => r.count > 0);
  const todo = todoRows.reduce((n, r) => n + r.count, 0);

  const upcoming = events.filter((e) => inRange(today, e.date, AGENDA_DAYS));
  const agenda = selected ? events.filter((e) => e.date === selected) : upcoming;
  const soonDeadlines = upcoming.filter((e) => e.kind === "deadline").length;

  const recent = dedupe((activity.data ?? []).filter((it) => SAVED_KINDS.has(`${it.kind}:${it.action}`))).slice(0, 3);

  // 예산·오류는 평소엔 숨긴다: 예산 80% 이상이거나 거부·오류가 있을 때만 한 줄
  const u = usage.data;
  const budget = u?.monthly_budget_usd ? Number(u.monthly_budget_usd) : null;
  const spent = u ? Number(u.month_spent_usd) : 0;
  const nearBudget = budget !== null && budget > 0 && spent / budget >= 0.8;
  const troubled = u ? u.total.errors + u.total.denied > 0 : false;

  const loading = postings.isPending || experiences.isPending || apps.isPending;
  const summary = [
    soonDeadlines ? `2주 안 마감 ${soonDeadlines}` : null,
    drafts.length ? `쓰는 중 ${drafts.length}` : null,
    todo ? `확인할 것 ${todo}` : null,
  ].filter(Boolean);

  const todoBlock = (
    <Block title="확인할 것">
      {todoRows.length || fetching.length ? (
        <Rows>
          {todoRows.map((r) => (
            <PlainRow key={r.label} to={r.to} title={r.label} right={<Count n={r.count} />} />
          ))}
          {fetching.length ? <PlainRow to="/postings" title="공고 가져오는 중" right={<Spinner />} /> : null}
        </Rows>
      ) : !loading ? (
        <Quiet>확인할 것이 없습니다.</Quiet>
      ) : null}
    </Block>
  );

  return (
    <div className="mx-auto max-w-5xl">
      <PageHeader title="오늘" subtitle={[formatToday(), ...summary].join(" · ")} />
      {loading ? (
        <div className="mb-6 flex items-center gap-2 text-sm text-muted">
          <Spinner /> 불러오는 중
        </div>
      ) : null}
      <ErrorText error={postings.error ?? experiences.error ?? apps.error} className="mb-4" />
      <div className="mb-6 space-y-3 empty:hidden">
        <TokenExpiryNotice />
        <ApprovalRequestsCard />
      </div>

      <div className="lg:grid lg:grid-cols-[minmax(0,1fr)_16rem] lg:gap-14">
        <div className="space-y-10">
          <div className="lg:hidden">
            <WeekStrip events={events} today={today} selected={selected} onSelect={setSelected} />
          </div>

          <Block
            title={selected ? dayTitle(selected, today) : "다가오는 일정"}
            action={
              selected ? (
                <button
                  type="button"
                  onClick={() => setSelected(null)}
                  className="text-[13px] text-muted hover:text-fg"
                >
                  2주 보기
                </button>
              ) : null
            }
          >
            {agenda.length ? (
              <Agenda events={agenda} today={today} appById={appById} showDate={!selected} />
            ) : (
              <Quiet>
                {selected
                  ? "이날 일정이 없습니다."
                  : "앞으로 2주 동안 마감·전형·시험이 없습니다. 공고를 저장하면 마감이 여기에 나옵니다."}
              </Quiet>
            )}
          </Block>

          <div className="lg:hidden">{todoBlock}</div>

          <Block title="진행 중인 지원" action={<More to="/applications">지원 현황</More>}>
            {active.length ? (
              <Rows>
                {active.map((a) => (
                  <PlainRow
                    key={a.id}
                    to="/applications/$applicationId"
                    params={{ applicationId: a.id }}
                    title={a.company ?? a.title}
                    meta={a.job_title ?? undefined}
                    right={<ApplicationState a={a} next={nextStage.get(a.id)} today={today} />}
                  />
                ))}
              </Rows>
            ) : !apps.isPending ? (
              <Quiet>진행 중인 지원이 없습니다. 공고 화면의 "지원 만들기"로 시작합니다.</Quiet>
            ) : null}
          </Block>

          {notApplied.length ? (
            <Block title="아직 지원 전인 공고" action={<More to="/postings">공고</More>}>
              <Rows>
                {notApplied.slice(0, 5).map((p) => (
                  <PlainRow
                    key={p.id}
                    to="/postings/$postingId"
                    params={{ postingId: p.id }}
                    title={p.company ?? postingName(p)}
                    meta={p.company ? postingName(p) : undefined}
                    right={<Dday date={toKey(p.deadline)} today={today} />}
                  />
                ))}
              </Rows>
            </Block>
          ) : null}
        </div>

        <aside className="mt-10 space-y-10 lg:mt-0">
          <div className="hidden lg:block">
            <MiniMonth events={events} today={today} selected={selected} onSelect={setSelected} />
            <Legend />
          </div>
          <div className="hidden lg:block">{todoBlock}</div>
          <Block title="최근 저장">
            {recent.length ? (
              <Rows>
                {recent.map((it) => (
                  <RecentRow key={`${it.kind}-${it.action}-${it.id}`} it={it} />
                ))}
              </Rows>
            ) : !activity.isPending ? (
              <Quiet>아직 저장한 것이 없습니다.</Quiet>
            ) : null}
            <ErrorText error={activity.error} />
          </Block>
        </aside>
      </div>

      {nearBudget || troubled ? (
        <p className="mt-12 text-[13px] text-muted">
          {nearBudget ? `이번 달 모델 비용 ${usd(spent, 2)} / 예산 ${usd(budget, 2)}. ` : null}
          {troubled ? `최근 30일 모델 호출 중 오류·거부 ${u!.total.errors + u!.total.denied}건. ` : null}
          <Link to="/manage" search={{ tab: "usage" }} className="underline underline-offset-2 hover:text-fg">
            사용량 보기
          </Link>
        </p>
      ) : null}
    </div>
  );
}

/* ---------- 조각 ---------- */

function inRange(today: string, day: string, days: number) {
  const n = daysBetween(today, day);
  return n >= 0 && n <= days;
}

function toKey(deadline: string | null) {
  if (!deadline || deadline === "always") return null;
  return DATE_ONLY.test(deadline) ? deadline : dateKey(deadline);
}

function weekday(day: string) {
  return WEEKDAY[
    new Date(Date.UTC(Number(day.slice(0, 4)), Number(day.slice(5, 7)) - 1, Number(day.slice(8, 10)))).getUTCDay()
  ];
}

function dayTitle(day: string, today: string) {
  const n = daysBetween(today, day);
  const label = `${Number(day.slice(5, 7))}월 ${Number(day.slice(8))}일 ${weekday(day)}요일`;
  return n === 0 ? `오늘 · ${label}` : n === 1 ? `내일 · ${label}` : label;
}

function timeOf(iso: string | null) {
  if (!iso) return null;
  return formatDateTime(iso).match(/오[전후]\s*\d{1,2}:\d{2}/)?.[0] ?? null;
}

function dedupe(items: ActivityItem[]) {
  const seen = new Set<string>();
  return items.filter((it) => {
    const k = `${it.kind}:${it.title}`;
    if (seen.has(k)) return false;
    seen.add(k);
    return true;
  });
}

function Block({ title, action, children }: { title: ReactNode; action?: ReactNode; children: ReactNode }) {
  return (
    <section>
      <div className="mb-2 flex items-baseline justify-between gap-3">
        <h2 className="text-[15px] font-semibold">{title}</h2>
        {action}
      </div>
      {children}
    </section>
  );
}

function Rows({ children }: { children: ReactNode }) {
  return <ul className="divide-y divide-line border-t border-line">{children}</ul>;
}

function Quiet({ children }: { children: ReactNode }) {
  return <p className="border-t border-line pt-3 text-sm text-muted">{children}</p>;
}

function More({ to, children }: { to: string; children: ReactNode }) {
  return (
    <Link to={to} className="text-[13px] text-muted hover:text-fg">
      {children}
    </Link>
  );
}

function Count({ n }: { n: number }) {
  return <span className="text-sm font-semibold tabular-nums">{n}</span>;
}

function PlainRow({
  to,
  params,
  search,
  title,
  meta,
  right,
}: {
  to: string;
  params?: Record<string, string>;
  search?: Record<string, string>;
  title: ReactNode;
  meta?: ReactNode;
  right?: ReactNode;
}) {
  return (
    <li>
      <Link
        to={to}
        params={params}
        search={search}
        className="-mx-2 flex items-center gap-4 rounded-md px-2 py-3 transition-colors duration-150 hover:bg-subtle/60"
      >
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-medium">{title}</div>
          {meta ? <div className="truncate text-[13px] text-muted">{meta}</div> : null}
        </div>
        {right ? <div className="shrink-0 text-right">{right}</div> : null}
      </Link>
    </li>
  );
}

/** 마감까지 남은 날. 3일 안이면 진하게. */
function Dday({ date, today }: { date: string | null; today: string }) {
  if (!date) return <span className="text-[13px] text-muted">마감 모름</span>;
  const n = daysBetween(today, date);
  const label = n === 0 ? "오늘 마감" : n === 1 ? "내일 마감" : n > 0 ? `D-${n}` : "마감 지남";
  return (
    <span className={cn("text-[13px] tabular-nums", n >= 0 && n <= 3 ? "font-semibold" : "text-muted")}>{label}</span>
  );
}

function ApplicationState({
  a,
  next,
  today,
}: {
  a: ApplicationSummary;
  next: ApplicationStage | undefined;
  today: string;
}) {
  if (a.status === "draft") {
    return (
      <div className="leading-tight">
        <div className="text-[13px] tabular-nums">
          {a.questions ? (a.unwritten ? `문항 ${a.unwritten}개 남음` : "문항 다 씀") : "준비 중"}
        </div>
        <Dday date={toKey(a.deadline)} today={today} />
      </div>
    );
  }
  if (next?.scheduled_at) {
    const day = dateKey(next.scheduled_at);
    return (
      <div className="leading-tight">
        <div className="text-[13px]">{STAGE_KIND[next.kind]}</div>
        <div className="text-[13px] text-muted tabular-nums">
          {Number(day.slice(5, 7))}/{Number(day.slice(8))} {weekday(day)}
        </div>
      </div>
    );
  }
  return <span className="text-[13px] text-muted">결과 대기</span>;
}

function agendaWhat(e: AgendaEvent, app: ApplicationSummary | undefined) {
  if (e.kind === "stage") return e.sub;
  if (e.kind === "test") return ["시험", ...e.sub.split(" · ").filter((part) => part && part !== e.title)].join(" · ");
  if (!app) return "마감 · 지원 전";
  if (app.status !== "draft") return "마감 · 제출함";
  if (!app.questions) return "마감";
  return app.unwritten ? `마감 · 문항 ${app.unwritten}개 남음` : "마감 · 문항 다 씀";
}

function Agenda({
  events,
  today,
  appById,
  showDate,
}: {
  events: AgendaEvent[];
  today: string;
  appById: Map<string, ApplicationSummary>;
  showDate: boolean;
}) {
  return (
    <ul className="border-t border-line">
      {events.map((e, i) => {
        const firstOfDay = i === 0 || events[i - 1]!.date !== e.date;
        const n = daysBetween(today, e.date);
        const app = e.params?.applicationId ? appById.get(e.params.applicationId) : undefined;
        const time = timeOf(e.at);
        return (
          <li key={e.key} className={cn(firstOfDay && i > 0 && "border-t border-line")}>
            <Link
              to={e.to}
              params={e.params}
              search={e.search}
              className="-mx-2 grid grid-cols-[2.5rem_minmax(0,1fr)_auto] items-center gap-3 rounded-md px-2 py-3 transition-colors duration-150 hover:bg-subtle/60"
            >
              <div className={cn("leading-tight", !(showDate && firstOfDay) && "invisible")}>
                <div className="text-base font-semibold tabular-nums">{Number(e.date.slice(8))}</div>
                <div className="text-xs text-muted">{n === 0 ? "오늘" : n === 1 ? "내일" : weekday(e.date)}</div>
              </div>
              <div className="min-w-0">
                <div className="truncate text-sm font-medium">{e.title}</div>
                <div className="flex min-w-0 items-center gap-1.5 text-[13px] text-muted">
                  <KindMark kind={e.kind} />
                  <span className="truncate">{[agendaWhat(e, app), time].filter(Boolean).join(" · ")}</span>
                </div>
              </div>
              <span className={cn("text-[13px] tabular-nums", n <= 3 ? "font-semibold" : "text-muted")}>
                {n === 0 ? "오늘" : `D-${n}`}
              </span>
            </Link>
          </li>
        );
      })}
    </ul>
  );
}

function Legend() {
  return (
    <div className="mt-3 flex gap-4 text-xs text-muted">
      <span className="inline-flex items-center gap-1.5">
        <KindMark kind="deadline" /> 마감
      </span>
      <span className="inline-flex items-center gap-1.5">
        <KindMark kind="stage" /> 전형
      </span>
      <span className="inline-flex items-center gap-1.5">
        <KindMark kind="test" /> 시험
      </span>
    </div>
  );
}

function activityLink(it: ActivityItem): {
  to: string;
  params?: Record<string, string>;
} {
  const r = it.refs;
  if (r.document_id && r.application_id)
    return {
      to: "/applications/$applicationId/documents/$documentId",
      params: { applicationId: r.application_id, documentId: r.document_id },
    };
  if (r.document_id)
    return {
      to: "/documents/$documentId",
      params: { documentId: r.document_id },
    };
  if (r.experience_id)
    return {
      to: "/brain/$experienceId",
      params: { experienceId: r.experience_id },
    };
  if (r.posting_id) return { to: "/postings/$postingId", params: { postingId: r.posting_id } };
  if (r.study_item_id) return { to: "/study/items/$itemId", params: { itemId: r.study_item_id } };
  return { to: "/study" };
}

function RecentRow({ it }: { it: ActivityItem }) {
  const link = activityLink(it);
  return (
    <PlainRow
      to={link.to}
      params={link.params}
      title={it.title}
      meta={`${ACTIVITY_LABEL[`${it.kind}:${it.action}`] ?? it.kind} · ${formatDateTime(it.at)}`}
    />
  );
}
