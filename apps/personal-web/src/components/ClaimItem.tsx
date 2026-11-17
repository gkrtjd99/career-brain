import type { Claim, ClaimKind, Contribution } from "@career-brain/client";
import { Button, cn, Label, Select, SelectItem, STATUS_LABEL, Textarea, type VerificationStatus } from "@career-brain/ui";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Check, FileText, MessageSquareQuote, Pencil, ThumbsDown, Archive } from "lucide-react";
import { useState } from "react";
import { api } from "../lib/api";
import { CONTRIBUTION_LABEL, KIND_LABEL, ORIGIN_LABEL } from "../lib/labels";
import { ErrorText } from "./common";

const KINDS = Object.keys(KIND_LABEL) as ClaimKind[];

/** 주장 하나: 문장, 근거, 확인·이견·고치기·보관. */
export function ClaimItem({
  claim,
  experienceId,
  onOpenSpan,
}: {
  claim: Claim;
  experienceId: string;
  onOpenSpan: (spanId: string) => void;
}) {
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [askContribution, setAskContribution] = useState(false);
  const done = () => {
    qc.invalidateQueries({ queryKey: ["experience", experienceId] });
    qc.invalidateQueries({ queryKey: ["experiences"] });
    qc.invalidateQueries({ queryKey: ["wiki-md", experienceId] });
  };
  const confirm = useMutation({
    mutationFn: (contribution?: Contribution) => api.confirmClaim(claim.id, { contribution }),
    onSuccess: () => {
      setAskContribution(false);
      done();
    },
  });
  const dispute = useMutation({ mutationFn: () => api.disputeClaim(claim.id), onSuccess: done });
  const retire = useMutation({ mutationFn: () => api.retireClaim(claim.id), onSuccess: done });
  const busy = confirm.isPending || dispute.isPending || retire.isPending;
  const status = claim.status;
  const pending = status === "extracted" || status === "disputed";
  const confirmed = status === "self_confirmed" || status === "evidence_supported";

  const onConfirm = () => {
    if (claim.contribution === "unknown") setAskContribution(true);
    else confirm.mutate(undefined);
  };

  // 확인 대기·이견은 묶음 제목이 상태를 말하므로, 확인된 것(본인·자료)과 보관됨만 글자로 적는다.
  const meta: { text: string; strong?: boolean }[] = [
    ...(confirmed || status === "retired" ? [{ text: STATUS_LABEL[status as VerificationStatus] ?? status }] : []),
    { text: KIND_LABEL[claim.kind] ?? claim.kind },
    // 기여를 모르면 확정 전에 골라야 하므로 진하게
    {
      text: CONTRIBUTION_LABEL[claim.contribution] ?? claim.contribution,
      strong: claim.contribution === "unknown",
    },
    { text: ORIGIN_LABEL[claim.origin] ?? claim.origin },
    ...(claim.evidence.length ? [] : [{ text: "근거 없음" }]),
  ];

  return (
    <article data-status={status} className="py-4">
      {editing ? (
        <ClaimEditor
          claim={claim}
          onDone={() => {
            setEditing(false);
            done();
          }}
          onCancel={() => setEditing(false)}
        />
      ) : (
        <p className="text-sm leading-6">{claim.statement}</p>
      )}

      <p className="mt-1 text-[13px] text-muted">
        {meta.map((m, i) => (
          <span key={i}>
            {i ? " · " : ""}
            <span className={m.strong ? "font-medium text-fg" : undefined}>{m.text}</span>
          </span>
        ))}
      </p>

      {claim.evidence.length ? (
        <ul className="mt-2 space-y-0.5">
          {claim.evidence.map((ev) =>
            ev.kind === "source_span" && ev.span_id ? (
              <li key={ev.id}>
                <button
                  type="button"
                  onClick={() => onOpenSpan(ev.span_id!)}
                  className={cn(
                    "-mx-2 flex w-[calc(100%+1rem)] items-start gap-2 rounded-md px-2 py-1.5 text-left text-[13px] leading-5",
                    "transition-colors duration-150 hover:bg-subtle/60",
                    "focus-visible:outline-2 focus-visible:outline-offset-0 focus-visible:outline-accent",
                  )}
                >
                  <FileText size={14} className="mt-0.5 shrink-0 text-muted" />
                  <span className="min-w-0 flex-1">
                    <span className="line-clamp-2">“{ev.quote}”</span>
                    <span className="text-xs text-muted">
                      {ev.source_title ?? "원문"}
                      {ev.page ? ` · ${ev.page}쪽` : ""} · 원문 보기
                    </span>
                    {ev.weak_quote ? (
                      <span className="block text-xs font-medium text-warn">
                        인용이 약함 — 이 문장의 숫자·핵심 낱말이 인용에 없습니다
                      </span>
                    ) : null}
                  </span>
                </button>
              </li>
            ) : (
              <li key={ev.id} className="flex items-start gap-2 py-1.5 text-[13px] leading-5">
                <MessageSquareQuote size={14} className="mt-0.5 shrink-0 text-muted" />
                <span className="min-w-0">
                  <span className="block text-xs text-muted">인터뷰: {ev.question}</span>
                  {ev.answer}
                </span>
              </li>
            ),
          )}
        </ul>
      ) : null}

      {askContribution ? (
        <div className="mt-3 rounded-md border border-line p-3">
          <p className="mb-2 text-[13px]">이 일은 누구의 몫인가요? 확정하려면 골라야 합니다.</p>
          <div className="flex flex-wrap gap-1.5">
            <Button size="sm" className="max-sm:h-9" variant="primary" onClick={() => confirm.mutate("personal")}>
              본인이 한 일
            </Button>
            <Button size="sm" className="max-sm:h-9" onClick={() => confirm.mutate("team")}>
              팀 성과
            </Button>
            <Button size="sm" className="max-sm:h-9" variant="ghost" onClick={() => setAskContribution(false)}>
              취소
            </Button>
          </div>
        </div>
      ) : null}
      <ErrorText error={confirm.error ?? dispute.error ?? retire.error} className="mt-2" />

      {editing || status === "retired" ? null : (
        <div className="mt-3 flex flex-wrap gap-1.5">
          {pending ? (
            <Button size="sm" className="max-sm:h-9" variant="primary" onClick={onConfirm} disabled={busy}>
              <Check size={14} /> 맞아요
            </Button>
          ) : null}
          <Button size="sm" className="max-sm:h-9" onClick={() => setEditing(true)} disabled={busy}>
            <Pencil size={14} /> 수정
          </Button>
          {status !== "disputed" ? (
            <Button size="sm" className="max-sm:h-9" variant="ghost" onClick={() => dispute.mutate()} disabled={busy}>
              <ThumbsDown size={14} /> 사실과 달라요
            </Button>
          ) : null}
          {confirmed || status === "disputed" ? (
            <Button size="sm" className="max-sm:h-9" variant="ghost" onClick={() => retire.mutate()} disabled={busy}>
              <Archive size={14} /> 보관
            </Button>
          ) : null}
        </div>
      )}
    </article>
  );
}

function ClaimEditor({ claim, onDone, onCancel }: { claim: Claim; onDone: () => void; onCancel: () => void }) {
  const [statement, setStatement] = useState(claim.statement);
  const [kind, setKind] = useState<ClaimKind>(claim.kind);
  const [contribution, setContribution] = useState<Contribution>(claim.contribution);
  const save = useMutation({
    mutationFn: () =>
      api.editClaim(claim.id, {
        statement: statement.trim() !== claim.statement ? statement.trim() : undefined,
        kind: kind !== claim.kind ? kind : undefined,
        contribution: contribution !== claim.contribution ? contribution : undefined,
      }),
    onSuccess: onDone,
  });
  return (
    <form
      className="space-y-2"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <Textarea
        value={statement}
        onChange={(e) => setStatement(e.target.value)}
        aria-label="사실 문장"
        maxLength={4000}
        autoFocus
      />
      <div className="grid grid-cols-2 gap-2">
        <div>
          <Label htmlFor={`kind-${claim.id}`}>종류</Label>
          <Select id={`kind-${claim.id}`} value={kind} onValueChange={(v) => setKind(v as ClaimKind)}>
            {KINDS.map((k) => (
              <SelectItem key={k} value={k}>
                {KIND_LABEL[k]}
              </SelectItem>
            ))}
          </Select>
        </div>
        <div>
          <Label htmlFor={`contrib-${claim.id}`}>기여</Label>
          <Select
            id={`contrib-${claim.id}`}
            value={contribution}
            onValueChange={(v) => setContribution(v as Contribution)}
          >
            <SelectItem value="personal">본인</SelectItem>
            <SelectItem value="team">팀</SelectItem>
            <SelectItem value="unknown">모름</SelectItem>
          </Select>
        </div>
      </div>
      <ErrorText error={save.error} />
      <div className="flex gap-1.5">
        <Button
          size="sm"
          className="max-sm:h-9"
          variant="primary"
          type="submit"
          disabled={save.isPending || !statement.trim()}
        >
          저장
        </Button>
        <Button size="sm" className="max-sm:h-9" variant="ghost" onClick={onCancel}>
          취소
        </Button>
      </div>
      <p className="text-[13px] text-muted">
        확정한 사실을 수정해도, 확정 권한이 있는 토큰이면 확정 상태로 남습니다.
      </p>
    </form>
  );
}
