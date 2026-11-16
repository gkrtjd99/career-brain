import type { ApplicationSummary, PostingSummary } from "@career-brain/client";
import { Badge, Button, EmptyState, Spinner } from "@career-brain/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { Plus } from "lucide-react";
import type { ReactNode } from "react";
import { ErrorText, PageHeader } from "../components/common";
import { DdayBadge } from "../components/DdayBadge";
import { api } from "../lib/api";
import { daysLeft } from "../lib/dates";
import { hostOf } from "../lib/format";
import { APP_STATUS } from "../lib/labels";

/** 카드의 다음 행동 한 줄 */
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
  const interest = (postings.data ?? [])
    .filter((p) => p.status === "saved" && !withApp.has(p.id))
    .sort(byDeadline);
  const draft = list.filter((a) => a.status === "draft").sort(byDeadline);
  const submitted = list.filter((a) => a.status === "submitted");
  const done = list.filter((a) => ["passed", "rejected", "withdrawn"].includes(a.status));

  return (
    <div>
      <PageHeader
        title="지원 현황"
        subtitle="저장한 공고 → 준비 중 → 제출 → 결과. 카드를 누르면 문항과 문서로 갑니다."
      />
      {apps.isPending || postings.isPending ? (
        <div className="flex items-center gap-2 text-muted">
          <Spinner /> 불러오는 중
        </div>
      ) : null}
      <ErrorText error={apps.error ?? postings.error ?? create.error} className="mb-3" />

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <Column title="관심" count={interest.length} hint="저장한 공고 중 아직 지원을 만들지 않은 것">
          {interest.map((p) => (
            <InterestCard key={p.id} p={p} busy={create.isPending} onCreate={() => create.mutate(p.id)} />
          ))}
          {!interest.length ? (
            <EmptyState className="py-4">
              <Link to="/postings" className="text-accent hover:underline">
                공고 저장하기
              </Link>
            </EmptyState>
          ) : null}
        </Column>
        <Column title="준비 중" count={draft.length}>
          {draft.map((a) => (
            <AppCard key={a.id} a={a} />
          ))}
        </Column>
        <Column title="제출" count={submitted.length}>
          {submitted.map((a) => (
            <AppCard key={a.id} a={a} />
          ))}
        </Column>
        <Column title="결과" count={done.length} hint="개인 표본은 작습니다. 결과로 원인을 단정하지 않습니다.">
          {done.map((a) => (
            <AppCard key={a.id} a={a} />
          ))}
        </Column>
      </div>
    </div>
  );
}

function Column({
  title,
  count,
  hint,
  children,
}: {
  title: string;
  count: number;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <section className="min-w-0 rounded-lg bg-subtle/60 p-2">
      <h2 className="flex items-center gap-1.5 px-1 pb-1 text-[13px] font-semibold">
        {title}
        <span className="font-normal text-muted">{count}</span>
      </h2>
      {hint ? <p className="px-1 pb-2 text-xs text-muted">{hint}</p> : null}
      <div className="space-y-2">{children}</div>
    </section>
  );
}

function InterestCard({ p, onCreate, busy }: { p: PostingSummary; onCreate: () => void; busy: boolean }) {
  return (
    <div className="rounded-md border border-line bg-surface p-2.5">
      <Link to="/postings/$postingId" params={{ postingId: p.id }} className="block hover:underline">
        <div className="truncate text-sm font-medium">{p.title ?? hostOf(p.canonical_url)}</div>
        <div className="truncate text-xs text-muted">{p.company}</div>
      </Link>
      <div className="mt-2 flex items-center justify-between gap-2">
        <DdayBadge deadline={p.deadline} />
        <Button size="sm" onClick={onCreate} disabled={busy}>
          <Plus size={14} /> 지원 만들기
        </Button>
      </div>
    </div>
  );
}

function AppCard({ a }: { a: ApplicationSummary }) {
  const st = APP_STATUS[a.status];
  return (
    <Link
      to="/applications/$applicationId"
      params={{ applicationId: a.id }}
      className="block rounded-md border border-line bg-surface p-2.5 hover:border-accent/50"
    >
      <div className="truncate text-sm font-medium">{a.company ?? a.title}</div>
      <div className="truncate text-xs text-muted">{a.job_title ?? a.title}</div>
      <div className="mt-2 flex flex-wrap items-center gap-1">
        {a.status === "draft" ? <DdayBadge deadline={a.deadline} /> : <Badge tone={st.tone}>{st.label}</Badge>}
        {a.status === "draft" && a.questions ? (
          a.unwritten ? (
            <Badge tone="warn">안 쓴 문항 {a.unwritten}</Badge>
          ) : (
            <Badge tone="ok">문항 다 씀</Badge>
          )
        ) : null}
      </div>
      <p className="mt-1.5 text-xs text-accent">{nextAction(a)}</p>
    </Link>
  );
}
