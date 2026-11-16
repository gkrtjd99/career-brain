import type { ClaimKind, Contribution, InterviewQuestion } from "@career-brain/client";
import { Badge, Button, EmptyState, Label, Select, Textarea } from "@career-brain/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Sparkles } from "lucide-react";
import { useEffect, useState } from "react";
import { api, ApiError } from "../lib/api";
import { FOCUS_LABEL, KIND_LABEL } from "../lib/labels";
import { ErrorText, Loaded } from "./common";

// 서버에 인터뷰 목록 API가 없어서, 이 브라우저에서 시작한 세션 ID를 경험별로 기억한다.
const key = (experienceId: string) => `career-brain:interview:${experienceId}`;
function rememberedSession(experienceId: string): string | null {
  try {
    return localStorage.getItem(key(experienceId));
  } catch {
    return null;
  }
}
function remember(experienceId: string, sessionId: string | null) {
  try {
    if (sessionId) localStorage.setItem(key(experienceId), sessionId);
    else localStorage.removeItem(key(experienceId));
  } catch {
    // 저장 못 하면 이번 화면에서만 유지
  }
}

/** 인터뷰형 경험 정리: 정해진 질문 + (선택) AI 보충 질문. 답변은 근거가 연결된 주장으로 저장된다. */
export function InterviewPanel({ experienceId }: { experienceId: string }) {
  const qc = useQueryClient();
  const [sessionId, setSessionId] = useState(() => rememberedSession(experienceId));
  const start = useMutation({
    mutationFn: () => api.startInterview(experienceId),
    onSuccess: (iv) => {
      remember(experienceId, iv.id);
      qc.setQueryData(["interview", iv.id], iv);
      setSessionId(iv.id);
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

  // 기억한 세션이 지워졌으면 잊는다.
  const gone = !!sessionId && q.error instanceof ApiError && q.error.status === 404;
  useEffect(() => {
    if (gone) {
      remember(experienceId, null);
      setSessionId(null);
    }
  }, [gone, experienceId]);

  if (!sessionId)
    return (
      <EmptyState className="space-y-3">
        <p>질문에 답하며 이 경험의 배경, 본인 기여, 수치, 판단을 정리합니다.</p>
        <Button variant="primary" onClick={() => start.mutate()} disabled={start.isPending}>
          인터뷰 시작
        </Button>
        <ErrorText error={start.error} />
      </EmptyState>
    );

  return (
    <Loaded query={q}>
      {(iv) => (
        <div className="space-y-4">
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
          <ol className="space-y-3">
            {iv.questions.map((question) => (
              <QuestionCard
                key={question.id}
                question={question}
                experienceId={experienceId}
                sessionId={iv.id}
                closed={iv.status !== "open"}
              />
            ))}
          </ol>
        </div>
      )}
    </Loaded>
  );
}

function QuestionCard({
  question,
  experienceId,
  sessionId,
  closed,
}: {
  question: InterviewQuestion;
  experienceId: string;
  sessionId: string;
  closed: boolean;
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
