import type { AppDocument, ClaimBrief, CountRule, RevisionConflictDetail, TextCounts } from "@career-brain/client";
import { Badge, Button, cn, Input, Label, Select, Sheet } from "@career-brain/ui";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getRouteApi, Link, useBlocker } from "@tanstack/react-router";
import { AlertTriangle, History, Lock, Save, Settings2 } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ClaimPicker } from "../components/ClaimPicker";
import { ErrorText, Loaded, PageHeader, Tabs } from "../components/common";
import { BlockEditor, newKey, type DraftBlock } from "../components/doc/BlockEditor";
import { ChecksPanel } from "../components/doc/ChecksPanel";
import { SuggestionsPanel } from "../components/doc/SuggestionsPanel";
import { api, ApiError } from "../lib/api";
import { formatDateTime, int } from "../lib/format";
import { DOC_KIND, NEWLINE_RULE, REVISION_ORIGIN, SPACES_RULE } from "../lib/labels";

const route = getRouteApi("/applications/$applicationId/documents/$documentId");

function toDraft(doc: AppDocument): DraftBlock[] {
  if (!doc.blocks.length) return [{ key: newKey(), id: null, text: "", origin: "user", claim_ids: [] }];
  return doc.blocks.map((b) => ({ key: newKey(), id: b.id, text: b.text, origin: b.origin, claim_ids: [...b.claim_ids] }));
}

/** 저장할 때 서버가 하는 정리(앞뒤 공백·빈 문단 제외)와 같은 기준으로 비교한다. */
function canonical(blocks: { id: string | null; text: string; claim_ids: string[] }[]) {
  return JSON.stringify(
    blocks
      .map((b) => ({ id: b.id, text: b.text.normalize("NFC").replace(/\r\n?/g, "\n").trim(), c: b.claim_ids }))
      .filter((b) => b.text),
  );
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

export function DocumentPage() {
  const { applicationId, documentId } = route.useParams();
  const q = useQuery({
    queryKey: ["document", documentId],
    queryFn: () => api.document(applicationId, documentId),
  });
  return <Loaded query={q}>{(doc) => <DocumentEditor key={doc.id} appId={applicationId} initial={doc} />}</Loaded>;
}

type Panel = "checks" | "ai" | "history";

function DocumentEditor({ appId, initial }: { appId: string; initial: AppDocument }) {
  const qc = useQueryClient();
  const docQuery = useQuery({
    queryKey: ["document", initial.id],
    queryFn: () => api.document(appId, initial.id),
    initialData: initial,
  });
  const doc = docQuery.data;
  const app = useQuery({ queryKey: ["application", appId], queryFn: () => api.application(appId) });

  // 편집 상태: 마지막으로 불러온(또는 저장한) revision 을 기준(base)으로 둔다.
  const [base, setBase] = useState(doc.revision_no);
  const [baseBlocks, setBaseBlocks] = useState(doc.blocks);
  const [blocks, setBlocks] = useState<DraftBlock[]>(() => toDraft(doc));
  const [conflict, setConflict] = useState<RevisionConflictDetail | null>(null);
  const [activeKey, setActiveKey] = useState<string | null>(null);
  const [pickFor, setPickFor] = useState<string | null>(null);
  const [focus, setFocus] = useState<{ key: string; n: number } | null>(null);
  const [panel, setPanel] = useState<Panel>("checks");
  const [settings, setSettings] = useState(false);
  const [compare, setCompare] = useState(false);
  const [historyRev, setHistoryRev] = useState<number | null>(null);
  const focusN = useRef(0);

  const reset = useCallback((d: AppDocument) => {
    setBase(d.revision_no);
    setBaseBlocks(d.blocks);
    setBlocks(toDraft(d));
    setConflict(null);
  }, []);

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
              .map((b) => ({ id: b.id, text: b.text, claim_ids: b.claim_ids })),
          },
        ],
        reason: opts.reason ?? null,
        // 같은 기준·같은 내용을 두 번 보내면 서버가 처음 결과를 돌려준다 (중복 저장 방지)
        idempotency_key: `${doc.id}:${opts.baseRevision}:${fnv1a(canonical(blocks))}`,
      }),
    onSuccess: async () => {
      const fresh = await qc.fetchQuery({ queryKey: ["document", doc.id], queryFn: () => api.document(appId, doc.id) });
      reset(fresh);
      qc.invalidateQueries({ queryKey: ["application", appId] });
      qc.invalidateQueries({ queryKey: ["application-checks", appId] });
      qc.invalidateQueries({ queryKey: ["applications"] });
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
  const pickBlock = blocks.find((b) => b.key === pickFor) ?? null;
  const docs = app.data?.documents ?? [];
  const idx = docs.findIndex((d) => d.id === doc.id);
  const saveError = save.error && !(save.error instanceof ApiError && save.error.status === 409 && conflict) ? save.error : null;

  return (
    <div>
      <PageHeader
        back={{ to: `/applications/${appId}`, label: app.data?.title ?? "지원" }}
        title={
          <>
            {doc.question_index !== null ? `${doc.question_index + 1}. ` : ""}
            {doc.title}
          </>
        }
        subtitle={
          <span className="flex flex-wrap items-center gap-1.5">
            <span>{DOC_KIND[doc.kind] ?? doc.kind}</span>
            <span>· {base ? `r${base}` : "저장 전"}</span>
            {dirty ? <Badge tone="warn">저장 안 함</Badge> : base ? <Badge tone="ok">저장됨</Badge> : null}
            {!editable ? (
              <Badge tone="none">
                <Lock size={11} /> 읽기 전용
              </Badge>
            ) : null}
          </span>
        }
        actions={
          editable ? (
            <Button
              variant="primary"
              size="sm"
              onClick={() => save.mutate({ baseRevision: base })}
              disabled={!dirty || !!conflict || save.isPending}
            >
              <Save size={14} /> {save.isPending ? "저장하는 중" : "저장"}
            </Button>
          ) : null
        }
      />

      {docs.length > 1 ? (
        <nav className="mb-4 flex flex-wrap gap-1 text-[13px]" aria-label="같은 지원의 문서">
          {docs.map((d, i) => (
            <Link
              key={d.id}
              to="/applications/$applicationId/documents/$documentId"
              params={{ applicationId: appId, documentId: d.id }}
              className={cn(
                "rounded-full border px-2.5 py-0.5",
                i === idx ? "border-accent bg-accent-soft text-accent" : "border-line bg-surface text-muted hover:text-fg",
              )}
            >
              {d.question_index !== null ? `문항 ${d.question_index + 1}` : d.title}
            </Link>
          ))}
        </nav>
      ) : null}

      {!editable ? (
        <p className="mb-4 rounded-lg border border-line bg-subtle/60 px-3 py-2 text-[13px] text-muted">
          제출본을 고정한 지원이라 고칠 수 없습니다. 다음 지원은 새로 만듭니다.
        </p>
      ) : null}

      {conflict ? (
        <div role="alert" className="mb-4 rounded-lg border border-danger/40 bg-danger-soft px-3 py-3 text-[13px] text-danger">
          <p className="flex items-center gap-1.5 font-semibold">
            <AlertTriangle size={15} /> 저장 충돌: 다른 곳에서 r{conflict.current_revision}을 먼저 저장했습니다
          </p>
          <p className="mt-1 text-fg">
            이 편집은 r{base}를 기준으로 했습니다. 아직 아무것도 덮어쓰지 않았고, 내 편집은 이 화면에 그대로 있습니다. 어떻게 할지 고르세요.
          </p>
          <div className="mt-2 flex flex-wrap gap-1.5">
            <Button size="sm" onClick={() => setCompare(true)}>
              최신 글과 비교
            </Button>
            <Button size="sm" variant="danger" onClick={() => docQuery.data && reset(docQuery.data)} disabled={docQuery.isFetching}>
              내 편집 버리고 최신 불러오기
            </Button>
            <Button
              size="sm"
              variant="primary"
              disabled={save.isPending || docQuery.isFetching}
              onClick={() =>
                save.mutate({ baseRevision: conflict.current_revision, reason: `r${base} 편집을 r${conflict.current_revision} 위에 다시 저장` })
              }
            >
              확인했고, 내 편집으로 새 revision 저장
            </Button>
          </div>
        </div>
      ) : null}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,380px)]">
        <div className="min-w-0 space-y-3">
          {doc.question_prompt ? (
            <div className="rounded-lg border border-line bg-subtle/60 px-3 py-2 text-[13px] leading-6">{doc.question_prompt}</div>
          ) : null}
          <CountBar counts={counts} counting={counting} onSettings={editable ? () => setSettings(true) : undefined} />
          <div className="rounded-lg border border-line bg-surface px-2 py-3 md:px-4">
            <BlockEditor
              blocks={blocks}
              onChange={setBlocks}
              readOnly={!editable || save.isPending}
              claims={claimMap}
              findings={!dirty ? (validate.data?.findings ?? []) : (validate.data?.findings ?? []).filter((f) => {
                const b = blocks.find((x) => x.id === f.block_id);
                const orig = baseBlocks.find((x) => x.id === f.block_id);
                return b && orig && b.text === orig.text;
              })}
              activeKey={activeKey}
              onActive={setActiveKey}
              onPickEvidence={setPickFor}
              focusRequest={focus}
            />
          </div>
          <p className="text-xs text-muted">
            Enter 문단 나누기 · Shift+Enter 문단 안 줄바꿈 · 맨 앞에서 Backspace 앞 문단과 합치기 · ⌘/Ctrl+S 저장. 근거는 확인한 주장만 셉니다.
          </p>
          <ErrorText error={saveError} />
        </div>

        <aside className="min-w-0">
          <Tabs<Panel>
            value={panel}
            onChange={setPanel}
            items={[
              { value: "checks", label: "검사" },
              { value: "ai", label: "AI 수정안" },
              { value: "history", label: "기록" },
            ]}
          />
          {panel === "checks" ? (
            <Loaded query={validate}>
              {(r) => <ChecksPanel result={r} dirty={dirty} onJump={jump} blockNo={blockNo} />}
            </Loaded>
          ) : null}
          {panel === "ai" ? (
            <SuggestionsPanel
              appId={appId}
              docId={doc.id}
              base={base}
              dirty={dirty}
              editable={editable && !conflict}
              activeBlockId={activeBlock?.id ?? null}
              blockNo={blockNo}
              onApplied={async () => {
                const fresh = await qc.fetchQuery({ queryKey: ["document", doc.id], queryFn: () => api.document(appId, doc.id) });
                reset(fresh);
                qc.invalidateQueries({ queryKey: ["revisions", doc.id] });
                qc.invalidateQueries({ queryKey: ["application", appId] });
              }}
            />
          ) : null}
          {panel === "history" ? <HistoryPanel appId={appId} docId={doc.id} current={doc.current_revision} onOpen={setHistoryRev} /> : null}
        </aside>
      </div>

      <ClaimPicker
        open={!!pickFor}
        onClose={() => setPickFor(null)}
        selected={pickBlock?.claim_ids ?? []}
        title={pickBlock ? `문단 ${blocks.indexOf(pickBlock) + 1} 근거 연결` : "근거 연결"}
        onPick={(c) => {
          if (!pickFor) return;
          setBlocks((bs) =>
            bs.map((b) => (b.key === pickFor && !b.claim_ids.includes(c.id) ? { ...b, claim_ids: [...b.claim_ids, c.id] } : b)),
          );
        }}
      />
      <SettingsSheet appId={appId} doc={doc} open={settings} onClose={() => setSettings(false)} />
      <CompareSheet
        open={compare}
        onClose={() => setCompare(false)}
        latest={docQuery.data}
        mine={blocks.map((b) => b.text).filter((t) => t.trim()).join("\n")}
        base={base}
      />
      <RevisionSheet appId={appId} docId={doc.id} revision={historyRev} onClose={() => setHistoryRev(null)} />
    </div>
  );
}

function CountBar({ counts, counting, onSettings }: { counts: TextCounts; counting: boolean; onSettings?: () => void }) {
  const over = counts.over_char_limit || counts.over_byte_limit;
  return (
    <div
      className={cn(
        "flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border px-3 py-2 text-[13px]",
        over ? "border-danger/40 bg-danger-soft text-danger" : "border-line bg-surface",
        counting && "opacity-70",
      )}
      aria-live="polite"
    >
      <span className="font-medium tabular-nums">
        {int(counts.chars)}
        {counts.char_limit ? ` / ${int(counts.char_limit)}` : ""}자
      </span>
      <span className="tabular-nums">
        {int(counts.bytes)}
        {counts.byte_limit ? ` / ${int(counts.byte_limit)}` : ""} byte
      </span>
      <span className="text-xs text-muted">
        {SPACES_RULE[counts.rule.spaces]} · {NEWLINE_RULE[counts.rule.newlines]} · {counts.rule.encoding} · 공백 포함{" "}
        {int(counts.chars_with_spaces)} / 제외 {int(counts.chars_without_spaces)}
      </span>
      {counts.unencodable.length ? (
        <span className="text-xs text-warn">양식에 없는 문자: {counts.unencodable.join(" ")}</span>
      ) : null}
      <span className="ml-auto flex items-center gap-1 text-xs text-muted">
        서버 계산
        {onSettings ? (
          <Button size="sm" variant="ghost" onClick={onSettings} aria-label="세는 기준 바꾸기">
            <Settings2 size={14} />
          </Button>
        ) : null}
      </span>
    </div>
  );
}

function SettingsSheet({ appId, doc, open, onClose }: { appId: string; doc: AppDocument; open: boolean; onClose: () => void }) {
  const qc = useQueryClient();
  const [form, setForm] = useState({
    title: doc.title,
    char: doc.char_limit?.toString() ?? "",
    byte: doc.byte_limit?.toString() ?? "",
    rule: doc.count_rule,
  });
  useEffect(() => {
    if (open)
      setForm({ title: doc.title, char: doc.char_limit?.toString() ?? "", byte: doc.byte_limit?.toString() ?? "", rule: doc.count_rule });
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
      qc.invalidateQueries({ queryKey: ["application", appId] });
      qc.invalidateQueries({ queryKey: ["validate", doc.id] });
      onClose();
    },
  });
  const rule = (k: keyof CountRule, v: string) => setForm({ ...form, rule: { ...form.rule, [k]: v } });
  return (
    <Sheet open={open} onOpenChange={(o) => !o && onClose()} title="문서 설정" description="제한과 세는 기준은 지원 양식에 맞춥니다. 숫자는 항상 서버 코드가 셉니다.">
      <div className="space-y-3">
        <div>
          <Label htmlFor="ds-title">제목</Label>
          <Input id="ds-title" value={form.title} maxLength={300} onChange={(e) => setForm({ ...form, title: e.target.value })} />
        </div>
        <div className="grid grid-cols-2 gap-2">
          <div>
            <Label htmlFor="ds-char">글자 제한</Label>
            <Input id="ds-char" type="number" min={1} inputMode="numeric" value={form.char} onChange={(e) => setForm({ ...form, char: e.target.value })} />
          </div>
          <div>
            <Label htmlFor="ds-byte">바이트 제한</Label>
            <Input id="ds-byte" type="number" min={1} inputMode="numeric" value={form.byte} onChange={(e) => setForm({ ...form, byte: e.target.value })} />
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
      <div className="grid gap-4 md:grid-cols-2">
        <section>
          <h3 className="mb-1 text-[13px] font-semibold">서버 최신 r{latest?.revision_no ?? "?"}</h3>
          <div className="rounded-md border border-line bg-subtle/50 p-3 text-sm leading-7 whitespace-pre-wrap">{latest?.text || "(비어 있음)"}</div>
        </section>
        <section>
          <h3 className="mb-1 text-[13px] font-semibold">내 편집 (r{base} 기준, 저장 안 함)</h3>
          <div className="rounded-md border border-accent/40 bg-surface p-3 text-sm leading-7 whitespace-pre-wrap">{mine || "(비어 있음)"}</div>
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
  appId: string;
  docId: string;
  current: number;
  onOpen: (n: number) => void;
}) {
  const q = useQuery({ queryKey: ["revisions", docId, current], queryFn: () => api.revisions(appId, docId) });
  return (
    <Loaded query={q}>
      {(revs) =>
        revs.length ? (
          <ul className="divide-y divide-line rounded-lg border border-line bg-surface text-[13px]">
            {[...revs].reverse().map((r) => (
              <li key={r.id}>
                <button className="flex w-full items-start gap-2 px-3 py-2 text-left hover:bg-subtle/60" onClick={() => onOpen(r.revision_no)}>
                  <History size={14} className="mt-0.5 shrink-0 text-muted" />
                  <span className="min-w-0 flex-1">
                    <span className="font-medium">r{r.revision_no}</span> · {REVISION_ORIGIN[r.origin] ?? r.origin}
                    {r.revision_no === current ? <Badge tone="ok" className="ml-1">최신</Badge> : null}
                    <span className="block text-xs text-muted">
                      {formatDateTime(r.created_at)} · {int(r.counts.chars)}자{r.reason ? ` · ${r.reason}` : ""}
                    </span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-[13px] text-muted">아직 저장한 기록이 없습니다.</p>
        )
      }
    </Loaded>
  );
}

function RevisionSheet({ appId, docId, revision, onClose }: { appId: string; docId: string; revision: number | null; onClose: () => void }) {
  const q = useQuery({
    queryKey: ["document", docId, "rev", revision],
    queryFn: () => api.document(appId, docId, revision!),
    enabled: revision !== null,
    staleTime: Infinity,
  });
  return (
    <Sheet open={revision !== null} onOpenChange={(o) => !o && onClose()} title={`r${revision ?? ""} 보기`} description="저장된 revision 은 바뀌지 않습니다." width="lg">
      <Loaded query={q}>
        {(d) => (
          <div className="space-y-2">
            <p className="text-xs text-muted">
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
