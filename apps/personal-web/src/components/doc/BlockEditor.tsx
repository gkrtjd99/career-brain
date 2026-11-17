import type { ClaimBrief, DocBlock, Finding, SpanIn } from "@career-brain/client";
import { Badge, Button, cn, StatusBadge } from "@career-brain/ui";
import { Link2, Plus, Quote, Trash2, X } from "lucide-react";
import { useEffect, useLayoutEffect, useRef, useState, type ClipboardEvent, type KeyboardEvent } from "react";
import { FINDING, FINDING_GROUP } from "../../lib/labels";

export interface DraftBlock {
  /** 화면용 키. 서버 블록 ID와 다를 수 있다 (새 문단은 id가 null) */
  key: string;
  id: string | null;
  text: string;
  origin: string;
  claim_ids: string[];
  /** 문장 단위 근거. 위치 대신 가리키는 글(quote)을 들고 있다가 저장할 때 위치를 다시 찾는다. */
  spans?: DraftSpan[];
}

export interface DraftSpan {
  quote: string;
  /** 처음 위치 (같은 글이 여러 번 나오면 가까운 쪽을 고른다). UTF-16 기준 */
  at: number;
  claim_ids: string[];
}

/** 서버가 저장 전에 하는 정리와 같다: NFC, 줄바꿈 \n, 앞뒤 공백 제거 */
export function normalizeBlockText(text: string): string {
  return text.normalize("NFC").replace(/\r\n?/g, "\n").trim();
}

/** UTF-16 위치 → 코드 포인트 위치 (서버는 코드 포인트로 센다) */
const cp = (text: string, i: number) => Array.from(text.slice(0, i)).length;

/** 서버 블록의 span(코드 포인트 위치) → 화면 span(인용문) */
export function draftSpans(b: DocBlock): DraftSpan[] {
  const chars = Array.from(b.text);
  return (b.spans ?? []).map((sp) => {
    const quote = chars.slice(sp.start, sp.end).join("");
    return { quote, at: chars.slice(0, sp.start).join("").length, claim_ids: [...sp.claim_ids] };
  });
}

/** 저장할 span: 지금 글에서 인용문을 다시 찾는다. 못 찾으면 뺀다 (근거 없는 위치를 만들지 않는다). */
export function spansForSave(text: string, spans: DraftSpan[] | undefined): SpanIn[] {
  const norm = normalizeBlockText(text);
  // 앞 공백을 지운 만큼 처음 위치를 당긴다
  const lead = text.length - text.trimStart().length;
  const out: SpanIn[] = [];
  for (const sp of spans ?? []) {
    const q = sp.quote.normalize("NFC").replace(/\r\n?/g, "\n");
    if (!q.trim()) continue;
    const hits: number[] = [];
    for (let i = norm.indexOf(q); i >= 0; i = norm.indexOf(q, i + 1)) hits.push(i);
    if (!hits.length) continue;
    const want = sp.at - lead;
    const i = hits.reduce((a, b) => (Math.abs(b - want) < Math.abs(a - want) ? b : a));
    out.push({ start: cp(norm, i), end: cp(norm, i + q.length), claim_ids: sp.claim_ids });
  }
  return out;
}

let seq = 0;
export const newKey = () => `k${Date.now().toString(36)}${(seq++).toString(36)}`;

function AutoTextarea({
  value,
  onChange,
  readOnly,
  onKeyDown,
  onPaste,
  onFocus,
  onSelect,
  inputRef,
  label,
  placeholder,
}: {
  value: string;
  onChange: (v: string) => void;
  readOnly?: boolean;
  onKeyDown?: (e: KeyboardEvent<HTMLTextAreaElement>) => void;
  onPaste?: (e: ClipboardEvent<HTMLTextAreaElement>) => void;
  onFocus?: () => void;
  onSelect?: (start: number, end: number) => void;
  inputRef: (el: HTMLTextAreaElement | null) => void;
  label: string;
  placeholder?: string;
}) {
  const ref = useRef<HTMLTextAreaElement | null>(null);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, [value]);
  return (
    <textarea
      ref={(el) => {
        ref.current = el;
        inputRef(el);
      }}
      value={value}
      rows={1}
      readOnly={readOnly}
      aria-label={label}
      placeholder={placeholder}
      onChange={(e) => onChange(e.target.value)}
      onKeyDown={onKeyDown}
      onPaste={onPaste}
      onFocus={onFocus}
      onSelect={(e) => onSelect?.(e.currentTarget.selectionStart, e.currentTarget.selectionEnd)}
      className="block w-full resize-none overflow-hidden bg-transparent text-base leading-8 text-fg outline-none placeholder:text-muted"
    />
  );
}

/**
 * 문단(블록) 편집기. 문단마다 안정된 ID와 근거 연결을 가진다.
 * Enter = 문단 나누기, Shift+Enter = 문단 안 줄바꿈, 문단 맨 앞 Backspace = 앞 문단과 합치기.
 * 빈 줄이 있는 글을 붙여넣으면 문단으로 나눈다. 한글 조합 중 Enter는 건드리지 않는다.
 */
export function BlockEditor({
  blocks,
  onChange,
  readOnly,
  claims,
  findings,
  activeKey,
  onActive,
  onPickEvidence,
  focusRequest,
}: {
  blocks: DraftBlock[];
  onChange: (next: DraftBlock[]) => void;
  readOnly: boolean;
  claims: Map<string, ClaimBrief>;
  /** 저장한 revision 검사 결과 (block_id 기준) */
  findings: Finding[];
  activeKey: string | null;
  onActive: (key: string) => void;
  /** range 가 있으면 그 문장(선택한 부분)에만 근거를 붙인다 */
  onPickEvidence: (key: string, range?: { start: number; end: number; quote: string }) => void;
  /** 바깥에서 특정 문단으로 이동할 때 */
  focusRequest?: { key: string; at?: number; n: number } | null;
}) {
  const refs = useRef(new Map<string, HTMLTextAreaElement>());
  // 문단 안에서 고른 글 (문장 단위 근거용)
  const [selection, setSelection] = useState<{ key: string; start: number; end: number } | null>(null);
  const pending = useRef<{ key: string; at: number } | null>(null);
  // 문장 근거를 끊을 때 그 근거가 문단 근거에도 있으면 같이 뺄지 묻는다 (shared = 같이 뺄 수 있는 claim)
  const [unlinking, setUnlinking] = useState<{ key: string; si: number; shared: string[] } | null>(null);

  useEffect(() => {
    if (!pending.current) return;
    const { key, at } = pending.current;
    const el = refs.current.get(key);
    if (el) {
      el.focus();
      el.setSelectionRange(at, at);
      pending.current = null;
    }
  });

  useEffect(() => {
    if (!focusRequest) return;
    const el = refs.current.get(focusRequest.key);
    if (el) {
      el.scrollIntoView({ block: "center", behavior: "smooth" });
      el.focus({ preventScroll: true });
      if (focusRequest.at !== undefined) el.setSelectionRange(focusRequest.at, focusRequest.at);
    }
  }, [focusRequest]);

  const update = (i: number, patch: Partial<DraftBlock>) =>
    onChange(blocks.map((b, j) => (j === i ? { ...b, ...patch } : b)));

  /** 문장 근거 끊기. 같은 근거가 문단 근거에도 있고 다른 문장이 쓰지 않으면 먼저 묻는다. */
  const unlinkSpan = (i: number, b: DraftBlock, si: number) => {
    const spans = b.spans ?? [];
    const others = spans.filter((_, j) => j !== si);
    const shared = (spans[si]?.claim_ids ?? []).filter(
      (c) => b.claim_ids.includes(c) && !others.some((o) => o.claim_ids.includes(c)),
    );
    if (!shared.length) {
      update(i, { spans: others });
      return;
    }
    setUnlinking({ key: b.key, si, shared });
  };
  const finishUnlink = (i: number, b: DraftBlock, alsoParagraph: boolean) => {
    if (!unlinking) return;
    const others = (b.spans ?? []).filter((_, j) => j !== unlinking.si);
    update(i, {
      spans: others,
      ...(alsoParagraph ? { claim_ids: b.claim_ids.filter((c) => !unlinking.shared.includes(c)) } : {}),
    });
    setUnlinking(null);
  };

  const onKeyDown = (i: number) => (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (readOnly || e.nativeEvent.isComposing || e.keyCode === 229) return;
    const el = e.currentTarget;
    const b = blocks[i]!;
    if (e.key === "Enter" && !e.shiftKey && !e.metaKey && !e.ctrlKey && !e.altKey) {
      e.preventDefault();
      const at = el.selectionStart;
      const end = el.selectionEnd;
      const next: DraftBlock = { key: newKey(), id: null, text: b.text.slice(end), origin: "user", claim_ids: [] };
      // 문장 근거는 가리키는 글이 간 문단으로 따라간다
      next.spans = (b.spans ?? []).filter((sp) => sp.at >= end).map((sp) => ({ ...sp, at: sp.at - end }));
      const copy = [...blocks];
      copy.splice(i, 1, { ...b, text: b.text.slice(0, at), spans: (b.spans ?? []).filter((sp) => sp.at < at) }, next);
      pending.current = { key: next.key, at: 0 };
      onChange(copy);
    } else if (e.key === "Backspace" && i > 0 && el.selectionStart === 0 && el.selectionEnd === 0) {
      e.preventDefault();
      const prev = blocks[i - 1]!;
      const joined: DraftBlock = {
        ...prev,
        text: prev.text + b.text,
        claim_ids: [...new Set([...prev.claim_ids, ...b.claim_ids])],
        spans: [...(prev.spans ?? []), ...(b.spans ?? []).map((sp) => ({ ...sp, at: sp.at + prev.text.length }))],
      };
      const copy = [...blocks];
      copy.splice(i - 1, 2, joined);
      pending.current = { key: prev.key, at: prev.text.length };
      onChange(copy);
    } else if (e.key === "ArrowUp" && i > 0 && el.selectionStart === 0 && el.selectionEnd === 0) {
      const prev = blocks[i - 1]!;
      const target = refs.current.get(prev.key);
      if (target) {
        e.preventDefault();
        target.focus();
        target.setSelectionRange(prev.text.length, prev.text.length);
      }
    } else if (
      e.key === "ArrowDown" &&
      i < blocks.length - 1 &&
      el.selectionStart === b.text.length &&
      el.selectionEnd === b.text.length
    ) {
      const target = refs.current.get(blocks[i + 1]!.key);
      if (target) {
        e.preventDefault();
        target.focus();
        target.setSelectionRange(0, 0);
      }
    }
  };

  const onPaste = (i: number) => (e: ClipboardEvent<HTMLTextAreaElement>) => {
    if (readOnly) return;
    const text = e.clipboardData.getData("text/plain");
    if (!/\n[ \t]*\n/.test(text)) return;
    e.preventDefault();
    const el = e.currentTarget;
    const b = blocks[i]!;
    const parts = text.replace(/\r\n?/g, "\n").split(/\n[ \t]*\n+/);
    const head = b.text.slice(0, el.selectionStart) + parts[0];
    const tail = b.text.slice(el.selectionEnd);
    const middle = parts.slice(1, -1);
    const lastPart = parts.length > 1 ? parts[parts.length - 1]! : "";
    const created: DraftBlock[] = [
      ...middle.map((t) => ({ key: newKey(), id: null, text: t, origin: "user", claim_ids: [] })),
      { key: newKey(), id: null, text: lastPart + tail, origin: "user", claim_ids: [] },
    ];
    const copy = [...blocks];
    copy.splice(i, 1, { ...b, text: head }, ...created);
    pending.current = { key: created[created.length - 1]!.key, at: lastPart.length };
    onChange(copy);
  };

  const byBlock = new Map<string, Finding[]>();
  for (const f of findings) {
    if (!f.block_id) continue;
    byBlock.set(f.block_id, [...(byBlock.get(f.block_id) ?? []), f]);
  }

  return (
    <div className="space-y-1">
      {blocks.map((b, i) => {
        const fs = b.id ? (byBlock.get(b.id) ?? []) : [];
        const marks = [...new Set(fs.filter((f) => FINDING[f.code]?.group !== "info").map((f) => f.code))];
        const active = activeKey === b.key;
        return (
          <div
            key={b.key}
            className={cn(
              "group relative rounded-md py-1 pr-1 pl-3 transition-colors duration-150",
              active ? "bg-subtle/60" : "hover:bg-subtle/30",
            )}
          >
            <AutoTextarea
              value={b.text}
              readOnly={readOnly}
              label={`문단 ${i + 1}`}
              placeholder={
                i === 0 && blocks.length === 1 ? "직접 쓴 문장으로 시작합니다. AI는 먼저 쓰지 않습니다." : undefined
              }
              onChange={(v) => update(i, { text: v })}
              onKeyDown={onKeyDown(i)}
              onPaste={onPaste(i)}
              onFocus={() => onActive(b.key)}
              onSelect={(start, end) =>
                setSelection(end > start && b.text.slice(start, end).trim() ? { key: b.key, start, end } : null)
              }
              inputRef={(el) => {
                if (el) refs.current.set(b.key, el);
                else refs.current.delete(b.key);
              }}
            />
            <div className="flex flex-wrap items-center gap-1 pb-0.5">
              {b.origin === "ai" ? <Badge tone="neutral">AI 수정 문단</Badge> : null}
              {b.claim_ids.map((cid) => {
                const c = claims.get(cid);
                return (
                  <span
                    key={cid}
                    className="inline-flex max-w-full items-center gap-1 rounded border border-line px-1.5 py-0.5 text-xs"
                    title={c?.statement}
                  >
                    {c ? <StatusBadge status={c.status} /> : <Badge tone="none">근거</Badge>}
                    <span className="max-w-[14rem] truncate">{c?.statement ?? "불러오는 중"}</span>
                    {!readOnly ? (
                      <button
                        aria-label="근거 연결 끊기"
                        className="rounded p-0.5 text-muted hover:text-danger"
                        onClick={() => update(i, { claim_ids: b.claim_ids.filter((x) => x !== cid) })}
                      >
                        <X size={12} />
                      </button>
                    ) : null}
                  </span>
                );
              })}
              {(b.spans ?? []).map((sp, si) => (
                <span
                  key={`sp-${si}`}
                  className="inline-flex max-w-full items-center gap-1 rounded border border-line px-1.5 py-0.5 text-xs"
                  title={sp.claim_ids.map((c) => claims.get(c)?.statement ?? c).join("\n")}
                >
                  <Quote size={11} className="shrink-0 text-muted" />
                  <span className="max-w-[12rem] truncate">“{sp.quote}”</span>
                  <span className="text-muted">근거 {sp.claim_ids.length}</span>
                  {!readOnly ? (
                    <button
                      aria-label="문장 근거 끊기"
                      className="rounded p-0.5 text-muted hover:text-danger"
                      onClick={() => unlinkSpan(i, b, si)}
                    >
                      <X size={12} />
                    </button>
                  ) : null}
                </span>
              ))}
              {unlinking?.key === b.key && !readOnly ? (
                <span
                  role="group"
                  aria-label="문장 근거 끊기 확인"
                  className="flex w-full flex-wrap items-center gap-1.5 py-1.5 text-xs"
                >
                  <span className="min-w-0 flex-1">
                    이 문장의 근거 {unlinking.shared.length}개는 문단 근거에도 연결돼 있습니다. 문단 근거에서도 뺄까요?
                  </span>
                  <Button size="sm" onClick={() => finishUnlink(i, b, false)}>
                    문장만 끊기
                  </Button>
                  <Button size="sm" variant="danger" onClick={() => finishUnlink(i, b, true)}>
                    문단 근거에서도 빼기
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => setUnlinking(null)}>
                    취소
                  </Button>
                </span>
              ) : null}
              {marks.map((code) => {
                const info = FINDING[code];
                const tone = info ? FINDING_GROUP[info.group].tone : "neutral";
                return (
                  <Badge key={code} tone={tone}>
                    {info?.label ?? code}
                  </Badge>
                );
              })}
              {!readOnly ? (
                <span
                  className={cn(
                    "ml-auto flex gap-0.5",
                    active ? "" : "md:opacity-0 md:group-focus-within:opacity-100 md:group-hover:opacity-100",
                  )}
                >
                  {selection?.key === b.key ? (
                    <Button
                      size="sm"
                      variant="ghost"
                      // 버튼을 눌러도 문단의 선택이 풀리지 않게 포커스를 옮기지 않는다
                      onMouseDown={(e) => e.preventDefault()}
                      onClick={() =>
                        onPickEvidence(b.key, {
                          start: selection.start,
                          end: selection.end,
                          quote: b.text.slice(selection.start, selection.end),
                        })
                      }
                      aria-label={`문단 ${i + 1}에서 고른 문장 근거 연결`}
                    >
                      <Quote size={14} /> 고른 문장 근거
                    </Button>
                  ) : null}
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => onPickEvidence(b.key)}
                    aria-label={`문단 ${i + 1} 근거 연결`}
                  >
                    <Link2 size={14} /> 근거
                  </Button>
                  {blocks.length > 1 ? (
                    <Button
                      size="sm"
                      variant="ghost"
                      aria-label={`문단 ${i + 1} 지우기`}
                      onClick={() => onChange(blocks.filter((_, j) => j !== i))}
                    >
                      <Trash2 size={14} />
                    </Button>
                  ) : null}
                </span>
              ) : null}
            </div>
          </div>
        );
      })}
      {!readOnly ? (
        <Button
          size="sm"
          variant="ghost"
          className="ml-2"
          onClick={() => {
            const nb: DraftBlock = { key: newKey(), id: null, text: "", origin: "user", claim_ids: [] };
            pending.current = { key: nb.key, at: 0 };
            onChange([...blocks, nb]);
          }}
        >
          <Plus size={14} /> 문단 추가
        </Button>
      ) : null}
    </div>
  );
}
