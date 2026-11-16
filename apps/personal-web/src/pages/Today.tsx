import type { ActivityItem, ApplicationSummary, PostingSummary } from "@career-brain/client";
import { Badge, EmptyState, Section, Spinner } from "@career-brain/ui";
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { AlertTriangle, ChevronRight } from "lucide-react";
import type { ReactNode } from "react";
import { ApprovalRequestsCard } from "../components/ApprovalRequestsCard";
import { ErrorText, List, PageHeader } from "../components/common";
import { DdayBadge } from "../components/DdayBadge";
import { UpcomingTests } from "../components/study/UpcomingTests";
import { api } from "../lib/api";
import { daysLeft, formatDateTime, formatDeadline, formatToday } from "../lib/dates";
import { hostOf, usd } from "../lib/format";
import { ACTIVITY_LABEL, FETCH_ERROR, FIELD_LABEL, MISTAKE_REASON, POSTING_STATUS } from "../lib/labels";

const SOON_DAYS = 14;

function Row({
  to,
  params,
  title,
  meta,
  right,
}: {
  to: string;
  params?: Record<string, string>;
  title: ReactNode;
  meta?: ReactNode;
  right?: ReactNode;
}) {
  return (
    <li>
      <Link
        to={to}
        params={params}
        className="flex min-h-12 items-center gap-3 px-3 py-2.5 hover:bg-subtle/60"
      >
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-medium">{title}</div>
          {meta ? <div className="truncate text-xs text-muted">{meta}</div> : null}
        </div>
        {right}
        <ChevronRight size={16} className="shrink-0 text-muted" />
      </Link>
    </li>
  );
}

function postingName(p: PostingSummary) {
  return p.title ?? hostOf(p.canonical_url);
}

export function TodayPage() {
  const postings = useQuery({ queryKey: ["postings"], queryFn: api.postings });
  const experiences = useQuery({ queryKey: ["experiences"], queryFn: api.experiences });
  const apps = useQuery({ queryKey: ["applications"], queryFn: api.applications });
  const due = useQuery({ queryKey: ["due-reviews"], queryFn: () => api.dueReviews({ limit: 100 }) });
  const activity = useQuery({ queryKey: ["activity"], queryFn: () => api.activity({ limit: 12 }) });
  const usage = useQuery({
    queryKey: ["usage", 30, "status"],
    queryFn: () => api.usage({ days: 30, groupBy: "status" }),
  });

  const list = postings.data ?? [];
  const saved = list.filter((p) => p.status === "saved");
  const soon = saved
    .map((p) => ({ p, n: daysLeft(p.deadline) }))
    .filter((x): x is { p: PostingSummary; n: number } => x.n !== null && x.n >= 0 && x.n <= SOON_DAYS)
    .sort((a, b) => a.n - b.n);

  const appByPosting = new Map<string, ApplicationSummary>();
  for (const a of apps.data ?? []) if (!appByPosting.has(a.posting_id) || a.status === "draft") appByPosting.set(a.posting_id, a);

  const reviewExperiences = (experiences.data ?? []).filter((e) => e.needs_review > 0);
  const drafts = list.filter((p) => p.status === "draft" && p.last_fetch_status === "done");
  const changed = saved.filter((p) => p.pending_changes.length > 0);
  const failed = list.filter(
    (p) => p.status !== "archived" && (p.last_fetch_status === "failed" || p.last_fetch_status === "listing"),
  );
  const running = list.filter((p) => p.last_fetch_status === "pending");
  const reviewCount =
    reviewExperiences.reduce((n, e) => n + e.needs_review, 0) + drafts.length + changed.length + failed.length;

  // 시스템 항목은 평소엔 조용히: 예산 80% 이상이거나 거부·오류가 있을 때만 보인다.
  const u = usage.data;
  const budget = u?.monthly_budget_usd ? Number(u.monthly_budget_usd) : null;
  const spent = u ? Number(u.month_spent_usd) : 0;
  const nearBudget = budget !== null && budget > 0 && spent / budget >= 0.8;
  const troubled = u ? u.total.errors + u.total.denied > 0 : false;

  const loading = postings.isPending || experiences.isPending;
  const today = formatToday();

  return (
    <div>
      <PageHeader
        title="오늘"
        subtitle={
          <>
            {today}
            {!loading && reviewCount ? ` · 확인할 것 ${reviewCount}개` : ""}
          </>
        }
      />
      {loading ? (
        <div className="flex items-center gap-2 text-muted">
          <Spinner /> 불러오는 중
        </div>
      ) : null}
      <ErrorText error={postings.error ?? experiences.error} className="mb-4" />

      <div className="grid grid-cols-1 gap-8 lg:grid-cols-2">
        <ApprovalRequestsCard className="lg:col-span-2" />
        <Section title="마감 임박 · 안 쓴 문항" className="lg:col-span-2">
          {soon.length ? (
            <List>
              {soon.map(({ p }) => {
                const a = appByPosting.get(p.id);
                return a ? (
                  <Row
                    key={p.id}
                    to="/applications/$applicationId"
                    params={{ applicationId: a.id }}
                    title={postingName(p)}
                    meta={[p.company, formatDeadline(p.deadline)].filter(Boolean).join(" · ")}
                    right={
                      <span className="flex shrink-0 flex-wrap justify-end gap-1">
                        {a.status !== "draft" ? (
                          <Badge tone="ok">제출함</Badge>
                        ) : a.unwritten ? (
                          <Badge tone="warn">안 쓴 문항 {a.unwritten}</Badge>
                        ) : a.questions ? (
                          <Badge tone="ok">문항 다 씀</Badge>
                        ) : null}
                        <DdayBadge deadline={p.deadline} />
                      </span>
                    }
                  />
                ) : (
                  <Row
                    key={p.id}
                    to="/postings/$postingId"
                    params={{ postingId: p.id }}
                    title={postingName(p)}
                    meta={[p.company, formatDeadline(p.deadline), "지원 준비 전"].filter(Boolean).join(" · ")}
                    right={
                      <span className="flex shrink-0 gap-1">
                        <Badge tone="none">지원 없음</Badge>
                        <DdayBadge deadline={p.deadline} />
                      </span>
                    }
                  />
                );
              })}
            </List>
          ) : (
            <EmptyState>
              {SOON_DAYS}일 안에 마감하는 저장 공고가 없습니다.{" "}
              <Link to="/postings" className="text-accent underline-offset-2 hover:underline">
                공고 저장하기
              </Link>
            </EmptyState>
          )}
        </Section>

        <Section title="확인 대기">
          {reviewExperiences.length + drafts.length + changed.length + failed.length + running.length ? (
            <List>
              {reviewExperiences.map((e) => (
                <Row
                  key={e.id}
                  to="/brain/$experienceId"
                  params={{ experienceId: e.id }}
                  title={e.title}
                  meta="추출한 경험 확인"
                  right={<Badge tone="warn">확인 필요 {e.needs_review}</Badge>}
                />
              ))}
              {changed.map((p) => (
                <Row
                  key={p.id}
                  to="/postings/$postingId"
                  params={{ postingId: p.id }}
                  title={postingName(p)}
                  meta={`공고 변경: ${p.pending_changes
                    .map((f) => FIELD_LABEL[f as keyof typeof FIELD_LABEL] ?? f)
                    .join(", ")}`}
                  right={<Badge tone="warn">변경</Badge>}
                />
              ))}
              {drafts.map((p) => (
                <Row
                  key={p.id}
                  to="/postings/$postingId"
                  params={{ postingId: p.id }}
                  title={postingName(p)}
                  meta="추출 결과를 확인하고 저장"
                  right={
                    <span className="flex gap-1">
                      <DdayBadge deadline={p.deadline} />
                      <Badge tone={POSTING_STATUS.draft.tone}>{POSTING_STATUS.draft.label}</Badge>
                    </span>
                  }
                />
              ))}
              {failed.map((p) => (
                <Row
                  key={p.id}
                  to="/postings/$postingId"
                  params={{ postingId: p.id }}
                  title={postingName(p)}
                  meta={
                    p.last_fetch_status === "listing"
                      ? "목록 페이지: 저장할 공고를 고르세요"
                      : `가져오기 실패: ${FETCH_ERROR[p.last_fetch_error ?? ""] ?? p.last_fetch_error ?? "알 수 없음"} (마감 아님)`
                  }
                  right={
                    <Badge tone={p.last_fetch_status === "listing" ? "warn" : "danger"}>
                      {p.last_fetch_status === "listing" ? "목록" : "실패"}
                    </Badge>
                  }
                />
              ))}
              {running.map((p) => (
                <Row
                  key={p.id}
                  to="/postings/$postingId"
                  params={{ postingId: p.id }}
                  title={postingName(p)}
                  meta="가져오는 중"
                  right={<Spinner />}
                />
              ))}
            </List>
          ) : !loading ? (
            <EmptyState>확인할 것이 없습니다.</EmptyState>
          ) : null}
        </Section>

        <Section
          title="오늘 복습"
          action={
            <Link to="/study" className="text-[13px] text-muted hover:text-fg">
              학습
            </Link>
          }
        >
          <UpcomingTests />
          {due.data?.length ? (
            <List>
              {due.data.slice(0, 4).map((m) => (
                <Row
                  key={m.id}
                  to="/study"
                  title={m.question}
                  meta={[m.study_item_title, MISTAKE_REASON[m.reason]].filter(Boolean).join(" · ")}
                />
              ))}
              {due.data.length > 4 ? (
                <Row to="/study" title={`나머지 ${due.data.length - 4}개`} meta="학습 화면에서 복습" />
              ) : null}
            </List>
          ) : !due.isPending ? (
            <EmptyState>오늘 복습할 오답이 없습니다.</EmptyState>
          ) : null}
        </Section>

        <Section
          title="최근 공고"
          action={
            <Link to="/postings" className="text-[13px] text-muted hover:text-fg">
              전체
            </Link>
          }
        >
          {list.length ? (
            <List>
              {list.slice(0, 5).map((p) => (
                <Row
                  key={p.id}
                  to="/postings/$postingId"
                  params={{ postingId: p.id }}
                  title={postingName(p)}
                  meta={[p.company, hostOf(p.canonical_url)].filter(Boolean).join(" · ")}
                  right={<Badge tone={POSTING_STATUS[p.status].tone}>{POSTING_STATUS[p.status].label}</Badge>}
                />
              ))}
            </List>
          ) : !loading ? (
            <EmptyState>아직 저장한 공고가 없습니다.</EmptyState>
          ) : null}
        </Section>

        <Section title="최근 활동" className="lg:col-span-2">
          {activity.data?.length ? (
            <List>
              {activity.data.map((it) => (
                <ActivityRow key={`${it.kind}-${it.action}-${it.id}`} it={it} />
              ))}
            </List>
          ) : !activity.isPending ? (
            <EmptyState>아직 활동이 없습니다.</EmptyState>
          ) : null}
          <ErrorText error={activity.error} />
        </Section>

        {nearBudget || troubled ? (
          <Section title="시스템" className="lg:col-span-2">
            <Link
              to="/usage"
              className="flex items-start gap-2 rounded-lg border border-line bg-warn-soft px-3 py-2.5 text-[13px] text-warn"
            >
              <AlertTriangle size={16} className="mt-0.5 shrink-0" />
              <span>
                {nearBudget
                  ? `이번 달 모델 비용 ${usd(spent, 2)} / 예산 ${usd(budget, 2)}. `
                  : null}
                {troubled ? `최근 30일 모델 호출 중 오류·거부 ${u!.total.errors + u!.total.denied}건. ` : null}
                사용량 보기
              </span>
            </Link>
          </Section>
        ) : null}
      </div>
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
  if (r.experience_id) return { to: "/brain/$experienceId", params: { experienceId: r.experience_id } };
  if (r.posting_id) return { to: "/postings/$postingId", params: { postingId: r.posting_id } };
  if (r.study_item_id) return { to: "/study/items/$itemId", params: { itemId: r.study_item_id } };
  return { to: "/study" };
}

function ActivityRow({ it }: { it: ActivityItem }) {
  const link = activityLink(it);
  const label = ACTIVITY_LABEL[`${it.kind}:${it.action}`] ?? it.kind;
  const detail =
    it.kind === "fetch_run"
      ? it.status === "failed"
        ? `실패: ${FETCH_ERROR[it.detail ?? ""] ?? it.detail ?? "알 수 없음"}`
        : it.status === "pending"
          ? "가져오는 중"
          : null
      : it.detail;
  return (
    <Row
      to={link.to}
      params={link.params}
      title={it.title}
      meta={[label, detail, formatDateTime(it.at)].filter(Boolean).join(" · ")}
    />
  );
}
