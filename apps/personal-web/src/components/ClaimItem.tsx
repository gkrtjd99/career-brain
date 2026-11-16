import type { Claim, ClaimKind, Contribution } from "@career-brain/client";
import { Badge, Button, EvidenceCard, Label, Select, Textarea } from "@career-brain/ui";
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

  return (
    <EvidenceCard
      status={status}
      title={editing ? null : claim.statement}
      sourceLabel={
        <span className="flex flex-wrap gap-x-2">
          <span>{KIND_LABEL[claim.kind] ?? claim.kind}</span>
          <span
            className={claim.contribution === "unknown" ? "font-medium text-warn" : undefined}
          >
            {CONTRIBUTION_LABEL[claim.contribution] ?? claim.contribution}
          </span>
          <span>{ORIGIN_LABEL[claim.origin] ?? claim.origin}</span>
        </span>
      }
      actions={
        editing || status === "retired" ? null : (
          <>
            {pending ? (
              <Button size="sm" variant="primary" onClick={onConfirm} disabled={busy}>
                <Check size={14} /> 맞아요
              </Button>
            ) : null}
            <Button size="sm" onClick={() => setEditing(true)} disabled={busy}>
              <Pencil size={14} /> 고치기
            </Button>
            {status !== "disputed" ? (
              <Button size="sm" variant="ghost" onClick={() => dispute.mutate()} disabled={busy}>
                <ThumbsDown size={14} /> 사실과 달라요
              </Button>
            ) : null}
            {confirmed || status === "disputed" ? (
              <Button size="sm" variant="ghost" onClick={() => retire.mutate()} disabled={busy}>
                <Archive size={14} /> 보관
              </Button>
            ) : null}
          </>
        )
      }
    >
      {editing ? (
        <ClaimEditor
          claim={claim}
          onDone={() => {
            setEditing(false);
            done();
          }}
          onCancel={() => setEditing(false)}
        />
      ) : null}

      {claim.evidence.length ? (
        <ul className="mt-2 space-y-1.5">
          {claim.evidence.map((ev) =>
            ev.kind === "source_span" && ev.span_id ? (
              <li key={ev.id}>
                <button
                  onClick={() => onOpenSpan(ev.span_id!)}
                  className="group flex w-full items-start gap-1.5 rounded-md bg-subtle px-2 py-1.5 text-left text-[13px] leading-5 hover:bg-accent-soft"
                >
                  <FileText size={14} className="mt-0.5 shrink-0 text-muted" />
                  <span className="min-w-0 flex-1">
                    <span className="line-clamp-2">“{ev.quote}”</span>
                    <span className="text-xs text-muted">
                      {ev.source_title ?? "원문"}
                      {ev.page ? ` · ${ev.page}쪽` : ""} · 원문 보기
                    </span>
                  </span>
                </button>
              </li>
            ) : (
              <li
                key={ev.id}
                className="flex items-start gap-1.5 rounded-md bg-subtle px-2 py-1.5 text-[13px] leading-5"
              >
                <MessageSquareQuote size={14} className="mt-0.5 shrink-0 text-muted" />
                <span className="min-w-0">
                  <span className="block text-xs text-muted">인터뷰: {ev.question}</span>
                  {ev.answer}
                </span>
              </li>
            ),
          )}
        </ul>
      ) : (
        <div className="mt-2">
          <Badge tone="none">근거 없음</Badge>
        </div>
      )}

      {askContribution ? (
        <div className="mt-2 rounded-md border border-line p-2">
          <p className="mb-1.5 text-[13px]">이 일은 누구의 몫인가요? 확정하려면 골라야 합니다.</p>
          <div className="flex flex-wrap gap-1.5">
            <Button size="sm" variant="primary" onClick={() => confirm.mutate("personal")}>
              본인이 한 일
            </Button>
            <Button size="sm" onClick={() => confirm.mutate("team")}>
              팀 성과
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setAskContribution(false)}>
              취소
            </Button>
          </div>
        </div>
      ) : null}
      <ErrorText error={confirm.error ?? dispute.error ?? retire.error} className="mt-2" />
    </EvidenceCard>
  );
}

function ClaimEditor({
  claim,
  onDone,
  onCancel,
}: {
  claim: Claim;
  onDone: () => void;
  onCancel: () => void;
}) {
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
        aria-label="주장 문장"
        maxLength={4000}
        autoFocus
      />
      <div className="grid grid-cols-2 gap-2">
        <div>
          <Label htmlFor={`kind-${claim.id}`}>종류</Label>
          <Select
            id={`kind-${claim.id}`}
            value={kind}
            onChange={(e) => setKind(e.target.value as ClaimKind)}
          >
            {KINDS.map((k) => (
              <option key={k} value={k}>
                {KIND_LABEL[k]}
              </option>
            ))}
          </Select>
        </div>
        <div>
          <Label htmlFor={`contrib-${claim.id}`}>기여</Label>
          <Select
            id={`contrib-${claim.id}`}
            value={contribution}
            onChange={(e) => setContribution(e.target.value as Contribution)}
          >
            <option value="personal">본인</option>
            <option value="team">팀</option>
            <option value="unknown">모름</option>
          </Select>
        </div>
      </div>
      <ErrorText error={save.error} />
      <div className="flex gap-1.5">
        <Button size="sm" variant="primary" type="submit" disabled={save.isPending || !statement.trim()}>
          저장
        </Button>
        <Button size="sm" variant="ghost" onClick={onCancel}>
          취소
        </Button>
      </div>
      <p className="text-xs text-muted">
        확정된 주장을 고치면, 확정 권한이 있는 토큰에서는 다시 확정 상태로 남습니다.
      </p>
    </form>
  );
}
