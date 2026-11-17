import type { AppDocument, Run } from "@career-brain/client";
import { Button, Label, Sheet, Textarea } from "@career-brain/ui";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ListChecks } from "lucide-react";
import { useCallback, useMemo, useState } from "react";
import { api } from "../../lib/api";
import { ErrorText } from "../common";
import { CostEstimate, newRunKey, RunProgress, runKey } from "../runs/RunParts";

const WORKFLOW = "suggest_document_batch";
const MAX_BLOCKS = 50;

/**
 * 문단별 AI 수정안 일괄 실행 (c0023, workflow ``suggest_document_batch``). 지원 문서만, 저장한 revision 기준.
 * 수정안은 초안이다: 결과는 AI 수정안 탭에서 사람이 하나씩 적용·거절한다. 실행 기록에는 글이 없다.
 */
export function BatchSuggestButton({
  appId,
  doc,
  dirty,
  onShowSuggestions,
}: {
  appId: string | null;
  doc: AppDocument;
  dirty: boolean;
  onShowSuggestions: () => void;
}) {
  const [open, setOpen] = useState(false);
  if (!appId || !doc.editable) return null;
  return (
    <>
      <Button size="sm" onClick={() => setOpen(true)} disabled={!doc.blocks.length}>
        <ListChecks size={14} /> 일괄 수정안
      </Button>
      {open ? (
        <BatchSuggestSheet
          appId={appId}
          doc={doc}
          dirty={dirty}
          onClose={() => setOpen(false)}
          onShowSuggestions={() => {
            setOpen(false);
            onShowSuggestions();
          }}
        />
      ) : null}
    </>
  );
}

function BatchSuggestSheet({
  appId,
  doc,
  dirty,
  onClose,
  onShowSuggestions,
}: {
  appId: string;
  doc: AppDocument;
  dirty: boolean;
  onClose: () => void;
  onShowSuggestions: () => void;
}) {
  const qc = useQueryClient();
  const blocks = doc.blocks;
  const [picked, setPicked] = useState<Set<string>>(() => new Set(blocks.slice(0, MAX_BLOCKS).map((b) => b.id)));
  const [instruction, setInstruction] = useState("");
  const [runId, setRunId] = useState<string | null>(null);
  const [finished, setFinished] = useState<Run | null>(null);
  // 이 창에서 시작 버튼을 다시 눌러도(응답을 못 받은 경우) 같은 실행
  const [key] = useState(() => newRunKey(`suggest:${doc.id}:r${doc.revision_no}`));
  const chosen = useMemo(() => blocks.filter((b) => picked.has(b.id)).map((b) => b.id), [blocks, picked]);

  // 시작할 입력 그대로 (예상 비용도 같은 입력으로 글자 수를 센다)
  const input = useMemo(
    () => ({
      application_id: appId,
      document_id: doc.id,
      base_revision: doc.revision_no,
      block_ids: chosen,
      instruction: instruction.trim() || null,
    }),
    [appId, doc.id, doc.revision_no, chosen, instruction],
  );

  const start = useMutation({
    mutationFn: () => api.startRun(WORKFLOW, input, runKey(key, input)),
    onSuccess: (run) => setRunId(run.id),
  });
  const onFinished = useCallback(
    (run: Run) => {
      setFinished(run);
      void qc.invalidateQueries({ queryKey: ["suggestions", doc.id] });
      void qc.invalidateQueries({ queryKey: ["suggestion-requests", doc.id] });
    },
    [qc, doc.id],
  );
  const toggle = (id: string) =>
    setPicked((s) => {
      const n = new Set(s);
      if (n.has(id)) n.delete(id);
      else if (n.size < MAX_BLOCKS) n.add(id);
      return n;
    });
  const result = finished?.result as { proposed?: number; rejected?: number; questions?: number } | null | undefined;

  return (
    <Sheet open onOpenChange={(o) => (o ? null : onClose())} title="문단별 AI 수정안 (일괄)" width="lg">
      {runId ? (
        <div className="space-y-3">
          <RunProgress runId={runId} listKey="blocks" noun="문단" onFinished={onFinished} />
          {finished?.status === "done" ? (
            <div className="space-y-2 text-[13px]">
              <p>
                수정안 {result?.proposed ?? 0}개
                {result?.rejected ? ` · 사실 변경 등으로 거부 ${result.rejected}개` : ""}
                {result?.questions ? ` · AI가 물어본 것 ${result.questions}개` : ""}. 적용은 AI 수정안 탭에서 하나씩
                고릅니다.
              </p>
              <Button variant="primary" size="sm" onClick={onShowSuggestions}>
                AI 수정안 탭에서 보기
              </Button>
            </div>
          ) : null}
        </div>
      ) : (
        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            if (chosen.length && !dirty) start.mutate();
          }}
        >
          <p className="text-[13px] text-muted">
            고른 문단마다 한 번씩 AI에게 수정안을 요청합니다. 문단 글을 모델로 보내며 비용이 듭니다 (제한 자료 근거는
            보내지 않음). 수정안은 초안이고 문서는 바뀌지 않습니다. 실행 중에 문서를 저장하면 남은 문단은 하지 않고
            멈춥니다.
          </p>
          {dirty ? (
            <p role="alert" className="text-[13px] font-medium">
              저장하지 않은 편집이 있습니다. 저장한 r{doc.revision_no} 기준으로만 시작할 수 있습니다 — 먼저 저장하세요.
            </p>
          ) : null}
          <fieldset className="space-y-1.5">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <legend className="text-[13px] font-medium tabular-nums">
                문단 {chosen.length}/{blocks.length}
                {blocks.length > MAX_BLOCKS ? ` (최대 ${MAX_BLOCKS})` : ""}
              </legend>
              <div className="flex gap-1">
                <Button
                  size="sm"
                  variant="ghost"
                  type="button"
                  onClick={() => setPicked(new Set(blocks.slice(0, MAX_BLOCKS).map((b) => b.id)))}
                >
                  모두
                </Button>
                <Button size="sm" variant="ghost" type="button" onClick={() => setPicked(new Set())}>
                  비우기
                </Button>
              </div>
            </div>
            <ul className="-mx-2 max-h-[40dvh] divide-y divide-line overflow-y-auto border-y border-line">
              {blocks.map((b, i) => (
                <li key={b.id}>
                  <label className="flex cursor-pointer items-start gap-2 px-2 py-2.5 text-[13px] transition-colors duration-150 hover:bg-subtle/60">
                    <input
                      type="checkbox"
                      className="mt-1"
                      checked={picked.has(b.id)}
                      onChange={() => toggle(b.id)}
                      aria-label={`문단 ${i + 1}`}
                    />
                    <span className="w-6 shrink-0 text-muted tabular-nums">{i + 1}</span>
                    <span className="line-clamp-2 min-w-0 flex-1 break-words">{b.text}</span>
                  </label>
                </li>
              ))}
            </ul>
          </fieldset>
          <div>
            <Label htmlFor="batch-instruction">요청 (선택)</Label>
            <Textarea
              id="batch-instruction"
              value={instruction}
              onChange={(e) => setInstruction(e.target.value)}
              placeholder="예: 문장을 짧게. 사실·수치는 그대로."
              maxLength={500}
              className="min-h-14"
            />
          </div>
          <CostEstimate workflow={WORKFLOW} units={chosen.length} noun="문단" input={input} />
          <ErrorText error={start.error} />
          <Button type="submit" variant="primary" disabled={!chosen.length || dirty || start.isPending}>
            {start.isPending ? "시작하는 중" : `${chosen.length}개 문단 시작`}
          </Button>
        </form>
      )}
    </Sheet>
  );
}
