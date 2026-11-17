import type {
  Application,
  ApplicationChecks,
  ApplicationStage,
  DocumentKind,
  DocumentSummary,
  RequirementVisibility,
  SensitiveUnacknowledgedDetail,
  StageKind,
  StageOutcome,
  Submission,
  TextCounts,
} from "@career-brain/client";
import {
  Button,
  cn,
  ConfirmDialog,
  EmptyState,
  Input,
  Label,
  Section,
  Select,
  Sheet,
  StatusBadge,
  Textarea,
  type BadgeTone,
} from "@career-brain/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getRouteApi, Link } from "@tanstack/react-router";
import { Copy, Download, Lock, Plus, ShieldAlert, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { ErrorText, List, Loaded, PageHeader } from "../components/common";
import { ExportSheet } from "../components/doc/ExportSheet";
import { api, ApiError } from "../lib/api";
import { daysLeft, ddayLabel, formatDateTime, formatDeadline, fromLocalInput, toLocalInput } from "../lib/dates";
import { int } from "../lib/format";
import {
  APP_STATUS,
  DOC_KIND,
  FINDING,
  FINDING_GROUP,
  FIELD_LABEL,
  REQ_FIELD,
  STAGE_KIND,
  STAGE_OUTCOME,
  VISIBILITY,
} from "../lib/labels";

const route = getRouteApi("/applications/$applicationId");

export function countLine(c: TextCounts | null | undefined) {
  if (!c) return "아직 안 씀";
  const parts = [
    `${int(c.chars)}${c.char_limit ? ` / ${int(c.char_limit)}` : ""}자`,
    `${int(c.bytes)}${c.byte_limit ? ` / ${int(c.byte_limit)}` : ""}B`,
  ];
  return parts.join(" · ");
}

/** 배지 대신 글자로 상태를 보인다: 위험은 빨강, 확인할 것은 진하게, 나머지는 흐리게. */
export function toneText(tone: BadgeTone) {
  if (tone === "danger") return "text-danger";
  if (tone === "warn" || tone === "accent") return "font-medium text-fg";
  return "text-muted";
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
  const dday = ddayLabel(deadline);
  const left = daysLeft(deadline);
  const soon = left !== null && left >= 0 && left <= 3;

  return (
    <div>
      <PageHeader
        back={{ to: "/applications", label: "지원 현황" }}
        title={app.title}
        subtitle={
          <span className="flex flex-wrap items-center gap-x-1.5 tabular-nums">
            <span className="font-medium text-fg">{st.label}</span>
            {draft && dday ? <span className={cn(soon && "font-semibold text-fg")}>· {dday}</span> : null}
            {deadline ? <span>· 마감 {formatDeadline(deadline)}</span> : null}
            <span aria-hidden>·</span>
            <Link
              to="/postings/$postingId"
              params={{ postingId: app.posting_id }}
              className="underline underline-offset-2 hover:text-fg"
            >
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
      {app.result_note ? <p className="mb-4 text-[13px] text-muted">메모: {app.result_note}</p> : null}
      {app.posting_pending_changes.length ? (
        <p role="status" className="mb-6 rounded-md border border-line px-3 py-2.5 text-[13px]">
          <span className="font-medium">공고에 확인하지 않은 변경이 있습니다:</span>{" "}
          {app.posting_pending_changes.map((f) => FIELD_LABEL[f as keyof typeof FIELD_LABEL] ?? f).join(", ")}.{" "}
          <Link
            to="/postings/$postingId"
            params={{ postingId: app.posting_id }}
            className="underline underline-offset-2"
          >
            공고에서 확인
          </Link>
        </p>
      ) : null}

      {draft ? (
        <div className="grid grid-cols-1 gap-y-10 lg:grid-cols-[minmax(0,1fr)_minmax(0,360px)] lg:gap-x-14">
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

/** 검사 요약(분량·위험·근거…)을 글자로. 참고(info)는 뺀다. */
function summaryFlags(summary: Record<string, number> | undefined) {
  if (!summary) return [];
  const groups: Record<string, number> = {};
  for (const [code, n] of Object.entries(summary)) {
    const g = FINDING[code]?.group ?? "info";
    if (g === "info") continue;
    groups[g] = (groups[g] ?? 0) + n;
  }
  return Object.entries(groups).map(([g, n]) => {
    const info = FINDING_GROUP[g as keyof typeof FINDING_GROUP];
    return { key: g, label: `${info.label} ${n}`, tone: info.tone };
  });
}

function Flags({ items }: { items: { key: string; label: string; tone: BadgeTone }[] }) {
  if (!items.length) return null;
  return (
    <span className="flex flex-wrap justify-end gap-x-2 text-[13px] tabular-nums">
      {items.map((f) => (
        <span key={f.key} className={toneText(f.tone)}>
          {f.label}
        </span>
      ))}
    </span>
  );
}

function DocumentList({ app, checks }: { app: Application; checks?: ApplicationChecks }) {
  if (!app.documents.length) return <EmptyState>문서가 없습니다. 이력서·자기소개서를 추가하세요.</EmptyState>;
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
  const flags = [
    ...(unwritten ? [{ key: "unwritten", label: "안 씀", tone: "warn" as const }] : []),
    ...(over ? [{ key: "over", label: "분량 초과", tone: "danger" as const }] : []),
    ...summaryFlags(summary),
  ];
  const meta = [
    DOC_KIND[d.kind] ?? d.kind,
    countLine(d.counts),
    d.current_revision ? `r${d.current_revision}` : null,
    d.source ? `공용 문서 r${d.source.revision_no ?? "?"}에서 복사` : null,
  ].filter(Boolean);
  return (
    <li>
      <Link
        to="/applications/$applicationId/documents/$documentId"
        params={{ applicationId: appId, documentId: d.id }}
        className="-mx-2 flex items-start gap-4 rounded-md px-2 py-3 transition-colors duration-150 hover:bg-subtle/60"
      >
        <div className="min-w-0 flex-1">
          <div className="text-sm font-medium">
            {d.question_index !== null ? `${d.question_index + 1}. ` : ""}
            {d.title}
          </div>
          {d.question_prompt && d.question_prompt !== d.title ? (
            <div className="mt-0.5 line-clamp-2 text-[13px] text-muted">{d.question_prompt}</div>
          ) : null}
          <div className="mt-0.5 text-[13px] text-muted tabular-nums">{meta.join(" · ")}</div>
        </div>
        {flags.length ? (
          <div className="max-w-[40%] shrink-0 pt-0.5 text-right">
            <Flags items={flags} />
          </div>
        ) : null}
      </Link>
    </li>
  );
}

/** 요건 × 문서 × 브레인 표. 값은 "근거가 보이는가"이지 합격 가능성이 아니다. */
export function VisibilityTable({ rows, compact }: { rows: RequirementVisibility[]; compact?: boolean }) {
  if (!rows.length) return <EmptyState>공고에 자격·우대 요건이 없습니다.</EmptyState>;
  return (
    <div>
      <ul className="divide-y divide-line border-t border-line">
        {rows.map((r, i) => {
          const v = VISIBILITY[r.verdict] ?? { label: r.verdict, tone: "neutral" as const };
          return (
            <li key={i} className="py-2.5">
              <div className="flex items-start gap-3">
                <p className="min-w-0 flex-1 text-[13px] leading-5">{r.requirement}</p>
                <span className={cn("shrink-0 text-[13px]", toneText(v.tone))}>{v.label}</span>
              </div>
              <div className="mt-1 flex flex-wrap gap-x-2 gap-y-0.5 text-xs text-muted">
                <span>{REQ_FIELD[r.kind] ?? r.kind}</span>
                {r.brain_claim_ids.length ? (
                  <span>브레인 근거 {r.brain_claim_ids.length}</span>
                ) : (
                  <span>브레인 근거 없음</span>
                )}
                {r.document_terms.length ? <span>문서: {r.document_terms.join(", ")}</span> : null}
                {r.verdict === "met_visible" ? (
                  <span>{r.linked ? "문단에 근거 연결됨" : "문단에 근거 연결 안 됨"}</span>
                ) : null}
              </div>
              {!compact || r.verdict !== "met_visible" ? <p className="mt-0.5 text-xs">{r.suggestion}</p> : null}
            </li>
          );
        })}
      </ul>
      <p className="mt-2 text-xs text-muted">
        단어 일치로 본 판정입니다. 합격 가능성이 아니라 근거가 문서에 보이는지를 봅니다.
      </p>
    </div>
  );
}

const KINDS: DocumentKind[] = ["resume", "cover_letter", "portfolio", "question", "other"];

function AddDocumentSheet({ appId, open, onClose }: { appId: string; open: boolean; onClose: () => void }) {
  const qc = useQueryClient();
  const [mode, setMode] = useState<"new" | "copy">("new");
  const [form, setForm] = useState({ kind: "resume" as DocumentKind, title: "", prompt: "", limit: "" });
  const shared = useQuery({
    queryKey: ["shared-documents"],
    queryFn: api.sharedDocuments,
    enabled: open && mode === "copy",
  });
  const copy = useMutation({
    mutationFn: (sourceId: string) => api.copyDocument(appId, { source_document_id: sourceId }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["application", appId] });
      qc.invalidateQueries({ queryKey: ["applications"] });
      qc.invalidateQueries({ queryKey: ["shared-documents"] });
      onClose();
    },
  });
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
      <div className="mb-4 inline-flex rounded-md border border-line p-0.5" role="radiogroup" aria-label="추가 방법">
        {(
          [
            ["new", "새로 쓰기"],
            ["copy", "공용 문서에서 복사"],
          ] as const
        ).map(([v, label]) => (
          <button
            key={v}
            role="radio"
            aria-checked={mode === v}
            onClick={() => setMode(v)}
            className={
              mode === v
                ? "h-9 rounded bg-accent px-3 text-[13px] font-medium text-accent-fg"
                : "h-9 rounded px-3 text-[13px] text-muted hover:text-fg"
            }
          >
            {label}
          </button>
        ))}
      </div>
      {mode === "copy" ? (
        <div className="space-y-3">
          <p className="text-xs text-muted">
            공용 문서의 최신 revision 을 이 지원의 새 문서로 복사합니다. 근거 연결도 같이 옮기고, 출처(문서·revision)를
            남깁니다. 복사한 뒤 원본을 수정해도 이 문서는 바뀌지 않습니다.
          </p>
          <Loaded query={shared}>
            {(docs) =>
              docs.filter((d) => d.current_revision > 0).length ? (
                <ul className="divide-y divide-line border-y border-line">
                  {docs
                    .filter((d) => d.current_revision > 0)
                    .map((d) => (
                      <li key={d.id} className="flex items-center gap-3 py-2.5">
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-[13px] font-medium">{d.title}</span>
                          <span className="text-xs text-muted tabular-nums">
                            {DOC_KIND[d.kind]} · r{d.current_revision} · {countLine(d.counts)}
                          </span>
                        </span>
                        <Button size="sm" onClick={() => copy.mutate(d.id)} disabled={copy.isPending}>
                          <Copy size={14} /> 복사
                        </Button>
                      </li>
                    ))}
                </ul>
              ) : (
                <EmptyState>
                  저장한 공용 문서가 없습니다.{" "}
                  <Link to="/documents" className="text-fg underline underline-offset-2">
                    공용 문서 만들기
                  </Link>
                </EmptyState>
              )
            }
          </Loaded>
          <ErrorText error={copy.error} />
        </div>
      ) : (
        <form
          className="space-y-3"
          onSubmit={(e) => {
            e.preventDefault();
            add.mutate();
          }}
        >
          <div>
            <Label htmlFor="doc-kind">종류</Label>
            <Select
              id="doc-kind"
              value={form.kind}
              onChange={(e) => setForm({ ...form, kind: e.target.value as DocumentKind })}
            >
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
            <p className="mt-1 text-xs text-muted">
              "공백 제외", "1000byte", "한글 2byte" 같은 문구에서 세는 기준을 읽습니다.
            </p>
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
      )}
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
  const [ack, setAck] = useState(false);
  // 제출 직전 재검사: 주민등록번호·본인 것이 아닌 연락처·대외비 표시. 지우지 않고, 확인을 받는다.
  const sensitive = useQuery({
    queryKey: ["sensitive-check", app.id, app.documents.map((d) => `${d.id}:${d.current_revision}`).join(",")],
    queryFn: () => api.sensitiveCheck(app.id),
    enabled: open,
    staleTime: 0,
  });
  useEffect(() => setAck(false), [open, sensitive.data?.sensitive_hash]);
  const hash = sensitive.data?.sensitive_hash ?? null;
  const submit = useMutation({
    mutationFn: () =>
      api.submitApplication(
        app.id,
        Object.fromEntries(app.documents.map((d) => [d.id, d.current_revision])),
        note.trim() || null,
        ack ? hash : null,
      ),
    onSuccess: () => {
      onClose();
      onDone();
    },
    onError: (err) => {
      // 확인한 뒤 글이 바뀌어 경고가 달라졌으면 새 경고를 다시 보여 준다
      if (
        err instanceof ApiError &&
        (err.detail as SensitiveUnacknowledgedDetail | null)?.code === "sensitive_unacknowledged"
      )
        void sensitive.refetch();
    },
  });
  const sensitiveError =
    submit.error instanceof ApiError &&
    (submit.error.detail as SensitiveUnacknowledgedDetail | null)?.code === "sensitive_unacknowledged";
  const stale =
    !sensitiveError &&
    submit.error instanceof ApiError &&
    submit.error.status === 409 &&
    typeof submit.error.detail === "object";
  const needsAck = !!sensitive.data?.requires_ack;
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
      description="지금 저장된 글을 그대로 고정합니다. 고정한 뒤에는 이 지원의 문서를 수정할 수 없고, 이후 경력·공고를 수정해도 제출본은 바뀌지 않습니다."
      confirmLabel={submit.isPending ? "고정하는 중" : "확인한 내용으로 고정"}
      busy={submit.isPending}
      confirmDisabled={!filled.length || sensitive.isPending || sensitive.isError || (needsAck && !ack)}
      onConfirm={() => submit.mutate()}
    >
      <ul className="divide-y divide-line border-y border-line">
        {filled.map((d) => (
          <li key={d.id} className="flex items-start gap-3 py-2">
            <div className="min-w-0 flex-1">
              <div className="text-[13px] font-medium">{d.title}</div>
              <div className="text-xs text-muted tabular-nums">
                r{d.current_revision} · {countLine(d.counts)}
              </div>
            </div>
            <div className="max-w-[45%] shrink-0 text-right">
              <Flags
                items={[
                  ...(d.counts?.over_char_limit || d.counts?.over_byte_limit
                    ? [{ key: "over", label: "분량 초과", tone: "danger" as const }]
                    : []),
                  ...summaryFlags(byDoc.get(d.id)?.summary),
                ]}
              />
            </div>
          </li>
        ))}
      </ul>
      {empty.length ? (
        <p className="mt-2 text-xs font-medium">
          글이 없는 문서 {empty.length}개({empty.map((d) => d.title).join(", ")})는 제출본에 넣지 않습니다.
        </p>
      ) : null}
      {!filled.length ? <p className="mt-2 text-xs text-danger">저장한 글이 없어 고정할 수 없습니다.</p> : null}
      <ErrorText error={sensitive.error} className="mt-2" />
      {needsAck && sensitive.data ? (
        <div className="mt-3 rounded-md border border-danger/40 bg-danger-soft px-3 py-2.5 text-[13px]">
          <p className="flex items-center gap-1.5 font-semibold text-danger">
            <ShieldAlert size={15} /> 민감 정보 경고 {sensitive.data.findings.length}개
          </p>
          <ul className="mt-1.5 space-y-1">
            {sensitive.data.findings.map((f, i) => (
              <li key={`${f.document_id}-${f.code}-${i}`} className="text-fg">
                <span className="font-medium text-danger">{FINDING[f.code]?.label ?? f.code}</span>{" "}
                <span className="text-xs text-muted">{f.title}</span>
                <span className="block text-xs">{f.message}</span>
              </li>
            ))}
          </ul>
          <p className="mt-1.5 text-xs text-muted">
            아무것도 자동으로 지우지 않습니다. 수정하려면 창을 닫고 문서를 수정한 뒤 저장하세요. 본인 연락처는 설정에 적어
            두면 경고하지 않습니다.
          </p>
          <label className="mt-2 flex min-h-9 items-start gap-2 py-1">
            <input type="checkbox" className="mt-1" checked={ack} onChange={(e) => setAck(e.target.checked)} />
            <span>위 내용을 확인했고 이대로 제출본에 넣습니다.</span>
          </label>
        </div>
      ) : null}
      <div className="mt-3">
        <Label htmlFor="submit-note">메모 (선택)</Label>
        <Input
          id="submit-note"
          value={note}
          maxLength={2000}
          onChange={(e) => setNote(e.target.value)}
          placeholder="예: 채용 사이트에 붙여넣어 제출함"
        />
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
    app.status === "draft"
      ? ["withdrawn"]
      : app.status === "submitted"
        ? ["passed", "rejected", "withdrawn"]
        : ["rejected", "withdrawn"];
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
  const [exporting, setExporting] = useState(false);
  if (!app.submission_id) return <EmptyState>제출본 없이 끝난 지원입니다 (제출 전 철회).</EmptyState>;
  return (
    <div className="space-y-10">
      <StagesSection app={app} />
      <Section
        title="제출본"
        action={
          <Button size="sm" variant="ghost" onClick={() => setExporting(true)}>
            <Download size={14} /> 내보내기
          </Button>
        }
      >
        <Loaded query={q}>{(sub) => <SubmissionBody sub={sub} />}</Loaded>
      </Section>
      <ExportSheet
        open={exporting}
        onClose={() => setExporting(false)}
        title="제출본 내보내기"
        description="고정된 제출본(모든 문서)을 한 파일로 만듭니다. 문서 내보내기와 같은 writer 를 쓰고, 만든 파일을 다시 읽어 숨김 글·조작 문구·민감 정보를 검사합니다. 경고만 합니다."
        queryKey={["submission-export-check", app.id, app.submission_id]}
        check={(format) => api.submissionExportCheck(app.id, format)}
        download={(format) => api.submissionExportFile(app.id, format)}
        label="제출본"
      />
    </div>
  );
}

// ---------------------------------------------------------------- 전형 단계 (제출본에 묶인 일정·결과)

const STAGE_KINDS: StageKind[] = ["document", "test", "assignment", "interview", "final", "other"];
const STAGE_OUTCOMES: StageOutcome[] = ["pending", "passed", "failed", "withdrawn", "cancelled"];

/**
 * 제출 뒤 전형 단계와 결과를 기록한다. 결과만 적고, 왜 떨어졌는지 같은 원인은 시스템이 판단하지 않는다.
 * 지원 상태(통과·불합격)는 단계 결과로 자동으로 바꾸지 않는다.
 */
function StagesSection({ app }: { app: Application }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["stages", app.id], queryFn: () => api.stages(app.id) });
  const [editing, setEditing] = useState<ApplicationStage | "new" | null>(null);
  return (
    <Section
      title="전형 단계"
      action={
        <Button size="sm" variant="ghost" onClick={() => setEditing("new")}>
          <Plus size={14} /> 단계 추가
        </Button>
      }
    >
      <Loaded query={q}>
        {(stages) =>
          stages.length ? (
            <ol className="divide-y divide-line border-t border-line">
              {stages.map((st) => {
                const o = STAGE_OUTCOME[st.outcome];
                const meta = [st.scheduled_at ? formatDateTime(st.scheduled_at) : null, st.note].filter(Boolean);
                return (
                  <li key={st.id}>
                    <button
                      type="button"
                      className="-mx-2 flex w-[calc(100%+1rem)] items-start gap-4 rounded-md px-2 py-3 text-left transition-colors duration-150 hover:bg-subtle/60"
                      onClick={() => setEditing(st)}
                    >
                      <span className="min-w-0 flex-1">
                        <span className="block text-sm font-medium">{st.name}</span>
                        {meta.length ? (
                          <span className="block text-[13px] text-muted tabular-nums">{meta.join(" · ")}</span>
                        ) : null}
                      </span>
                      <span className={cn("shrink-0 text-[13px]", toneText(o.tone))}>{o.label}</span>
                    </button>
                  </li>
                );
              })}
            </ol>
          ) : (
            <EmptyState>기록한 단계가 없습니다. 서류 결과·면접 일정 같은 것을 적어 둡니다.</EmptyState>
          )
        }
      </Loaded>
      <p className="mt-3 text-xs text-muted">
        결과만 기록합니다. 원인은 판단하지 않고, 지원 상태도 자동으로 바꾸지 않습니다.
      </p>
      <StageSheet
        appId={app.id}
        stage={editing}
        onClose={() => setEditing(null)}
        onSaved={() => qc.invalidateQueries({ queryKey: ["stages", app.id] })}
      />
    </Section>
  );
}

function StageSheet({
  appId,
  stage,
  onClose,
  onSaved,
}: {
  appId: string;
  stage: ApplicationStage | "new" | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const existing = stage && stage !== "new" ? stage : null;
  const [form, setForm] = useState({
    kind: "document" as StageKind,
    name: "",
    scheduled: "",
    outcome: "pending" as StageOutcome,
    note: "",
  });
  useEffect(() => {
    if (!stage) return;
    setForm(
      existing
        ? {
            kind: existing.kind,
            name: existing.name,
            scheduled: toLocalInput(existing.scheduled_at),
            outcome: existing.outcome,
            note: existing.note ?? "",
          }
        : { kind: "document", name: "", scheduled: "", outcome: "pending", note: "" },
    );
  }, [stage, existing]);
  const done = () => {
    onSaved();
    onClose();
  };
  const save = useMutation({
    mutationFn: () => {
      const body = {
        kind: form.kind,
        name: form.name.trim() || null,
        scheduled_at: fromLocalInput(form.scheduled),
        outcome: form.outcome,
        note: form.note.trim() || null,
      };
      return existing ? api.patchStage(appId, existing.id, body) : api.addStage(appId, body);
    },
    onSuccess: done,
  });
  const remove = useMutation({
    mutationFn: () => api.deleteStage(appId, existing!.id),
    onSuccess: done,
  });
  return (
    <Sheet open={!!stage} onOpenChange={(o) => !o && onClose()} title={existing ? "전형 단계" : "전형 단계 추가"}>
      <form
        className="space-y-3"
        onSubmit={(e) => {
          e.preventDefault();
          save.mutate();
        }}
      >
        <div className="grid grid-cols-2 gap-2">
          <div>
            <Label htmlFor="stg-kind">종류</Label>
            <Select
              id="stg-kind"
              value={form.kind}
              onChange={(e) => setForm({ ...form, kind: e.target.value as StageKind })}
            >
              {STAGE_KINDS.map((k) => (
                <option key={k} value={k}>
                  {STAGE_KIND[k]}
                </option>
              ))}
            </Select>
          </div>
          <div>
            <Label htmlFor="stg-outcome">결과</Label>
            <Select
              id="stg-outcome"
              value={form.outcome}
              onChange={(e) => setForm({ ...form, outcome: e.target.value as StageOutcome })}
            >
              {STAGE_OUTCOMES.map((o) => (
                <option key={o} value={o}>
                  {STAGE_OUTCOME[o].label}
                </option>
              ))}
            </Select>
          </div>
        </div>
        <div>
          <Label htmlFor="stg-name">이름 (선택)</Label>
          <Input
            id="stg-name"
            value={form.name}
            maxLength={200}
            placeholder={STAGE_KIND[form.kind]}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
          />
        </div>
        <div>
          <Label htmlFor="stg-at">일정 (선택)</Label>
          <Input
            id="stg-at"
            type="datetime-local"
            value={form.scheduled}
            onChange={(e) => setForm({ ...form, scheduled: e.target.value })}
          />
        </div>
        <div>
          <Label htmlFor="stg-note">메모 (선택)</Label>
          <Textarea
            id="stg-note"
            value={form.note}
            maxLength={2000}
            onChange={(e) => setForm({ ...form, note: e.target.value })}
          />
        </div>
        <ErrorText error={save.error ?? remove.error} />
        <div className="flex flex-wrap gap-2">
          <Button type="submit" variant="primary" disabled={save.isPending}>
            저장
          </Button>
          {existing ? (
            <Button variant="danger" onClick={() => remove.mutate()} disabled={remove.isPending}>
              <Trash2 size={14} /> 삭제
            </Button>
          ) : null}
        </div>
      </form>
    </Sheet>
  );
}

function SubmissionBody({ sub }: { sub: Submission }) {
  return (
    <div className="space-y-10">
      <div className="space-y-1 text-[13px] text-muted">
        <p className="flex flex-wrap items-center gap-1.5 font-medium text-fg tabular-nums">
          <Lock size={14} /> {formatDateTime(sub.submitted_at)} 고정
        </p>
        <p className="text-xs break-all">내용 해시 {sub.content_hash}</p>
        {sub.note ? <p className="text-fg">메모: {sub.note}</p> : null}
        {sub.posting.pending_changes?.length ? (
          <p className="font-medium text-fg">
            고정 당시 확인하지 않은 공고 변경: {sub.posting.pending_changes.join(", ")}
          </p>
        ) : null}
        <p>이후 경력·공고를 수정해도 이 내용은 바뀌지 않습니다.</p>
      </div>
      {sub.documents.map((d) => (
        <section key={d.document_id} className="space-y-3 border-t border-line pt-4">
          <div>
            <h3 className="text-sm font-semibold">{d.title}</h3>
            <p className="text-[13px] text-muted tabular-nums">
              r{d.revision_no} · {countLine(d.counts)}
            </p>
            {d.question_prompt ? <p className="mt-1 text-[13px] text-muted">{d.question_prompt}</p> : null}
          </div>
          <article className="text-base leading-8 whitespace-pre-wrap">{d.text}</article>
          {d.evidence.length ? (
            <details className="text-[13px]">
              <summary className="cursor-pointer py-1 text-muted hover:text-fg">
                제출 당시 근거 {d.evidence.length}개
              </summary>
              <ul className="mt-2 divide-y divide-line border-t border-line">
                {d.evidence.map((e) => (
                  <li key={e.claim_id} className="flex items-start gap-3 py-2">
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
