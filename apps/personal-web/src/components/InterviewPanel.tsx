import type { ClaimKind, Contribution, InterviewQuestion } from "@career-brain/client";
import { Badge, Button, EmptyState, Label, Select, Textarea } from "@career-brain/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { Sparkles } from "lucide-react";
import { useState } from "react";
import { api } from "../lib/api";
import { formatDate } from "../lib/dates";
import { FOCUS_LABEL, KIND_LABEL } from "../lib/labels";
import { ErrorText, Loaded } from "./common";

/** 인터뷰형 경험 정리: 정해진 질문 + (선택) AI 보충 질문. 답변은 근거가 연결된 주장으로 저장된다. */
export function InterviewPanel({ experienceId }: { experienceId: string }) {
  const qc = useQueryClient();
  const sessions = useQuery({
    queryKey: ["interviews", experienceId],
    queryFn: () => api.interviews({ experienceId }),
  });
  const [chosen, setChosen] = useState<string | null>(null);
  const [byRun, setByRun] = useState(false);
  const sessionId = chosen ?? sessions.data?.[0]?.id ?? null;
  const start = useMutation({
    mutationFn: () => api.startInterview(experienceId),
    onSuccess: (iv) => {
      qc.setQueryData(["interview", iv.id], iv);
      qc.invalidateQueries({ queryKey: ["interviews", experienceId] });
      setChosen(iv.id);
    },
  });
  const q = useQuery({
    queryKey: ["interview", sessionId],
    queryFn: () => api.interview(sessionId!),
    enabled: !!sessionId,
  });
  const ai = useMutation({
    mutationFn: () => api.aiQuestions(sessionId!, 3),
    onSuccess: (iv) => qc.setQueryData(["interview", iv.id], iv),
  });

  if (sessions.isPending) return <Loaded query={sessions}>{() => null}</Loaded>;
  if (!sessionId)
    return (
      <EmptyState className="space-y-3">
        <p>질문에 답하며 이 경험의 배경, 본인 기여, 수치, 판단을 정리합니다.</p>
        <Button variant="primary" onClick={() => start.mutate()} disabled={start.isPending}>
          인터뷰 시작
        </Button>
        <ErrorText error={start.error ?? sessions.error} />
      </EmptyState>
    );

  const list = sessions.data ?? [];
  return (
    <Loaded query={q}>
      {(iv) => (
        <div className="space-y-4">
          {list.length > 1 ? (
            <div className="flex flex-wrap items-center gap-1.5 text-[13px]" role="radiogroup" aria-label="인터뷰 세션">
              {list.map((se, i) => (
                <button
                  key={se.id}
                  role="radio"
                  aria-checked={se.id === sessionId}
                  onClick={() => setChosen(se.id)}
                  className={
                    se.id === sessionId
                      ? "rounded-full bg-accent-soft px-2.5 py-0.5 font-medium text-accent"
                      : "rounded-full border border-line bg-surface px-2.5 py-0.5 text-muted hover:text-fg"
                  }
                >
                  {formatDate(se.created_at)} · {se.answered}/{se.questions}
                  {se.run_ids?.length ? " · 일괄" : ""}
                  {i === 0 ? " (최근)" : ""}
                </button>
              ))}
            </div>
          ) : null}
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="text-xs text-muted">
              답변은 인터뷰 근거가 붙은 주장으로 저장됩니다. 기여(본인/팀)를 고르면 바로 “본인 확인” 상태가 됩니다.
            </p>
            <div className="flex gap-1.5">
              <Button size="sm" onClick={() => ai.mutate()} disabled={ai.isPending || iv.status !== "open"}>
                <Sparkles size={14} /> AI 보충 질문
              </Button>
              <Button size="sm" variant="ghost" onClick={() => start.mutate()} disabled={start.isPending}>
                새 인터뷰
              </Button>
            </div>
          </div>
          <p className="-mt-2 text-xs text-muted">AI 보충 질문은 이 경험의 내용을 모델에 보내며 비용이 듭니다.</p>
          <ErrorText error={ai.error} />
          {iv.questions.some((x) => x.run_id) ? (
            <label className="flex items-center gap-2 text-[13px]">
              <input type="checkbox" checked={byRun} onChange={(e) => setByRun(e.target.checked)} />
              실행별로 묶기
            </label>
          ) : null}
          {(byRun ? groupByRun(iv.questions) : [{ key: "all", title: null, runId: null, items: iv.questions }]).map(
            (g) => (
              <section key={g.key} className="space-y-2">
                {g.title ? (
                  <h3 className="flex flex-wrap items-center gap-2 text-[13px] font-medium">
                    {g.title} <span className="font-normal text-muted">{g.items.length}개</span>
                    {g.runId ? <RunLink runId={g.runId} /> : null}
                  </h3>
                ) : null}
                <ol className="space-y-3">
                  {g.items.map((question) => (
                    <QuestionCard
                      key={question.id}
                      question={question}
                      experienceId={experienceId}
                      sessionId={iv.id}
                      closed={iv.status !== "open"}
                      showRun={!byRun}
                    />
                  ))}
                </ol>
              </section>
            ),
          )}
        </div>
      )}
    </Loaded>
  );
}

type QuestionGroup = { key: string; title: string | null; runId: string | null; items: InterviewQuestion[] };

/** c0023: 정해진 질문 / 직접 요청한 AI 질문 / 일괄 실행별 (실행은 처음 나온 순서) */
function groupByRun(questions: InterviewQuestion[]): QuestionGroup[] {
  const fixed: QuestionGroup = { key: "template", title: "정해진 질문", runId: null, items: [] };
  const direct: QuestionGroup = { key: "direct", title: "직접 요청한 AI 질문", runId: null, items: [] };
  const runs = new Map<string, QuestionGroup>();
  for (const q of questions) {
    if (q.run_id) {
      let g = runs.get(q.run_id);
      if (!g) {
        g = { key: q.run_id, title: `일괄 실행 ${q.run_id.slice(0, 8)}`, runId: q.run_id, items: [] };
        runs.set(q.run_id, g);
      }
      g.items.push(q);
    } else (q.origin === "ai" ? direct : fixed).items.push(q);
  }
  return [fixed, direct, ...runs.values()].filter((g) => g.items.length);
}

function RunLink({ runId }: { runId: string }) {
  return (
    <Link
      to="/system"
      search={{ run: runId }}
      className="text-xs font-normal text-accent hover:underline"
      title="시스템 화면에서 이 실행 보기"
    >
      실행 보기
    </Link>
  );
}

function QuestionCard({
  question,
  experienceId,
  sessionId,
  closed,
  showRun,
}: {
  question: InterviewQuestion;
  experienceId: string;
  sessionId: string;
  closed: boolean;
  showRun: boolean;
}) {
  const qc = useQueryClient();
  const [answer, setAnswer] = useState("");
  const [makeClaim, setMakeClaim] = useState(true);
  const [statement, setStatement] = useState("");
  const [kind, setKind] = useState<ClaimKind>("other");
  const [contribution, setContribution] = useState<Contribution>("personal");
  const [open, setOpen] = useState(question.answers.length === 0);
  const send = useMutation({
    mutationFn: () =>
      api.answer(question.id, {
        answer: answer.trim(),
        make_claim: makeClaim,
        statement: makeClaim && statement.trim() ? statement.trim() : null,
        kind,
        contribution,
        experience_id: experienceId,
      }),
    onSuccess: () => {
      setAnswer("");
      setStatement("");
      setOpen(false);
      qc.invalidateQueries({ queryKey: ["interview", sessionId] });
      qc.invalidateQueries({ queryKey: ["interviews", experienceId] });
      qc.invalidateQueries({ queryKey: ["experience", experienceId] });
      qc.invalidateQueries({ queryKey: ["experiences"] });
      qc.invalidateQueries({ queryKey: ["wiki-md", experienceId] });
    },
  });

  return (
    <li className="rounded-lg border border-line bg-surface p-3">
      <div className="mb-1 flex flex-wrap items-center gap-1.5">
        <Badge tone="neutral">{FOCUS_LABEL[question.focus] ?? question.focus}</Badge>
        {question.origin === "ai" ? <Badge tone="accent">AI 질문</Badge> : null}
        {question.run_id && showRun ? (
          <Link to="/system" search={{ run: question.run_id }} className="hover:underline" title="이 질문을 만든 일괄 실행">
            <Badge tone="neutral">일괄 {question.run_id.slice(0, 8)}</Badge>
          </Link>
        ) : null}
        {question.answers.length ? <Badge tone="ok">답함</Badge> : null}
      </div>
      <p className="text-sm leading-6 font-medium">{question.prompt}</p>
      {question.answers.map((a, i) => (
        <p key={i} className="mt-2 rounded-md bg-subtle px-2.5 py-1.5 text-[13px] leading-6 whitespace-pre-wrap">
          {a}
        </p>
      ))}
      {closed ? null : open ? (
        <form
          className="mt-2 space-y-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (answer.trim()) send.mutate();
          }}
        >
          <Textarea
            value={answer}
            onChange={(e) => setAnswer(e.target.value)}
            placeholder="답변"
            maxLength={8000}
            aria-label="답변"
          />
          {answer.trim() ? (
            <label className="flex items-center gap-2 text-[13px]">
              <input type="checkbox" checked={makeClaim} onChange={(e) => setMakeClaim(e.target.checked)} />
              주장으로 저장
            </label>
          ) : null}
          {makeClaim && answer.trim() ? (
            <div className="space-y-2 rounded-md border border-line p-2">
              <Textarea
                value={statement}
                onChange={(e) => setStatement(e.target.value)}
                placeholder="주장 문장 (비우면 답변을 그대로 씁니다)"
                className="min-h-14"
                maxLength={4000}
                aria-label="주장 문장"
              />
              <div className="grid grid-cols-2 gap-2">
                <div>
                  <Label htmlFor={`k-${question.id}`}>종류</Label>
                  <Select id={`k-${question.id}`} value={kind} onChange={(e) => setKind(e.target.value as ClaimKind)}>
                    {(Object.keys(KIND_LABEL) as ClaimKind[]).map((k) => (
                      <option key={k} value={k}>
                        {KIND_LABEL[k]}
                      </option>
                    ))}
                  </Select>
                </div>
                <div>
                  <Label htmlFor={`c-${question.id}`}>기여</Label>
                  <Select
                    id={`c-${question.id}`}
                    value={contribution}
                    onChange={(e) => setContribution(e.target.value as Contribution)}
                  >
                    <option value="personal">본인</option>
                    <option value="team">팀</option>
                    <option value="unknown">모름</option>
                  </Select>
                </div>
              </div>
            </div>
          ) : null}
          <ErrorText error={send.error} />
          <div className="flex gap-1.5">
            <Button size="sm" variant="primary" type="submit" disabled={!answer.trim() || send.isPending}>
              답변 저장
            </Button>
            {question.answers.length ? (
              <Button size="sm" variant="ghost" onClick={() => setOpen(false)}>
                취소
              </Button>
            ) : null}
          </div>
        </form>
      ) : (
        <Button size="sm" variant="ghost" className="mt-1" onClick={() => setOpen(true)}>
          덧붙이기
        </Button>
      )}
    </li>
  );
}
