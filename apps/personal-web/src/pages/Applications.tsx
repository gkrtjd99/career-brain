import type { ApplicationSummary } from "@career-brain/client";
import { cn, Spinner } from "@career-brain/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import type { ReactNode } from "react";
import { ErrorText, PageHeader } from "../components/common";
import { interviewReady } from "../lib/interview";
import { api } from "../lib/api";
import { daysLeft, ddayLabel, formatDateTime } from "../lib/dates";
import { APP_STATUS, STAGE_OUTCOME } from "../lib/labels";

/** 지원 하나의 다음 행동 한 줄 */
export function nextAction(a: ApplicationSummary): string {
  if (a.status === "draft") {
    if (a.unwritten > 0) return `안 쓴 문항 ${a.unwritten}개 쓰기`;
    if (!a.documents) return "문서 추가";
    return "검사 후 제출본 고정";
  }
  if (a.status === "submitted") return a.stage ? "결과 기록" : "전형 단계 기록";
  if (a.status === "passed") return "다음 전형 준비";
  return "회고";
}

function byDeadline<T extends { deadline: string | null }>(a: T, b: T) {
  const x = daysLeft(a.deadline);
  const y = daysLeft(b.deadline);
  return (x ?? 9999) - (y ?? 9999);
}

export function useCreateApplication() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  return useMutation({
    mutationFn: (postingId: string) => api.createApplication(postingId),
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ["applications"] });
      qc.invalidateQueries({ queryKey: ["postings"] });
      void navigate({ to: "/applications/$applicationId", params: { applicationId: res.id } });
    },
  });
}

/** 제출한 지원 중 서류 결과를 기다리는 것은 "제출", 그 뒤 단계로 넘어갔으면 "전형 중" */
export function inProcess(a: ApplicationSummary) {
  if (a.status !== "submitted" || !a.stage) return false;
  return !(a.stage.kind === "document" && a.stage.outcome === "pending");
}

export function ApplicationsPage() {
  const apps = useQuery({ queryKey: ["applications"], queryFn: api.applications });
  const list = apps.data ?? [];
  const draft = list.filter((a) => a.status === "draft").sort(byDeadline);
  const submitted = list.filter((a) => a.status === "submitted" && !inProcess(a));
  const process = list.filter(inProcess);
  const done = list.filter((a) => ["passed", "rejected", "withdrawn"].includes(a.status));
  const loading = apps.isPending;
  const summary = loading
    ? null
    : [`준비 중 ${draft.length}`, `제출 ${submitted.length}`, `전형 중 ${process.length}`, `결과 ${done.length}`].join(
        " · ",
      );

  return (
    <div>
      <PageHeader title="지원" subtitle={summary} />
      {loading ? (
        <div className="mb-6 flex items-center gap-2 text-sm text-muted">
          <Spinner /> 불러오는 중
        </div>
      ) : null}
      <ErrorText error={apps.error} className="mb-4" />

      {/* 넓은 화면: 네 칸 보드. 좁은 화면: 칸별로 묶은 목록 */}
      <div className="grid grid-cols-1 gap-y-10 md:grid-cols-2 md:gap-x-10 xl:grid-cols-4 xl:gap-x-8">
        <Column title="준비 중" count={draft.length}>
          {draft.length ? (
            <Rows items={draft} />
          ) : !loading ? (
            <Quiet>
              준비 중인 지원이 없습니다.{" "}
              <Link to="/postings" className="text-fg underline underline-offset-2">
                공고
              </Link>
              에서 지원서 쓰기를 누르면 여기에 생깁니다.
            </Quiet>
          ) : null}
        </Column>
        <Column title="제출" count={submitted.length}>
          {submitted.length ? <Rows items={submitted} /> : !loading ? <Quiet>제출하고 결과를 기다리는 지원이 없습니다.</Quiet> : null}
        </Column>
        <Column title="전형 중" count={process.length}>
          {process.length ? <Rows items={process} /> : !loading ? <Quiet>서류 다음 전형을 진행 중인 지원이 없습니다.</Quiet> : null}
        </Column>
        <Column title="결과" count={done.length}>
          {done.length ? <Rows items={done} /> : !loading ? <Quiet>기록한 결과가 없습니다.</Quiet> : null}
          {done.length ? (
            <p className="mt-3 text-[13px] text-muted">개인 표본은 작습니다. 결과로 원인을 단정하지 않습니다.</p>
          ) : null}
        </Column>
      </div>
    </div>
  );
}

function Column({ title, count, children }: { title: string; count: number; children: ReactNode }) {
  return (
    <section className="min-w-0">
      <h2 className="mb-2 text-[15px] font-semibold">
        {title} <span className="font-normal text-muted tabular-nums">{count}</span>
      </h2>
      {children}
    </section>
  );
}

function Quiet({ children }: { children: ReactNode }) {
  return <p className="border-t border-line pt-3 text-sm text-muted">{children}</p>;
}

function Rows({ items }: { items: ApplicationSummary[] }) {
  return (
    <ul className="divide-y divide-line border-y border-line">
      {items.map((a) => (
        <AppRow key={a.id} a={a} />
      ))}
    </ul>
  );
}

/** 지금 전형 단계 한 줄: "1차 면접 · 결과 전 · 11월 20일 오후 2:00" */
function stageLine(a: ApplicationSummary): string | null {
  const st = a.stage;
  if (!st) return null;
  return [
    st.name,
    STAGE_OUTCOME[st.outcome]?.label,
    st.outcome === "pending" && st.scheduled_at ? formatDateTime(st.scheduled_at) : null,
  ]
    .filter(Boolean)
    .join(" · ");
}

function AppRow({ a }: { a: ApplicationSummary }) {
  const dday = a.status === "draft" ? ddayLabel(a.deadline) : null;
  const n = daysLeft(a.deadline);
  const soon = n !== null && n >= 0 && n <= 3;
  const status =
    a.status === "draft"
      ? nextAction(a)
      : a.status === "submitted"
        ? (stageLine(a) ?? "제출함 · 전형 단계 기록 전")
        : [APP_STATUS[a.status].label, stageLine(a)].filter(Boolean).join(" · ");
  return (
    <li>
      <Link
        to="/applications/$applicationId"
        params={{ applicationId: a.id }}
        className="-mx-2 flex min-h-14 items-start gap-3 rounded-md px-2 py-3 transition-colors duration-150 hover:bg-subtle/60"
      >
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-medium">{a.company ?? a.title}</div>
          {a.job_title || a.company ? (
            <div className="truncate text-[13px] text-muted">{a.job_title ?? a.title}</div>
          ) : null}
          <div
            className={cn(
              "mt-0.5 truncate text-[13px] tabular-nums",
              a.status === "passed" || (a.status === "submitted" && a.stage?.outcome === "pending")
                ? "text-fg"
                : "text-muted",
            )}
          >
            {status}
          </div>
        </div>
        {dday ? (
          <span className={cn("shrink-0 text-[13px] tabular-nums", soon ? "font-semibold" : "text-muted")}>{dday}</span>
        ) : null}
      </Link>
      {interviewReady(a) ? (
        <Link
          to="/applications/$applicationId/interview"
          params={{ applicationId: a.id }}
          className="-mt-2 mb-1 inline-flex min-h-9 items-center text-[13px] text-muted underline underline-offset-2 hover:text-fg sm:min-h-6"
        >
          면접 준비
        </Link>
      ) : null}
    </li>
  );
}
