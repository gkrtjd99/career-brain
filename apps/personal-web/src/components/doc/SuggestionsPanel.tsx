import type { Suggestion, SuggestionRequest, SuggestOut } from "@career-brain/client";
import { Button, Checkbox, cn, Disclosure, EmptyState, Label, Textarea } from "@career-brain/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Ban, Check, X } from "lucide-react";
import { useRef, useState } from "react";
import { api } from "../../lib/api";
import { wordDiff } from "../../lib/diff";
import { formatDateTime } from "../../lib/dates";
import { REJECT_REASON, SUGGESTION_STATUS } from "../../lib/labels";
import { toneText } from "../../pages/Application";
import { ErrorText, Loaded } from "../common";

/** 요청 하나의 멱등 키 (보안 문맥이 아니면 randomUUID 가 없을 수 있다) */
function newRequestKey(): string {
  return typeof crypto.randomUUID === "function"
    ? crypto.randomUUID()
    : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

function Diff({ before, after }: { before: string; after: string }) {
  return (
    <p className="text-[13px] leading-6 whitespace-pre-wrap">
      {wordDiff(before, after).map((p, i) =>
        p.kind === "same" ? (
          <span key={i}>{p.text}</span>
        ) : p.kind === "add" ? (
          <ins key={i} className="rounded-sm bg-ok-soft text-ok no-underline">
            {p.text}
          </ins>
        ) : (
          <del key={i} className="rounded-sm bg-danger-soft text-danger">
            {p.text}
          </del>
        ),
      )}
    </p>
  );
}

/**
 * AI 수정안: 요청 → 코드가 사실 변경·조작 문구를 거부 → 사용자가 고른 것만 적용(새 revision).
 * 수정안은 브레인 근거가 되지 않는다.
 */
export function SuggestionsPanel({
  appId,
  docId,
  base,
  dirty,
  editable,
  activeBlockId,
  blockNo,
  onApplied,
}: {
  appId: string | null;
  docId: string;
  base: number;
  dirty: boolean;
  editable: boolean;
  activeBlockId: string | null;
  blockNo: (blockId: string) => number | null;
  onApplied: () => void;
}) {
  const qc = useQueryClient();
  const [instruction, setInstruction] = useState("");
  const [onlyActive, setOnlyActive] = useState(false);
  const [last, setLast] = useState<SuggestOut | null>(null);
  const list = useQuery({
    queryKey: ["suggestions", docId],
    queryFn: () => api.suggestions(appId, docId),
  });
  // c0016: 요청마다 남긴 모델 질문 (일괄 실행에서 나온 것 포함). 실행 이벤트에는 글을 싣지 않는다.
  const requests = useQuery({
    queryKey: ["suggestion-requests", docId],
    queryFn: () => api.suggestionRequests(appId, docId, { limit: 20 }),
  });
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["suggestions", docId] });
    qc.invalidateQueries({ queryKey: ["suggestion-requests", docId] });
  };
  // 응답을 못 받고 같은 요청을 다시 보내면 같은 키를 쓴다 → 서버가 모델을 다시 부르지 않고 저장된 수정안을 준다
  const attempt = useRef<{ params: string; key: string } | null>(null);
  const ask = useMutation({
    mutationFn: () => {
      const body = {
        base_revision: base,
        block_ids: onlyActive && activeBlockId ? [activeBlockId] : null,
        instruction: instruction.trim() || null,
      };
      const params = JSON.stringify([docId, body]);
      if (attempt.current?.params !== params) attempt.current = { params, key: newRequestKey() };
      return api.requestSuggestions(appId, docId, { ...body, idempotency_key: attempt.current.key });
    },
    onSuccess: (res) => {
      attempt.current = null;
      setLast(res);
      refresh();
    },
  });
  const apply = useMutation({
    mutationFn: (s: Suggestion) => api.applySuggestion(appId, docId, s.id, base),
    onSuccess: () => {
      refresh();
      onApplied();
    },
  });
  const dismiss = useMutation({
    mutationFn: (s: Suggestion) => api.dismissSuggestion(appId, docId, s.id),
    onSuccess: refresh,
  });
  const blocked = !editable
    ? "제출한 문서입니다."
    : dirty
      ? "먼저 저장하세요. 수정안은 저장한 글을 기준으로 만듭니다."
      : base === 0
        ? "먼저 글을 쓰고 저장하세요."
        : null;

  return (
    <div className="space-y-6">
      {editable ? (
        <form
          className="space-y-3"
          onSubmit={(e) => {
            e.preventDefault();
            ask.mutate();
          }}
        >
          <p className="text-xs text-muted">
            수치·역할·조직·기술명을 바꾸는 제안은 받지 않습니다. 고른 문단과 근거, 공고 요건을 모델로 보내며 비용이
            듭니다.
          </p>
          <div>
            <Label htmlFor="sg-ins">요청 (선택)</Label>
            <Textarea
              id="sg-ins"
              value={instruction}
              maxLength={500}
              className="min-h-14"
              placeholder="예: 첫 문장을 더 짧게"
              onChange={(e) => setInstruction(e.target.value)}
            />
          </div>
          <label className="flex min-h-9 items-center gap-2 text-[13px]">
            <Checkbox
              checked={onlyActive}
              disabled={!activeBlockId}
              onCheckedChange={(checked) => setOnlyActive(checked)}
            />
            {activeBlockId && blockNo(activeBlockId) !== null
              ? `선택한 문단(${blockNo(activeBlockId)! + 1})만`
              : "선택한 문단만 (저장된 문단을 먼저 누르세요)"}
          </label>
          {blocked ? <p className="text-xs font-medium">{blocked}</p> : null}
          <ErrorText error={ask.error} />
          <Button type="submit" size="sm" disabled={!!blocked || ask.isPending}>
            {ask.isPending ? "요청하는 중" : "수정안 요청"}
          </Button>
        </form>
      ) : null}

      {last ? (
        <div className="space-y-1 text-[13px]" aria-live="polite">
          {last.questions.length ? (
            <div className="pb-2">
              <p className="mb-1 text-[13px] font-semibold">AI가 사실 대신 물어본 것</p>
              <p className="mb-1.5 text-xs text-muted">답은 브레인 인터뷰에 직접 적습니다.</p>
              <ul className="list-disc space-y-0.5 pl-4">
                {last.questions.map((q, i) => (
                  <li key={i}>{q}</li>
                ))}
              </ul>
            </div>
          ) : null}
          {last.dropped ? (
            <p className="text-xs text-muted">없는 문단·같은 글·중복이라 버린 제안 {last.dropped}개</p>
          ) : null}
          {last.replayed ? (
            <p className="text-xs text-muted">같은 요청을 다시 보내 모델을 부르지 않고 저장된 수정안을 보여 줍니다.</p>
          ) : null}
          {!last.suggestions.length ? <p className="text-xs text-muted">이번에는 제안이 없습니다.</p> : null}
        </div>
      ) : null}

      <PastQuestions rows={(requests.data ?? []).filter((r) => r.questions.length && r.id !== last?.request_id)} />

      <ErrorText error={apply.error ?? dismiss.error} />
      <Loaded query={list}>
        {(rows) => {
          const recent = new Set(last?.suggestions.map((x) => x.id) ?? []);
          const isOpen = (s: Suggestion) => s.status === "proposed" || (s.status === "rejected" && recent.has(s.id));
          const open = rows.filter(isOpen);
          const past = rows.filter((s) => !isOpen(s));
          if (!rows.length) return <EmptyState>수정안이 없습니다.</EmptyState>;
          return (
            <div className="space-y-4">
              <ul className="divide-y divide-line border-t border-line">
                {open.map((s) => (
                  <SuggestionCard
                    key={s.id}
                    s={s}
                    no={blockNo(s.block_id)}
                    canApply={editable && !dirty}
                    busy={apply.isPending || dismiss.isPending}
                    onApply={() => apply.mutate(s)}
                    onDismiss={() => dismiss.mutate(s)}
                  />
                ))}
              </ul>
              {past.length ? (
                <Disclosure title={<>지난 수정안 {past.length}개</>} className="text-[13px]">
                  <ul className="mt-2 divide-y divide-line border-t border-line">
                    {past.map((s) => (
                      <SuggestionCard key={s.id} s={s} no={blockNo(s.block_id)} canApply={false} busy />
                    ))}
                  </ul>
                </Disclosure>
              ) : null}
            </div>
          );
        }}
      </Loaded>
    </div>
  );
}

/** 지난 요청에서 AI가 사실 대신 물어본 것. 답은 브레인 인터뷰에 직접 적는다. */
function PastQuestions({ rows }: { rows: SuggestionRequest[] }) {
  if (!rows.length) return null;
  const total = rows.reduce((n, r) => n + r.questions.length, 0);
  return (
    <Disclosure title={<>지난 요청에서 AI가 물어본 것 {total}개</>} className="text-[13px]">
      <ul className="mt-2 divide-y divide-line border-t border-line">
        {rows.map((r) => (
          <li key={r.id} className="py-2.5">
            <p className="mb-1 text-xs text-muted tabular-nums">
              {formatDateTime(r.created_at)}
              {r.run_id ? " · 일괄 실행" : ""} · 제안 {r.proposed} · 거부 {r.rejected}
            </p>
            <ul className="list-disc space-y-0.5 pl-4">
              {r.questions.map((q, i) => (
                <li key={i}>{q}</li>
              ))}
            </ul>
          </li>
        ))}
      </ul>
    </Disclosure>
  );
}

function SuggestionCard({
  s,
  no,
  canApply,
  busy,
  onApply,
  onDismiss,
}: {
  s: Suggestion;
  no: number | null;
  canApply: boolean;
  busy: boolean;
  onApply?: () => void;
  onDismiss?: () => void;
}) {
  const st = SUGGESTION_STATUS[s.status] ?? { label: s.status, tone: "neutral" as const };
  return (
    <li className="py-3">
      <p className="mb-1.5 flex flex-wrap gap-x-1.5 text-xs text-muted tabular-nums">
        <span className={cn(toneText(st.tone))}>{st.label}</span>
        <span>· {no !== null ? `문단 ${no + 1}` : "지금은 없는 문단"}</span>
        <span>· {formatDateTime(s.created_at)}</span>
      </p>
      <Diff before={s.original_text} after={s.suggested_text} />
      {s.reason ? <p className="mt-1.5 text-xs text-muted">이유: {s.reason}</p> : null}
      {s.status === "rejected" ? (
        <div className="mt-2 text-xs text-danger">
          <p className="flex items-center gap-1 font-medium">
            <Ban size={12} /> 사실을 바꾸는 수정이라 적용할 수 없습니다
          </p>
          <ul className="mt-0.5 list-disc pl-4">
            {s.reject_reasons.map((r, i) => (
              <li key={i}>
                {REJECT_REASON[r.code] ?? r.code}
                {r.values?.length ? `: ${r.values.join(", ")}` : ""}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {s.status === "proposed" && onApply && onDismiss ? (
        <div className="mt-2 flex flex-wrap gap-1.5">
          <Button size="sm" variant="primary" onClick={onApply} disabled={!canApply || busy}>
            <Check size={14} /> 적용
          </Button>
          <Button size="sm" onClick={onDismiss} disabled={busy}>
            <X size={14} /> 거절
          </Button>
          {!canApply ? <span className="self-center text-xs text-muted">저장한 뒤 적용할 수 있습니다</span> : null}
        </div>
      ) : null}
    </li>
  );
}
