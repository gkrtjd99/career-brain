import { ANSWER_ASSET_KINDS, type AnswerAsset, type AnswerAssetClaim, type AnswerAssetKind } from "@career-brain/client";
import { Button, ConfirmDialog, Input, Label, Select, SelectItem, Textarea, Tooltip, TooltipContent, TooltipTrigger } from "@career-brain/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getRouteApi, Link, useNavigate } from "@tanstack/react-router";
import { Plus, X } from "lucide-react";
import { useState } from "react";
import { PlaceSheet } from "../components/answers/PlaceSheet";
import { ClaimPicker, type PickedClaim } from "../components/ClaimPicker";
import { errorText, Loaded, PageHeader } from "../components/common";
import { api } from "../lib/api";
import { ANSWER_KIND_LABEL, meErrorText } from "../lib/brainMe";
import { formatDate, formatDateTime } from "../lib/dates";

const route = getRouteApi("/brain/answers/$answerId");
const CONFIRMED = ["self_confirmed", "evidence_supported"];
const message = (e: unknown) => meErrorText(e) ?? errorText(e);

/** 공통 답변 한 편: 글·근거(확인한 사실)·쓴 곳. 지원서에 넣으면 문단마다 같은 근거가 붙는다. */
export function AnswerPage() {
  const { answerId } = route.useParams();
  const q = useQuery({ queryKey: ["answer-assets", answerId], queryFn: () => api.answerAsset(answerId) });
  return (
    <div className="max-w-3xl">
      <Loaded query={q}>{(a) => <AnswerEditor key={a.id + a.updated_at} initial={a} />}</Loaded>
    </div>
  );
}

interface Values {
  kind: AnswerAssetKind;
  title: string;
  body: string;
  claims: Pick<AnswerAssetClaim, "id" | "statement" | "confirmed" | "experience_title">[];
}

const valuesOf = (a: AnswerAsset): Values => ({
  kind: a.kind,
  title: a.title,
  body: a.body,
  claims: a.claims,
});

function AnswerEditor({ initial }: { initial: AnswerAsset }) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [v, setV] = useState<Values>(() => valuesOf(initial));
  const [picking, setPicking] = useState(false);
  const [placing, setPlacing] = useState(false);
  const [removing, setRemoving] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const set = (patch: Partial<Values>) => setV((prev) => ({ ...prev, ...patch }));

  const ids = (x: Values) => x.claims.map((c) => c.id).join(",");
  const dirty =
    v.kind !== initial.kind ||
    v.title !== initial.title ||
    v.body !== initial.body ||
    ids(v) !== ids(valuesOf(initial));
  const save = useMutation({
    mutationFn: () =>
      api.patchAnswerAsset(initial.id, {
        kind: v.kind,
        title: v.title.trim(),
        body: v.body,
        claim_ids: v.claims.map((c) => c.id),
      }),
    onSuccess: (a) => {
      qc.setQueryData(["answer-assets", a.id], a);
      void qc.invalidateQueries({ queryKey: ["answer-assets"], exact: true });
    },
  });
  const remove = useMutation({
    mutationFn: () => api.deleteAnswerAsset(initial.id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["answer-assets"] });
      void navigate({ to: "/brain/answers" });
    },
  });
  const pick = (c: PickedClaim) => {
    if (!CONFIRMED.includes(String(c.status))) {
      setNotice("확인 전 사실은 근거로 이을 수 없습니다. 브레인에서 먼저 확인하세요.");
      return;
    }
    setNotice(null);
    if (v.claims.some((x) => x.id === c.id)) return;
    set({
      claims: [
        ...v.claims,
        { id: c.id, statement: c.statement, confirmed: true, experience_title: c.experience_title ?? "" },
      ],
    });
  };

  const chars = [...v.body].length;
  const noSpace = [...v.body.replace(/\s/g, "")].length;

  return (
    <>
      <PageHeader
        back={{ to: "/brain/answers", label: "공통 답변" }}
        title={initial.title}
        subtitle={`${ANSWER_KIND_LABEL[initial.kind]} · 수정한 날 ${formatDate(initial.updated_at)}`}
        actions={
          <Button onClick={() => setPlacing(true)} disabled={dirty || !initial.body.trim()}>
            지원서에 넣기
          </Button>
        }
      />
      <form
        className="space-y-6"
        onSubmit={(e) => {
          e.preventDefault();
          if (dirty && v.title.trim()) save.mutate();
        }}
      >
        <div className="grid gap-3 sm:grid-cols-[10rem_minmax(0,1fr)]">
          <div>
            <Label htmlFor="an-kind" className="mb-1">
              종류
            </Label>
            <Select id="an-kind" value={v.kind} onValueChange={(v) => set({ kind: v as AnswerAssetKind })}>
              {ANSWER_ASSET_KINDS.map((k) => (
                <SelectItem key={k} value={k}>
                  {ANSWER_KIND_LABEL[k]}
                </SelectItem>
              ))}
            </Select>
          </div>
          <div>
            <Label htmlFor="an-title" className="mb-1">
              제목
            </Label>
            <Input
              id="an-title"
              value={v.title}
              onChange={(e) => set({ title: e.target.value })}
              maxLength={200}
              required
            />
          </div>
        </div>
        <div>
          <div className="mb-1 flex items-baseline justify-between gap-2">
            <Label htmlFor="an-body">본문</Label>
            <span className="text-[13px] text-muted tabular-nums" aria-live="polite">
              {chars.toLocaleString()}자 · 공백 제외 {noSpace.toLocaleString()}자
            </span>
          </div>
          <Textarea
            id="an-body"
            value={v.body}
            onChange={(e) => set({ body: e.target.value })}
            maxLength={20000}
            rows={12}
            className="leading-7"
            placeholder="빈 줄로 문단을 나눕니다. 지원서에 넣으면 문단마다 한 블록이 됩니다."
          />
        </div>

        <section aria-labelledby="an-evidence">
          <div className="flex items-baseline justify-between gap-2">
            <h2 id="an-evidence" className="text-[15px] font-semibold">
              근거 <span className="font-normal text-muted tabular-nums">{v.claims.length}</span>
            </h2>
            <Button size="sm" variant="ghost" className="max-sm:h-9" onClick={() => setPicking(true)}>
              <Plus size={14} /> 확인한 사실 잇기
            </Button>
          </div>
          {v.claims.length ? (
            <ul className="mt-2 flex flex-wrap gap-2">
              {v.claims.map((c) => (
                <li
                  key={c.id}
                  className="inline-flex max-w-full min-h-9 items-center gap-1 rounded-full bg-subtle py-1 pr-1 pl-3 text-[13px]"
                >
                  <span className="min-w-0 truncate" title={`${c.statement} · ${c.experience_title}`}>
                    {c.statement}
                  </span>
                  {!c.confirmed ? <span className="shrink-0 font-semibold">확인 해제됨</span> : null}
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <button
                        type="button"
                        aria-label={`근거 빼기: ${c.statement}`}
                        onClick={() => set({ claims: v.claims.filter((x) => x.id !== c.id) })}
                        className="inline-flex size-7 shrink-0 items-center justify-center rounded-full text-muted hover:bg-bg hover:text-fg"
                      >
                        <X size={14} />
                      </button>
                    </TooltipTrigger>
                    <TooltipContent>근거 빼기</TooltipContent>
                  </Tooltip>
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-1 text-[13px] text-muted">
              이 글이 기대는 확인한 사실을 이어 두면, 지원서에 넣을 때 문단마다 근거로 붙습니다.
            </p>
          )}
          {notice ? <p className="mt-2 text-[13px] font-medium">{notice}</p> : null}
        </section>

        {save.error ? (
          <p role="alert" className="text-[13px] text-danger">
            {message(save.error)}
          </p>
        ) : null}
        <div className="flex flex-wrap items-center gap-2 border-t border-line pt-4">
          <Button type="submit" variant="primary" disabled={!dirty || !v.title.trim() || save.isPending}>
            {save.isPending ? "저장하는 중…" : "저장"}
          </Button>
          {dirty ? (
            <Button variant="ghost" onClick={() => setV(valuesOf(initial))}>
              되돌리기
            </Button>
          ) : null}
          <Button variant="ghost" className="ml-auto text-danger" onClick={() => setRemoving(true)}>
            삭제
          </Button>
        </div>
      </form>

      <section aria-labelledby="an-uses" className="mt-12">
        <h2 id="an-uses" className="text-[15px] font-semibold">
          쓴 곳 <span className="font-normal text-muted tabular-nums">{initial.uses.length}</span>
        </h2>
        {initial.uses.length ? (
          <ul className="mt-2 divide-y divide-line border-y border-line">
            {initial.uses.map((u, i) => (
              <li key={`${u.document_id}-${i}`}>
                <Link
                  to="/applications/$applicationId/documents/$documentId"
                  params={{ applicationId: u.application_id, documentId: u.document_id }}
                  className="-mx-2 flex min-h-11 items-center gap-3 rounded-md px-2 py-2.5 transition-colors duration-150 hover:bg-subtle/60"
                >
                  <span className="min-w-0 flex-1 truncate text-sm">
                    {u.application_title}
                    <span className="text-muted"> · {u.document_title}</span>
                  </span>
                  <span className="shrink-0 text-[13px] text-muted tabular-nums">{formatDateTime(u.created_at)}</span>
                </Link>
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-1 text-[13px] text-muted">아직 지원서에 넣은 적이 없습니다.</p>
        )}
        {initial.source_submission_id && initial.source_document_id ? (
          <p className="mt-3 text-[13px] text-muted">
            <Link
              to="/brain/submissions/$submissionId/$documentId"
              params={{ submissionId: initial.source_submission_id, documentId: initial.source_document_id }}
              className="text-fg underline underline-offset-2"
            >
              시작한 제출본
            </Link>
            에서 이어 썼습니다.
          </p>
        ) : null}
      </section>

      <ClaimPicker
        open={picking}
        onClose={() => setPicking(false)}
        onPick={pick}
        selected={v.claims.map((c) => c.id)}
        title="확인한 사실 잇기"
      />
      <PlaceSheet
        open={placing}
        onClose={() => setPlacing(false)}
        title={`"${initial.title}" 넣기`}
        previewKey={`answer:${initial.id}`}
        place={(body) => api.useAnswerAsset(initial.id, body)}
      />
      <ConfirmDialog
        open={removing}
        onOpenChange={setRemoving}
        title={`"${initial.title}" 삭제`}
        description="이 답변과 쓴 곳 기록을 삭제합니다. 지원서에 이미 넣은 글은 그대로 남습니다."
        confirmLabel="삭제"
        tone="danger"
        busy={remove.isPending}
        onConfirm={() => remove.mutate()}
      >
        {remove.error ? <p className="text-[13px] text-danger">{message(remove.error)}</p> : null}
      </ConfirmDialog>
    </>
  );
}
