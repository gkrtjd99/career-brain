import type { ExperienceSummary, Run } from "@career-brain/client";
import { Button, Checkbox, Label, Select, SelectItem, Sheet } from "@career-brain/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { ChevronRight, MessagesSquare } from "lucide-react";
import { useCallback, useMemo, useState } from "react";
import { api } from "../../lib/api";
import { ErrorText } from "../common";
import { CostEstimate, newRunKey, RunProgress, runKey } from "./RunParts";

const WORKFLOW = "interview_question_batch";
// 서버 규칙과 같다 (workflows/interviews.py MAX_BATCH_EXPERIENCES, usecases/interviews.py MAX_AI_QUESTIONS)
const MAX_EXPERIENCES = 20;
const MAX_COUNT = 5;

/**
 * 경험별 AI 인터뷰 질문 일괄 실행 (c0023, workflow ``interview_question_batch``).
 * 경험마다 인터뷰 세션을 하나 만들고 보충 질문을 요청한다. AI는 질문만 만든다 — 답·주장은 사용자가 쓴다.
 */
export function InterviewBatchButton({ experiences }: { experiences: ExperienceSummary[] }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button onClick={() => setOpen(true)} disabled={!experiences.length}>
        <MessagesSquare size={15} /> 질문 받으며 채우기
      </Button>
      {open ? <InterviewBatchSheet experiences={experiences} onClose={() => setOpen(false)} /> : null}
    </>
  );
}

function InterviewBatchSheet({ experiences, onClose }: { experiences: ExperienceSummary[]; onClose: () => void }) {
  const qc = useQueryClient();
  // 확인할 것이 남은 경험부터 고른다
  const [picked, setPicked] = useState<Set<string>>(
    () =>
      new Set(
        experiences
          .filter((e) => e.needs_review > 0)
          .slice(0, MAX_EXPERIENCES)
          .map((e) => e.id),
      ),
  );
  const [count, setCount] = useState(3);
  const [runId, setRunId] = useState<string | null>(null);
  const [finished, setFinished] = useState<Run | null>(null);
  const [key] = useState(() => newRunKey("interviews"));
  const chosen = useMemo(() => experiences.filter((e) => picked.has(e.id)).map((e) => e.id), [experiences, picked]);

  // 시작할 입력 그대로 (예상 비용도 같은 입력으로 글자 수를 센다)
  const input = useMemo(() => ({ experience_ids: chosen, count }), [chosen, count]);

  const start = useMutation({
    mutationFn: () => api.startRun(WORKFLOW, input, runKey(key, input)),
    onSuccess: (run) => setRunId(run.id),
  });
  const onFinished = useCallback(
    (run: Run) => {
      setFinished(run);
      void qc.invalidateQueries({ queryKey: ["interviews"] });
    },
    [qc],
  );
  const toggle = (id: string) =>
    setPicked((s) => {
      const n = new Set(s);
      if (n.has(id)) n.delete(id);
      else if (n.size < MAX_EXPERIENCES) n.add(id);
      return n;
    });

  return (
    <Sheet open onOpenChange={(o) => (o ? null : onClose())} title="질문 받으며 채우기" width="lg">
      {runId ? (
        <div className="space-y-3">
          <RunProgress runId={runId} listKey="experiences" noun="경험" onFinished={onFinished} />
          {finished ? <RunSessions runId={runId} questions={finished.result?.questions} /> : null}
        </div>
      ) : (
        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            if (chosen.length) start.mutate();
          }}
        >
          <p className="text-[13px] text-muted">
            고른 경험마다 보충 질문을 만듭니다. 확인한 사실을 모델로 보내며 비용이 듭니다. 이 기기 밖으로 보내지 않는
            자료는 뺍니다. 답은 직접 씁니다.
          </p>
          <fieldset className="space-y-1.5">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <legend className="text-[13px] font-medium tabular-nums">
                경험 {chosen.length}/{experiences.length}
                {experiences.length > MAX_EXPERIENCES ? ` (최대 ${MAX_EXPERIENCES})` : ""}
              </legend>
              <div className="flex gap-1">
                <Button
                  size="sm"
                  variant="ghost"
                  type="button"
                  onClick={() => setPicked(new Set(experiences.slice(0, MAX_EXPERIENCES).map((e) => e.id)))}
                >
                  모두
                </Button>
                <Button size="sm" variant="ghost" type="button" onClick={() => setPicked(new Set())}>
                  비우기
                </Button>
              </div>
            </div>
            <ul className="-mx-2 max-h-[40dvh] divide-y divide-line overflow-y-auto border-y border-line">
              {experiences.map((e) => (
                <li key={e.id}>
                  <label className="flex cursor-pointer items-start gap-2 px-2 py-2.5 text-[13px] transition-colors duration-150 hover:bg-subtle/60">
                    <Checkbox className="mt-1" checked={picked.has(e.id)} onCheckedChange={() => toggle(e.id)} />
                    <span className="min-w-0 flex-1">
                      <span className="block break-words font-medium">{e.title}</span>
                      <span className="block text-muted">
                        {[e.organization, e.period_text].filter(Boolean).join(" · ")}
                        {e.needs_review
                          ? `${e.organization || e.period_text ? " · " : ""}확인 필요 ${e.needs_review}`
                          : ""}
                      </span>
                    </span>
                  </label>
                </li>
              ))}
            </ul>
          </fieldset>
          <div className="max-w-40">
            <Label htmlFor="batch-count">경험마다 질문 수</Label>
            <Select id="batch-count" value={count} onValueChange={(v) => setCount(Number(v))}>
              {Array.from({ length: MAX_COUNT }, (_, i) => i + 1).map((n) => (
                <SelectItem key={n} value={n}>
                  {n}개
                </SelectItem>
              ))}
            </Select>
          </div>
          <CostEstimate workflow={WORKFLOW} units={chosen.length} noun="경험" input={input} />
          <ErrorText error={start.error} />
          <Button type="submit" variant="primary" disabled={!chosen.length || start.isPending}>
            {start.isPending ? "시작하는 중" : `${chosen.length}개 경험 시작`}
          </Button>
        </form>
      )}
    </Sheet>
  );
}

/** 이 실행이 질문을 만든 인터뷰 세션 (실행 기록에는 ID만 있으니 인터뷰 목록에서 읽는다) */
function RunSessions({ runId, questions }: { runId: string; questions: unknown }) {
  const q = useQuery({ queryKey: ["interviews", { runId }], queryFn: () => api.interviews({ runId }) });
  const sessions = q.data ?? [];
  return (
    <div className="space-y-2 text-[13px]">
      <p>
        질문 {typeof questions === "number" ? questions : 0}개 · 세션 {sessions.length}개. 답은 각 경험의 인터뷰 탭에서
        씁니다.
      </p>
      <ErrorText error={q.error} />
      {sessions.length ? (
        <ul className="divide-y divide-line border-t border-line">
          {sessions.map((s) =>
            s.experience_id ? (
              <li key={s.id}>
                <Link
                  to="/brain/$experienceId"
                  params={{ experienceId: s.experience_id }}
                  search={{ tab: "interview" }}
                  className="-mx-2 flex items-center gap-2 rounded-md px-2 py-2.5 transition-colors duration-150 hover:bg-subtle/60"
                >
                  <span className="min-w-0 flex-1 break-words">{s.experience_title ?? "경험"}</span>
                  <span className="shrink-0 text-muted tabular-nums">
                    답 {s.answered}/{s.questions}
                  </span>
                  <ChevronRight size={14} className="shrink-0 text-muted" />
                </Link>
              </li>
            ) : null,
          )}
        </ul>
      ) : null}
    </div>
  );
}
