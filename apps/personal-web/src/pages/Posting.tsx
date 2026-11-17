import {
  POSTING_FIELDS,
  POSTING_LIST_FIELDS,
  type EssayQuestion,
  type FetchRun,
  type FieldEvidence,
  type Posting,
  type PostingField,
  type PostingValue,
  type PostingVersion,
} from "@career-brain/client";
import { Badge, Button, cn, Highlight, Input, Section, Sheet, Spinner, Textarea } from "@career-brain/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getRouteApi, Link } from "@tanstack/react-router";
import { Archive, ExternalLink, FileText, RefreshCw, Trash2, Plus } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { ErrorText, Loaded, PageHeader } from "../components/common";
import { DdayBadge } from "../components/DdayBadge";
import { ChoiceQuestions, CostConfirmation } from "../components/postings/PostingExtras";
import { useCreateApplication } from "./Applications";
import { api } from "../lib/api";
import { ddayLabel, formatDateTime, formatDeadline } from "../lib/dates";
import { hostOf } from "../lib/format";
import {
  ADAPTER_LABEL,
  FETCH_ERROR,
  FETCH_STATUS,
  FIELD_LABEL,
  METHOD_LABEL,
  noteLabel,
  POSTING_STATUS,
} from "../lib/labels";

const route = getRouteApi("/postings/$postingId");
const REVIEW_FIELDS: PostingField[] = ["title", "company", "deadline"];
const isList = (f: PostingField) => POSTING_LIST_FIELDS.includes(f);

// ---------------------------------------------------------------- 편집 상태

type Draft = Record<PostingField, string | EssayQuestion[]>;

function toDraft(field: PostingField, value: PostingValue | undefined): string | EssayQuestion[] {
  if (field === "questions") return Array.isArray(value) ? (value as EssayQuestion[]).map((q) => ({ ...q })) : [];
  if (isList(field)) return Array.isArray(value) ? (value as string[]).join("\n") : "";
  return typeof value === "string" ? value : "";
}

function fromDraft(field: PostingField, v: string | EssayQuestion[]): PostingValue | null {
  if (field === "questions") {
    const qs = (v as EssayQuestion[])
      .map((q) => ({ prompt: q.prompt.trim(), char_limit: q.char_limit && q.char_limit > 0 ? q.char_limit : null }))
      .filter((q) => q.prompt);
    return qs.length ? qs : null;
  }
  if (isList(field)) {
    const items = (v as string)
      .split("\n")
      .map((s) => s.trim())
      .filter(Boolean);
    return items.length ? items : null;
  }
  const s = (v as string).trim();
  return s ? s : null;
}

const same = (a: PostingValue | null | undefined, b: PostingValue | null | undefined) =>
  JSON.stringify(a ?? null) === JSON.stringify(b ?? null);

/** 확정 화면의 시작값: 새 버전에서 바뀐 항목은 새 값, 나머지는 이미 확정한 값(사용자 수정 포함). */
function initialDraft(p: Posting): Draft {
  const v = p.latest;
  const changed = new Set(p.pending_changes);
  const out = {} as Draft;
  for (const f of POSTING_FIELDS) {
    const useConfirmed = p.saved_at !== null && !changed.has(f);
    out[f] = toDraft(f, useConfirmed || !v ? p.fields[f] : v.extracted[f]?.value);
  }
  return out;
}

// ---------------------------------------------------------------- 화면

export function PostingPage() {
  const { postingId } = route.useParams();
  const q = useQuery({
    queryKey: ["posting", postingId],
    queryFn: () => api.posting(postingId),
    refetchInterval: (query) => (query.state.data?.runs[0]?.status === "pending" ? 2500 : false),
  });
  return <Loaded query={q}>{(p) => <PostingView p={p} />}</Loaded>;
}

function PostingView({ p }: { p: Posting }) {
  const qc = useQueryClient();
  const [evidenceOf, setEvidenceOf] = useState<{ field: PostingField; index?: number } | null>(null);
  const [versionId, setVersionId] = useState<string | null>(null);
  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["posting", p.id] });
    qc.invalidateQueries({ queryKey: ["postings"] });
  };
  const refetch = useMutation({ mutationFn: () => api.refetchPosting(p.id), onSuccess: invalidate });
  const archive = useMutation({ mutationFn: () => api.archivePosting(p.id), onSuccess: invalidate });

  const run = p.runs[0];
  const title =
    (p.fields.title as string | undefined) ??
    (p.latest?.extracted.title?.value as string | undefined) ??
    hostOf(p.canonical_url);
  const deadline = (p.fields.deadline ?? p.latest?.extracted.deadline?.value) as string | undefined;
  const st = POSTING_STATUS[p.status];
  const pendingRun = run?.status === "pending";

  return (
    <div className="mx-auto max-w-5xl">
      <PageHeader
        back={{ to: "/postings", label: "공고" }}
        title={title}
        subtitle={
          <span className="flex flex-wrap items-center gap-x-1.5">
            <span className={p.status === "draft" ? "font-medium text-fg" : undefined}>{st.label}</span>
            {ddayLabel(deadline) ? (
              <>
                <span aria-hidden>·</span>
                <DdayBadge deadline={deadline} />
              </>
            ) : null}
            <span aria-hidden>·</span>
            <a
              href={p.canonical_url}
              target="_blank"
              rel="noreferrer noopener"
              className="inline-flex min-w-0 items-center gap-0.5 underline-offset-2 hover:text-fg hover:underline"
            >
              {hostOf(p.canonical_url)} <ExternalLink size={12} />
            </a>
          </span>
        }
        actions={
          <>
            <Button onClick={() => refetch.mutate()} disabled={refetch.isPending || pendingRun}>
              <RefreshCw size={14} className={pendingRun ? "animate-spin" : undefined} /> 다시 가져오기
            </Button>
            {p.status !== "archived" ? (
              <Button variant="ghost" onClick={() => archive.mutate()} disabled={archive.isPending}>
                <Archive size={14} /> 보관
              </Button>
            ) : null}
          </>
        }
      />
      <ErrorText error={refetch.error ?? archive.error} className="mb-4" />

      <div className="lg:grid lg:grid-cols-[minmax(0,1fr)_17rem] lg:gap-14">
        <div className="min-w-0 space-y-10">
          {run ? <RunNotice run={run} posting={p} /> : null}
          {p.pending_changes.length && p.status === "saved" ? (
            <div role="status" className="rounded-md bg-subtle px-3 py-2.5 text-[13px]">
              다시 가져온 공고에서 바뀐 항목:{" "}
              <strong>{p.pending_changes.map((f) => FIELD_LABEL[f as PostingField] ?? f).join(", ")}</strong>. 아래에서
              새 값을 확인하고 다시 저장하세요.
            </div>
          ) : null}
          <ChoiceQuestions p={p} />
          {p.latest || p.status !== "draft" ? (
            <ReviewForm
              key={`${p.latest?.id}-${p.confirmed_version_id}-${p.saved_at}`}
              p={p}
              onEvidence={setEvidenceOf}
              onSaved={invalidate}
            />
          ) : null}
          <ManualText
            postingId={p.id}
            open={run?.status === "failed" || (!p.latest && !pendingRun && run?.status !== "listing")}
            onDone={invalidate}
          />
        </div>

        <aside className="mt-10 min-w-0 space-y-10 lg:mt-0">
          <Section title="가져오기 기록">
            <Runs runs={p.runs} onVersion={setVersionId} />
          </Section>
          {p.status === "saved" ? (
            <Section title="다음 단계">
              <NextSteps p={p} />
            </Section>
          ) : null}
          {p.latest ? (
            <Section title="버전">
              <Versions p={p} onOpen={setVersionId} />
            </Section>
          ) : null}
        </aside>
      </div>

      {p.latest ? (
        <EvidenceSheet posting={p} version={p.latest} target={evidenceOf} onClose={() => setEvidenceOf(null)} />
      ) : null}
      <VersionSheet postingId={p.id} versionId={versionId} onClose={() => setVersionId(null)} />
    </div>
  );
}

// ---------------------------------------------------------------- 가져오기 상태

function RunNotice({ run, posting }: { run: FetchRun; posting: Posting }) {
  const qc = useQueryClient();
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const save = useMutation({
    mutationFn: () => api.savePostings([...picked]),
    onSuccess: () => {
      setPicked(new Set());
      qc.invalidateQueries({ queryKey: ["postings"] });
    },
  });
  if (run.status === "pending")
    return (
      <p role="status" className="flex items-center gap-2 text-[13px] text-muted">
        <Spinner /> 페이지를 가져오는 중입니다. worker가 켜져 있어야 합니다.
      </p>
    );
  if (run.status === "needs_confirmation") return <CostConfirmation run={run} postingId={posting.id} />;
  if (run.status === "failed")
    return (
      <div role="alert" className="rounded-md bg-danger-soft px-3 py-2.5 text-[13px] text-danger">
        가져오기 실패: <strong>{FETCH_ERROR[run.error_code ?? ""] ?? run.error_code}</strong>
        {run.http_status ? ` (HTTP ${run.http_status})` : ""}. 마감으로 처리하지 않습니다. 본문을 직접 붙여넣을 수
        있습니다.
        {run.error_code === "bot_blocked" ? (
          <p className="mt-1 text-fg">
            사이트의 자동 요청 확인 화면(봇 차단)이 막았습니다. 우회하지 않으며, 다시 가져와도 같을 수 있습니다.
            브라우저에서 공고 본문을 복사해 아래에 붙여넣거나 캡처 이미지·PDF를 올려 주세요.
          </p>
        ) : null}
        {run.error_code === "maybe_bot_blocked" ? (
          <p className="mt-1 text-fg">
            알려진 서비스는 아니지만 자동 요청 확인 화면(캡차·확인 스크립트)으로 보입니다. 막혔을 수 있으며 우회하지
            않습니다. 브라우저에서 공고 본문을 복사해 아래에 붙여넣어 주세요.
          </p>
        ) : null}
      </div>
    );
  if (run.status === "listing")
    return (
      <section>
        <h2 className="mb-1 text-[15px] font-semibold">저장할 공고 고르기</h2>
        <p className="mb-3 text-[13px] text-muted">
          공고 목록 페이지입니다. 목록을 따라가지 않으니, 저장할 공고를 골라 주세요.
          {run.candidates.length ? ` (후보 ${run.candidates.length}개)` : ""}
        </p>
        {run.candidates.length ? (
          <div className="border-y border-line">
            <ul className="-mx-2 max-h-80 divide-y divide-line overflow-y-auto">
              {run.candidates.map((c) => (
                <li key={c.url}>
                  <label className="flex cursor-pointer items-start gap-3 rounded-md px-2 py-2.5 text-[13px] transition-colors duration-150 hover:bg-subtle/60">
                    <input
                      type="checkbox"
                      className="mt-0.5 h-4 w-4 shrink-0 accent-fg"
                      checked={picked.has(c.url)}
                      onChange={(e) => {
                        const next = new Set(picked);
                        if (e.target.checked) next.add(c.url);
                        else next.delete(c.url);
                        setPicked(next);
                      }}
                    />
                    <span className="min-w-0">
                      <span className="block font-medium">{c.title}</span>
                      <span className="block truncate font-mono text-xs text-muted">{c.url}</span>
                    </span>
                  </label>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
        <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-2">
          <Button
            variant="primary"
            disabled={!picked.size || picked.size > 20 || save.isPending}
            onClick={() => save.mutate()}
          >
            선택한 {picked.size}건 가져오기
          </Button>
          {save.isSuccess ? <span className="text-[13px]">요청했습니다. 공고 목록에서 확인하세요.</span> : null}
          {picked.size > 20 ? <span className="text-[13px] font-medium">한 번에 20건까지</span> : null}
          {posting.status !== "archived" ? (
            <span className="text-[13px] text-muted">이 목록 항목은 보관해도 됩니다.</span>
          ) : null}
        </div>
        <ErrorText error={save.error} className="mt-1" />
      </section>
    );
  if (run.unchanged)
    return <p className="text-[13px] text-muted">마지막으로 가져온 내용이 이전과 같아 새 버전을 만들지 않았습니다.</p>;
  return null;
}

function Runs({ runs, onVersion }: { runs: FetchRun[]; onVersion: (id: string) => void }) {
  if (!runs.length) return <p className="border-t border-line pt-3 text-[13px] text-muted">기록 없음</p>;
  return (
    <ul className="divide-y divide-line border-t border-line text-[13px]">
      {runs.map((r) => {
        const s = FETCH_STATUS[r.status] ?? { label: r.status, tone: "neutral" as const };
        return (
          <li key={r.id} className="py-2.5">
            <div className="flex flex-wrap items-baseline gap-x-1.5">
              <span className={cn("font-medium", r.status === "failed" && "text-danger")}>{s.label}</span>
              {r.unchanged ? <span className="text-muted">· 변경 없음</span> : null}
              <span className="text-muted tabular-nums">· {formatDateTime(r.created_at)}</span>
            </div>
            {r.error_code ? (
              <div className="mt-0.5 text-danger">
                {FETCH_ERROR[r.error_code] ?? r.error_code}
                {r.http_status ? ` · HTTP ${r.http_status}` : ""}
              </div>
            ) : null}
            {r.notes.length ? (
              <ul className="mt-0.5 space-y-0.5 text-xs text-muted">
                {r.notes.map((n) => (
                  <li key={n}>{noteLabel(n)}</li>
                ))}
              </ul>
            ) : null}
            {r.version_id && !r.unchanged ? (
              <button
                type="button"
                className="-my-1 mt-0.5 py-1 underline-offset-2 hover:underline"
                onClick={() => onVersion(r.version_id!)}
              >
                이 버전 보기
              </button>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}

function Versions({ p, onOpen }: { p: Posting; onOpen: (id: string) => void }) {
  const q = useQuery({
    queryKey: ["posting-versions", p.id, p.latest?.id],
    queryFn: () => api.postingVersions(p.id),
  });
  const rows = q.data ?? (p.latest ? [p.latest] : []);
  return (
    <div className="text-[13px]">
      <ErrorText error={q.error} className="mb-2" />
      <ul className="divide-y divide-line border-t border-line">
        {rows.map((v) => (
          <li key={v.id}>
            <button
              type="button"
              onClick={() => onOpen(v.id)}
              className="-mx-2 block w-[calc(100%+1rem)] rounded-md px-2 py-2.5 text-left transition-colors duration-150 hover:bg-subtle/60"
            >
              <div className="flex flex-wrap items-center gap-x-1.5 gap-y-1">
                <span className="font-medium tabular-nums">v{v.version_no}</span>
                <span className="text-muted tabular-nums">{formatDateTime(v.created_at)}</span>
                {v.id === p.confirmed_version_id ? (
                  <span className="text-muted">· 확정 기준</span>
                ) : v.id === p.latest?.id ? (
                  <Badge tone="warn">최신 · 미확정</Badge>
                ) : null}
              </div>
              <div className="mt-0.5 text-xs text-muted">
                {METHOD_LABEL[v.method] ?? v.method} · {ADAPTER_LABEL[v.adapter] ?? v.adapter}
                {v.has_screenshot ? (v.adapter === "image" ? " · 원본 이미지 보관" : " · 스크린샷 있음") : ""}
              </div>
              {v.changed_fields.length ? (
                <div className="mt-0.5 text-xs">
                  이전 버전과 달라진 항목: {v.changed_fields.map((f) => FIELD_LABEL[f as PostingField] ?? f).join(", ")}
                </div>
              ) : null}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** 저장한 공고에서 지원·학습으로 넘어가는 길. */
function NextSteps({ p }: { p: Posting }) {
  const apps = useQuery({ queryKey: ["applications"], queryFn: api.applications });
  const create = useCreateApplication();
  const app = apps.data?.find((a) => a.posting_id === p.id);
  const row =
    "-mx-2 flex min-h-11 flex-col justify-center rounded-md px-2 py-2.5 transition-colors duration-150 hover:bg-subtle/60";
  return (
    <div className="text-[13px]">
      {app ? null : (
        <div className="mb-3">
          <Button variant="primary" onClick={() => create.mutate(p.id)} disabled={create.isPending || apps.isLoading}>
            <Plus size={14} /> 지원 만들기
          </Button>
          <ErrorText error={create.error} className="mt-1" />
        </div>
      )}
      <ul className="divide-y divide-line border-t border-line">
        {app ? (
          <li>
            <Link to="/applications/$applicationId" params={{ applicationId: app.id }} className={row}>
              <span className="text-sm font-medium">지원서 열기</span>
              <span className="text-muted tabular-nums">
                문항 {app.questions}
                {app.unwritten ? ` · 안 쓴 문항 ${app.unwritten}` : ""}
              </span>
            </Link>
          </li>
        ) : null}
        <li>
          <Link to="/study" search={{ tab: "coverage", posting: p.id }} className={row}>
            <span className="text-sm font-medium">요건별 준비 상태 보기</span>
          </Link>
        </li>
      </ul>
    </div>
  );
}

// ---------------------------------------------------------------- 확인·수정·확정

function ReviewForm({
  p,
  onEvidence,
  onSaved,
}: {
  p: Posting;
  onEvidence: (t: { field: PostingField; index?: number }) => void;
  onSaved: () => void;
}) {
  const version = p.latest;
  const [draft, setDraft] = useState<Draft>(() => initialDraft(p));
  const [showEmpty, setShowEmpty] = useState(false);

  // 확정 요청: 기준 버전의 추출값과 비교해 바뀐 것은 overrides, 비운 것은 clear.
  const payload = useMemo(() => {
    const overrides: Partial<Record<PostingField, PostingValue>> = {};
    const clear: PostingField[] = [];
    for (const f of POSTING_FIELDS) {
      const now = fromDraft(f, draft[f]);
      const base = version?.extracted[f]?.value ?? null;
      if (same(now, base)) continue;
      if (now === null) {
        if (base !== null) clear.push(f);
      } else overrides[f] = now;
    }
    return { version_id: version?.id ?? null, overrides, clear };
  }, [draft, version]);

  const dirtyVsConfirmed = useMemo(
    () =>
      p.status === "draft" ||
      p.pending_changes.length > 0 ||
      POSTING_FIELDS.some((f) => !same(fromDraft(f, draft[f]), p.fields[f] ?? null)),
    [draft, p],
  );

  const confirm = useMutation({
    mutationFn: () => api.confirmPosting(p.id, payload),
    onSuccess: onSaved,
  });
  useEffect(() => confirm.reset(), [draft]); // eslint-disable-line react-hooks/exhaustive-deps

  const set = (f: PostingField, v: string | EssayQuestion[]) => setDraft((d) => ({ ...d, [f]: v }));
  const missing = new Set(version?.missing ?? []);
  const fields = POSTING_FIELDS.filter(
    (f) => showEmpty || REVIEW_FIELDS.includes(f) || fromDraft(f, draft[f]) !== null || version?.extracted[f],
  );
  const hidden = POSTING_FIELDS.length - fields.length;
  const titleEmpty = fromDraft("title", draft.title) === null;

  return (
    <Section
      title={p.status === "draft" ? "추출 결과 확인" : "공고 내용"}
      action={
        <span className="text-[13px] text-muted tabular-nums">
          {version ? `v${version.version_no} 기준` : "직접 입력"}
        </span>
      }
    >
      <p className="text-[13px] text-muted">
        항목마다 원문 근거를 확인하세요. 원문에서 못 찾은 값은 비워 두었고, 수정한 값은 근거가 “직접 입력”이 됩니다.
      </p>
      <form
        className="mt-3 divide-y divide-line border-t border-line"
        onSubmit={(e) => {
          e.preventDefault();
          if (!titleEmpty) confirm.mutate();
        }}
      >
        {fields.map((f) => (
          <FieldRow
            key={f}
            field={f}
            value={draft[f]}
            onChange={(v) => set(f, v)}
            evidence={evidenceFor(p, f, fromDraft(f, draft[f]))}
            missing={missing.has(f)}
            onEvidence={(index) => onEvidence({ field: f, index })}
          />
        ))}
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2 pt-4">
          <Button type="submit" variant="primary" disabled={titleEmpty || confirm.isPending || !dirtyVsConfirmed}>
            {p.status === "draft"
              ? "확인하고 저장"
              : p.pending_changes.length
                ? "새 버전으로 다시 저장"
                : "수정 내용 저장"}
          </Button>
          {hidden > 0 ? (
            <Button variant="ghost" onClick={() => setShowEmpty(true)}>
              빈 항목 {hidden}개 보기
            </Button>
          ) : null}
          {titleEmpty ? <span className="text-[13px] font-medium">공고명은 꼭 있어야 합니다.</span> : null}
          {confirm.isSuccess ? (
            <span role="status" className="text-[13px] text-muted">
              저장했습니다.
            </span>
          ) : null}
          <ErrorText error={confirm.error} />
        </div>
      </form>
    </Section>
  );
}

type EvidenceInfo =
  | { kind: "user" }
  | { kind: "structured"; ref: string }
  | { kind: "quote"; quote: string }
  | { kind: "quotes"; quotes: string[] }
  | { kind: "edited" }
  | { kind: "none" };

function evidenceFor(p: Posting, f: PostingField, now: PostingValue | null): EvidenceInfo {
  // 확정값과 같고 확정 근거가 사용자 입력이면
  const confirmedEv = p.evidence[f];
  if (confirmedEv?.kind === "user" && same(now, p.fields[f] ?? null)) return { kind: "user" };
  const ex = p.latest?.extracted[f];
  if (!ex) return now === null ? { kind: "none" } : { kind: "edited" };
  if (!same(now, ex.value)) return now === null ? { kind: "none" } : { kind: "edited" };
  return describe(ex.evidence);
}

function describe(ev: FieldEvidence): EvidenceInfo {
  if (ev.kind === "structured" && typeof ev.ref === "string") return { kind: "structured", ref: ev.ref };
  if (ev.kind === "quote" && typeof ev.ref === "string") return { kind: "quote", quote: ev.ref };
  if (ev.kind === "quotes" && Array.isArray(ev.ref))
    return { kind: "quotes", quotes: (ev.ref as { ref: string }[]).map((r) => r.ref) };
  if (ev.kind === "user") return { kind: "user" };
  return { kind: "none" };
}

/** 항목별 근거. 배지는 손대야 하는 "확인 필요"에만, 나머지는 흐린 글자. */
function EvidenceLine({
  info,
  missing,
  review,
  onOpen,
}: {
  info: EvidenceInfo;
  missing: boolean;
  review: boolean;
  onOpen: (index?: number) => void;
}) {
  switch (info.kind) {
    case "structured":
      return (
        <span className="flex min-w-0 flex-wrap items-baseline gap-x-1.5 text-muted">
          구조화 데이터
          <code className="text-[11px] break-all">{info.ref}</code>
        </span>
      );
    case "quote":
      return (
        <button
          onClick={() => onOpen()}
          type="button"
          className="flex items-baseline gap-1.5 text-left text-muted transition-colors duration-150 hover:text-fg"
        >
          <span className="shrink-0">원문 인용</span>
          <span className="line-clamp-2">“{info.quote}”</span>
        </button>
      );
    case "quotes":
      return (
        <span className="flex flex-wrap items-baseline gap-x-1.5 text-muted">
          원문 인용 {info.quotes.length}개
          <button type="button" className="text-fg underline-offset-2 hover:underline" onClick={() => onOpen(0)}>
            원문에서 보기
          </button>
        </span>
      );
    case "user":
      return <span className="text-muted">직접 입력</span>;
    case "edited":
      return <span className="font-medium">수정함 · 근거: 직접 입력</span>;
    case "none":
      return missing && review ? (
        <span className="font-medium">확인 필요 · 원문에서 못 찾음</span>
      ) : (
        <span className="text-muted">근거 없음 · 비어 있음</span>
      );
  }
}

function FieldRow({
  field,
  value,
  onChange,
  evidence,
  missing,
  onEvidence,
}: {
  field: PostingField;
  value: string | EssayQuestion[];
  onChange: (v: string | EssayQuestion[]) => void;
  evidence: EvidenceInfo;
  missing: boolean;
  onEvidence: (index?: number) => void;
}) {
  const id = `pf-${field}`;
  const review = REVIEW_FIELDS.includes(field);
  return (
    <div className="grid gap-1.5 py-3 xl:grid-cols-[110px_minmax(0,1fr)] xl:gap-3">
      <label htmlFor={id} className="text-[13px] font-medium text-muted xl:pt-2">
        {FIELD_LABEL[field]}
      </label>
      <div className="min-w-0 space-y-1.5">
        {field === "questions" ? (
          <QuestionsEditor value={value as EssayQuestion[]} onChange={onChange} />
        ) : isList(field) ? (
          <Textarea
            id={id}
            value={value as string}
            onChange={(e) => onChange(e.target.value)}
            rows={Math.min(10, Math.max(2, (value as string).split("\n").length))}
            placeholder="한 줄에 한 항목"
          />
        ) : (
          <Input
            id={id}
            value={value as string}
            onChange={(e) => onChange(e.target.value)}
            placeholder={
              field === "deadline"
                ? "2026-12-31 또는 2026-12-31T18:00, 상시면 always"
                : field === "posted_at"
                  ? "2026-11-01"
                  : undefined
            }
          />
        )}
        {field === "deadline" && typeof value === "string" && value.trim() ? (
          <div className="flex flex-wrap items-center gap-x-1.5 text-[13px] text-muted tabular-nums">
            {formatDeadline(value.trim())}
            {ddayLabel(value.trim()) && value.trim() !== "always" ? (
              <>
                <span aria-hidden>·</span>
                <DdayBadge deadline={value.trim()} />
              </>
            ) : null}
          </div>
        ) : null}
        <div className="text-xs">
          <EvidenceLine info={evidence} missing={missing} review={review} onOpen={onEvidence} />
        </div>
      </div>
    </div>
  );
}

function QuestionsEditor({ value, onChange }: { value: EssayQuestion[]; onChange: (v: EssayQuestion[]) => void }) {
  const update = (i: number, patch: Partial<EssayQuestion>) =>
    onChange(value.map((q, j) => (j === i ? { ...q, ...patch } : q)));
  return (
    <div className="space-y-2">
      {value.map((q, i) => (
        <div key={i} className="flex gap-2">
          <Textarea
            value={q.prompt}
            onChange={(e) => update(i, { prompt: e.target.value })}
            className="min-h-14 flex-1"
            aria-label={`문항 ${i + 1}`}
          />
          <div className="w-24 shrink-0 space-y-1">
            <Input
              type="number"
              min={1}
              inputMode="numeric"
              value={q.char_limit ?? ""}
              onChange={(e) => update(i, { char_limit: e.target.value ? Number(e.target.value) : null })}
              placeholder="글자 수"
              aria-label={`문항 ${i + 1} 글자 수`}
            />
            <Button
              variant="ghost"
              className="w-full"
              aria-label={`문항 ${i + 1} 빼기`}
              onClick={() => onChange(value.filter((_, j) => j !== i))}
            >
              <Trash2 size={14} />
            </Button>
          </div>
        </div>
      ))}
      <Button onClick={() => onChange([...value, { prompt: "", char_limit: null }])}>
        <Plus size={14} /> 문항 추가
      </Button>
    </div>
  );
}

// ---------------------------------------------------------------- 근거·버전 패널

function useVersionText(postingId: string, versionId: string | null) {
  return useQuery({
    queryKey: ["posting-version", postingId, versionId],
    queryFn: () => api.postingVersion(postingId, versionId!),
    enabled: !!versionId,
    staleTime: Infinity,
  });
}

function EvidenceSheet({
  posting,
  version,
  target,
  onClose,
}: {
  posting: Posting;
  version: PostingVersion;
  target: { field: PostingField; index?: number } | null;
  onClose: () => void;
}) {
  const full = useVersionText(posting.id, target ? version.id : null);
  const [index, setIndex] = useState(0);
  useEffect(() => setIndex(target?.index ?? 0), [target]);
  const ev = target ? version.extracted[target.field]?.evidence : undefined;
  const spans: { start: number; end: number; ref: string }[] =
    ev?.kind === "quote" && typeof ev.start === "number" && typeof ev.end === "number"
      ? [{ start: ev.start, end: ev.end, ref: String(ev.ref) }]
      : ev?.kind === "quotes" && Array.isArray(ev.ref)
        ? (ev.ref as { start: number; end: number; ref: string }[])
        : [];
  const span = spans[Math.min(index, spans.length - 1)];
  return (
    <Sheet
      open={!!target}
      onOpenChange={(o) => !o && onClose()}
      title={target ? `${FIELD_LABEL[target.field]} 근거` : "근거"}
      description={`v${version.version_no} 원문 · ${formatDateTime(version.created_at)}`}
      width="lg"
    >
      {spans.length > 1 ? (
        <div className="sticky -top-4 z-10 -mx-4 mb-3 flex flex-wrap gap-1 border-b border-line bg-surface px-4 py-2">
          {spans.map((s, i) => (
            <button
              key={i}
              onClick={() => setIndex(i)}
              className={
                i === index
                  ? "max-w-full truncate rounded bg-accent-soft px-2 py-0.5 text-xs text-accent"
                  : "max-w-full truncate rounded border border-line px-2 py-0.5 text-xs text-muted"
              }
              title={s.ref}
            >
              {i + 1}. {s.ref.slice(0, 24)}
            </button>
          ))}
        </div>
      ) : null}
      <Loaded query={full}>
        {(v) =>
          v.text && span ? (
            <Highlight key={`${span.start}-${span.end}`} text={v.text} start={span.start} end={span.end} />
          ) : (
            <p className="text-sm text-muted">원문 본문이 없습니다.</p>
          )
        }
      </Loaded>
    </Sheet>
  );
}

function VersionSheet({
  postingId,
  versionId,
  onClose,
}: {
  postingId: string;
  versionId: string | null;
  onClose: () => void;
}) {
  const q = useVersionText(postingId, versionId);
  return (
    <Sheet
      open={!!versionId}
      onOpenChange={(o) => !o && onClose()}
      title={q.data ? `버전 v${q.data.version_no}` : "버전"}
      description={
        q.data
          ? `${formatDateTime(q.data.created_at)} · ${METHOD_LABEL[q.data.method] ?? q.data.method} · ${ADAPTER_LABEL[q.data.adapter] ?? q.data.adapter}`
          : undefined
      }
      width="lg"
    >
      <Loaded query={q}>
        {(v) => (
          <div className="space-y-4">
            <dl className="grid grid-cols-[100px_minmax(0,1fr)] gap-x-3 gap-y-1.5 text-[13px]">
              {POSTING_FIELDS.filter((f) => v.extracted[f]).map((f) => {
                const val = v.extracted[f]!.value;
                return (
                  <div key={f} className="contents">
                    <dt className="text-muted">{FIELD_LABEL[f]}</dt>
                    <dd className="min-w-0">
                      {Array.isArray(val) ? `${val.length}개 항목` : f === "deadline" ? formatDeadline(val) : val}
                      {v.changed_fields.includes(f) ? (
                        <Badge tone="warn" className="ml-1.5">
                          바뀜
                        </Badge>
                      ) : null}
                    </dd>
                  </div>
                );
              })}
            </dl>
            {v.missing.length ? (
              <p className="text-xs text-muted">
                원문에서 못 찾은 항목: {v.missing.map((f) => FIELD_LABEL[f as PostingField] ?? f).join(", ")}
              </p>
            ) : null}
            <div>
              <div className="mb-1 flex items-center gap-1.5 text-[13px] font-medium">
                <FileText size={14} /> 정리한 본문
              </div>
              <div className="max-h-[60dvh] overflow-y-auto rounded-md border border-line bg-bg p-3 text-[13px] leading-6 whitespace-pre-wrap">
                {v.text || "본문 없음"}
              </div>
            </div>
            <p className="truncate text-xs text-muted">가져온 주소: {v.final_url}</p>
          </div>
        )}
      </Loaded>
    </Sheet>
  );
}

// ---------------------------------------------------------------- 본문 직접 붙여넣기

function ManualText({
  postingId,
  open: initiallyOpen,
  onDone,
}: {
  postingId: string;
  open: boolean;
  onDone: () => void;
}) {
  const [open, setOpen] = useState(initiallyOpen);
  const [text, setText] = useState("");
  useEffect(() => {
    if (initiallyOpen) setOpen(true);
  }, [initiallyOpen]);
  const send = useMutation({
    mutationFn: () => api.manualPostingText(postingId, text),
    onSuccess: () => {
      setText("");
      setOpen(false);
      onDone();
    },
  });
  if (!open)
    return (
      <Button variant="ghost" className="-ml-3.5" onClick={() => setOpen(true)}>
        본문 직접 붙여넣기
      </Button>
    );
  return (
    <Section title="본문 직접 붙여넣기">
      <p className="text-[13px] text-muted">
        페이지를 못 가져왔거나 로그인이 필요한 공고라면, 공고 본문을 복사해 넣으세요. 새 버전으로 추출합니다.
      </p>
      <form
        className="space-y-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (text.trim()) send.mutate();
        }}
      >
        <Textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          className="min-h-40"
          maxLength={60000}
          aria-label="공고 본문"
        />
        <ErrorText error={send.error} />
        <div className="flex gap-2">
          <Button type="submit" variant="primary" disabled={!text.trim() || send.isPending}>
            {send.isPending ? <Spinner /> : null} 본문으로 추출
          </Button>
          <Button variant="ghost" onClick={() => setOpen(false)}>
            닫기
          </Button>
        </div>
      </form>
    </Section>
  );
}
