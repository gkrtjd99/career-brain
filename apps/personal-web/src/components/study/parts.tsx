import type { Attempt, AttemptResult, Mistake, MistakeReason, ReviewGrade } from "@career-brain/client";
import { Button, cn, Input, Label, Select, Textarea } from "@career-brain/ui";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { api } from "../../lib/api";
import { formatDate, formatDateTime } from "../../lib/dates";
import { ATTEMPT_RESULT, GRADE, MISTAKE_REASON } from "../../lib/labels";
import { ErrorText } from "../common";

/** 학습 화면의 목록·요약을 모두 새로 받는다 (기록 하나가 여러 목록에 걸친다). */
export function useStudyInvalidate() {
  const qc = useQueryClient();
  return () => {
    for (const key of [
      "study-items",
      "study-item",
      "assessments",
      "assessment",
      "mistakes",
      "due-reviews",
      "coverage",
      "activity",
      "study-settings",
      "workspace-settings",
      "claim-suggestions",
      "requirement-frequency",
      "upcoming-assessments",
    ])
      qc.invalidateQueries({ queryKey: [key] });
  };
}

/** 작은 버튼도 휴대폰에서는 손가락 높이(36px)를 넘게. */
export const TAP = "max-sm:h-9";

/** 얇은 구분선 목록. 상자 없이 제목 아래에 바로 둔다. */
export function Rows({ children, className }: { children: ReactNode; className?: string }) {
  return <ul className={cn("divide-y divide-line border-t border-line", className)}>{children}</ul>;
}

/** 비었을 때 한 줄. 목록 자리에서는 위에 구분선, 탭 바로 아래에서는 plain. */
export function Quiet({ children, plain }: { children: ReactNode; plain?: boolean }) {
  return <p className={cn("text-sm text-muted", !plain && "border-t border-line pt-3")}>{children}</p>;
}

export function score(a: Attempt) {
  if (a.score === null) return null;
  const s = Number(a.score);
  return a.max_score !== null ? `${s} / ${Number(a.max_score)}` : String(s);
}

export function AttemptRow({ a }: { a: Attempt }) {
  const r = ATTEMPT_RESULT[a.result];
  return (
    <div className="min-w-0 text-sm">
      <div className="flex flex-wrap items-baseline gap-x-2">
        <span className="font-medium">{r.label}</span>
        {score(a) ? <span className="tabular-nums">{score(a)}</span> : null}
        <span className="text-[13px] text-muted tabular-nums">
          {formatDateTime(a.taken_at)}
          {a.minutes ? ` · ${a.minutes}분` : ""}
        </span>
      </div>
      {a.notes ? <p className="mt-0.5 text-[13px] whitespace-pre-wrap text-muted">{a.notes}</p> : null}
    </div>
  );
}

export function AttemptForm({
  assessmentId,
  studyItemId,
  onDone,
}: {
  assessmentId?: string;
  studyItemId?: string;
  onDone?: () => void;
}) {
  const invalidate = useStudyInvalidate();
  const [form, setForm] = useState({ result: "unknown" as AttemptResult, score: "", max: "", minutes: "", notes: "" });
  const save = useMutation({
    mutationFn: () =>
      api.recordAttempt({
        result: form.result,
        assessment_id: assessmentId ?? null,
        study_item_id: studyItemId ?? null,
        score: form.score === "" ? null : form.score,
        max_score: form.max === "" ? null : form.max,
        minutes: form.minutes ? Number(form.minutes) : null,
        notes: form.notes.trim() || null,
      }),
    onSuccess: () => {
      setForm({ result: "unknown", score: "", max: "", minutes: "", notes: "" });
      invalidate();
      onDone?.();
    },
  });
  const pid = assessmentId ?? studyItemId ?? "x";
  return (
    <form
      className="space-y-2"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <div>
          <Label htmlFor={`ar-${pid}`}>결과</Label>
          <Select
            id={`ar-${pid}`}
            value={form.result}
            onChange={(e) => setForm({ ...form, result: e.target.value as AttemptResult })}
          >
            {(Object.keys(ATTEMPT_RESULT) as AttemptResult[]).map((k) => (
              <option key={k} value={k}>
                {ATTEMPT_RESULT[k].label}
              </option>
            ))}
          </Select>
        </div>
        <div>
          <Label htmlFor={`as-${pid}`}>점수</Label>
          <Input
            id={`as-${pid}`}
            inputMode="decimal"
            value={form.score}
            onChange={(e) => setForm({ ...form, score: e.target.value })}
          />
        </div>
        <div>
          <Label htmlFor={`am-${pid}`}>만점</Label>
          <Input
            id={`am-${pid}`}
            inputMode="decimal"
            value={form.max}
            onChange={(e) => setForm({ ...form, max: e.target.value })}
          />
        </div>
        <div>
          <Label htmlFor={`at-${pid}`}>걸린 시간(분)</Label>
          <Input
            id={`at-${pid}`}
            type="number"
            min={1}
            max={1440}
            value={form.minutes}
            onChange={(e) => setForm({ ...form, minutes: e.target.value })}
          />
        </div>
      </div>
      <Textarea
        value={form.notes}
        maxLength={4000}
        className="min-h-14"
        placeholder="메모 (선택)"
        aria-label="응시 메모"
        onChange={(e) => setForm({ ...form, notes: e.target.value })}
      />
      <ErrorText error={save.error} />
      <Button type="submit" size="sm" variant="primary" className={TAP} disabled={save.isPending}>
        응시 기록
      </Button>
    </form>
  );
}

export function MistakeForm({
  attemptId,
  studyItemId,
  onDone,
}: {
  attemptId?: string | null;
  studyItemId?: string | null;
  onDone?: () => void;
}) {
  const invalidate = useStudyInvalidate();
  const empty = { question: "", wrong: "", correct: "", reason: "unknown" as MistakeReason, note: "" };
  const [form, setForm] = useState(empty);
  const save = useMutation({
    mutationFn: () =>
      api.addMistake({
        question: form.question.trim(),
        wrong_answer: form.wrong.trim() || null,
        correct_answer: form.correct.trim() || null,
        reason: form.reason,
        reason_note: form.note.trim() || null,
        attempt_id: attemptId ?? null,
        study_item_id: studyItemId ?? null,
      }),
    onSuccess: () => {
      setForm(empty);
      invalidate();
      onDone?.();
    },
  });
  const pid = attemptId ?? studyItemId ?? "x";
  return (
    <form
      className="space-y-2"
      onSubmit={(e) => {
        e.preventDefault();
        if (form.question.trim()) save.mutate();
      }}
    >
      <Textarea
        value={form.question}
        maxLength={4000}
        className="min-h-14"
        placeholder="틀린 문제 (내 말로 요약)"
        aria-label="틀린 문제"
        onChange={(e) => setForm({ ...form, question: e.target.value })}
      />
      <div className="grid gap-2 sm:grid-cols-2">
        <Input
          value={form.wrong}
          maxLength={4000}
          placeholder="내 답"
          aria-label="내 답"
          onChange={(e) => setForm({ ...form, wrong: e.target.value })}
        />
        <Input
          value={form.correct}
          maxLength={4000}
          placeholder="맞는 답"
          aria-label="맞는 답"
          onChange={(e) => setForm({ ...form, correct: e.target.value })}
        />
      </div>
      <div className="grid gap-2 sm:grid-cols-[160px_minmax(0,1fr)]">
        <div>
          <Label htmlFor={`mr-${pid}`}>틀린 이유</Label>
          <Select
            id={`mr-${pid}`}
            value={form.reason}
            onChange={(e) => setForm({ ...form, reason: e.target.value as MistakeReason })}
          >
            {(Object.keys(MISTAKE_REASON) as MistakeReason[]).map((k) => (
              <option key={k} value={k}>
                {MISTAKE_REASON[k]}
              </option>
            ))}
          </Select>
        </div>
        <div>
          <Label htmlFor={`mn-${pid}`}>메모</Label>
          <Input
            id={`mn-${pid}`}
            value={form.note}
            maxLength={4000}
            onChange={(e) => setForm({ ...form, note: e.target.value })}
          />
        </div>
      </div>
      <p className="text-[13px] text-muted">
        적으면 내일 첫 복습이 잡힙니다. 내 기록일 뿐 시험 내용을 사실로 확정하지 않습니다.
      </p>
      <ErrorText error={save.error} />
      <Button
        type="submit"
        size="sm"
        variant="primary"
        className={TAP}
        disabled={!form.question.trim() || save.isPending}
      >
        오답 추가
      </Button>
    </form>
  );
}

/** 오답 한 줄: 답 보기 → 스스로 평가(다시·어려움·보통·쉬움) → 다음 복습 날짜. */
export function ReviewCard({
  m,
  title,
  showGrade = true,
  onReviewed,
}: {
  m: Pick<
    Mistake,
    "id" | "question" | "wrong_answer" | "correct_answer" | "reason" | "reason_note" | "due_at" | "interval_days"
  > & {
    status?: string;
  };
  title?: string | null;
  showGrade?: boolean;
  /** 평가하면 목록에서 빠지므로, 다음 날짜를 목록 밖에 보여 줄 때 쓴다. */
  onReviewed?: (message: string) => void;
}) {
  const invalidate = useStudyInvalidate();
  const [reveal, setReveal] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const review = useMutation({
    mutationFn: (g: ReviewGrade) => api.reviewMistake(m.id, g),
    onSuccess: (r) => {
      const msg = `다음 복습 ${formatDate(r.due_at)} (${r.interval_days}일 뒤)`;
      setResult(msg);
      onReviewed?.(`“${m.question.slice(0, 40)}” ${msg}`);
      setReveal(false);
      invalidate();
    },
  });
  const status = useMutation({
    mutationFn: (s: "open" | "resolved") => api.setMistakeStatus(m.id, s),
    onSuccess: invalidate,
  });
  const resolved = m.status === "resolved";
  const meta = [MISTAKE_REASON[m.reason] ?? m.reason, title, resolved ? "다 익힘" : `복습 ${formatDate(m.due_at)}`];
  return (
    <article className="py-4">
      <p className="text-sm leading-6 whitespace-pre-wrap">{m.question}</p>
      <p className="truncate text-[13px] text-muted tabular-nums">{meta.filter(Boolean).join(" · ")}</p>
      {reveal || !showGrade ? (
        <dl className="mt-2 grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1 text-[13px]">
          {m.wrong_answer ? (
            <>
              <dt className="text-muted">내 답</dt>
              <dd className="whitespace-pre-wrap">{m.wrong_answer}</dd>
            </>
          ) : null}
          <dt className="text-muted">맞는 답</dt>
          {m.correct_answer ? (
            <dd className="whitespace-pre-wrap">{m.correct_answer}</dd>
          ) : (
            <dd className="text-muted">적지 않았습니다.</dd>
          )}
          {m.reason_note ? (
            <>
              <dt className="text-muted">메모</dt>
              <dd className="whitespace-pre-wrap text-muted">{m.reason_note}</dd>
            </>
          ) : null}
        </dl>
      ) : null}
      {result ? <p className="mt-2 text-[13px] font-medium">{result}</p> : null}
      <ErrorText error={review.error ?? status.error} className="mt-1" />
      <div className={cn("mt-3 flex flex-wrap gap-1.5", !(showGrade && !resolved) && "-ml-2.5")}>
        {showGrade && !resolved ? (
          reveal ? (
            (Object.keys(GRADE) as ReviewGrade[]).map((g) => (
              <Button
                key={g}
                size="sm"
                variant={g === "good" ? "primary" : "secondary"}
                className={TAP}
                disabled={review.isPending}
                onClick={() => review.mutate(g)}
                title={GRADE[g].hint}
              >
                {GRADE[g].label}
              </Button>
            ))
          ) : (
            <Button size="sm" className={TAP} onClick={() => setReveal(true)}>
              답 보기
            </Button>
          )
        ) : null}
        <Button
          size="sm"
          variant="ghost"
          className={TAP}
          disabled={status.isPending}
          onClick={() => status.mutate(resolved ? "open" : "resolved")}
        >
          {resolved ? "다시 복습하기" : "다 익힘"}
        </Button>
      </div>
    </article>
  );
}
