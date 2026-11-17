import type { ApplicationSummary, PostingSummary } from "@career-brain/client";
import { Button, cn, Spinner } from "@career-brain/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { Plus } from "lucide-react";
import type { ReactNode } from "react";
import { ErrorText, PageHeader } from "../components/common";
import { api } from "../lib/api";
import { daysLeft, ddayLabel } from "../lib/dates";
import { hostOf } from "../lib/format";
import { APP_STATUS } from "../lib/labels";

/** 지원 하나의 다음 행동 한 줄 */
export function nextAction(a: ApplicationSummary): string {
  if (a.status === "draft") {
    if (a.unwritten > 0) return `안 쓴 문항 ${a.unwritten}개 쓰기`;
    if (!a.documents) return "문서 추가";
    return "검사 후 제출본 고정";
  }
  if (a.status === "submitted") return "결과 기록";
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
      void navigate({ to: "/applications/$applicationId", params: { applicationId: res.id } });
    },
  });
}

export function ApplicationsPage() {
  const apps = useQuery({ queryKey: ["applications"], queryFn: api.applications });
  const postings = useQuery({ queryKey: ["postings"], queryFn: api.postings });
  const create = useCreateApplication();

  const list = apps.data ?? [];
  const withApp = new Set(list.map((a) => a.posting_id));
  const interest = (postings.data ?? []).filter((p) => p.status === "saved" && !withApp.has(p.id)).sort(byDeadline);
  const draft = list.filter((a) => a.status === "draft").sort(byDeadline);
  const submitted = list.filter((a) => a.status === "submitted");
  const done = list.filter((a) => ["passed", "rejected", "withdrawn"].includes(a.status));
  const loading = apps.isPending || postings.isPending;
  const summary = loading
    ? null
    : [`관심 ${interest.length}`, `준비 중 ${draft.length}`, `제출 ${submitted.length}`, `결과 ${done.length}`].join(
        " · ",
      );

  return (
    <div>
      <PageHeader title="지원 현황" subtitle={summary} />
      {loading ? (
        <div className="mb-6 flex items-center gap-2 text-sm text-muted">
          <Spinner /> 불러오는 중
        </div>
      ) : null}
      <ErrorText error={apps.error ?? postings.error ?? create.error} className="mb-4" />

      <div className="grid grid-cols-1 gap-y-10 lg:grid-cols-2 lg:gap-x-14">
        <Block title="관심" count={interest.length}>
          {interest.length ? (
            <Rows>
              {interest.map((p) => (
                <InterestRow key={p.id} p={p} busy={create.isPending} onCreate={() => create.mutate(p.id)} />
              ))}
            </Rows>
          ) : !loading ? (
            <Quiet>
              지원 전인 공고가 없습니다.{" "}
              <Link to="/postings" className="text-fg underline underline-offset-2">
                공고 저장하기
              </Link>
            </Quiet>
          ) : null}
        </Block>
        <Block title="준비 중" count={draft.length}>
          <AppRows
            items={draft}
            empty={!loading ? "준비 중인 지원이 없습니다. 관심 공고에서 지원을 만듭니다." : null}
          />
        </Block>
        <Block title="제출" count={submitted.length}>
          <AppRows items={submitted} empty={!loading ? "제출한 지원이 없습니다." : null} />
        </Block>
        <Block title="결과" count={done.length}>
          <AppRows items={done} empty={!loading ? "기록한 결과가 없습니다." : null} />
          {done.length ? (
            <p className="mt-3 text-[13px] text-muted">개인 표본은 작습니다. 결과로 원인을 단정하지 않습니다.</p>
          ) : null}
        </Block>
      </div>
    </div>
  );
}

function Block({ title, count, children }: { title: string; count: number; children: ReactNode }) {
  return (
    <section className="min-w-0">
      <h2 className="mb-2 text-[15px] font-semibold">
        {title} <span className="font-normal text-muted tabular-nums">{count}</span>
      </h2>
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

/** 마감까지 남은 날. 3일 안이면 진하게, 지났으면 흐리게. */
function Dday({ deadline }: { deadline: string | null | undefined }) {
  const label = ddayLabel(deadline);
  if (!label) return null;
  const n = daysLeft(deadline);
  return (
    <span className={cn("text-[13px] tabular-nums", n !== null && n >= 0 && n <= 3 ? "font-semibold" : "text-muted")}>
      {label}
    </span>
  );
}

function InterestRow({ p, onCreate, busy }: { p: PostingSummary; onCreate: () => void; busy: boolean }) {
  const name = p.title ?? hostOf(p.canonical_url);
  return (
    <li className="flex items-center gap-3">
      <Link
        to="/postings/$postingId"
        params={{ postingId: p.id }}
        className="-mx-2 flex min-w-0 flex-1 items-center gap-4 rounded-md px-2 py-3 transition-colors duration-150 hover:bg-subtle/60"
      >
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-medium">{p.company ?? name}</div>
          {p.company ? <div className="truncate text-[13px] text-muted">{name}</div> : null}
        </div>
        <div className="shrink-0">
          <Dday deadline={p.deadline} />
        </div>
      </Link>
      <Button size="sm" className="h-9" onClick={onCreate} disabled={busy}>
        <Plus size={14} /> 지원 만들기
      </Button>
    </li>
  );
}

function AppRows({ items, empty }: { items: ApplicationSummary[]; empty: string | null }) {
  if (!items.length) return empty ? <Quiet>{empty}</Quiet> : null;
  return (
    <Rows>
      {items.map((a) => (
        <AppRow key={a.id} a={a} />
      ))}
    </Rows>
  );
}

function AppRow({ a }: { a: ApplicationSummary }) {
  const right =
    a.status === "draft" ? (
      <Dday deadline={a.deadline} />
    ) : a.status === "submitted" ? null : (
      <span className={cn("text-[13px]", a.status === "passed" ? "font-medium" : "text-muted")}>
        {APP_STATUS[a.status].label}
      </span>
    );
  return (
    <li>
      <Link
        to="/applications/$applicationId"
        params={{ applicationId: a.id }}
        className="-mx-2 flex items-center gap-4 rounded-md px-2 py-3 transition-colors duration-150 hover:bg-subtle/60"
      >
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-medium">{a.company ?? a.title}</div>
          <div className="truncate text-[13px] text-muted">
            {[a.job_title ?? (a.company ? a.title : null), nextAction(a)].filter(Boolean).join(" · ")}
          </div>
        </div>
        {right ? <div className="shrink-0 text-right">{right}</div> : null}
      </Link>
    </li>
  );
}
