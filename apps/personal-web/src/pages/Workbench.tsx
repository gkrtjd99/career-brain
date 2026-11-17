// 지원 작업대 (/applications/$id, /applications/$id/documents/$docId). 준비 중인 지원의 문항·문서를 한 화면에서 쓴다.
// 넓은 화면: 왼쪽 문항 목록 · 가운데 편집기(문항·글자 수 고정) · 오른쪽 쓸 경험/검사/회사/기록.
// 좁은 화면: 문항 고르기 → 쓸 경험 → 쓰기 → 검사(아래 시트) 단계와 아래에 붙는 버튼 줄.
// 문서 URL은 작업대에서 그 문서를 고른 화면이다 (예전 링크가 그대로 맞는다).
import type { AppDocument, Application, ApplicationChecks, ApplicationPrep, DocumentSummary } from "@career-brain/client";
import { Button, cn, EmptyState, Sheet } from "@career-brain/ui";
import { useQuery, useQueryClient, type UseQueryResult } from "@tanstack/react-query";
import { getRouteApi, Link, Navigate } from "@tanstack/react-router";
import { ChevronRight, Lock, Plus, Save } from "lucide-react";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Loaded, PageHeader, Tabs } from "../components/common";
import { CompanyPanel } from "../components/doc/CompanyPanel";
import { MoreMenu } from "../components/doc/MoreMenu";
import { docLabel, PrepPanel, prepKey } from "../components/doc/PrepPanel";
import { api } from "../lib/api";
import { daysLeft, ddayLabel, formatDeadline } from "../lib/dates";
import { int } from "../lib/format";
import { APP_STATUS, DOC_KIND, FIELD_LABEL } from "../lib/labels";
import { AddDocumentSheet, ApplicationView, StatusSheet, SubmitDialog, summaryFlags } from "./Application";
import { DocumentEditor, type EditorParts, type Scaffold } from "./Document";

const appRoute = getRouteApi("/applications/$applicationId");
const docRoute = getRouteApi("/applications/$applicationId/documents/$documentId");

/** 넓은 화면(세 칸) 기준. Tailwind xl 과 같다. */
const WIDE = "(min-width: 1280px)";

function useMedia(query: string): boolean {
  const [match, setMatch] = useState(() => typeof window !== "undefined" && window.matchMedia(query).matches);
  useEffect(() => {
    const mq = window.matchMedia(query);
    const on = () => setMatch(mq.matches);
    on();
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, [query]);
  return match;
}

export function ApplicationWorkbenchPage() {
  const { applicationId } = appRoute.useParams();
  return <WorkbenchLoader appId={applicationId} docId={null} />;
}

export function DocumentWorkbenchPage() {
  const { applicationId, documentId } = docRoute.useParams();
  return <WorkbenchLoader appId={applicationId} docId={documentId} />;
}

function WorkbenchLoader({ appId, docId }: { appId: string; docId: string | null }) {
  const q = useQuery({ queryKey: ["application", appId], queryFn: () => api.application(appId) });
  return (
    <Loaded query={q}>
      {(app) =>
        app.status === "draft" ? (
          <Workbench app={app} docId={docId} />
        ) : docId ? (
          <FrozenDocument appId={appId} docId={docId} />
        ) : (
          <ApplicationView app={app} />
        )
      }
    </Loaded>
  );
}

/** 제출본을 고정한 지원의 문서: 읽기 전용 편집기 (기록·검사·내보내기). */
function FrozenDocument({ appId, docId }: { appId: string; docId: string }) {
  const q = useQuery({ queryKey: ["document", docId], queryFn: () => api.document(appId, docId) });
  return <Loaded query={q}>{(doc) => <DocumentEditor key={doc.id} appId={appId} initial={doc} />}</Loaded>;
}

type Tab = "prep" | "checks" | "company" | "history";
type Step = "pick" | "prep" | "write";

function defaultDoc(docs: DocumentSummary[]) {
  return docs.find((d) => d.kind === "question" || d.question_prompt) ?? docs[0];
}

function Workbench({ app, docId }: { app: Application; docId: string | null }) {
  const wide = useMedia(WIDE);
  const [tab, setTab] = useState<Tab>("prep");
  const checks = useQuery({ queryKey: ["application-checks", app.id], queryFn: () => api.applicationChecks(app.id) });
  const prep = useQuery({ queryKey: prepKey(app.id), queryFn: () => api.applicationPrep(app.id) });
  const docQ = useQuery({
    queryKey: ["document", docId],
    queryFn: () => api.document(app.id, docId!),
    enabled: !!docId,
  });
  const summary = app.documents.find((d) => d.id === docId);

  // 고른 경험 → 그 사실이 연결된 빈 문단 (편집기가 화면에만 둔다)
  const scaffolds = useMemo<Scaffold[]>(() => {
    if (!docId || !prep.data) return [];
    const q = prep.data.questions.find((x) => x.document_id === docId);
    return (prep.data.choices[docId] ?? []).map((c) => {
      const title = q?.candidates.find((x) => x.experience_id === c.experience_id)?.title;
      return {
        experience_id: c.experience_id,
        claim_ids: c.claim_ids,
        hint: title
          ? `“${title}” 경험으로 직접 씁니다. 근거는 연결해 두었습니다.`
          : "고른 경험으로 직접 씁니다. 근거는 연결해 두었습니다.",
      };
    });
  }, [docId, prep.data]);

  const first = defaultDoc(app.documents);
  // 넓은 화면은 늘 문서 하나를 연다: 지원 첫 화면이면 첫 문항으로 (URL도 그 문서로)
  if (!docId && wide && first)
    return (
      <Navigate
        to="/applications/$applicationId/documents/$documentId"
        params={{ applicationId: app.id, documentId: first.id }}
        replace
      />
    );
  const initialStep: Step = !docId ? "pick" : summary && summary.current_revision > 0 ? "write" : "prep";
  const frame = (parts: EditorParts | null, pending = false) => (
    <Frame
      app={app}
      docId={docId}
      checks={checks.data}
      prep={prep}
      parts={parts}
      pending={pending}
      wide={wide}
      tab={tab}
      setTab={setTab}
      initialStep={initialStep}
    />
  );
  if (docId && docQ.data && docQ.data.id === docId)
    return (
      <DocumentEditor
        key={docQ.data.id}
        appId={app.id}
        initial={docQ.data as AppDocument}
        scaffolds={scaffolds}
        frame={(parts) => frame(parts)}
      />
    );
  return frame(null, !!docId && !docQ.isError);
}

function Frame({
  app,
  docId,
  checks,
  prep,
  parts,
  pending,
  wide,
  tab,
  setTab,
  initialStep,
}: {
  app: Application;
  docId: string | null;
  checks: ApplicationChecks | undefined;
  prep: UseQueryResult<ApplicationPrep>;
  parts: EditorParts | null;
  pending: boolean;
  wide: boolean;
  tab: Tab;
  setTab: (t: Tab) => void;
  initialStep: Step;
}) {
  const qc = useQueryClient();
  const [step, setStep] = useState<Step>(initialStep);
  const [sheet, setSheet] = useState(false);
  const [sheetTab, setSheetTab] = useState<Exclude<Tab, "prep">>("checks");
  const [suggestOpen, setSuggestOpen] = useState(false);
  const [adding, setAdding] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [withdrawing, setWithdrawing] = useState(false);
  const doc = app.documents.find((d) => d.id === docId);

  // 일괄 수정안에서 "수정안 보기"를 누르면 검사 칸의 수정안을 연다
  const ask = parts?.suggestAsk ?? 0;
  useEffect(() => {
    if (!ask) return;
    setSuggestOpen(true);
    if (wide) setTab("checks");
    else {
      setSheetTab("checks");
      setSheet(true);
    }
  }, [ask, wide, setTab]);

  const warnings = parts?.warnings ?? 0;
  const overlaps = prep.data?.overlaps ?? [];

  const checksPanel = parts ? (
    <div className="space-y-6">
      {overlaps.length ? (
        <div role="status" className="rounded-md border border-line px-3 py-2.5 text-[13px]">
          <p className="font-medium">같은 경험을 여러 문서에 씁니다</p>
          <ul className="mt-1 space-y-0.5 text-xs">
            {overlaps.map((o) => (
              <li key={o.experience_id}>
                {o.title ?? "경험"}: {o.document_ids.map((d) => docLabel(app.documents.find((x) => x.id === d))).join(", ")}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {parts.checks}
      {parts.editable ? (
        <details
          className="group border-t border-line pt-4 text-[13px]"
          open={suggestOpen}
          onToggle={(e) => setSuggestOpen((e.target as HTMLDetailsElement).open)}
        >
          <summary className="flex min-h-9 cursor-pointer list-none items-center gap-1 font-medium hover:text-fg sm:min-h-6 [&::-webkit-details-marker]:hidden">
            <ChevronRight size={14} className="text-muted transition-transform group-open:rotate-90" />
            AI 수정안
            <span className="font-normal text-muted">(쓴 문장을 다듬기만, 요청할 때만)</span>
          </summary>
          <div className="mt-3 space-y-4">
            <div>{parts.batch}</div>
            {parts.suggestions}
          </div>
        </details>
      ) : null}
    </div>
  ) : null;

  const prepPanel = (
    <Loaded query={prep}>
      {(p) => (
        <PrepPanel
          appId={app.id}
          postingId={app.posting_id}
          prep={p}
          doc={doc}
          docs={app.documents}
          editable
          onChosen={wide ? undefined : () => setStep("write")}
        />
      )}
    </Loaded>
  );

  const panelFor = (t: Tab) =>
    t === "prep" ? prepPanel : t === "checks" ? checksPanel : t === "company" ? <CompanyPanel postingId={app.posting_id} /> : parts?.history;

  const strip = (
    <Strip
      app={app}
      onSubmit={() => setSubmitting(true)}
      onAdd={() => setAdding(true)}
      onWithdraw={() => setWithdrawing(true)}
    />
  );
  const list = (
    <DocList app={app} checks={checks} prep={prep.data} selected={docId} onAdd={() => setAdding(true)} />
  );
  const pinned = parts ? <Pinned parts={parts} doc={doc} sticky={wide} /> : null;
  const centerEmpty = pending ? (
    <p className="py-6 text-muted">불러오는 중</p>
  ) : (
    <EmptyState>
      {app.documents.length ? "왼쪽에서 쓸 문항을 고릅니다." : "문서가 없습니다. 이력서·자기소개서 문항을 추가합니다."}
    </EmptyState>
  );

  const sheets = (
    <>
      {parts?.sheets}
      <AddDocumentSheet appId={app.id} open={adding} onClose={() => setAdding(false)} />
      <SubmitDialog
        app={app}
        checks={checks}
        open={submitting}
        onClose={() => setSubmitting(false)}
        onDone={() => {
          qc.invalidateQueries({ queryKey: ["application", app.id] });
          qc.invalidateQueries({ queryKey: ["applications"] });
        }}
      />
      <StatusSheet app={app} open={withdrawing} onClose={() => setWithdrawing(false)} />
    </>
  );

  if (wide)
    return (
      // 넓은 화면은 본문 최대 폭(64rem)보다 넓게 쓴다: 남는 폭만큼 양쪽으로 넓힌다 (최대 84rem, 왼쪽 메뉴 14rem 기준).
      <div className="mx-[calc((min(100vw_-_18rem,64rem)_-_min(100vw_-_20rem,84rem))_/_2)]">
        {strip}
        <div className="grid grid-cols-[11rem_minmax(0,1fr)_18rem] items-start gap-x-6 2xl:grid-cols-[13rem_minmax(0,1fr)_22rem] 2xl:gap-x-8">
          <div className="sticky top-4 -mx-2 max-h-[calc(100dvh-2rem)] overflow-y-auto px-2">{list}</div>
          <section className="min-w-0" aria-label="쓰기">
            {parts ? (
              <>
                {pinned}
                {parts.notices}
                {parts.editor}
              </>
            ) : (
              centerEmpty
            )}
          </section>
          <aside className="sticky top-4 -mx-2 max-h-[calc(100dvh-2rem)] min-w-0 overflow-y-auto px-2 pb-4">
            <h2 className="sr-only">쓸 경험·검사·회사·기록</h2>
            <Tabs<Tab>
              value={tab}
              onChange={setTab}
              items={[
                { value: "prep", label: "쓸 경험" },
                { value: "checks", label: warnings ? `검사 ${warnings}` : "검사" },
                { value: "company", label: "회사" },
                { value: "history", label: "기록" },
              ]}
            />
            {parts || tab === "prep" || tab === "company" ? panelFor(tab) : centerEmpty}
          </aside>
        </div>
        {sheets}
      </div>
    );

  // ---- 좁은 화면: 단계
  const go = (s: Step) => setStep(s);
  const openChecks = () => {
    setSheetTab("checks");
    setSheet(true);
  };
  return (
    <div>
      {strip}
      <StepNav step={step} hasDoc={!!parts} onStep={go} onChecks={openChecks} warnings={warnings} />
      <div className="min-h-[40dvh]">
        {step === "pick" || !parts ? (
          step === "pick" ? (
            list
          ) : (
            centerEmpty
          )
        ) : step === "prep" ? (
          <section aria-label="쓸 경험">
            <QuestionHead doc={doc} />
            <h3 className="sr-only">쓸 경험</h3>
            {prepPanel}
          </section>
        ) : (
          <section aria-label="쓰기">
            {pinned}
            {parts.notices}
            {parts.editor}
          </section>
        )}
      </div>

      {/* 아래에 붙는 버튼 줄: 좁은 화면 아래 메뉴(3.5rem) 위에 */}
      {step !== "pick" && parts ? (
      <div className="sticky bottom-[calc(3.5rem+env(safe-area-inset-bottom))] z-20 -mx-4 mt-6 flex items-center gap-2 border-t border-line bg-bg px-4 py-2 md:bottom-0 md:-mx-8 md:px-8">
        {step === "prep" ? (
          <>
            <Button onClick={() => go("pick")}>문항</Button>
            <Button variant="primary" className="ml-auto" onClick={() => go("write")}>
              쓰기로
            </Button>
          </>
        ) : (
          <>
            <Button onClick={() => go("prep")}>쓸 경험</Button>
            <Button onClick={openChecks}>{warnings ? `검사 ${warnings}` : "검사"}</Button>
            {parts.editable ? (
              <Button variant="primary" className="ml-auto" onClick={parts.onSave} disabled={!parts.canSave}>
                <Save size={14} />{" "}
                {parts.saving ? "저장하는 중" : parts.dirty || !parts.saved ? "저장" : "저장됨"}
              </Button>
            ) : null}
          </>
        )}
      </div>
      ) : null}

      <Sheet open={sheet} onOpenChange={setSheet} title="검사" description="저장한 글을 봅니다. 경고만 하고 글을 바꾸지 않습니다.">
        <Tabs<Exclude<Tab, "prep">>
          value={sheetTab}
          onChange={setSheetTab}
          items={[
            { value: "checks", label: warnings ? `검사 ${warnings}` : "검사" },
            { value: "company", label: "회사" },
            { value: "history", label: "기록" },
          ]}
        />
        {panelFor(sheetTab)}
      </Sheet>
      {sheets}
    </div>
  );
}

/** 맨 위 띠: 회사·직무, D-day, 전형 단계, 제출본 고정. 철회는 ⋯ 안에. */
function Strip({
  app,
  onSubmit,
  onAdd,
  onWithdraw,
}: {
  app: Application;
  onSubmit: () => void;
  onAdd: () => void;
  onWithdraw: () => void;
}) {
  const company = app.posting.company as string | undefined;
  const jobTitle = app.posting.title as string | undefined;
  const deadline = app.posting.deadline as string | undefined;
  const dday = ddayLabel(deadline);
  const left = daysLeft(deadline);
  const soon = left !== null && left >= 0 && left <= 3;
  return (
    <>
      <PageHeader
        back={{ to: "/applications", label: "지원" }}
        title={company && jobTitle ? `${company} · ${jobTitle}` : app.title}
        subtitle={
          <span className="flex flex-wrap items-center gap-x-1.5 tabular-nums">
            {dday ? <span className={cn(soon && "font-semibold text-fg")}>{dday}</span> : null}
            {deadline ? <span>{dday ? "· " : ""}마감 {formatDeadline(deadline)}</span> : null}
            <span>
              {dday || deadline ? "· " : ""}
              {APP_STATUS[app.status].label}
            </span>
            <span aria-hidden>·</span>
            <Link
              to="/postings/$postingId"
              params={{ postingId: app.posting_id }}
              className="inline-flex min-h-9 items-center underline underline-offset-2 hover:text-fg sm:min-h-6"
            >
              공고 보기
            </Link>
          </span>
        }
        actions={
          <div className="flex items-center gap-2">
            <Button size="sm" variant="primary" onClick={onSubmit}>
              <Lock size={14} /> 제출본 고정
            </Button>
            <MoreMenu
              label="지원 메뉴"
              items={[
                { label: "문서 추가", onSelect: onAdd },
                { label: "지원 철회", onSelect: onWithdraw, danger: true },
              ]}
            />
          </div>
        }
      />
      {app.posting_pending_changes.length ? (
        <p role="status" className="-mt-4 mb-6 rounded-md border border-line px-3 py-2.5 text-[13px]">
          <span className="font-medium">공고에 확인하지 않은 변경이 있습니다:</span>{" "}
          {app.posting_pending_changes.map((f) => FIELD_LABEL[f as keyof typeof FIELD_LABEL] ?? f).join(", ")}.{" "}
          <Link to="/postings/$postingId" params={{ postingId: app.posting_id }} className="underline underline-offset-2">
            공고에서 확인
          </Link>
        </p>
      ) : null}
    </>
  );
}

function rowText(d: DocumentSummary) {
  if (d.question_prompt) return d.question_prompt;
  return d.title;
}

function countText(d: DocumentSummary) {
  const chars = d.counts?.chars ?? 0;
  const limit = d.char_limit ?? d.counts?.char_limit ?? null;
  return limit ? `${int(chars)}/${int(limit)}` : `${int(chars)}자`;
}

/** 왼쪽 목록: 문항 앞부분 · 238/700 · 안 씀/씀/경고. 이력서·포트폴리오도 같은 목록에. */
function DocList({
  app,
  checks,
  prep,
  selected,
  onAdd,
}: {
  app: Application;
  checks: ApplicationChecks | undefined;
  prep: ApplicationPrep | undefined;
  selected: string | null;
  onAdd: () => void;
}) {
  const byDoc = new Map(checks?.documents.map((d) => [d.document_id, d]) ?? []);
  const overlapDocs = new Set((prep?.overlaps ?? []).flatMap((o) => o.document_ids));
  return (
    <nav aria-label="문항·문서">
      <h2 className="mb-2 text-xs font-medium text-muted">문항·문서</h2>
      {app.documents.length ? (
        <ul className="divide-y divide-line border-y border-line">
          {app.documents.map((d) => {
            const unwritten = d.current_revision === 0 || !d.counts?.chars_with_spaces;
            const over = !!(d.counts?.over_char_limit || d.counts?.over_byte_limit);
            const flags = summaryFlags(byDoc.get(d.id)?.summary);
            const danger = over || flags.some((f) => f.tone === "danger");
            const warn = danger || flags.length > 0 || overlapDocs.has(d.id);
            const state = unwritten ? "안 씀" : warn ? "경고" : "씀";
            const on = d.id === selected;
            const isQuestion = d.kind === "question" || !!d.question_prompt;
            return (
              <li key={d.id}>
                <Link
                  to="/applications/$applicationId/documents/$documentId"
                  params={{ applicationId: app.id, documentId: d.id }}
                  aria-current={on ? "page" : undefined}
                  className={cn(
                    "-mx-2 flex min-h-9 flex-col rounded-md px-2 py-2.5 transition-colors duration-150 hover:bg-subtle/60",
                    on && "bg-subtle",
                  )}
                >
                  {!isQuestion ? <span className="text-xs text-muted">{DOC_KIND[d.kind] ?? d.kind}</span> : null}
                  <span className={cn("line-clamp-2 text-[13px] leading-5", on && "font-medium")}>{rowText(d)}</span>
                  <span className="mt-0.5 flex gap-2 text-xs text-muted tabular-nums">
                    <span>{countText(d)}</span>
                    <span
                      className={cn(
                        state === "경고" && (danger ? "font-medium text-danger" : "font-medium text-fg"),
                        state === "안 씀" && "text-muted",
                      )}
                    >
                      {state}
                    </span>
                  </span>
                </Link>
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="border-y border-line py-3 text-[13px] text-muted">문서가 없습니다.</p>
      )}
      <Button size="sm" variant="ghost" className="-ml-2.5 mt-2" onClick={onAdd}>
        <Plus size={14} /> 문서 추가
      </Button>
    </nav>
  );
}

function QuestionHead({
  doc,
}: {
  doc: Pick<DocumentSummary, "kind" | "question_index" | "question_prompt" | "title"> | undefined;
}) {
  if (!doc) return null;
  const isQuestion = doc.kind === "question" || !!doc.question_prompt;
  return (
    <div className="mb-4">
      <p className="text-xs text-muted">
        {isQuestion ? (doc.question_index !== null ? `문항 ${doc.question_index + 1}` : "문항") : DOC_KIND[doc.kind]}
      </p>
      <h2 className="text-[15px] leading-6 font-medium">{doc.question_prompt ?? doc.title}</h2>
    </div>
  );
}

/** 가운데 위에 고정: 문항과 글자 수, 저장. */
function Pinned({ parts, doc, sticky }: { parts: EditorParts; doc: DocumentSummary | undefined; sticky: boolean }) {
  const d = parts.doc;
  return (
    <div className={cn("mb-4 border-b border-line bg-bg pb-3", sticky && "sticky top-0 z-10 pt-1")}>
      <QuestionHead doc={doc ?? d} />
      <div className="-mt-2 flex items-start gap-2">
        <div className="min-w-0 flex-1">{parts.countBar}</div>
        <span className="hidden pt-2 text-xs text-muted sm:inline" aria-live="polite">
          {parts.dirty ? <span className="font-medium text-fg">저장 안 함</span> : parts.saved ? "저장됨" : "저장 전"}
        </span>
        {parts.editable && sticky ? (
          <Button size="sm" onClick={parts.onSave} disabled={!parts.canSave}>
            <Save size={14} /> {parts.saving ? "저장하는 중" : "저장"}
          </Button>
        ) : null}
        <MoreMenu
          label="문서 메뉴"
          items={[
            { label: "내보내기", onSelect: parts.openExport, disabled: !d.current_revision },
            ...(parts.editable ? [{ label: "제한·세는 기준", onSelect: parts.openSettings }] : []),
          ]}
        />
      </div>
    </div>
  );
}

const STEPS: { value: Step | "check"; label: string }[] = [
  { value: "pick", label: "문항 고르기" },
  { value: "prep", label: "쓸 경험" },
  { value: "write", label: "쓰기" },
  { value: "check", label: "검사" },
];

function StepNav({
  step,
  hasDoc,
  onStep,
  onChecks,
  warnings,
}: {
  step: Step;
  hasDoc: boolean;
  onStep: (s: Step) => void;
  onChecks: () => void;
  warnings: number;
}): ReactNode {
  return (
    <nav aria-label="쓰는 단계" className="mb-5">
      <ol className="grid grid-cols-4 border-b border-line text-[13px]">
        {STEPS.map((s, i) => {
          const current = s.value === step;
          const disabled = s.value !== "pick" && !hasDoc;
          return (
            <li key={s.value}>
              <button
                type="button"
                disabled={disabled}
                aria-current={current ? "step" : undefined}
                onClick={() => (s.value === "check" ? onChecks() : onStep(s.value))}
                className={cn(
                  "-mb-px flex min-h-11 w-full flex-col items-center justify-center border-b-2 px-1 py-1.5 leading-4 disabled:opacity-40",
                  current ? "border-fg font-medium text-fg" : "border-transparent text-muted",
                )}
              >
                <span className="text-xs tabular-nums">{i + 1}</span>
                <span>
                  {s.label}
                  {s.value === "check" && warnings ? ` ${warnings}` : ""}
                </span>
              </button>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
