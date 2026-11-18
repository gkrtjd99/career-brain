// 지원 쓰기 (/applications/$id). 준비 중인 지원을 한 줄 흐름으로 쓴다 (plan §22).
// 머리: 회사 · 직무, D-day, 주 버튼 하나("초안 쓰기" / 다 썼으면 "제출본 고정"). 문제가 있을 때만 짧은 목록.
// 그 아래 문항을 위에서 아래로: 문항, 바로 고칠 수 있는 글, 글자 수, "쓴 경험: A, B · 확인 전 1" 한 줄(펼치면 인용·원본).
// 자세한 편집기(문장 근거 연결·AI 수정안·검사·기록)는 문항 메뉴의 "자세히 편집" (/applications/$id/documents/$docId).
import type {
  AppDocument,
  Application,
  ApplicationChecks,
  ApplicationDraft,
  DocumentSummary,
  DraftDocument,
  DraftResult,
  Run,
  TextCounts,
} from "@career-brain/client";
import {
  Button,
  cn,
  ConfirmDialog,
  Disclosure,
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
  Label,
  MoreMenu,
  Sheet,
  Spinner,
  Textarea,
  toast,
} from "@career-brain/ui";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getRouteApi, Link, useNavigate } from "@tanstack/react-router";
import { ChevronDown, PenLine } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ErrorText, Loaded, PageHeader } from "../components/common";
import { CompanyPanel } from "../components/doc/CompanyPanel";
import { PrepPanel, prepKey } from "../components/doc/PrepPanel";
import { CostEstimate, newRunKey, runKey } from "../components/runs/RunParts";
import { api, ApiError } from "../lib/api";
import { daysLeft, ddayLabel, formatDeadline } from "../lib/dates";
import { int } from "../lib/format";
import { APP_STATUS, DOC_KIND, FIELD_LABEL } from "../lib/labels";
import { RUN_CODE } from "../lib/systemLabels";
import { AddDocumentSheet, ApplicationView, StatusSheet, SubmitDialog, summaryFlags, withoutUnconfirmed } from "./Application";
import { DocumentEditor } from "./Document";

const appRoute = getRouteApi("/applications/$applicationId");
const docRoute = getRouteApi("/applications/$applicationId/documents/$documentId");

const DRAFT_WORKFLOW = "draft_application";
const ACTIVE: Run["status"][] = ["queued", "running", "waiting_approval"];

export function ApplicationWorkbenchPage() {
  const { applicationId } = appRoute.useParams();
  const { draft } = appRoute.useSearch();
  const q = useQuery({ queryKey: ["application", applicationId], queryFn: () => api.application(applicationId) });
  return (
    <Loaded query={q}>
      {(app) => (app.status === "draft" ? <WritePage app={app} offerDraft={!!draft} /> : <ApplicationView app={app} />)}
    </Loaded>
  );
}

/** 자세히 편집: 문장 근거 연결·AI 수정안·검사·기록이 있는 편집기 (제출본을 고정했으면 읽기 전용). */
export function DocumentWorkbenchPage() {
  const { applicationId, documentId } = docRoute.useParams();
  const q = useQuery({ queryKey: ["document", documentId], queryFn: () => api.document(applicationId, documentId) });
  return <Loaded query={q}>{(doc) => <DocumentEditor key={doc.id} appId={applicationId} initial={doc} />}</Loaded>;
}

// ---------------------------------------------------------------- 쓰기 화면

const isQuestion = (d: Pick<DocumentSummary, "kind" | "question_prompt">) => d.kind === "question" || !!d.question_prompt;
const isEmpty = (d: DocumentSummary) => d.current_revision === 0 || !d.counts?.chars_with_spaces;

function docName(d: DocumentSummary, docs: DocumentSummary[]) {
  if (isQuestion(d)) {
    const n = docs.filter(isQuestion).indexOf(d) + 1;
    return `문항 ${n}`;
  }
  return d.title;
}

type Progress = "waiting" | "writing" | "done";

/** 실행 위치에서 문항마다 진행 상태 */
function progressOf(run: Run | undefined): Map<string, Progress> {
  const out = new Map<string, Progress>();
  if (!run || !ACTIVE.includes(run.status)) return out;
  const list = run.position.documents;
  if (!Array.isArray(list)) {
    return out;
  }
  const next = typeof run.position.next === "number" ? run.position.next : 0;
  (list as string[]).forEach((id, i) => out.set(id, i < next ? "done" : i === next ? "writing" : "waiting"));
  return out;
}

function WritePage({ app, offerDraft }: { app: Application; offerDraft: boolean }) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const draft = useQuery({ queryKey: ["application-draft", app.id], queryFn: () => api.applicationDraft(app.id) });
  const checks = useQuery({ queryKey: ["application-checks", app.id], queryFn: () => api.applicationChecks(app.id) });
  const prep = useQuery({ queryKey: prepKey(app.id), queryFn: () => api.applicationPrep(app.id) });

  // 지켜볼 초안 실행: 이 화면에서 시작한 것, 아니면 마지막 실행이 아직 도는 중이면 그것
  const [runId, setRunId] = useState<string | null>(null);
  const watched = runId ?? draft.data?.run_id ?? null;
  const run = useQuery({
    queryKey: ["run", watched],
    queryFn: () => api.run(watched!),
    enabled: !!watched,
    refetchInterval: (q) => (q.state.data && !ACTIVE.includes(q.state.data.status) ? false : 1500),
  });
  const running = !!run.data && ACTIVE.includes(run.data.status);
  const progress = useMemo(() => progressOf(run.data), [run.data]);

  // 한 문항이 끝날 때마다 그 글을 다시 읽는다. 실행이 끝나면 알림 한 줄.
  const seenNext = useRef<number>(-1);
  const reported = useRef<string | null>(null);
  const wasActive = useRef(false);
  useEffect(() => {
    const r = run.data;
    if (!r) return;
    const next = typeof r.position.next === "number" ? r.position.next : 0;
    const refresh = () => {
      qc.invalidateQueries({ queryKey: ["application", app.id] });
      qc.invalidateQueries({ queryKey: ["application-draft", app.id] });
      qc.invalidateQueries({ queryKey: ["application-checks", app.id] });
      qc.invalidateQueries({ queryKey: ["applications"] });
      qc.invalidateQueries({ queryKey: ["document"] });
    };
    if (ACTIVE.includes(r.status)) {
      wasActive.current = true;
      if (next !== seenNext.current) {
        seenNext.current = next;
        if (next > 0) refresh();
      }
      return;
    }
    if (!wasActive.current || reported.current === r.id) return;
    reported.current = r.id;
    wasActive.current = false;
    refresh();
    if (r.status === "done") toast.success(doneMessage(r, app.documents));
    else if (r.status === "failed")
      toast.error(`초안을 쓰지 못했습니다. ${RUN_CODE[r.error_code ?? ""] ?? r.error_code ?? ""}`.trim());
  }, [run.data, qc, app.id, app.documents]);

  const docs = app.documents;
  const questions = docs.filter(isQuestion);
  const empty = questions.filter(isEmpty);
  const [dialog, setDialog] = useState<{ docIds: string[] | null; overwrite: boolean } | null>(
    offerDraft && questions.some(isEmpty) ? { docIds: null, overwrite: false } : null,
  );
  // 막 만든 지원에서 연 창은 한 번만 (주소의 draft 를 지운다)
  useEffect(() => {
    if (offerDraft)
      void navigate({ to: "/applications/$applicationId", params: { applicationId: app.id }, search: {}, replace: true });
  }, [offerDraft, navigate, app.id]);

  const [adding, setAdding] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [withdrawing, setWithdrawing] = useState(false);
  const [company, setCompany] = useState(false);
  const [candidatesFor, setCandidatesFor] = useState<DocumentSummary | null>(null);

  const byDraft = new Map(draft.data?.documents.map((d) => [d.document_id, d]) ?? []);
  const company_ = app.posting.company as string | undefined;
  const jobTitle = app.posting.title as string | undefined;
  const deadline = app.posting.deadline as string | undefined;
  const dday = ddayLabel(deadline);
  const left = daysLeft(deadline);
  const soon = left !== null && left >= 0 && left <= 3;
  const allWritten = docs.length > 0 && empty.length === 0;

  const primary = running ? (
    <Button size="sm" variant="primary" disabled>
      <Spinner /> 초안 쓰는 중
    </Button>
  ) : !allWritten && questions.length ? (
    <Button size="sm" variant="primary" onClick={() => setDialog({ docIds: null, overwrite: false })}>
      <PenLine size={14} /> 초안 쓰기
    </Button>
  ) : (
    <>
      {questions.length ? (
        <DropdownMenu modal={false}>
          <DropdownMenuTrigger asChild>
            <Button size="sm">
              다시 쓰기 <ChevronDown size={14} />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            {questions.map((d) => (
              <DropdownMenuItem key={d.id} onSelect={() => setDialog({ docIds: [d.id], overwrite: true })}>
                {docName(d, docs)}만 다시 쓰기
              </DropdownMenuItem>
            ))}
            <DropdownMenuItem onSelect={() => setDialog({ docIds: null, overwrite: true })}>
              모든 문항 다시 쓰기
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      ) : null}
      {docs.length ? (
        <Button size="sm" variant="primary" onClick={() => setSubmitting(true)}>
          제출본 고정
        </Button>
      ) : null}
    </>
  );

  return (
    <div className="mx-auto max-w-3xl xl:max-w-[56rem]">
      <PageHeader
        back={{ to: "/applications", label: "지원" }}
        title={company_ && jobTitle ? `${company_} · ${jobTitle}` : app.title}
        subtitle={
          <span className="flex flex-wrap items-center gap-x-1.5 tabular-nums">
            {[
              dday ? <span className={cn(soon && "font-semibold text-fg")}>{dday}</span> : null,
              deadline && deadline !== "always" ? <span>마감 {formatDeadline(deadline)}</span> : null,
              <span>{APP_STATUS[app.status].label}</span>,
            ]
              .filter(Boolean)
              .flatMap((el, i) => (i ? [<span key={`d${i}`} aria-hidden>·</span>, <span key={i}>{el}</span>] : [<span key={i}>{el}</span>]))}
          </span>
        }
        actions={
          <div className="flex items-center gap-2">
            {primary}
            <MoreMenu
              label="지원 메뉴"
              items={[
                { label: "공고 보기", onSelect: () => void navigate({ to: "/postings/$postingId", params: { postingId: app.posting_id } }) },
                { label: "회사 정보", onSelect: () => setCompany(true) },
                {
                  label: "이 공고용 이력서",
                  onSelect: () => void navigate({ to: "/brain/resumes", search: { application: app.id } }),
                },
                {
                  label: "이 공고용 포트폴리오",
                  onSelect: () => void navigate({ to: "/brain/portfolio", search: { application: app.id } }),
                },
                {
                  label: "면접 준비",
                  onSelect: () =>
                    void navigate({ to: "/applications/$applicationId/interview", params: { applicationId: app.id } }),
                },
                { label: "문서 추가", onSelect: () => setAdding(true), separated: true },
                !allWritten && docs.length ? { label: "제출본 고정", onSelect: () => setSubmitting(true) } : null,
                { label: "지원 철회", onSelect: () => setWithdrawing(true), danger: true, separated: true },
              ]}
            />
          </div>
        }
      />

      <Issues app={app} checks={checks.data} draft={draft.data} overlaps={prep.data?.overlaps ?? []} />

      <div className="xl:grid xl:grid-cols-[9rem_minmax(0,1fr)] xl:gap-x-12">
        <QuestionIndex docs={docs} progress={progress} />
        <div className="min-w-0 max-w-[44rem]">
          {docs.length ? (
            <ol className="divide-y divide-line border-y border-line">
              {docs.map((d) => (
                <QuestionBlock
                  key={d.id}
                  app={app}
                  summary={d}
                  name={docName(d, docs)}
                  number={isQuestion(d) ? questions.indexOf(d) + 1 : null}
                  draft={byDraft.get(d.id)}
                  progress={progress.get(d.id)}
                  busy={running}
                  onRewrite={() => setDialog({ docIds: [d.id], overwrite: !isEmpty(d) })}
                  onCandidates={() => setCandidatesFor(d)}
                />
              ))}
            </ol>
          ) : (
            <p className="border-y border-line py-6 text-sm text-muted">
              공고에 문항이 없습니다. 문서를 추가하면 여기에 쌓입니다.{" "}
              <button type="button" className="text-fg underline underline-offset-2" onClick={() => setAdding(true)}>
                문서 추가
              </button>
            </p>
          )}
        </div>
      </div>

      {dialog ? (
        <DraftDialog
          app={app}
          docIds={dialog.docIds}
          overwrite={dialog.overwrite}
          onClose={() => setDialog(null)}
          onStarted={(r) => {
            setRunId(r.id);
            seenNext.current = -1;
            qc.setQueryData(["run", r.id], r);
          }}
        />
      ) : null}
      <Sheet open={company} onOpenChange={setCompany} title="회사 정보">
        <CompanyPanel postingId={app.posting_id} />
      </Sheet>
      <Sheet
        open={!!candidatesFor}
        onOpenChange={(o) => (o ? null : setCandidatesFor(null))}
        title={candidatesFor ? `${docName(candidatesFor, docs)} 경험 후보` : "경험 후보"}
        width="lg"
      >
        <Loaded query={prep}>
          {(p) => (
            <div className="space-y-6">
              <PrepPanel
                appId={app.id}
                postingId={app.posting_id}
                prep={p}
                doc={candidatesFor ?? undefined}
                docs={docs}
                editable
              />
              {candidatesFor && isQuestion(candidatesFor) ? (
                <div className="border-t border-line pt-4">
                  <Button
                    variant="primary"
                    disabled={running}
                    onClick={() => {
                      const d = candidatesFor;
                      setCandidatesFor(null);
                      setDialog({ docIds: [d.id], overwrite: !isEmpty(d) });
                    }}
                  >
                    고른 경험으로 다시 쓰기
                  </Button>
                </div>
              ) : null}
            </div>
          )}
        </Loaded>
      </Sheet>
      <AddDocumentSheet appId={app.id} open={adding} onClose={() => setAdding(false)} />
      <SubmitDialog
        app={app}
        checks={checks.data}
        open={submitting}
        onClose={() => setSubmitting(false)}
        onDone={() => {
          qc.invalidateQueries({ queryKey: ["application", app.id] });
          qc.invalidateQueries({ queryKey: ["applications"] });
          qc.invalidateQueries({ queryKey: ["document"] });
          qc.invalidateQueries({ queryKey: ["application-checks", app.id] });
          qc.invalidateQueries({ queryKey: prepKey(app.id) });
        }}
      />
      <StatusSheet app={app} open={withdrawing} onClose={() => setWithdrawing(false)} />
    </div>
  );
}

function doneMessage(run: Run, docs: DocumentSummary[]): string {
  const results = (Array.isArray(run.position.results) ? run.position.results : []) as DraftResult[];
  const written = results.filter((r) => r.status === "written").length;
  const unconfirmed = results.reduce((a, r) => a + (r.unconfirmed ?? 0), 0);
  const missing = results.filter((r) => r.status === "no_material" || r.status === "model_empty");
  const parts = [written ? `초안 ${written}개를 썼습니다.` : "새로 쓴 초안이 없습니다."];
  if (unconfirmed) parts.push(`확인 전 사실에 기댄 문장이 ${unconfirmed}개 있습니다.`);
  if (missing.length) {
    const names = missing.map((r) => {
      const d = docs.find((x) => x.id === r.document_id);
      return d ? docName(d, docs) : "문항";
    });
    parts.push(`${names.join(", ")}은 쓸 경험을 찾지 못해 비워 두었습니다.`);
  }
  return parts.join(" ");
}

// ---------------------------------------------------------------- 문제 목록 (있을 때만)

function Issues({
  app,
  checks,
  draft,
  overlaps,
}: {
  app: Application;
  checks: ApplicationChecks | undefined;
  draft: ApplicationDraft | undefined;
  overlaps: { experience_id: string; title: string | null; document_ids: string[] }[];
}) {
  const docs = app.documents;
  const byCheck = new Map(checks?.documents.map((d) => [d.document_id, d]) ?? []);
  const byDraft = new Map(draft?.documents.map((d) => [d.document_id, d]) ?? []);
  const rows: { key: string; target?: string; label: ReactNode; danger?: boolean }[] = [];
  if (app.posting_pending_changes.length)
    rows.push({
      key: "posting",
      label: (
        <>
          공고에 확인하지 않은 변경이 있습니다:{" "}
          {app.posting_pending_changes.map((f) => FIELD_LABEL[f as keyof typeof FIELD_LABEL] ?? f).join(", ")}.{" "}
          <Link to="/postings/$postingId" params={{ postingId: app.posting_id }} className="underline underline-offset-2">
            공고에서 확인
          </Link>
        </>
      ),
    });
  for (const d of docs) {
    if (isEmpty(d)) continue;
    const unconfirmed = byDraft.get(d.id)?.unconfirmed_sentences.length ?? 0;
    const flags = summaryFlags(withoutUnconfirmed(byCheck.get(d.id)?.summary, unconfirmed));
    const over = !!(d.counts?.over_char_limit || d.counts?.over_byte_limit);
    const parts = [
      ...(over && !flags.some((f) => f.key === "limit") ? ["분량 초과"] : []),
      ...flags.map((f) => f.label),
      ...(unconfirmed ? [`확인 전 ${unconfirmed}`] : []),
    ];
    if (!parts.length) continue;
    rows.push({
      key: d.id,
      target: d.id,
      danger: over || flags.some((f) => f.tone === "danger"),
      label: (
        <>
          <span className="font-medium text-fg">{docName(d, docs)}</span> · {parts.join(" · ")}
        </>
      ),
    });
  }
  for (const o of overlaps)
    rows.push({
      key: `overlap-${o.experience_id}`,
      label: (
        <>
          같은 경험을 여러 문항에 씁니다: {o.title ?? "경험"} (
          {o.document_ids
            .map((id) => docs.find((d) => d.id === id))
            .filter((d): d is DocumentSummary => !!d)
            .map((d) => docName(d, docs))
            .join(", ")}
          )
        </>
      ),
    });
  if (!rows.length) return null;
  return (
    <section aria-label="확인할 것" className="mb-8 max-w-[44rem] xl:ml-[calc(9rem+3rem)]">
      <ul className="space-y-1 text-[13px] text-muted">
        {rows.map((r) => (
          <li key={r.key} className={cn("flex min-h-7 items-start gap-2", r.danger && "text-danger")}>
            <span aria-hidden className={cn("mt-2 h-1 w-1 shrink-0 rounded-full", r.danger ? "bg-danger" : "bg-muted")} />
            {r.target ? (
              <a href={`#q-${r.target}`} className="hover:text-fg">
                {r.label}
              </a>
            ) : (
              <span>{r.label}</span>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

// ---------------------------------------------------------------- 왼쪽 목차 (넓은 화면)

function QuestionIndex({ docs, progress }: { docs: DocumentSummary[]; progress: Map<string, Progress> }) {
  if (docs.length < 2) return <div className="hidden xl:block" />;
  return (
    <nav aria-label="문항" className="hidden xl:block">
      <ol className="sticky top-6 space-y-0.5 text-[13px]">
        {docs.map((d) => {
          const p = progress.get(d.id);
          const limit = d.char_limit ?? d.counts?.char_limit ?? null;
          return (
            <li key={d.id}>
              <a
                href={`#q-${d.id}`}
                className="-mx-2 flex min-h-9 flex-col justify-center rounded-md px-2 py-1.5 text-muted transition-colors duration-150 hover:bg-subtle/60 hover:text-fg"
              >
                <span className="truncate text-fg">{docName(d, docs)}</span>
                <span className="text-xs tabular-nums">
                  {p === "writing"
                    ? "쓰는 중"
                    : p === "waiting"
                      ? "기다리는 중"
                      : isEmpty(d)
                        ? "비어 있음"
                        : `${int(d.counts?.chars ?? 0)}${limit ? `/${int(limit)}` : "자"}`}
                </span>
              </a>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}

// ---------------------------------------------------------------- 문항 하나

function countText(c: TextCounts | null | undefined, limit: number | null) {
  const chars = c?.chars ?? 0;
  return limit ? `${int(chars)} / ${int(limit)}자` : `${int(chars)}자`;
}

function QuestionBlock({
  app,
  summary,
  name,
  number,
  draft,
  progress,
  busy,
  onRewrite,
  onCandidates,
}: {
  app: Application;
  summary: DocumentSummary;
  name: string;
  number: number | null;
  draft: DraftDocument | undefined;
  progress: Progress | undefined;
  busy: boolean;
  onRewrite: () => void;
  onCandidates: () => void;
}) {
  const navigate = useNavigate();
  const docQ = useQuery({
    queryKey: ["document", summary.id],
    queryFn: () => api.document(app.id, summary.id),
    enabled: !isEmpty(summary),
  });
  const [editing, setEditing] = useState(false);
  const doc = docQ.data;
  const blocks = doc && doc.revision_no === summary.current_revision ? doc.blocks : isEmpty(summary) ? [] : doc?.blocks ?? [];
  const limit = summary.char_limit ?? summary.counts?.char_limit ?? null;
  const over = !!(summary.counts?.over_char_limit || summary.counts?.over_byte_limit);
  const writing = progress === "writing" || progress === "waiting";

  return (
    <li id={`q-${summary.id}`} className="scroll-mt-6 py-7">
      <div className="flex items-start gap-3">
        <h2 className="min-w-0 flex-1 text-[15px] leading-6 font-medium text-balance">
          {number !== null ? <span className="mr-1.5 text-muted tabular-nums">{number}.</span> : null}
          {summary.question_prompt ?? (
            <>
              <span className="mr-1.5 text-muted">{DOC_KIND[summary.kind] ?? summary.kind}</span>
              {summary.title}
            </>
          )}
        </h2>
        <MoreMenu
          label={`${name} 메뉴`}
          items={[
            !isEmpty(summary) && !editing ? { label: "수정", onSelect: () => setEditing(true) } : null,
            isQuestion(summary)
              ? { label: isEmpty(summary) ? "이 문항만 초안 쓰기" : "다시 쓰기", onSelect: onRewrite, disabled: busy }
              : null,
            isQuestion(summary) ? { label: "경험 후보", onSelect: onCandidates } : null,
            {
              label: "자세히 편집",
              separated: true,
              onSelect: () =>
                void navigate({
                  to: "/applications/$applicationId/documents/$documentId",
                  params: { applicationId: app.id, documentId: summary.id },
                }),
            },
          ]}
        />
      </div>

      <div className="mt-3">
        {writing ? (
          <p className="flex min-h-24 items-center gap-2 text-[13px] text-muted" aria-live="polite">
            {progress === "writing" ? (
              <>
                <Spinner /> {name} 쓰는 중…
              </>
            ) : (
              "차례를 기다리는 중"
            )}
          </p>
        ) : editing && (doc || isEmpty(summary)) ? (
          <InlineEditor
            appId={app.id}
            summary={summary}
            doc={doc ?? null}
            onDone={() => setEditing(false)}
          />
        ) : blocks.length ? (
          <DraftText blocks={blocks} draft={draft} onEdit={() => setEditing(true)} />
        ) : isEmpty(summary) ? (
          <div className="flex min-h-16 flex-wrap items-center gap-x-3 gap-y-1 text-[13px] text-muted">
            <span>비어 있습니다.</span>
            <button
              type="button"
              className="inline-flex min-h-9 items-center underline underline-offset-2 hover:text-fg"
              onClick={() => setEditing(true)}
            >
              직접 쓰기
            </button>
          </div>
        ) : (
          <p className="min-h-16 text-[13px] text-muted">불러오는 중</p>
        )}
      </div>

      {!editing && !writing ? (
        <div className="mt-3 flex items-start justify-between gap-x-4">
          <EvidenceLine draft={draft} />
          {!isEmpty(summary) ? (
            <span className={cn("ml-auto shrink-0 pt-2 text-[13px] tabular-nums sm:pt-1", over ? "font-medium text-danger" : "text-muted")}>
              {countText(summary.counts, limit)}
            </span>
          ) : limit ? (
            <span className="ml-auto shrink-0 pt-2 text-[13px] text-muted tabular-nums sm:pt-1">{int(limit)}자 이내</span>
          ) : null}
        </div>
      ) : null}
    </li>
  );
}

/** 글: 문단마다, 확인 전 사실에만 기댄 문장은 점선 밑줄과 "확인 전". 누르면 수정. */
function DraftText({
  blocks,
  draft,
  onEdit,
}: {
  blocks: AppDocument["blocks"];
  draft: DraftDocument | undefined;
  onEdit: () => void;
}) {
  const marks = draft?.unconfirmed_sentences ?? [];
  return (
    <div
      role="button"
      tabIndex={0}
      aria-label="글 수정"
      onClick={(e) => {
        // 글을 고르는 중이면 수정 창으로 바꾸지 않는다
        if (window.getSelection()?.toString()) return;
        if ((e.target as HTMLElement).closest("a,button")) return;
        onEdit();
      }}
      onKeyDown={(e) => {
        if (e.key === "Enter") onEdit();
      }}
      className="-mx-2 cursor-text space-y-3 rounded-md px-2 py-1 text-[15px] leading-7 transition-colors duration-150 hover:bg-subtle/40 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
    >
      {blocks.map((b) => {
        const chars = Array.from(b.text);
        const mine = marks.filter((m) => m.block_id === b.id).sort((a, x) => a.start - x.start);
        const parts: ReactNode[] = [];
        let pos = 0;
        mine.forEach((m, i) => {
          if (m.start > pos) parts.push(chars.slice(pos, m.start).join(""));
          parts.push(
            <span key={i}>
              <span className="underline decoration-muted decoration-dotted underline-offset-[5px]">
                {chars.slice(m.start, m.end).join("")}
              </span>
              <span className="ml-1 rounded-sm border border-line px-1 align-[1px] text-[11px] leading-4 font-medium text-muted">
                확인 전
              </span>
            </span>,
          );
          pos = m.end;
        });
        if (pos < chars.length) parts.push(chars.slice(pos).join(""));
        return (
          <p key={b.id} className="break-words whitespace-pre-wrap">
            {parts}
          </p>
        );
      })}
    </div>
  );
}

/** "쓴 경험: A, B · 확인 전 1" 한 줄. 펼치면 기댄 사실·인용·원본. */
function EvidenceLine({ draft }: { draft: DraftDocument | undefined }) {
  if (!draft || !draft.claims.length) return <span />;
  const titles = draft.experiences.map((e) => e.title);
  const unconfirmed = draft.unconfirmed_sentences.length;
  const byExp = draft.experiences.map((e) => ({
    ...e,
    claims: draft.claims.filter((c) => c.experience_id === e.experience_id),
  }));
  return (
    <Disclosure
      tone="quiet"
      className="min-w-0 flex-1 text-[13px]"
      triggerClassName="min-h-9 max-w-full gap-1 text-muted hover:text-fg sm:min-h-7"
      title={
        <span className="min-w-0 truncate">
          쓴 경험: {titles.join(", ")}
          {unconfirmed ? <span className="text-fg"> · 확인 전 {unconfirmed}</span> : null}
        </span>
      }
    >
      <div className="mt-2 space-y-4 pl-5">
        {byExp.map((e) => (
          <div key={e.experience_id}>
            <Link
              to="/brain/$experienceId"
              params={{ experienceId: e.experience_id }}
              className="font-medium underline-offset-2 hover:underline"
            >
              {e.title}
            </Link>
            <ul className="mt-1.5 space-y-2.5">
              {e.claims.map((c) => (
                <li key={c.claim_id} className="leading-5">
                  <p>
                    {c.statement}
                    {!c.confirmed ? <span className="ml-1.5 text-xs font-medium text-muted">확인 전</span> : null}
                  </p>
                  {c.quote ? (
                    <p className="mt-0.5 text-xs text-muted">
                      “{c.quote.length > 160 ? `${c.quote.slice(0, 160)}…` : c.quote}”
                      {c.source?.kind === "span" && c.source.title ? (
                        <span>
                          {" "}
                          · {c.source.title}
                          {c.source.page ? ` ${c.source.page}쪽` : ""}
                        </span>
                      ) : c.source?.kind === "answer" ? (
                        <span> · 인터뷰 답</span>
                      ) : null}
                    </p>
                  ) : c.restricted ? (
                    <p className="mt-0.5 text-xs text-muted">증빙 자료 (인용은 보이지 않습니다)</p>
                  ) : null}
                </li>
              ))}
            </ul>
          </div>
        ))}
        {unconfirmed ? (
          <p className="text-xs text-muted">
            확인 전 사실은 브레인에서 확인하면 표시가 사라집니다. 사실이 아니면 그 문장을 수정합니다.
          </p>
        ) : null}
      </div>
    </Disclosure>
  );
}

// ---------------------------------------------------------------- 바로 고치기

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

/** UTF-16 위치 → 코드 포인트 위치 (서버는 코드 포인트로 센다) */
const cp = (text: string, i: number) => Array.from(text.slice(0, i)).length;

/**
 * 글 한 칸(문단은 빈 줄 또는 줄바꿈)을 블록으로 되돌린다. 문장 근거는 인용으로 다시 찾는다:
 * 예전 문장이 그대로 남아 있으면 그 문장의 근거가 따라오고, 바뀐 문장의 근거는 빠진다.
 */
function toBlocks(text: string, old: AppDocument["blocks"]) {
  const paras = text
    .normalize("NFC")
    .replace(/\r\n?/g, "\n")
    .split(/\n+/)
    .map((p) => p.trim())
    .filter(Boolean);
  const quotes = old.flatMap((b) => {
    const chars = Array.from(b.text);
    return (b.spans ?? []).map((sp) => ({ block: b.id, quote: chars.slice(sp.start, sp.end).join(""), claims: sp.claim_ids }));
  });
  const used = new Set<string>();
  return paras.map((p, i) => {
    // 이 문단에 남은 예전 문장이 가장 많은 블록의 ID를 잇는다 (같은 글이면 그대로)
    let best: string | null = null;
    let score = 0;
    for (const b of old) {
      if (used.has(b.id)) continue;
      const s = b.text === p ? 1000 : quotes.filter((q) => q.block === b.id && p.includes(q.quote)).length;
      if (s > score) {
        best = b.id;
        score = s;
      }
    }
    if (!best && old.length === paras.length && old[i] && !used.has(old[i]!.id)) best = old[i]!.id;
    if (best) used.add(best);
    const prev = old.find((b) => b.id === best);
    const spans: { start: number; end: number; claim_ids: string[] }[] = [];
    for (const q of quotes) {
      const at = q.quote ? p.indexOf(q.quote) : -1;
      if (at < 0 || spans.some((s) => s.start === cp(p, at))) continue;
      spans.push({ start: cp(p, at), end: cp(p, at + q.quote.length), claim_ids: q.claims });
    }
    spans.sort((a, b) => a.start - b.start);
    const claims = [...new Set([...(prev && !(prev.spans ?? []).length ? prev.claim_ids : []), ...spans.flatMap((s) => s.claim_ids)])];
    return { id: best, text: p, claim_ids: claims.slice(0, 20), spans };
  });
}

function InlineEditor({
  appId,
  summary,
  doc,
  onDone,
}: {
  appId: string;
  summary: DocumentSummary;
  doc: AppDocument | null;
  onDone: () => void;
}) {
  const qc = useQueryClient();
  const initial = (doc?.blocks ?? []).map((b) => b.text).join("\n\n");
  const [text, setText] = useState(initial);
  const ref = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.focus();
    el.setSelectionRange(el.value.length, el.value.length);
  }, []);
  const paras = useMemo(
    () =>
      text
        .split(/\n+/)
        .map((p) => p.trim())
        .filter(Boolean),
    [text],
  );
  const sent = useDebounced(paras, 300);
  const dirty = text.trim() !== initial.trim();
  const count = useQuery({
    queryKey: ["count", summary.id, sent],
    queryFn: ({ signal }) => api.countDraft(appId, summary.id, sent, signal),
    placeholderData: keepPreviousData,
    staleTime: Infinity,
  });
  const limit = summary.char_limit ?? null;
  const counts = count.data;
  const over = !!(counts?.over_char_limit || counts?.over_byte_limit);
  const base = doc?.revision_no ?? 0;
  const save = useMutation({
    mutationFn: () =>
      api.saveRevision(appId, summary.id, {
        base_revision: base,
        operations: [{ op: "set_blocks", blocks: toBlocks(text, doc?.blocks ?? []) }],
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["document", summary.id] });
      qc.invalidateQueries({ queryKey: ["application", appId] });
      qc.invalidateQueries({ queryKey: ["application-draft", appId] });
      qc.invalidateQueries({ queryKey: ["application-checks", appId] });
      qc.invalidateQueries({ queryKey: ["applications"] });
      onDone();
    },
  });
  const conflict = save.error instanceof ApiError && save.error.status === 409;
  return (
    <form
      className="space-y-2"
      onSubmit={(e) => {
        e.preventDefault();
        if (dirty) save.mutate();
        else onDone();
      }}
      onKeyDown={(e) => {
        if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "s") {
          e.preventDefault();
          if (dirty && !save.isPending) save.mutate();
        }
        if (e.key === "Escape" && !dirty) onDone();
      }}
    >
      <Label htmlFor={`edit-${summary.id}`} className="sr-only">
        {summary.question_prompt ?? summary.title}
      </Label>
      <Textarea
        id={`edit-${summary.id}`}
        ref={ref}
        value={text}
        onChange={(e) => setText(e.target.value)}
        className="min-h-40 text-[15px] leading-7 [field-sizing:content]"
        placeholder="문단 사이는 빈 줄로 나눕니다."
      />
      <div className="flex flex-wrap items-center gap-2">
        <span className={cn("text-[13px] tabular-nums", over ? "font-medium text-danger" : "text-muted")} aria-live="polite">
          {counts ? countText(counts, limit) : limit ? `${int(limit)}자 이내` : ""}
        </span>
        <div className="ml-auto flex gap-2">
          <Button type="button" size="sm" variant="ghost" onClick={onDone} disabled={save.isPending}>
            취소
          </Button>
          <Button type="submit" size="sm" variant="primary" disabled={!dirty || save.isPending}>
            {save.isPending ? "저장하는 중" : "저장"}
          </Button>
        </div>
      </div>
      {conflict ? (
        <p role="alert" className="text-[13px] text-danger">
          다른 곳에서 먼저 저장했습니다. 취소하고 최신 글을 다시 여세요.
        </p>
      ) : (
        <ErrorText error={save.error} />
      )}
    </form>
  );
}

// ---------------------------------------------------------------- 초안 쓰기 창

function DraftDialog({
  app,
  docIds,
  overwrite,
  onClose,
  onStarted,
}: {
  app: Application;
  /** null 이면 빈 문항 모두 (overwrite 면 모든 문항) */
  docIds: string[] | null;
  overwrite: boolean;
  onClose: () => void;
  onStarted: (run: Run) => void;
}) {
  const docs = app.documents;
  const questions = docs.filter(isQuestion);
  const targets = questions.filter((d) => (docIds ? docIds.includes(d.id) : overwrite || isEmpty(d)));
  const replacing = targets.filter((d) => !isEmpty(d));
  const [instruction, setInstruction] = useState("");
  const [key] = useState(() => newRunKey(`draft:${app.id}`));
  const input = useMemo(
    () => ({
      application_id: app.id,
      document_ids: docIds,
      overwrite,
      instruction: instruction.trim() || null,
    }),
    [app.id, docIds, overwrite, instruction],
  );
  const start = useMutation({
    mutationFn: () =>
      api.startDraft(app.id, {
        document_ids: docIds,
        overwrite,
        instruction: instruction.trim() || null,
        idempotency_key: runKey(key, input),
      }),
    onSuccess: (run) => {
      onStarted(run);
      onClose();
    },
  });
  return (
    <ConfirmDialog
      open
      onOpenChange={(o) => (o ? null : onClose())}
      title={replacing.length ? "다시 쓰기" : "초안 쓰기"}
      description="브레인에서 문항에 맞는 내 경험을 골라 씁니다. 확인한 사실을 먼저 쓰고, 확인 전 사실을 쓴 문장은 표시합니다."
      confirmLabel={start.isPending ? "시작하는 중" : targets.length > 1 ? `${targets.length}개 문항 쓰기` : "쓰기"}
      busy={start.isPending}
      confirmDisabled={!targets.length}
      onConfirm={() => start.mutate()}
    >
      <ul className="divide-y divide-line border-y border-line text-[13px]">
        {targets.map((d) => (
          <li key={d.id} className="flex items-start gap-3 py-2">
            <span className="shrink-0 text-muted tabular-nums">{docName(d, docs)}</span>
            <span className="line-clamp-2 min-w-0 flex-1">{d.question_prompt ?? d.title}</span>
          </li>
        ))}
      </ul>
      {!targets.length ? <p className="mt-2 text-[13px] text-muted">쓸 빈 문항이 없습니다.</p> : null}
      {replacing.length ? (
        <p className="mt-2 text-[13px] font-medium">
          지금 글을 새 초안으로 바꿉니다. 지금 글은 기록에 남아 되돌릴 수 있습니다.
        </p>
      ) : null}
      <Disclosure title="요청 더하기" tone="quiet" className="mt-3 text-[13px]" triggerClassName="min-h-9 gap-1 text-muted hover:text-fg">
        <Textarea
          aria-label="요청"
          value={instruction}
          onChange={(e) => setInstruction(e.target.value)}
          maxLength={500}
          placeholder="예: 협업 과정을 더 드러내 주세요."
          className="mt-2 min-h-16"
        />
      </Disclosure>
      {targets.length ? (
        <div className="mt-3">
          <CostEstimate workflow={DRAFT_WORKFLOW} units={targets.length} noun="문항" input={input} />
        </div>
      ) : null}
      <ErrorText error={start.error} className="mt-2" />
    </ConfirmDialog>
  );
}
