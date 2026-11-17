import type { ActivityItem, ApplicationStage, ApplicationSummary, PostingSummary } from "@career-brain/client";
import { Badge, cn, EmptyState, Section, Spinner } from "@career-brain/ui";
import { useQueries, useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { AlertTriangle, ChevronRight } from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";
import { ApprovalRequestsCard } from "../components/ApprovalRequestsCard";
import { ErrorText, List, PageHeader } from "../components/common";
import { DdayBadge } from "../components/DdayBadge";
import { TokenExpiryNotice } from "../components/TokenExpiryNotice";
import { KIND_CHIP, MonthCalendar } from "../components/today/MonthCalendar";
import { AGENDA_KIND, buildAgenda, daysBetween, postingName, type AgendaEvent } from "../lib/agenda";
import { api } from "../lib/api";
import { dateKey, daysLeft, formatDateTime, formatToday } from "../lib/dates";
import { usd } from "../lib/format";
import { ACTIVITY_LABEL, APP_STATUS, STAGE_KIND } from "../lib/labels";

/** 오른쪽 일정 목록이 보여 주는 기간 (날짜를 고르지 않았을 때) */
const AGENDA_DAYS = 14;
/** 최근 저장에 보이는 종류. 가져오기 시도·추가 같은 작업 기록은 빼고 사람이 남긴 것만. */
const SAVED_KINDS = new Set(["claim:confirmed", "posting:saved", "document_revision:revised", "study_attempt:attempted"]);

export function TodayPage() {
  const postings = useQuery({ queryKey: ["postings"], queryFn: api.postings });
  const experiences = useQuery({ queryKey: ["experiences"], queryFn: api.experiences });
  const apps = useQuery({ queryKey: ["applications"], queryFn: api.applications });
  const due = useQuery({ queryKey: ["due-reviews"], queryFn: () => api.dueReviews({ limit: 100 }) });
  const tests = useQuery({
    queryKey: ["upcoming-assessments", 62],
    queryFn: () => api.upcomingAssessments({ days: 62 }),
  });
  const activity = useQuery({ queryKey: ["activity", 30], queryFn: () => api.activity({ limit: 30 }) });
  const usage = useQuery({
    queryKey: ["usage", 30, "status"],
    queryFn: () => api.usage({ days: 30, groupBy: "status" }),
  });

  const appList = apps.data ?? [];
  const submitted = appList.filter((a) => a.status === "submitted");
  // 전형 일정은 제출한 지원에만 있다. 개인 규모라 지원마다 한 번씩 읽는다.
  const stageQueries = useQueries({
    queries: submitted.map((a) => ({ queryKey: ["stages", a.id], queryFn: () => api.stages(a.id) })),
  });
  const stages = stageQueries.flatMap((q) => q.data ?? []);
  const stagesKey = stageQueries.map((q) => q.dataUpdatedAt).join(",");

  const list = postings.data ?? [];
  const today = dateKey(new Date());
  const events = useMemo(
    () => buildAgenda({ postings: list, applications: appList, stages, tests: tests.data ?? [] }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [postings.data, apps.data, tests.data, stagesKey],
  );
  const [selected, setSelected] = useState<string | null>(null);

  const appByPosting = new Map<string, ApplicationSummary>();
  for (const a of appList) if (!appByPosting.has(a.posting_id) || a.status === "draft") appByPosting.set(a.posting_id, a);
  const nextStage = new Map<string, ApplicationStage>();
  for (const s of stages)
    if (s.outcome === "pending" && s.scheduled_at && dateKey(s.scheduled_at) >= today) {
      const cur = nextStage.get(s.application_id);
      if (!cur || s.scheduled_at < (cur.scheduled_at ?? "")) nextStage.set(s.application_id, s);
    }

  // 위 숫자 4개
  const weekDeadlines = events.filter((e) => e.kind === "deadline" && inRange(today, e.date, 7));
  const drafts = appList.filter((a) => a.status === "draft");
  const unwritten = drafts.reduce((n, a) => n + a.unwritten, 0);
  const reviewExperiences = (experiences.data ?? []).filter((e) => e.needs_review > 0);
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
  const needsReview = reviewExperiences.reduce((n, e) => n + e.needs_review, 0);
  const todo = needsReview + postingDrafts.length + changed.length + failed.length + (due.data?.length ?? 0);

  // 진행 중 지원: 준비 중(마감 가까운 순) → 제출함(다음 전형 가까운 순)
  const active = [
    ...drafts.sort((a, b) => (a.deadline ?? "~").localeCompare(b.deadline ?? "~")),
    ...submitted.sort((a, b) =>
      (nextStage.get(a.id)?.scheduled_at ?? "~").localeCompare(nextStage.get(b.id)?.scheduled_at ?? "~"),
    ),
  ];
  const notApplied = list
    .filter((p) => p.status === "saved" && !appByPosting.has(p.id))
    .filter((p) => (daysLeft(p.deadline) ?? 0) >= 0)
    .sort((a, b) => (a.deadline ?? "~").localeCompare(b.deadline ?? "~"));

  const agenda = selected
    ? events.filter((e) => e.date === selected)
    : events.filter((e) => inRange(today, e.date, AGENDA_DAYS));

  const recent = dedupe((activity.data ?? []).filter((it) => SAVED_KINDS.has(`${it.kind}:${it.action}`))).slice(0, 5);

  // 예산·오류는 평소엔 숨긴다: 예산 80% 이상이거나 거부·오류가 있을 때만 한 줄
  const u = usage.data;
  const budget = u?.monthly_budget_usd ? Number(u.monthly_budget_usd) : null;
  const spent = u ? Number(u.month_spent_usd) : 0;
  const nearBudget = budget !== null && budget > 0 && spent / budget >= 0.8;
  const troubled = u ? u.total.errors + u.total.denied > 0 : false;

  const loading = postings.isPending || experiences.isPending || apps.isPending;

  return (
    <div>
      <PageHeader title="오늘" subtitle={formatToday()} />
      {loading ? (
        <div className="mb-4 flex items-center gap-2 text-muted">
          <Spinner /> 불러오는 중
        </div>
      ) : null}
      <ErrorText error={postings.error ?? experiences.error ?? apps.error} className="mb-4" />

      <div className="space-y-5 sm:space-y-8">
        <div className="space-y-3 empty:hidden">
          <TokenExpiryNotice />
          <ApprovalRequestsCard />
        </div>

        <div className="grid grid-cols-4 gap-2 sm:gap-3">
          <Stat
            to="/postings"
            label="이번 주 마감"
            value={weekDeadlines.length}
            hint={weekDeadlines[0] ? `가장 가까운 곳 ${weekDeadlines[0].title}` : "7일 안 마감 없음"}
          />
          <Stat
            to="/applications"
            label="쓰는 중"
            value={drafts.length}
            hint={drafts.length ? `안 쓴 문항 ${unwritten}개` : "준비 중인 지원 없음"}
          />
          <Stat
            to="/applications"
            label="전형 진행"
            value={submitted.length}
            hint={nextHint(submitted, nextStage)}
          />
          <Stat
            to="/brain"
            label="확인할 것"
            value={todo}
            hint={todo ? "아래 목록" : "다 확인함"}
          />
        </div>

        <div className="grid grid-cols-1 gap-5 lg:grid-cols-3 lg:gap-6">
          <div className="lg:col-span-2">
            <MonthCalendar events={events} today={today} selected={selected} onSelect={setSelected} />
          </div>
          <Section
            title={selected ? dayTitle(selected, today) : `앞으로 ${AGENDA_DAYS}일`}
            action={
              selected ? (
                <button type="button" onClick={() => setSelected(null)} className="text-[13px] text-muted hover:text-fg">
                  {AGENDA_DAYS}일 보기
                </button>
              ) : null
            }
          >
            {agenda.length ? (
              <AgendaList events={agenda} today={today} grouped={!selected} />
            ) : (
              <EmptyState>{selected ? "이날 일정이 없습니다." : "가까운 일정이 없습니다."}</EmptyState>
            )}
          </Section>
        </div>

        <div className="grid grid-cols-1 gap-5 lg:grid-cols-3 lg:gap-6">
          <Section
            title="진행 중인 지원"
            className="lg:col-span-2"
            action={<MoreLink to="/applications">지원 현황</MoreLink>}
          >
            {active.length ? (
              <List>
                {active.map((a) => (
                  <ApplicationRow key={a.id} a={a} next={nextStage.get(a.id)} />
                ))}
              </List>
            ) : !apps.isPending ? (
              <EmptyState>
                진행 중인 지원이 없습니다. 공고 화면에서 "지원 만들기"로 시작합니다.
              </EmptyState>
            ) : null}
          </Section>

          <Section title="확인할 것">
            {todo || fetching.length ? (
              <List>
                {needsReview ? (
                  <Row to="/brain" title="추출한 경험 확인" meta={reviewExperiences.map((e) => e.title).join(", ")} count={needsReview} />
                ) : null}
                {postingDrafts.length ? (
                  <Row to="/postings" title="공고 초안 저장" meta={postingDrafts.map(company).join(", ")} count={postingDrafts.length} />
                ) : null}
                {changed.length ? (
                  <Row to="/postings" title="공고 내용 바뀜" meta={changed.map(company).join(", ")} count={changed.length} />
                ) : null}
                {failed.length ? (
                  <Row to="/postings" title="공고 직접 확인" meta="가져오기 실패·목록·비용 확인 (마감 아님)" count={failed.length} />
                ) : null}
                {due.data?.length ? (
                  <Row to="/study" title="오늘 복습" meta="틀린 문제 다시 풀기" count={due.data.length} />
                ) : null}
                {fetching.length ? (
                  <Row to="/postings" title="공고 가져오는 중" meta={fetching.map(company).join(", ")} right={<Spinner />} />
                ) : null}
              </List>
            ) : !loading ? (
              <EmptyState>확인할 것이 없습니다.</EmptyState>
            ) : null}
          </Section>
        </div>

        <div className="grid grid-cols-1 gap-5 lg:grid-cols-3 lg:gap-6">
          <Section
            title="저장한 공고 · 아직 지원 전"
            className="lg:col-span-2"
            action={<MoreLink to="/postings">공고 전체</MoreLink>}
          >
            {notApplied.length ? (
              <List>
                {notApplied.slice(0, 6).map((p, i) => (
                  <Row
                    className={i >= 4 ? "hidden sm:block" : undefined}
                    key={p.id}
                    to="/postings/$postingId"
                    params={{ postingId: p.id }}
                    title={p.company ?? postingName(p)}
                    meta={p.company ? postingName(p) : undefined}
                    right={p.deadline ? <DdayBadge deadline={p.deadline} /> : <Badge tone="none">마감 모름</Badge>}
                  />
                ))}
              </List>
            ) : !loading ? (
              <EmptyState>
                지원 전인 저장 공고가 없습니다.{" "}
                <Link to="/postings" className="underline underline-offset-2 hover:text-fg">
                  공고 저장하기
                </Link>
              </EmptyState>
            ) : null}
          </Section>

          <Section title="최근 저장">
            {recent.length ? (
              <ul className="space-y-1">
                {recent.map((it, i) => (
                  <RecentRow key={`${it.kind}-${it.action}-${it.id}`} it={it} className={i >= 3 ? "hidden sm:block" : undefined} />
                ))}
              </ul>
            ) : !activity.isPending ? (
              <EmptyState>아직 저장한 것이 없습니다.</EmptyState>
            ) : null}
            <ErrorText error={activity.error} />
          </Section>
        </div>

        {nearBudget || troubled ? (
          <Link
            to="/usage"
            className="flex items-start gap-2 rounded-lg border border-line px-3 py-2.5 text-[13px] text-muted hover:text-fg"
          >
            <AlertTriangle size={16} className="mt-0.5 shrink-0" />
            <span>
              {nearBudget ? `이번 달 모델 비용 ${usd(spent, 2)} / 예산 ${usd(budget, 2)}. ` : null}
              {troubled ? `최근 30일 모델 호출 중 오류·거부 ${u!.total.errors + u!.total.denied}건. ` : null}
              사용량 보기
            </span>
          </Link>
        ) : null}
      </div>
    </div>
  );
}

function inRange(today: string, day: string, days: number) {
  const n = daysBetween(today, day);
  return n >= 0 && n <= days;
}

function company(p: PostingSummary) {
  return p.company ?? postingName(p);
}

function dayTitle(day: string, today: string) {
  const n = daysBetween(today, day);
  const label = `${Number(day.slice(5, 7))}월 ${Number(day.slice(8))}일`;
  return n === 0 ? `${label} · 오늘` : n === 1 ? `${label} · 내일` : label;
}

function shortDate(iso: string | null, day: string) {
  const base = `${Number(day.slice(5, 7))}/${Number(day.slice(8))}`;
  if (!iso) return base;
  const t = formatDateTime(iso).match(/(오[전후]\s*\d{1,2}:\d{2})/);
  return t ? `${base} ${t[1]}` : base;
}

function nextHint(submitted: ApplicationSummary[], next: Map<string, ApplicationStage>) {
  if (!submitted.length) return "제출한 지원 없음";
  let best: { a: ApplicationSummary; s: ApplicationStage } | null = null;
  for (const a of submitted) {
    const s = next.get(a.id);
    if (s && (!best || (s.scheduled_at ?? "") < (best.s.scheduled_at ?? ""))) best = { a, s };
  }
  if (!best) return "잡힌 전형 일정 없음";
  return `다음 ${STAGE_KIND[best.s.kind]} ${shortDate(best.s.scheduled_at, dateKey(best.s.scheduled_at!))}`;
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

function Stat({ to, label, value, hint }: { to: string; label: string; value: number; hint: string }) {
  return (
    <Link
      to={to}
      title={hint}
      className="rounded-lg border border-line bg-surface px-2 py-2 hover:border-fg/40 sm:px-3 sm:py-3"
    >
      <div className="truncate text-[11px] text-muted sm:text-xs">{label}</div>
      <div className="text-lg leading-6 font-semibold tabular-nums sm:mt-0.5 sm:text-2xl sm:leading-8">{value}</div>
      <div className="hidden truncate text-xs text-muted sm:block">{hint}</div>
    </Link>
  );
}

function MoreLink({ to, children }: { to: string; children: ReactNode }) {
  return (
    <Link to={to} className="text-[13px] text-muted hover:text-fg">
      {children}
    </Link>
  );
}

function Row({
  to,
  params,
  search,
  title,
  meta,
  right,
  count,
  className,
}: {
  className?: string;
  to: string;
  params?: Record<string, string>;
  search?: Record<string, string>;
  title: ReactNode;
  meta?: ReactNode;
  right?: ReactNode;
  count?: number;
}) {
  return (
    <li className={className}>
      <Link to={to} params={params} search={search} className="flex min-h-11 items-center gap-3 px-3 py-2 hover:bg-subtle/60 sm:min-h-12 sm:py-2.5">
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-medium">{title}</div>
          {meta ? <div className="truncate text-xs text-muted">{meta}</div> : null}
        </div>
        {count !== undefined ? <span className="text-sm font-semibold tabular-nums">{count}</span> : null}
        {right}
        <ChevronRight size={16} className="shrink-0 text-muted" />
      </Link>
    </li>
  );
}

function ApplicationRow({ a, next }: { a: ApplicationSummary; next: ApplicationStage | undefined }) {
  const written = a.questions - a.unwritten;
  const pct = a.questions ? Math.round((written / a.questions) * 100) : 0;
  return (
    <li>
      <Link
        to="/applications/$applicationId"
        params={{ applicationId: a.id }}
        className="flex min-h-12 items-center gap-2 px-3 py-2 hover:bg-subtle/60 sm:min-h-14 sm:gap-3 sm:py-2.5"
      >
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="truncate text-sm font-medium">{a.company ?? a.title}</span>
            <Badge tone={APP_STATUS[a.status].tone}>{APP_STATUS[a.status].label}</Badge>
          </div>
          <div className="truncate text-xs text-muted">{a.job_title ?? a.title}</div>
        </div>
        {a.status === "draft" ? (
          <div className="hidden w-32 shrink-0 sm:block" title={`문항 ${written}/${a.questions}`}>
            <div className="mb-1 text-right text-xs text-muted tabular-nums">
              {a.questions ? `문항 ${written}/${a.questions}` : "문항 없음"}
            </div>
            <div className="h-1 overflow-hidden rounded-full bg-subtle">
              <div className="h-full rounded-full bg-fg" style={{ width: `${pct}%` }} />
            </div>
          </div>
        ) : null}
        {a.status === "draft" && a.questions ? (
          <span className="shrink-0 text-xs text-muted tabular-nums sm:hidden">
            {written}/{a.questions}
          </span>
        ) : null}
        <div className="shrink-0 text-right sm:w-24">
          {a.status === "draft" ? (
            <DdayBadge deadline={a.deadline} />
          ) : next?.scheduled_at ? (
            <span className="text-xs">
              {STAGE_KIND[next.kind]} {shortDate(next.scheduled_at, dateKey(next.scheduled_at))}
            </span>
          ) : (
            <span className="text-xs text-muted">결과 대기</span>
          )}
        </div>
        <ChevronRight size={16} className="shrink-0 text-muted" />
      </Link>
    </li>
  );
}

function AgendaList({ events, today, grouped }: { events: AgendaEvent[]; today: string; grouped: boolean }) {
  const days = [...new Set(events.map((e) => e.date))];
  return (
    <div className="space-y-3">
      {days.map((day) => (
        <div key={day}>
          {grouped ? (
            <div className="mb-1 text-xs font-medium text-muted">
              {dayTitle(day, today)}
              {daysBetween(today, day) > 1 ? ` · D-${daysBetween(today, day)}` : ""}
            </div>
          ) : null}
          <List>
            {events
              .filter((e) => e.date === day)
              .map((e) => (
                <li key={e.key}>
                  <Link
                    to={e.to}
                    params={e.params}
                    search={e.search}
                    className="flex min-h-10 items-center gap-2.5 px-3 py-1.5 hover:bg-subtle/60 sm:min-h-11 sm:py-2"
                  >
                    <span className={cn("shrink-0 rounded px-1.5 py-px text-[11px] leading-4", KIND_CHIP[e.kind])}>
                      {AGENDA_KIND[e.kind]}
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm font-medium">{e.title}</div>
                      {e.sub || e.at ? (
                        <div className="truncate text-xs text-muted">
                          {[e.sub, e.at ? shortDate(e.at, e.date).split(" ").slice(1).join(" ") : null]
                            .filter(Boolean)
                            .join(" · ")}
                        </div>
                      ) : null}
                    </div>
                  </Link>
                </li>
              ))}
          </List>
        </div>
      ))}
    </div>
  );
}

function activityLink(it: ActivityItem): { to: string; params?: Record<string, string> } {
  const r = it.refs;
  if (r.document_id && r.application_id)
    return {
      to: "/applications/$applicationId/documents/$documentId",
      params: { applicationId: r.application_id, documentId: r.document_id },
    };
  if (r.document_id) return { to: "/documents/$documentId", params: { documentId: r.document_id } };
  if (r.experience_id) return { to: "/brain/$experienceId", params: { experienceId: r.experience_id } };
  if (r.posting_id) return { to: "/postings/$postingId", params: { postingId: r.posting_id } };
  if (r.study_item_id) return { to: "/study/items/$itemId", params: { itemId: r.study_item_id } };
  return { to: "/study" };
}

function RecentRow({ it, className }: { it: ActivityItem; className?: string }) {
  const link = activityLink(it);
  const label = ACTIVITY_LABEL[`${it.kind}:${it.action}`] ?? it.kind;
  return (
    <li className={className}>
      <Link to={link.to} params={link.params} className="block rounded px-2 py-1 hover:bg-subtle/60 sm:py-1.5">
        <div className="truncate text-sm">{it.title}</div>
        <div className="truncate text-xs text-muted">
          {label} · {formatDateTime(it.at)}
        </div>
      </Link>
    </li>
  );
}
