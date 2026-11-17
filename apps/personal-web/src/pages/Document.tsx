import type { AppDocument, ClaimBrief, CountRule, RevisionConflictDetail, TextCounts } from "@career-brain/client";
import { Button, cn, Input, Label, Select, Sheet } from "@career-brain/ui";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getRouteApi, Link, useBlocker } from "@tanstack/react-router";
import { AlertTriangle, ChevronRight, Copy, Download, History, Lock, Save, Settings2 } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ClaimPicker } from "../components/ClaimPicker";
import { ErrorText, Loaded, PageHeader, Tabs } from "../components/common";
import { BatchSuggestButton } from "../components/doc/BatchSuggestSheet";
import { BlockEditor, draftSpans, newKey, spansForSave, type DraftBlock } from "../components/doc/BlockEditor";
import { ChecksPanel } from "../components/doc/ChecksPanel";
import { ExportSheet } from "../components/doc/ExportSheet";
import { SuggestionsPanel } from "../components/doc/SuggestionsPanel";
import { VisibilityJudgePanel } from "../components/doc/VisibilityJudgePanel";
import { api, ApiError } from "../lib/api";
import { formatDateTime } from "../lib/dates";
import { int } from "../lib/format";
import { DOC_KIND, FINDING, NEWLINE_RULE, REVISION_ORIGIN, SPACES_RULE } from "../lib/labels";

const sharedRoute = getRouteApi("/documents/$documentId");

function toDraft(doc: AppDocument): DraftBlock[] {
  if (!doc.blocks.length) return [{ key: newKey(), id: null, text: "", origin: "user", claim_ids: [] }];
  return doc.blocks.map((b) => ({
    key: newKey(),
    id: b.id,
    text: b.text,
    origin: b.origin,
    claim_ids: [...b.claim_ids],
    spans: draftSpans(b),
  }));
}

/** 저장할 때 서버가 하는 정리(앞뒤 공백·빈 문단 제외)와 같은 기준으로 비교한다. */
function canonical(blocks: { id: string | null; text: string; claim_ids: string[]; spans?: DraftBlock["spans"] }[]) {
  return JSON.stringify(
    blocks
      .map((b) => ({
        id: b.id,
        text: b.text.normalize("NFC").replace(/\r\n?/g, "\n").trim(),
        c: b.claim_ids,
        s: (b.spans ?? []).map((sp) => [sp.quote, sp.claim_ids]),
      }))
      .filter((b) => b.text),
  );
}

/** 서버 블록 → 비교용 (span 은 인용문으로) */
function baseShape(blocks: AppDocument["blocks"]) {
  return blocks.map((b) => ({ id: b.id, text: b.text, claim_ids: b.claim_ids, spans: draftSpans(b) }));
}

function fnv1a(text: string): string {
  let h = 0x811c9dc5;
  for (let i = 0; i < text.length; i++) {
    h ^= text.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return h.toString(16);
}

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

/** 공용 문서 (지원에 묶이지 않은 포트폴리오·기본 이력서). 편집 규칙은 지원 문서와 같다. */
export function SharedDocumentPage() {
  const { documentId } = sharedRoute.useParams();
  const q = useQuery({
    queryKey: ["document", documentId],
    queryFn: () => api.document(null, documentId),
  });
  return <Loaded query={q}>{(doc) => <DocumentEditor key={doc.id} appId={null} initial={doc} />}</Loaded>;
}

type Panel = "checks" | "ai" | "history";

/** 지원 준비에서 고른 경험: 그 확인한 사실이 연결된 빈 문단을 화면에 둔다 (글은 사람이 쓴다). */
export interface Scaffold {
  experience_id: string;
  claim_ids: string[];
  hint: string;
}

/**
 * 고른 경험마다 빈 문단(근거만 연결)을 둔다. 이미 그 사실들이 연결된 문단이 있으면 두지 않는다.
 * 글이 없는 문단은 저장하지 않으므로(서버 규칙) 이 문단은 화면에만 있고, 다시 열 때 고른 것에서 다시 만든다.
 * 고른 것을 취소하면 아직 글이 없는 그 빈 문단만 뺀다.
 */
function withScaffolds(blocks: DraftBlock[], scaffolds: Scaffold[]): DraftBlock[] {
  const keep = new Set(scaffolds.map((s) => s.experience_id));
  let next = blocks.filter((b) => !(b.scaffoldOf && !keep.has(b.scaffoldOf) && !b.text.trim()));
  for (const s of scaffolds) {
    if (!s.claim_ids.length) continue;
    const covered = next.some(
      (b) => b.scaffoldOf === s.experience_id || s.claim_ids.every((c) => b.claim_ids.includes(c)),
    );
    if (covered) continue;
    const nb: DraftBlock = {
      key: newKey(),
      id: null,
      text: "",
      origin: "user",
      claim_ids: [...s.claim_ids],
      scaffoldOf: s.experience_id,
      hint: s.hint,
    };
    // 아직 아무것도 없는 문서의 빈 첫 문단은 바꿔 쓴다
    const lone = next.length === 1 && !next[0]!.id && !next[0]!.text.trim() && !next[0]!.claim_ids.length;
    next = lone ? [nb] : [...next, nb];
  }
  return next.length === blocks.length && next.every((b, i) => b === blocks[i]) ? blocks : next;
}

/** 지원 작업대가 편집기 조각을 제 자리에 놓는다 (상태는 편집기가 가진다). */
export interface EditorParts {
  doc: AppDocument;
  dirty: boolean;
  editable: boolean;
  /** 한 번이라도 저장했는가 */
  saved: boolean;
  counts: TextCounts;
  saving: boolean;
  canSave: boolean;
  onSave: () => void;
  openExport: () => void;
  openSettings: () => void;
  /** 저장한 글의 경고 수 (참고 제외) */
  warnings: number;
  /** 일괄 수정안이 "수정안 보기"를 누를 때마다 늘어난다 */
  suggestAsk: number;
  notices: ReactNode;
  countBar: ReactNode;
  editor: ReactNode;
  checks: ReactNode;
  suggestions: ReactNode;
  batch: ReactNode;
  history: ReactNode;
  sheets: ReactNode;
}

export function DocumentEditor({
  appId,
  initial,
  frame,
  scaffolds,
}: {
  appId: string | null;
  initial: AppDocument;
  frame?: (parts: EditorParts) => ReactNode;
  scaffolds?: Scaffold[];
}) {
  const qc = useQueryClient();
  const docQuery = useQuery({
    queryKey: ["document", initial.id],
    queryFn: () => api.document(appId, initial.id),
    initialData: initial,
  });
  const doc = docQuery.data;
  const app = useQuery({
    queryKey: ["application", appId],
    queryFn: () => api.application(appId!),
    enabled: appId !== null,
  });

  // 편집 상태: 마지막으로 불러온(또는 저장한) revision 을 기준(base)으로 둔다.
  const [base, setBase] = useState(doc.revision_no);
  const [baseBlocks, setBaseBlocks] = useState(() => baseShape(doc.blocks));
  const [blocks, setBlocks] = useState<DraftBlock[]>(() => toDraft(doc));
  const [conflict, setConflict] = useState<RevisionConflictDetail | null>(null);
  const [activeKey, setActiveKey] = useState<string | null>(null);
  const [pickFor, setPickFor] = useState<{ key: string; range?: { start: number; end: number; quote: string } } | null>(
    null,
  );
  const [exporting, setExporting] = useState(false);
  const [focus, setFocus] = useState<{ key: string; n: number } | null>(null);
  const [panel, setPanel] = useState<Panel>("checks");
  const [settings, setSettings] = useState(false);
  const [compare, setCompare] = useState(false);
  const [historyRev, setHistoryRev] = useState<number | null>(null);
  const [suggestAsk, setSuggestAsk] = useState(0);
  const focusN = useRef(0);
  const scaffoldRef = useRef<Scaffold[]>(scaffolds ?? []);
  scaffoldRef.current = scaffolds ?? [];

  const reset = useCallback((d: AppDocument) => {
    setBase(d.revision_no);
    setBaseBlocks(baseShape(d.blocks));
    setBlocks(withScaffolds(toDraft(d), scaffoldRef.current));
    setConflict(null);
  }, []);

  // 고른 경험이 바뀌면 빈 문단을 더하거나 뺀다. 빈 문단은 저장할 편집으로 치지 않는다 (canonical 이 뺀다).
  const scaffoldKey = JSON.stringify(scaffolds ?? []);
  useEffect(() => {
    setBlocks((bs) => withScaffolds(bs, scaffoldRef.current));
  }, [scaffoldKey]);

  const dirty = canonical(blocks) !== canonical(baseBlocks);
  const editable = doc.editable;

  // 편집이 없을 때 서버에 새 revision 이 생기면 그대로 따라간다. 편집 중이면 덮지 않는다.
  useEffect(() => {
    if (doc.revision_no !== base && !dirty) reset(doc);
  }, [doc, base, dirty, reset]);

  useBlocker({
    shouldBlockFn: () => dirty && !window.confirm("저장하지 않은 편집이 있습니다. 이 화면을 나갈까요?"),
    enableBeforeUnload: () => dirty,
  });

  // 근거로 연결한 주장의 문장·상태
  const claimIds = useMemo(() => [...new Set(blocks.flatMap((b) => b.claim_ids))].sort(), [blocks]);
  const claims = useQuery({
    queryKey: ["claims-brief", claimIds.join(",")],
    queryFn: () => api.claimsByIds(claimIds),
    enabled: claimIds.length > 0,
    placeholderData: keepPreviousData,
    staleTime: 60_000,
  });
  const claimMap = useMemo(() => new Map<string, ClaimBrief>((claims.data ?? []).map((c) => [c.id, c])), [claims.data]);

  // 글자·바이트 수: 편집 중이면 서버 코드로 다시 센다 (저장 안 함)
  const texts = useMemo(() => blocks.map((b) => b.text), [blocks]);
  const debounced = useDebounced(texts, 350);
  const live = useQuery({
    queryKey: ["count", doc.id, doc.count_rule, doc.char_limit, doc.byte_limit, debounced],
    queryFn: ({ signal }) => api.countDraft(appId, doc.id, debounced, signal),
    enabled: dirty && editable,
    placeholderData: keepPreviousData,
    staleTime: Infinity,
  });
  const counts = dirty && live.data ? live.data : doc.counts;
  const counting = dirty && (live.isFetching || debounced !== texts);

  const validate = useQuery({
    queryKey: ["validate", doc.id, doc.revision_no],
    queryFn: () => api.validateDocument(appId, doc.id, { revision: doc.revision_no }),
    staleTime: Infinity,
  });

  const save = useMutation({
    mutationFn: (opts: { baseRevision: number; reason?: string }) =>
      api.saveRevision(appId, doc.id, {
        base_revision: opts.baseRevision,
        operations: [
          {
            op: "set_blocks",
            blocks: blocks
              .filter((b) => b.text.trim())
              .map((b) => ({ id: b.id, text: b.text, claim_ids: b.claim_ids, spans: spansForSave(b.text, b.spans) })),
          },
        ],
        reason: opts.reason ?? null,
        // 같은 기준·같은 내용을 두 번 보내면 서버가 처음 결과를 돌려준다 (중복 저장 방지)
        idempotency_key: `${doc.id}:${opts.baseRevision}:${fnv1a(canonical(blocks))}`,
      }),
    onSuccess: async () => {
      const fresh = await qc.fetchQuery({ queryKey: ["document", doc.id], queryFn: () => api.document(appId, doc.id) });
      reset(fresh);
      if (appId) {
        qc.invalidateQueries({ queryKey: ["application", appId] });
        qc.invalidateQueries({ queryKey: ["application-checks", appId] });
        qc.invalidateQueries({ queryKey: ["applications"] });
      } else qc.invalidateQueries({ queryKey: ["shared-documents"] });
      qc.invalidateQueries({ queryKey: ["revisions", doc.id] });
    },
    onError: (err) => {
      if (err instanceof ApiError && err.status === 409 && typeof err.detail === "object" && err.detail) {
        const d = err.detail as RevisionConflictDetail;
        if (d.code === "revision_conflict") {
          setConflict(d);
          qc.invalidateQueries({ queryKey: ["document", doc.id] });
        }
      }
    },
  });

  // Ctrl/Cmd+S 저장
  const saveRef = useRef<() => void>(() => {});
  saveRef.current = () => {
    if (editable && dirty && !conflict && !save.isPending) save.mutate({ baseRevision: base });
  };
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "s") {
        e.preventDefault();
        saveRef.current();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const blockNo = useCallback(
    (blockId: string) => {
      const i = blocks.findIndex((b) => b.id === blockId);
      return i < 0 ? null : i;
    },
    [blocks],
  );
  const jump = (blockId: string) => {
    const b = blocks.find((x) => x.id === blockId);
    if (b) {
      setActiveKey(b.key);
      setFocus({ key: b.key, n: ++focusN.current });
    }
  };
  const activeBlock = blocks.find((b) => b.key === activeKey) ?? null;
  const pickBlock = blocks.find((b) => b.key === pickFor?.key) ?? null;
  const saveError =
    save.error && !(save.error instanceof ApiError && save.error.status === 409 && conflict) ? save.error : null;

  const pickQuery = useMemo(
    () =>
      [pickBlock?.text.trim().slice(0, 200), doc.question_prompt?.trim().slice(0, 200)]
        .filter(Boolean)
        .join(" ")
        .slice(0, 500),
    // 고르는 창을 열 때의 문단·문항으로 정한다 (창이 열린 동안 근거를 더해도 검색어는 그대로)
    [pickFor?.key, pickFor?.range?.quote, doc.question_prompt],
  );
  const warnings = (validate.data?.findings ?? []).filter((f) => (FINDING[f.code]?.group ?? "info") !== "info").length;

  const notices = (
    <>
      {doc.source ? (
        <p className="mb-4 flex flex-wrap items-center gap-x-1.5 text-[13px] text-muted">
          <Copy size={14} />
          <span>
            공용 문서{" "}
            <Link
              to="/documents/$documentId"
              params={{ documentId: doc.source.document_id }}
              className="text-fg underline underline-offset-2"
            >
              {doc.source.title ?? "(지운 문서)"}
            </Link>
            에서 복사했습니다.
          </span>
          {doc.source.current_revision &&
          doc.source.revision_no &&
          doc.source.current_revision > doc.source.revision_no ? (
            <span>원본은 그 뒤에 바뀌었습니다. 이 문서에는 자동으로 들어오지 않습니다.</span>
          ) : null}
        </p>
      ) : null}

      {!editable ? (
        <p className="mb-4 flex items-center gap-1.5 text-[13px] text-muted">
          <Lock size={14} /> 제출본을 고정한 지원이라 수정할 수 없습니다. 다음 지원은 새로 만듭니다.
        </p>
      ) : null}

      {conflict ? (
        <div
          role="alert"
          className="mb-6 rounded-md border border-danger/40 bg-danger-soft px-3 py-3 text-[13px] text-danger"
        >
          <p className="flex items-center gap-1.5 font-semibold">
            <AlertTriangle size={15} /> 저장 충돌: 다른 곳에서 먼저 저장했습니다
          </p>
          <p className="mt-1 text-fg">
            아직 아무것도 덮어쓰지 않았고, 내 편집은 이 화면에 그대로 있습니다. 어떻게 할지 고릅니다.
          </p>
          <div className="mt-2 flex flex-wrap gap-1.5">
            <Button size="sm" onClick={() => setCompare(true)}>
              최신 글과 비교
            </Button>
            <Button
              size="sm"
              variant="danger"
              onClick={() => docQuery.data && reset(docQuery.data)}
              disabled={docQuery.isFetching}
            >
              내 편집 버리고 최신 불러오기
            </Button>
            <Button
              size="sm"
              variant="primary"
              disabled={save.isPending || docQuery.isFetching}
              onClick={() =>
                save.mutate({
                  baseRevision: conflict.current_revision,
                  reason: `r${base} 편집을 r${conflict.current_revision} 위에 다시 저장`,
                })
              }
            >
              확인했고, 내 편집으로 저장
            </Button>
          </div>
        </div>
      ) : null}
    </>
  );

  const editor = (
    <div className="min-w-0 space-y-3">
      <div className="rounded-lg border border-line bg-surface px-1 py-2 md:px-3 md:py-3">
        <BlockEditor
          blocks={blocks}
          onChange={setBlocks}
          readOnly={!editable || save.isPending}
          claims={claimMap}
          findings={
            !dirty
              ? (validate.data?.findings ?? [])
              : (validate.data?.findings ?? []).filter((f) => {
                  const b = blocks.find((x) => x.id === f.block_id);
                  const orig = baseBlocks.find((x) => x.id === f.block_id);
                  return b && orig && b.text === orig.text;
                })
          }
          activeKey={activeKey}
          onActive={setActiveKey}
          onPickEvidence={(key, range) => setPickFor({ key, range })}
          focusRequest={focus}
        />
      </div>
      <details className="group text-xs text-muted">
        <summary className="flex min-h-9 cursor-pointer list-none items-center gap-1 hover:text-fg sm:min-h-6 [&::-webkit-details-marker]:hidden">
          <ChevronRight size={13} className="transition-transform group-open:rotate-90" />
          쓰는 법
        </summary>
        <p className="mt-1 leading-5">
          Enter 문단 나누기 · Shift+Enter 문단 안 줄바꿈 · 맨 앞에서 Backspace 앞 문단과 합치기 · ⌘/Ctrl+S 저장. 문장을
          고르면 그 문장에만 근거를 붙일 수 있습니다. 근거는 확인한 사실만 셉니다. 글이 없는 문단은 저장하지 않습니다.
        </p>
      </details>
      <ErrorText error={saveError} />
    </div>
  );

  const countBar = (
    <CountBar counts={counts} counting={counting} onSettings={editable ? () => setSettings(true) : undefined} />
  );

  const checks = (
    <>
      <Loaded query={validate}>
        {(r) => <ChecksPanel result={r} dirty={dirty} onJump={jump} blockNo={blockNo} />}
      </Loaded>
      {appId ? (
        <VisibilityJudgePanel
          appId={appId}
          docId={doc.id}
          dirty={dirty}
          blocks={baseBlocks}
          onJump={jump}
          blockNo={blockNo}
        />
      ) : null}
    </>
  );

  const suggestions = (
    <SuggestionsPanel
      appId={appId}
      docId={doc.id}
      base={base}
      dirty={dirty}
      editable={editable && !conflict}
      activeBlockId={activeBlock?.id ?? null}
      blockNo={blockNo}
      onApplied={async () => {
        const fresh = await qc.fetchQuery({
          queryKey: ["document", doc.id],
          queryFn: () => api.document(appId, doc.id),
        });
        reset(fresh);
        qc.invalidateQueries({ queryKey: ["revisions", doc.id] });
        if (appId) qc.invalidateQueries({ queryKey: ["application", appId] });
      }}
    />
  );

  const history = (
    <div className="space-y-4">
      <p className="text-xs text-muted tabular-nums">
        {base ? `지금 r${base}` : "아직 저장 전"} · {int(counts.chars)}자 · {int(counts.bytes)} byte
        {validate.data ? ` · AI 수정 문단 ${Math.round(validate.data.ai_ratio * 100)}%` : ""}
        {doc.source?.revision_no ? ` · 공용 문서 r${doc.source.revision_no}에서 복사` : ""}
      </p>
      <HistoryPanel appId={appId} docId={doc.id} current={doc.current_revision} onOpen={setHistoryRev} />
    </div>
  );

  const batch = (
    <BatchSuggestButton
      appId={appId}
      doc={doc}
      dirty={dirty}
      onShowSuggestions={() => {
        setPanel("ai");
        setSuggestAsk((n) => n + 1);
      }}
    />
  );

  const sheets = (
    <>
      <ClaimPicker
        open={!!pickFor}
        onClose={() => setPickFor(null)}
        initialQuery={pickQuery}
        selected={
          pickFor?.range
            ? (pickBlock?.spans?.find((sp) => sp.quote === pickFor.range!.quote)?.claim_ids ?? [])
            : (pickBlock?.claim_ids ?? [])
        }
        title={
          pickBlock
            ? pickFor?.range
              ? `문단 ${blocks.indexOf(pickBlock) + 1}의 고른 문장 근거: “${pickFor.range.quote.slice(0, 30)}${pickFor.range.quote.length > 30 ? "…" : ""}”`
              : `문단 ${blocks.indexOf(pickBlock) + 1} 근거 연결`
            : "근거 연결"
        }
        onPick={(c) => {
          if (!pickFor) return;
          const range = pickFor.range;
          setBlocks((bs) =>
            bs.map((b) => {
              if (b.key !== pickFor.key) return b;
              if (!range) return b.claim_ids.includes(c.id) ? b : { ...b, claim_ids: [...b.claim_ids, c.id] };
              // 같은 문장에 이미 붙인 근거가 있으면 주장만 더한다
              const spans = [...(b.spans ?? [])];
              const at = spans.findIndex((sp) => sp.quote === range.quote && sp.at === range.start);
              if (at >= 0) {
                const sp = spans[at]!;
                if (!sp.claim_ids.includes(c.id)) spans[at] = { ...sp, claim_ids: [...sp.claim_ids, c.id] };
              } else spans.push({ quote: range.quote, at: range.start, claim_ids: [c.id] });
              return { ...b, spans };
            }),
          );
        }}
      />
      <DocumentExportSheet appId={appId} doc={doc} dirty={dirty} open={exporting} onClose={() => setExporting(false)} />
      <SettingsSheet appId={appId} doc={doc} open={settings} onClose={() => setSettings(false)} />
      <CompareSheet
        open={compare}
        onClose={() => setCompare(false)}
        latest={docQuery.data}
        mine={blocks
          .map((b) => b.text)
          .filter((t) => t.trim())
          .join("\n")}
        base={base}
      />
      <RevisionSheet appId={appId} docId={doc.id} revision={historyRev} onClose={() => setHistoryRev(null)} />
    </>
  );

  const parts: EditorParts = {
    doc,
    dirty,
    editable,
    saved: base > 0,
    counts,
    saving: save.isPending,
    canSave: editable && dirty && !conflict && !save.isPending,
    onSave: () => save.mutate({ baseRevision: base }),
    openExport: () => setExporting(true),
    openSettings: () => setSettings(true),
    warnings,
    suggestAsk,
    notices,
    countBar,
    editor,
    checks,
    suggestions,
    batch,
    history,
    sheets,
  };
  if (frame) return <>{frame(parts)}</>;

  return (
    <div>
      <PageHeader
        back={
          appId
            ? { to: `/applications/${appId}`, label: app.data?.title ?? "지원" }
            : { to: "/documents", label: "공용 문서" }
        }
        title={
          <>
            {doc.question_index !== null ? `${doc.question_index + 1}. ` : ""}
            {doc.title}
          </>
        }
        subtitle={
          <span className="flex flex-wrap items-center gap-x-1.5 tabular-nums" aria-live="polite">
            <span>{DOC_KIND[doc.kind] ?? doc.kind}</span>
            {appId === null ? <span>· 공용</span> : null}
            {dirty ? (
              <span className="font-medium text-fg">· 저장 안 함</span>
            ) : base ? (
              <span>· 저장됨</span>
            ) : (
              <span>· 저장 전</span>
            )}
            {!editable ? (
              <span className="inline-flex items-center gap-1">
                · <Lock size={12} /> 읽기 전용
              </span>
            ) : null}
          </span>
        }
        actions={
          <>
            {batch}
            <Button size="sm" onClick={() => setExporting(true)} disabled={!doc.current_revision}>
              <Download size={14} /> 내보내기
            </Button>
            {editable ? (
              <Button variant="primary" size="sm" onClick={parts.onSave} disabled={!parts.canSave}>
                <Save size={14} /> {save.isPending ? "저장하는 중" : "저장"}
              </Button>
            ) : null}
          </>
        }
      />
      {notices}
      <div className="grid grid-cols-1 gap-y-10 lg:grid-cols-[minmax(0,1fr)_minmax(0,380px)] lg:gap-x-12">
        <div className="min-w-0 space-y-3">
          {doc.question_prompt ? <h2 className="text-sm leading-6 font-normal">{doc.question_prompt}</h2> : null}
          {countBar}
          {editor}
        </div>
        <aside className="min-w-0">
          <h2 className="sr-only">검사·수정안·기록</h2>
          <Tabs<Panel>
            value={panel}
            onChange={setPanel}
            items={[
              { value: "checks", label: "검사" },
              { value: "ai", label: "AI 수정안" },
              { value: "history", label: "기록" },
            ]}
          />
          {panel === "checks" ? checks : null}
          {panel === "ai" ? suggestions : null}
          {panel === "history" ? history : null}
        </aside>
      </div>
      {sheets}
    </div>
  );
}

/** 글자 수 한 줄: "238 / 700자"만. 바이트·세는 기준은 펼쳐서 본다. 숫자는 서버 코드가 센다. */
function CountBar({
  counts,
  counting,
  onSettings,
}: {
  counts: TextCounts;
  counting: boolean;
  onSettings?: () => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className={cn("text-[13px] transition-opacity", counting && "opacity-60")}>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1" aria-live="polite">
        <span className={cn("font-semibold tabular-nums", counts.over_char_limit && "text-danger")}>
          {int(counts.chars)}
          {counts.char_limit ? ` / ${int(counts.char_limit)}` : ""}자
        </span>
        {counts.over_char_limit ? <span className="font-medium text-danger">분량 초과</span> : null}
        {counts.over_byte_limit ? <span className="font-medium text-danger">바이트 제한 초과</span> : null}
        {counts.unencodable.length ? (
          <span className="text-xs font-medium">양식에 없는 문자: {counts.unencodable.join(" ")}</span>
        ) : null}
        <button
          type="button"
          aria-expanded={open}
          onClick={() => setOpen((o) => !o)}
          className="inline-flex min-h-9 items-center text-xs text-muted underline underline-offset-2 hover:text-fg sm:min-h-6"
        >
          세는 기준
        </button>
        {onSettings ? (
          <Button
            size="icon"
            variant="ghost"
            className="-mr-2 ml-auto sm:h-8 sm:w-8"
            onClick={onSettings}
            aria-label="제한·세는 기준 바꾸기"
          >
            <Settings2 size={15} />
          </Button>
        ) : null}
      </div>
      {open ? (
        <p className="mt-1 text-xs text-muted tabular-nums">
          {int(counts.bytes)}
          {counts.byte_limit ? ` / ${int(counts.byte_limit)}` : ""} byte · {SPACES_RULE[counts.rule.spaces]} ·{" "}
          {NEWLINE_RULE[counts.rule.newlines]} · {counts.rule.encoding} · 공백 포함 {int(counts.chars_with_spaces)} /
          제외 {int(counts.chars_without_spaces)}
        </p>
      ) : null}
    </div>
  );
}

function SettingsSheet({
  appId,
  doc,
  open,
  onClose,
}: {
  appId: string | null;
  doc: AppDocument;
  open: boolean;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const [form, setForm] = useState({
    title: doc.title,
    char: doc.char_limit?.toString() ?? "",
    byte: doc.byte_limit?.toString() ?? "",
    rule: doc.count_rule,
  });
  useEffect(() => {
    if (open)
      setForm({
        title: doc.title,
        char: doc.char_limit?.toString() ?? "",
        byte: doc.byte_limit?.toString() ?? "",
        rule: doc.count_rule,
      });
  }, [open, doc]);
  const save = useMutation({
    mutationFn: () =>
      api.patchDocument(appId, doc.id, {
        title: form.title.trim() || doc.title,
        char_limit: form.char ? Number(form.char) : null,
        byte_limit: form.byte ? Number(form.byte) : null,
        count_rule: form.rule,
      }),
    onSuccess: (d) => {
      qc.setQueryData(["document", doc.id], d);
      if (appId) qc.invalidateQueries({ queryKey: ["application", appId] });
      else qc.invalidateQueries({ queryKey: ["shared-documents"] });
      qc.invalidateQueries({ queryKey: ["validate", doc.id] });
      onClose();
    },
  });
  const rule = (k: keyof CountRule, v: string) => setForm({ ...form, rule: { ...form.rule, [k]: v } });
  return (
    <Sheet
      open={open}
      onOpenChange={(o) => !o && onClose()}
      title="문서 설정"
      description="제한과 세는 기준은 지원 양식에 맞춥니다. 숫자는 항상 서버 코드가 셉니다."
    >
      <div className="space-y-3">
        <div>
          <Label htmlFor="ds-title">제목</Label>
          <Input
            id="ds-title"
            value={form.title}
            maxLength={300}
            onChange={(e) => setForm({ ...form, title: e.target.value })}
          />
        </div>
        <div className="grid grid-cols-2 gap-2">
          <div>
            <Label htmlFor="ds-char">글자 제한</Label>
            <Input
              id="ds-char"
              type="number"
              min={1}
              inputMode="numeric"
              value={form.char}
              onChange={(e) => setForm({ ...form, char: e.target.value })}
            />
          </div>
          <div>
            <Label htmlFor="ds-byte">바이트 제한</Label>
            <Input
              id="ds-byte"
              type="number"
              min={1}
              inputMode="numeric"
              value={form.byte}
              onChange={(e) => setForm({ ...form, byte: e.target.value })}
            />
          </div>
        </div>
        <div className="grid gap-2 sm:grid-cols-3">
          <div>
            <Label htmlFor="ds-sp">공백</Label>
            <Select id="ds-sp" value={form.rule.spaces} onChange={(e) => rule("spaces", e.target.value)}>
              <option value="include">공백 포함</option>
              <option value="exclude">공백 제외</option>
            </Select>
          </div>
          <div>
            <Label htmlFor="ds-nl">줄바꿈</Label>
            <Select id="ds-nl" value={form.rule.newlines} onChange={(e) => rule("newlines", e.target.value)}>
              <option value="one">1자</option>
              <option value="two">2자 (\r\n)</option>
              <option value="none">세지 않음</option>
            </Select>
          </div>
          <div>
            <Label htmlFor="ds-enc">바이트 기준</Label>
            <Select id="ds-enc" value={form.rule.encoding} onChange={(e) => rule("encoding", e.target.value)}>
              <option value="utf-8">UTF-8 (한글 3byte)</option>
              <option value="cp949">CP949 (한글 2byte)</option>
              <option value="euc-kr">EUC-KR</option>
            </Select>
          </div>
        </div>
        <ErrorText error={save.error} />
        <Button variant="primary" onClick={() => save.mutate()} disabled={save.isPending}>
          저장
        </Button>
      </div>
    </Sheet>
  );
}

function CompareSheet({
  open,
  onClose,
  latest,
  mine,
  base,
}: {
  open: boolean;
  onClose: () => void;
  latest: AppDocument | undefined;
  mine: string;
  base: number;
}) {
  return (
    <Sheet open={open} onOpenChange={(o) => !o && onClose()} title="최신 글과 내 편집 비교" width="lg">
      <div className="grid gap-8 md:grid-cols-2 md:gap-6">
        <section>
          <h3 className="mb-2 text-[13px] font-semibold">서버 최신 r{latest?.revision_no ?? "?"}</h3>
          <div className="border-t border-line pt-3 text-sm leading-7 whitespace-pre-wrap">
            {latest?.text || <span className="text-muted">(비어 있음)</span>}
          </div>
        </section>
        <section>
          <h3 className="mb-2 text-[13px] font-semibold">내 편집 (r{base} 기준, 저장 안 함)</h3>
          <div className="border-t border-line pt-3 text-sm leading-7 whitespace-pre-wrap">
            {mine || <span className="text-muted">(비어 있음)</span>}
          </div>
        </section>
      </div>
      <p className="mt-3 text-xs text-muted">자동으로 합치지 않습니다. 필요한 문장은 직접 옮긴 뒤 저장하세요.</p>
    </Sheet>
  );
}

function HistoryPanel({
  appId,
  docId,
  current,
  onOpen,
}: {
  appId: string | null;
  docId: string;
  current: number;
  onOpen: (n: number) => void;
}) {
  const q = useQuery({ queryKey: ["revisions", docId, current], queryFn: () => api.revisions(appId, docId) });
  return (
    <Loaded query={q}>
      {(revs) =>
        revs.length ? (
          <ul className="divide-y divide-line border-t border-line text-[13px]">
            {[...revs].reverse().map((r) => (
              <li key={r.id}>
                <button
                  type="button"
                  className="-mx-2 flex w-[calc(100%+1rem)] items-start gap-2 rounded-md px-2 py-2.5 text-left transition-colors duration-150 hover:bg-subtle/60"
                  onClick={() => onOpen(r.revision_no)}
                >
                  <History size={14} className="mt-0.5 shrink-0 text-muted" />
                  <span className="min-w-0 flex-1">
                    <span className="font-medium tabular-nums">r{r.revision_no}</span> ·{" "}
                    {REVISION_ORIGIN[r.origin] ?? r.origin}
                    {r.revision_no === current ? <span className="text-muted"> · 최신</span> : null}
                    <span className="block text-xs text-muted tabular-nums">
                      {formatDateTime(r.created_at)} · {int(r.counts.chars)}자{r.reason ? ` · ${r.reason}` : ""}
                    </span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <p className="border-t border-line pt-3 text-sm text-muted">아직 저장한 기록이 없습니다.</p>
        )
      }
    </Loaded>
  );
}

function RevisionSheet({
  appId,
  docId,
  revision,
  onClose,
}: {
  appId: string | null;
  docId: string;
  revision: number | null;
  onClose: () => void;
}) {
  const q = useQuery({
    queryKey: ["document", docId, "rev", revision],
    queryFn: () => api.document(appId, docId, revision!),
    enabled: revision !== null,
    staleTime: Infinity,
  });
  return (
    <Sheet
      open={revision !== null}
      onOpenChange={(o) => !o && onClose()}
      title={`r${revision ?? ""} 보기`}
      description="저장된 revision 은 바뀌지 않습니다."
      width="lg"
    >
      <Loaded query={q}>
        {(d) => (
          <div className="space-y-2">
            <p className="text-xs text-muted tabular-nums">
              {int(d.counts.chars)}자 · {int(d.counts.bytes)} byte · AI 수정 문단 {Math.round(d.ai_ratio * 100)}%
            </p>
            <div className="space-y-3 text-base leading-8">
              {d.blocks.map((b) => (
                <p key={b.id} className="whitespace-pre-wrap">
                  {b.text}
                </p>
              ))}
              {!d.blocks.length ? <p className="text-muted">(비어 있음)</p> : null}
            </div>
          </div>
        )}
      </Loaded>
    </Sheet>
  );
}

// ---------------------------------------------------------------- 내보내기

/** 저장한 최신 revision 내보내기. 시트·검사 표시는 components/doc/ExportSheet 를 제출본과 같이 쓴다. */
function DocumentExportSheet({
  appId,
  doc,
  dirty,
  open,
  onClose,
}: {
  appId: string | null;
  doc: AppDocument;
  dirty: boolean;
  open: boolean;
  onClose: () => void;
}) {
  return (
    <ExportSheet
      open={open}
      onClose={onClose}
      title="내보내기"
      description="저장한 최신 revision 을 파일로 만듭니다. 만든 파일을 다시 읽어 숨김 글·조작 문구·민감 정보를 검사합니다. 경고만 하고 지우지 않습니다."
      queryKey={["export-check", doc.id, doc.current_revision]}
      enabled={doc.current_revision > 0}
      check={(format) => api.exportCheck(appId, doc.id, format)}
      download={(format, c) => api.exportFile(appId, doc.id, format, c.revision)}
      note={dirty ? <p className="text-xs font-medium">저장하지 않은 편집은 들어가지 않습니다.</p> : null}
    />
  );
}
