import type {
  Application,
  ApplicationChecks,
  DocumentKind,
  DocumentSummary,
  RequirementVisibility,
  Submission,
  TextCounts,
} from "@career-brain/client";
import {
  Badge,
  Button,
  ConfirmDialog,
  EmptyState,
  Input,
  Label,
  Section,
  Select,
  Sheet,
  StatusBadge,
  Textarea,
} from "@career-brain/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getRouteApi, Link } from "@tanstack/react-router";
import { ChevronRight, FileText, Lock, Plus } from "lucide-react";
import { useState } from "react";
import { ErrorText, List, Loaded, PageHeader } from "../components/common";
import { DdayBadge } from "../components/DdayBadge";
import { api, ApiError } from "../lib/api";
import { formatDateTime, formatDeadline, int } from "../lib/format";
import { APP_STATUS, DOC_KIND, FINDING, FINDING_GROUP, FIELD_LABEL, REQ_FIELD, VISIBILITY } from "../lib/labels";

const route = getRouteApi("/applications/$applicationId");

export function countLine(c: TextCounts | null | undefined) {
  if (!c) return "아직 안 씀";
  const parts = [
    `${int(c.chars)}${c.char_limit ? ` / ${int(c.char_limit)}` : ""}자`,
    `${int(c.bytes)}${c.byte_limit ? ` / ${int(c.byte_limit)}` : ""}B`,
  ];
  return parts.join(" · ");
}

export function ApplicationPage() {
  const { applicationId } = route.useParams();
  const q = useQuery({ queryKey: ["application", applicationId], queryFn: () => api.application(applicationId) });
  return <Loaded query={q}>{(app) => <ApplicationView app={app} />}</Loaded>;
}

function ApplicationView({ app }: { app: Application }) {
  const qc = useQueryClient();
  const checks = useQuery({
    queryKey: ["application-checks", app.id],
    queryFn: () => api.applicationChecks(app.id),
  });
  const [adding, setAdding] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [statusOpen, setStatusOpen] = useState(false);
  const st = APP_STATUS[app.status];
  const draft = app.status === "draft";
  const company = app.posting.company as string | undefined;
  const jobTitle = app.posting.title as string | undefined;
  const deadline = app.posting.deadline as string | undefined;

  return (
    <div>
      <PageHeader
        back={{ to: "/applications", label: "지원 현황" }}
        title={app.title}
        subtitle={
          <span className="flex flex-wrap items-center gap-1.5">
            <Badge tone={st.tone}>{st.label}</Badge>
            {draft ? <DdayBadge deadline={deadline} /> : null}
            {deadline ? <span>마감 {formatDeadline(deadline)}</span> : null}
            <Link to="/postings/$postingId" params={{ postingId: app.posting_id }} className="hover:text-fg">
              공고 보기
            </Link>
          </span>
        }
        actions={
          <>
            {app.status !== "rejected" && app.status !== "withdrawn" ? (
              <Button size="sm" onClick={() => setStatusOpen(true)}>
                {draft ? "철회" : "결과 기록"}
              </Button>
            ) : null}
            {draft ? (
              <Button size="sm" variant="primary" onClick={() => setSubmitting(true)}>
                <Lock size={14} /> 제출본 고정
              </Button>
            ) : null}
          </>
        }
      />
      {app.result_note ? <p className="mb-3 text-[13px] text-muted">메모: {app.result_note}</p> : null}
      {app.posting_pending_changes.length ? (
        <div className="mb-4 rounded-lg border border-line bg-warn-soft px-3 py-2.5 text-[13px] text-warn">
          공고에 확인하지 않은 변경이 있습니다:{" "}
          {app.posting_pending_changes.map((f) => FIELD_LABEL[f as keyof typeof FIELD_LABEL] ?? f).join(", ")}.{" "}
          <Link to="/postings/$postingId" params={{ postingId: app.posting_id }} className="underline">
            공고에서 확인
          </Link>
        </div>
      ) : null}

      {draft ? (
        <div className="grid grid-cols-1 gap-8 lg:grid-cols-[minmax(0,1fr)_minmax(0,360px)]">
          <Section
            title="문서"
            action={
              <Button size="sm" variant="ghost" onClick={() => setAdding(true)}>
                <Plus size={14} /> 문서 추가
              </Button>
            }
          >
            <DocumentList app={app} checks={checks.data} />
          </Section>
          <Section title="요건 가시성 (모든 문서)">
            <Loaded query={checks}>{(c) => <VisibilityTable rows={c.requirements} compact />}</Loaded>
          </Section>
        </div>
      ) : (
        <FrozenView app={app} />
      )}

      <AddDocumentSheet appId={app.id} open={adding} onClose={() => setAdding(false)} />
      <SubmitDialog
        app={app}
        checks={checks.data}
        open={submitting}
        onClose={() => setSubmitting(false)}
        onDone={() => {
          qc.invalidateQueries({ queryKey: ["application", app.id] });
          qc.invalidateQueries({ queryKey: ["applications"] });
        }}
      />
      <StatusSheet app={app} open={statusOpen} onClose={() => setStatusOpen(false)} />
    </div>
  );
}

function summaryBadges(summary: Record<string, number> | undefined) {
  if (!summary) return null;
  const groups: Record<string, number> = {};
  for (const [code, n] of Object.entries(summary)) {
    const g = FINDING[code]?.group ?? "info";
    if (g === "info") continue;
    groups[g] = (groups[g] ?? 0) + n;
  }
  return Object.entries(groups).map(([g, n]) => {
    const info = FINDING_GROUP[g as keyof typeof FINDING_GROUP];
    return (
      <Badge key={g} tone={info.tone}>
        {info.label} {n}
      </Badge>
    );
  });
}

function DocumentList({ app, checks }: { app: Application; checks?: ApplicationChecks }) {
  if (!app.documents.length)
    return <EmptyState>문서가 없습니다. 이력서·자기소개서를 추가하세요.</EmptyState>;
  const byDoc = new Map(checks?.documents.map((d) => [d.document_id, d]) ?? []);
  return (
    <List>
      {app.documents.map((d) => (
        <DocumentRow key={d.id} appId={app.id} d={d} summary={byDoc.get(d.id)?.summary} />
      ))}
    </List>
  );
}

function DocumentRow({ appId, d, summary }: { appId: string; d: DocumentSummary; summary?: Record<string, number> }) {
  const unwritten = d.current_revision === 0 || !d.counts?.chars_with_spaces;
  const over = d.counts?.over_char_limit || d.counts?.over_byte_limit;
  return (
    <li>
      <Link
        to="/applications/$applicationId/documents/$documentId"
        params={{ applicationId: appId, documentId: d.id }}
        className="flex items-start gap-3 px-3 py-3 hover:bg-subtle/60"
      >
        <FileText size={16} className="mt-0.5 shrink-0 text-muted" />
        <div className="min-w-0 flex-1">
          <div className="text-sm font-medium">
            {d.question_index !== null ? `${d.question_index + 1}. ` : ""}
            {d.title}
          </div>
          {d.question_prompt && d.question_prompt !== d.title ? (
            <div className="line-clamp-2 text-xs text-muted">{d.question_prompt}</div>
          ) : null}
          <div className="mt-1 flex flex-wrap items-center gap-1 text-xs text-muted">
            <span>{DOC_KIND[d.kind] ?? d.kind}</span>
            <span>· {countLine(d.counts)}</span>
            {d.current_revision ? <span>· r{d.current_revision}</span> : null}
          </div>
          <div className="mt-1 flex flex-wrap gap-1">
            {unwritten ? <Badge tone="warn">안 씀</Badge> : null}
            {over ? <Badge tone="danger">분량 초과</Badge> : null}
            {summaryBadges(summary)}
          </div>
        </div>
        <ChevronRight size={16} className="mt-0.5 shrink-0 text-muted" />
      </Link>
    </li>
  );
}

/** 요건 × 문서 × 브레인 표. 값은 "근거가 보이는가"이지 합격 가능성이 아니다. */
export function VisibilityTable({ rows, compact }: { rows: RequirementVisibility[]; compact?: boolean }) {
  if (!rows.length) return <EmptyState>공고에 자격·우대 요건이 없습니다.</EmptyState>;
  return (
    <div className="space-y-2">
      <ul className="divide-y divide-line rounded-lg border border-line bg-surface">
        {rows.map((r, i) => {
          const v = VISIBILITY[r.verdict] ?? { label: r.verdict, tone: "neutral" as const };
          return (
            <li key={i} className="px-3 py-2.5">
              <div className="flex items-start gap-2">
                <p className="min-w-0 flex-1 text-[13px] leading-5">{r.requirement}</p>
                <Badge tone={v.tone}>{v.label}</Badge>
              </div>
              <div className="mt-1 flex flex-wrap gap-x-2 gap-y-0.5 text-xs text-muted">
                <span>{REQ_FIELD[r.kind] ?? r.kind}</span>
                {r.brain_claim_ids.length ? <span>브레인 근거 {r.brain_claim_ids.length}</span> : <span>브레인 근거 없음</span>}
                {r.document_terms.length ? <span>문서: {r.document_terms.join(", ")}</span> : null}
                {r.verdict === "met_visible" ? <span>{r.linked ? "문단에 근거 연결됨" : "문단에 근거 연결 안 됨"}</span> : null}
              </div>
              {!compact || r.verdict !== "met_visible" ? <p className="mt-0.5 text-xs">{r.suggestion}</p> : null}
            </li>
          );
        })}
      </ul>
      <p className="text-xs text-muted">단어 일치로 본 판정입니다. 합격 가능성이 아니라 근거가 문서에 보이는지를 봅니다.</p>
    </div>
  );
}

const KINDS: DocumentKind[] = ["resume", "cover_letter", "portfolio", "question", "other"];

function AddDocumentSheet({ appId, open, onClose }: { appId: string; open: boolean; onClose: () => void }) {
  const qc = useQueryClient();
  const [form, setForm] = useState({ kind: "resume" as DocumentKind, title: "", prompt: "", limit: "" });
  const add = useMutation({
    mutationFn: () =>
      api.addDocument(appId, {
        kind: form.kind,
        title: form.title.trim() || DOC_KIND[form.kind],
        question_prompt: form.prompt.trim() || null,
        char_limit: form.limit ? Number(form.limit) : null,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["application", appId] });
      qc.invalidateQueries({ queryKey: ["applications"] });
      setForm({ kind: "resume", title: "", prompt: "", limit: "" });
      onClose();
    },
  });
  return (
    <Sheet open={open} onOpenChange={(o) => !o && onClose()} title="문서 추가">
      <form
        className="space-y-3"
        onSubmit={(e) => {
          e.preventDefault();
          add.mutate();
        }}
      >
        <div>
          <Label htmlFor="doc-kind">종류</Label>
          <Select id="doc-kind" value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value as DocumentKind })}>
            {KINDS.map((k) => (
              <option key={k} value={k}>
                {DOC_KIND[k]}
              </option>
            ))}
          </Select>
        </div>
        <div>
          <Label htmlFor="doc-title">제목</Label>
          <Input
            id="doc-title"
            value={form.title}
            maxLength={300}
            placeholder={DOC_KIND[form.kind]}
            onChange={(e) => setForm({ ...form, title: e.target.value })}
          />
        </div>
        <div>
          <Label htmlFor="doc-prompt">문항·안내 (선택)</Label>
          <Textarea
            id="doc-prompt"
            value={form.prompt}
            maxLength={2000}
            placeholder="예: 협업 경험을 작성해 주세요. (공백 제외 700자)"
            onChange={(e) => setForm({ ...form, prompt: e.target.value })}
          />
          <p className="mt-1 text-xs text-muted">"공백 제외", "1000byte", "한글 2byte" 같은 문구에서 세는 기준을 읽습니다.</p>
        </div>
        <div>
          <Label htmlFor="doc-limit">글자 제한 (선택)</Label>
          <Input
            id="doc-limit"
            type="number"
            inputMode="numeric"
            min={1}
            value={form.limit}
            onChange={(e) => setForm({ ...form, limit: e.target.value })}
          />
        </div>
        <ErrorText error={add.error} />
        <Button type="submit" variant="primary" disabled={add.isPending}>
          추가
        </Button>
      </form>
    </Sheet>
  );
}

function SubmitDialog({
  app,
  checks,
  open,
  onClose,
  onDone,
}: {
  app: Application;
  checks?: ApplicationChecks;
  open: boolean;
  onClose: () => void;
  onDone: () => void;
}) {
  const [note, setNote] = useState("");
  const submit = useMutation({
    mutationFn: () =>
      api.submitApplication(
        app.id,
        Object.fromEntries(app.documents.map((d) => [d.id, d.current_revision])),
        note.trim() || null,
      ),
    onSuccess: () => {
      onClose();
      onDone();
    },
  });
  const stale =
    submit.error instanceof ApiError && submit.error.status === 409 && typeof submit.error.detail === "object";
  const byDoc = new Map(checks?.documents.map((d) => [d.document_id, d]) ?? []);
  const filled = app.documents.filter((d) => d.current_revision > 0);
  const empty = app.documents.filter((d) => d.current_revision === 0);
  return (
    <ConfirmDialog
      open={open}
      onOpenChange={(o) => {
        if (!o) {
          submit.reset();
          onClose();
        }
      }}
      title="제출본 고정"
      description="지금 저장된 글을 그대로 고정합니다. 고정한 뒤에는 이 지원의 문서를 고칠 수 없고, 이후 경력·공고를 고쳐도 제출본은 바뀌지 않습니다."
      confirmLabel={submit.isPending ? "고정하는 중" : "확인한 내용으로 고정"}
      busy={submit.isPending}
      confirmDisabled={!filled.length}
      onConfirm={() => submit.mutate()}
    >
      <ul className="space-y-2">
        {filled.map((d) => (
          <li key={d.id} className="rounded-md border border-line px-2.5 py-2">
            <div className="flex items-start gap-2">
              <span className="min-w-0 flex-1 text-[13px] font-medium">{d.title}</span>
              <Badge tone="neutral">r{d.current_revision}</Badge>
            </div>
            <div className="mt-1 flex flex-wrap items-center gap-1 text-xs text-muted">
              <span>{countLine(d.counts)}</span>
              {d.counts?.over_char_limit || d.counts?.over_byte_limit ? <Badge tone="danger">분량 초과</Badge> : null}
              {summaryBadges(byDoc.get(d.id)?.summary)}
            </div>
          </li>
        ))}
      </ul>
      {empty.length ? (
        <p className="mt-2 text-xs text-warn">
          글이 없는 문서 {empty.length}개({empty.map((d) => d.title).join(", ")})는 제출본에 넣지 않습니다.
        </p>
      ) : null}
      {!filled.length ? <p className="mt-2 text-xs text-danger">저장한 글이 없어 고정할 수 없습니다.</p> : null}
      <div className="mt-3">
        <Label htmlFor="submit-note">메모 (선택)</Label>
        <Input id="submit-note" value={note} maxLength={2000} onChange={(e) => setNote(e.target.value)} placeholder="예: 채용 사이트에 붙여넣어 제출함" />
      </div>
      {stale ? (
        <p role="alert" className="mt-2 text-[13px] text-danger">
          확인하는 사이 문서가 바뀌었습니다. 창을 닫고 최신 글을 다시 확인하세요.
        </p>
      ) : (
        <ErrorText error={submit.error} className="mt-2" />
      )}
    </ConfirmDialog>
  );
}

function StatusSheet({ app, open, onClose }: { app: Application; open: boolean; onClose: () => void }) {
  const qc = useQueryClient();
  const options: ("passed" | "rejected" | "withdrawn")[] =
    app.status === "draft" ? ["withdrawn"] : app.status === "submitted" ? ["passed", "rejected", "withdrawn"] : ["rejected", "withdrawn"];
  const [value, setValue] = useState(options[0]!);
  const [note, setNote] = useState(app.result_note ?? "");
  const save = useMutation({
    mutationFn: () => api.setApplicationStatus(app.id, value, note.trim() || null),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["application", app.id] });
      qc.invalidateQueries({ queryKey: ["applications"] });
      onClose();
    },
  });
  return (
    <Sheet open={open} onOpenChange={(o) => !o && onClose()} title={app.status === "draft" ? "지원 철회" : "결과 기록"}>
      <div className="space-y-3">
        <div>
          <Label htmlFor="st">상태</Label>
          <Select id="st" value={value} onChange={(e) => setValue(e.target.value as typeof value)}>
            {options.map((o) => (
              <option key={o} value={o}>
                {APP_STATUS[o].label}
              </option>
            ))}
          </Select>
        </div>
        <div>
          <Label htmlFor="st-note">메모 (선택)</Label>
          <Textarea id="st-note" value={note} maxLength={2000} onChange={(e) => setNote(e.target.value)} />
        </div>
        <p className="text-xs text-muted">결과는 기록만 합니다. 표본이 작아 원인을 단정하지 않습니다.</p>
        <ErrorText error={save.error} />
        <Button variant="primary" onClick={() => save.mutate()} disabled={save.isPending}>
          저장
        </Button>
      </div>
    </Sheet>
  );
}

// ---------------------------------------------------------------- 제출본 (읽기 전용)

function FrozenView({ app }: { app: Application }) {
  const q = useQuery({
    queryKey: ["submission", app.id],
    queryFn: () => api.submission(app.id),
    enabled: !!app.submission_id,
    staleTime: Infinity,
  });
  if (!app.submission_id)
    return <EmptyState>제출본 없이 끝난 지원입니다 (제출 전 철회).</EmptyState>;
  return <Loaded query={q}>{(sub) => <SubmissionBody sub={sub} />}</Loaded>;
}

function SubmissionBody({ sub }: { sub: Submission }) {
  return (
    <div className="space-y-6">
      <div className="rounded-lg border border-line bg-subtle/60 px-3 py-2.5 text-[13px]">
        <p className="flex flex-wrap items-center gap-1.5 font-medium">
          <Lock size={14} /> 제출본 · {formatDateTime(sub.submitted_at)} 고정
        </p>
        <p className="mt-1 text-xs break-all text-muted">내용 해시 {sub.content_hash}</p>
        {sub.note ? <p className="mt-1 text-xs">메모: {sub.note}</p> : null}
        {sub.posting.pending_changes?.length ? (
          <p className="mt-1 text-xs text-warn">고정 당시 확인하지 않은 공고 변경: {sub.posting.pending_changes.join(", ")}</p>
        ) : null}
        <p className="mt-1 text-xs text-muted">이후 경력·공고를 고쳐도 이 내용은 바뀌지 않습니다.</p>
      </div>
      {sub.documents.map((d) => (
        <section key={d.document_id} className="space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-[15px] font-semibold">{d.title}</h2>
            <Badge tone="neutral">r{d.revision_no}</Badge>
            <span className="text-xs text-muted">{countLine(d.counts)}</span>
          </div>
          {d.question_prompt ? <p className="text-xs text-muted">{d.question_prompt}</p> : null}
          <article className="rounded-lg border border-line bg-surface p-4 text-base leading-8 whitespace-pre-wrap">
            {d.text}
          </article>
          {d.evidence.length ? (
            <details className="text-[13px]">
              <summary className="cursor-pointer text-muted">제출 당시 근거 {d.evidence.length}개</summary>
              <ul className="mt-2 space-y-1.5">
                {d.evidence.map((e) => (
                  <li key={e.claim_id} className="flex items-start gap-2 rounded-md border border-line px-2.5 py-1.5">
                    <span className="min-w-0 flex-1">
                      {e.statement}
                      <span className="block text-xs text-muted">{e.experience.title}</span>
                    </span>
                    <StatusBadge status={e.status} />
                  </li>
                ))}
              </ul>
            </details>
          ) : null}
        </section>
      ))}
      {sub.checks.requirements?.length ? (
        <Section title="고정 당시 요건 가시성">
          <VisibilityTable rows={sub.checks.requirements} compact />
        </Section>
      ) : null}
    </div>
  );
}
