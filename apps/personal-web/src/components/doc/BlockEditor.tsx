import type { ClaimBrief, Finding } from "@career-brain/client";
import { Badge, Button, cn, StatusBadge } from "@career-brain/ui";
import { Link2, Plus, Trash2, X } from "lucide-react";
import { useEffect, useLayoutEffect, useRef, type ClipboardEvent, type KeyboardEvent } from "react";
import { FINDING, FINDING_GROUP } from "../../lib/labels";

export interface DraftBlock {
  /** 화면용 키. 서버 블록 ID와 다를 수 있다 (새 문단은 id가 null) */
  key: string;
  id: string | null;
  text: string;
  origin: string;
  claim_ids: string[];
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
  onPickEvidence: (key: string) => void;
  /** 바깥에서 특정 문단으로 이동할 때 */
  focusRequest?: { key: string; at?: number; n: number } | null;
}) {
  const refs = useRef(new Map<string, HTMLTextAreaElement>());
  const pending = useRef<{ key: string; at: number } | null>(null);

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

  const onKeyDown = (i: number) => (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (readOnly || e.nativeEvent.isComposing || e.keyCode === 229) return;
    const el = e.currentTarget;
    const b = blocks[i]!;
    if (e.key === "Enter" && !e.shiftKey && !e.metaKey && !e.ctrlKey && !e.altKey) {
      e.preventDefault();
      const at = el.selectionStart;
      const end = el.selectionEnd;
      const next: DraftBlock = { key: newKey(), id: null, text: b.text.slice(end), origin: "user", claim_ids: [] };
      const copy = [...blocks];
      copy.splice(i, 1, { ...b, text: b.text.slice(0, at) }, next);
      pending.current = { key: next.key, at: 0 };
      onChange(copy);
    } else if (e.key === "Backspace" && i > 0 && el.selectionStart === 0 && el.selectionEnd === 0) {
      e.preventDefault();
      const prev = blocks[i - 1]!;
      const joined: DraftBlock = {
        ...prev,
        text: prev.text + b.text,
        claim_ids: [...new Set([...prev.claim_ids, ...b.claim_ids])],
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
              "group relative rounded-md border-l-2 py-1 pr-1 pl-3 transition-colors",
              active ? "border-accent bg-subtle/50" : "border-transparent hover:bg-subtle/30",
            )}
          >
            <AutoTextarea
              value={b.text}
              readOnly={readOnly}
              label={`문단 ${i + 1}`}
              placeholder={i === 0 && blocks.length === 1 ? "직접 쓴 문장으로 시작합니다. AI는 먼저 쓰지 않습니다." : undefined}
              onChange={(v) => update(i, { text: v })}
              onKeyDown={onKeyDown(i)}
              onPaste={onPaste(i)}
              onFocus={() => onActive(b.key)}
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
                    className="inline-flex max-w-full items-center gap-1 rounded border border-line bg-surface px-1.5 py-0.5 text-xs"
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
                <span className={cn("ml-auto flex gap-0.5", active ? "" : "md:opacity-0 md:group-hover:opacity-100")}>
                  <Button size="sm" variant="ghost" onClick={() => onPickEvidence(b.key)} aria-label={`문단 ${i + 1} 근거 연결`}>
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
