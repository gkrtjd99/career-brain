// 면접 준비 (/applications/$id/interview, O16, plan §19.4). 지원 하나의 예상 질문과 사람이 쓰는 답변 메모.
// 질문마다 어디서 나왔는지(공고 요건·제출한 글의 문장·회사 항목·참고 면접 후기·공통 질문)를 함께 보인다.
// 넓은 화면: 왼쪽 질문 목록 · 오른쪽 질문 칸. 좁은 화면: 목록 → 질문 하나 (아래에 붙는 저장 줄).
// 답은 사람이 쓴다. 모델 의견은 "답 의견 받기"를 누를 때만 받고 저장하지 않는다.
import type {
  Application,
  InterviewFeedback,
  InterviewPrep,
  InterviewPrepQuestion,
  InterviewSourceKind,
} from "@career-brain/client";
import { Button, cn, ConfirmDialog, EmptyState, FilterChips, Input, MoreMenu, Sheet, Textarea, toast } from "@career-brain/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getRouteApi, Link, useBlocker, useNavigate } from "@tanstack/react-router";
import { ChevronLeft, ChevronRight, RefreshCw, Save } from "lucide-react";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { ErrorText, Loaded, PageHeader } from "../components/common";
import { api } from "../lib/api";
import { ANSWER_KIND_LABEL } from "../lib/brainMe";
import { formatDateTime } from "../lib/dates";
import { int } from "../lib/format";
import { hasAnswer, interviewNote, SOURCE_GROUP, SOURCE_ORDER, sourceLine, STALE_TEXT } from "../lib/interview";

const route = getRouteApi("/applications/$applicationId/interview");

export const interviewPrepKey = (appId: string) => ["interview-prep", appId] as const;

/** 질문 목록과 질문 칸을 나란히 두는 너비. Tailwind lg 와 같다. */
const WIDE = "(min-width: 1024px)";

function useMedia(query: string): boolean {
  const [match, setMatch] = useState(() => typeof window !== "undefined" && window.matchMedia(query).matches);
  useEffect(() => {
    const mq = window.matchMedia(query);
    const on = () => setMatch(mq.matches);
    on();
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, [query]);
  return match;
}

const GAP: Record<string, string> = {
  no_metric: "수치 없음",
  role_unclear: "본인 역할 불분명",
  period_unknown: "기간 모름",
};

const POINT_KIND: Record<InterviewFeedback["points"][number]["kind"], string> = {
  missing: "빠진 것",
  unsupported: "근거가 없는 말",
  role: "본인 역할",
  structure: "구성",
  other: "기타",
};

/** 글자 수 (공백 포함 · 공백 제외) */
function countChars(text: string): { chars: number; noSpace: number } {
  return { chars: [...text].length, noSpace: [...text.replace(/\s/g, "")].length };
}

/** 고정된 아래 줄: 좁은 화면 아래 메뉴(3.5rem) 위에 붙고, 넓은 화면에서는 제자리에 둔다. */
const BOTTOM_BAR =
  "sticky bottom-[calc(3.5rem+env(safe-area-inset-bottom))] z-20 -mx-4 mt-6 flex items-center gap-2 border-t border-line bg-bg px-4 py-2 md:bottom-0 md:-mx-8 md:px-8";

export function InterviewPrepPage() {
  const { applicationId } = route.useParams();
  const app = useQuery({ queryKey: ["application", applicationId], queryFn: () => api.application(applicationId) });
  const prep = useQuery({ queryKey: interviewPrepKey(applicationId), queryFn: () => api.interviewPrep(applicationId) });
  return (
    <Loaded query={app}>
      {(a) => (
        <Loaded query={prep}>
          {(p) => <InterviewPrepView app={a} prep={p} />}
        </Loaded>
      )}
    </Loaded>
  );
}

function InterviewPrepView({ app, prep }: { app: Application; prep: InterviewPrep }) {
  const { q: selected, practice } = route.useSearch();
  const navigate = useNavigate({ from: "/applications/$applicationId/interview" });
  const wide = useMedia(WIDE);
  const qc = useQueryClient();
  const [filter, setFilter] = useState<InterviewSourceKind | undefined>(undefined);

  const generate = useMutation({
    mutationFn: () => api.generateInterviewPrep(app.id),
    onSuccess: (res) => qc.setQueryData(interviewPrepKey(app.id), res),
  });

  const company = app.posting.company as string | undefined;
  const jobTitle = app.posting.title as string | undefined;
  // 목록 묶음 순서(제출한 글 → 공고 → …)와 같은 순서. 연습도 이 순서로 넘긴다
  const questions = useMemo(
    () =>
      [...prep.questions].sort(
        (a, b) => SOURCE_ORDER.indexOf(a.source_kind) - SOURCE_ORDER.indexOf(b.source_kind) || a.position - b.position,
      ),
    [prep.questions],
  );
  const current = questions.find((q) => q.id === selected) ?? null;
  const ready = prep.status === "ready";

  const select = (id: string | undefined) => navigate({ search: (s) => ({ ...s, q: id }) });

  const header = (
    <PageHeader
      back={{ to: `/applications/${app.id}`, label: "지원" }}
      title="면접 준비"
      subtitle={
        <span className="flex flex-wrap items-center gap-x-1.5 tabular-nums">
          <span>{company && jobTitle ? `${company} · ${jobTitle}` : app.title}</span>
          {ready ? (
            <span>
              · 답 {int(prep.answered)} / {int(questions.length)}
            </span>
          ) : null}
        </span>
      }
      actions={
        ready ? (
          <div className="flex items-center gap-2">
            {questions.length && !practice ? (
              <Button size="sm" onClick={() => navigate({ search: (s) => ({ ...s, practice: true }) })}>
                연습
              </Button>
            ) : null}
            <Button size="sm" variant="ghost" onClick={() => generate.mutate()} disabled={generate.isPending}>
              <RefreshCw size={14} /> {generate.isPending ? "만드는 중" : "다시 만들기"}
            </Button>
          </div>
        ) : null
      }
    />
  );

  if (!ready) {
    return (
      <div>
        {header}
        <EmptyState className="space-y-3">
          <p>공고, 제출한 글, 회사 정보, 면접 후기에서 예상 질문을 만듭니다. 답은 직접 씁니다.</p>
          <Button variant="primary" onClick={() => generate.mutate()} disabled={generate.isPending}>
            {generate.isPending ? "만드는 중" : "예상 질문 만들기"}
          </Button>
          <ErrorText error={generate.error} />
        </EmptyState>
      </div>
    );
  }

  if (practice) {
    return (
      <div>
        {header}
        <Practice
          questions={questions}
          start={selected}
          onDone={(id) => navigate({ search: { q: id } })}
        />
      </div>
    );
  }

  const notice = <PrepNotice prep={prep} />;
  const list = (
    <QuestionList
      appId={app.id}
      questions={questions}
      filter={filter}
      onFilter={setFilter}
      selected={current?.id ?? null}
    />
  );
  const pane = current ? (
    <QuestionPane
      key={current.id}
      appId={app.id}
      q={current}
      wide={wide}
      onBack={() => select(undefined)}
      onDeleted={() => select(undefined)}
    />
  ) : null;

  return (
    <div>
      {header}
      <ErrorText error={generate.error} />
      {wide ? (
        <div className="grid grid-cols-[minmax(0,22rem)_minmax(0,1fr)] gap-10">
          <div className="sticky top-4 -mx-2 max-h-[calc(100dvh-2rem)] self-start overflow-y-auto px-2 pb-4">
            {notice}
            {list}
          </div>
          <div className="min-w-0">
            {pane ?? (
              <p className="border-t border-line py-4 text-sm text-muted">
                왼쪽에서 질문을 고릅니다. 출처를 보고 답 메모를 씁니다.
              </p>
            )}
          </div>
        </div>
      ) : pane ? (
        pane
      ) : (
        <>
          {notice}
          {list}
        </>
      )}
    </div>
  );
}

/** 만든 때·방식, 바뀐 출처, 만들 때 메모 */
function PrepNotice({ prep }: { prep: InterviewPrep }) {
  const notes = prep.notes.map(interviewNote).filter((n): n is string => !!n);
  return (
    <div className="mb-4 space-y-1 text-xs leading-5 text-muted">
      <p className="tabular-nums">
        {formatDateTime(prep.generated_at)}에 만들었습니다.
      </p>
      {prep.stale ? (
        <p className="font-medium text-fg">
          만든 뒤 바뀐 것이 있습니다: {prep.stale_reasons.map((r) => STALE_TEXT[r]).join(" · ")}. 다시 만들어도 답 메모는
          그대로 둡니다.
        </p>
      ) : null}
      {notes.map((n) => (
        <p key={n}>{n}</p>
      ))}
    </div>
  );
}

function QuestionList({
  appId,
  questions,
  filter,
  onFilter,
  selected,
}: {
  appId: string;
  questions: InterviewPrepQuestion[];
  filter: InterviewSourceKind | undefined;
  onFilter: (k: InterviewSourceKind | undefined) => void;
  selected: string | null;
}) {
  const counts = useMemo(() => {
    const c: Partial<Record<InterviewSourceKind, number>> = {};
    for (const q of questions) c[q.source_kind] = (c[q.source_kind] ?? 0) + 1;
    return c;
  }, [questions]);
  const kinds = SOURCE_ORDER.filter((k) => counts[k]);
  const shown = filter && counts[filter] ? [filter] : kinds;

  return (
    <div className="space-y-6">
      {kinds.length > 1 ? (
        <FilterChips<InterviewSourceKind | undefined>
          label="출처별 보기"
          value={filter && counts[filter] ? filter : undefined}
          onChange={onFilter}
          items={[
            { value: undefined, label: "전체", count: questions.length },
            ...kinds.map((k) => ({ value: k, label: SOURCE_GROUP[k], count: counts[k] })),
          ]}
        />
      ) : null}
      {questions.length ? null : (
        <EmptyState>만든 질문이 없습니다. 아래에서 질문을 직접 더할 수 있습니다.</EmptyState>
      )}
      {shown.map((k) => (
        <section key={k} aria-label={SOURCE_GROUP[k]}>
          <h2 className="mb-1 text-xs font-medium text-muted">{SOURCE_GROUP[k]}</h2>
          <ul className="divide-y divide-line border-y border-line">
            {questions
              .filter((q) => q.source_kind === k)
              .map((q) => (
                <li key={q.id}>
                  <Link
                    to="/applications/$applicationId/interview"
                    params={{ applicationId: appId }}
                    search={{ q: q.id }}
                    aria-current={q.id === selected ? "true" : undefined}
                    className={cn(
                      "-mx-2 flex min-h-9 items-start gap-2 rounded-md px-2 py-3 hover:bg-subtle/60",
                      q.id === selected && "bg-subtle",
                    )}
                  >
                    <span className="min-w-0 flex-1">
                      <span className="line-clamp-2 text-sm leading-5">{q.question}</span>
                      <span className="mt-0.5 block truncate text-xs text-muted">
                        {hasAnswer(q) ? <span className="font-medium text-fg">답 있음 · </span> : null}
                        {q.stale ? <span className="font-medium text-fg">출처 바뀜 · </span> : null}
                        {sourceLine(q.source)}
                      </span>
                    </span>
                    <ChevronRight size={16} className="mt-0.5 shrink-0 text-muted lg:hidden" aria-hidden />
                  </Link>
                </li>
              ))}
          </ul>
        </section>
      ))}
      <AddQuestion appId={appId} />
    </div>
  );
}

function AddQuestion({ appId }: { appId: string }) {
  const qc = useQueryClient();
  const navigate = useNavigate({ from: "/applications/$applicationId/interview" });
  const [text, setText] = useState("");
  const add = useMutation({
    mutationFn: () => api.addInterviewQuestion(appId, text.trim()),
    onSuccess: async (res) => {
      setText("");
      await qc.invalidateQueries({ queryKey: interviewPrepKey(appId) });
      void navigate({ search: { q: res.id } });
    },
  });
  return (
    <form
      className="space-y-2"
      onSubmit={(e) => {
        e.preventDefault();
        if (text.trim()) add.mutate();
      }}
    >
      <label htmlFor="add-question" className="text-xs font-medium text-muted">
        질문 직접 더하기
      </label>
      <div className="flex gap-2">
        <Input
          id="add-question"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="예: 이 일을 다시 한다면 무엇을 바꾸겠습니까?"
          maxLength={300}
          className="min-w-0 flex-1"
        />
        <Button type="submit" disabled={!text.trim() || add.isPending}>
          더하기
        </Button>
      </div>
      <ErrorText error={add.error} />
    </form>
  );
}

/** 출처 원문: 질문이 어디서 나왔는지 그대로 */
function SourceQuote({ q }: { q: InterviewPrepQuestion }) {
  const s = q.source;
  const quote = (text: string, foot?: ReactNode) => (
    <blockquote className="border-l-2 border-line pl-3 text-[13px] leading-6 text-muted">
      “{text}”{foot ? <span className="mt-1 block text-xs">{foot}</span> : null}
    </blockquote>
  );
  switch (s.kind) {
    case "submission":
      return quote(
        s.quote,
        <>
          {s.question_prompt ? `문항: ${s.question_prompt}` : (s.document_title ?? "제출한 글")}
          {q.stale_reasons.includes("submission_replaced") ? " · 지금 제출본이 아닌 글입니다" : ""}
        </>,
      );
    case "posting":
      return quote(s.text);
    case "company":
      return quote(
        s.quote || s.body,
        <>
          회사 항목은 참고 지식입니다. 답의 근거로 쓰지 않습니다.
          {s.source_url ? (
            <>
              {" "}
              <a href={s.source_url} target="_blank" rel="noreferrer" className="underline underline-offset-2 hover:text-fg">
                출처 열기
              </a>
            </>
          ) : null}
        </>,
      );
    case "reference":
      return (
        <p className="text-[13px] leading-6 text-muted">
          참고 글의 구조 메모에서 온 질문입니다. 남의 답은 싣지 않습니다.
          {s.competencies.length ? ` 보는 역량: ${s.competencies.join(", ")}.` : ""}{" "}
          <Link
            to="/brain/references/$referenceId"
            params={{ referenceId: s.reference_id }}
            className="underline underline-offset-2 hover:text-fg"
          >
            참고 글 보기
          </Link>
        </p>
      );
    case "common":
      return (
        <p className="text-[13px] leading-6 text-muted">
          어느 회사에서나 자주 나오는 질문입니다. 공통 답변을 가져오거나, 여기서 쓴 답을 공통 답변으로 저장할 수 있습니다.
        </p>
      );
    case "user":
      return null;
  }
}

function QuestionPane({
  appId,
  q,
  wide,
  onBack,
  onDeleted,
}: {
  appId: string;
  q: InterviewPrepQuestion;
  wide: boolean;
  onBack: () => void;
  onDeleted: () => void;
}) {
  const qc = useQueryClient();
  const refresh = () => qc.invalidateQueries({ queryKey: interviewPrepKey(appId) });
  const [text, setText] = useState(q.answer);
  const [picking, setPicking] = useState(false);
  const [feedback, setFeedback] = useState<InterviewFeedback | null>(null);
  const dirty = text !== q.answer;

  useEffect(() => {
    // 서버 값이 바뀌면(저장·공통 답변으로 옮김) 편집 중이 아닐 때만 맞춘다
    if (!dirty) setText(q.answer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q.answer]);

  useBlocker({
    shouldBlockFn: () => dirty && !window.confirm("저장하지 않은 답 메모가 있습니다. 이 화면을 나갈까요?"),
    enableBeforeUnload: () => dirty,
  });

  const save = useMutation({
    mutationFn: () => api.setInterviewAnswer(appId, q.id, text),
    onSuccess: async () => {
      setFeedback(null);
      askFeedback.reset();
      await refresh();
    },
  });
  const unlink = useMutation({ mutationFn: () => api.unlinkInterviewAnswer(appId, q.id), onSuccess: refresh });
  const saveAsset = useMutation({
    mutationFn: () => api.saveInterviewAnswerAsAsset(appId, q.id, { kind: q.common_kind }),
    onSuccess: async () => {
      // 메모는 새 공통 답변으로 옮겨졌다 (서버가 비운다)
      setText("");
      void qc.invalidateQueries({ queryKey: ["answer-assets"] });
      await refresh();
    },
  });
  const askFeedback = useMutation({
    mutationFn: () => api.interviewAnswerFeedback(appId, q.id),
    onSuccess: setFeedback,
  });
  const [confirmDelete, setConfirmDelete] = useState(false);
  const remove = useMutation({
    mutationFn: () => api.deleteInterviewQuestion(appId, q.id),
    onSuccess: async () => {
      setConfirmDelete(false);
      await refresh();
      toast("질문을 삭제했습니다.");
      onDeleted();
    },
  });

  const count = countChars(text);
  const saved = !!q.answer.trim() && !dirty;
  const canFeedback = !dirty && hasAnswer(q);

  const saveButton = (
    <Button variant="primary" className="ml-auto" onClick={() => save.mutate()} disabled={!dirty || save.isPending}>
      <Save size={14} /> {save.isPending ? "저장하는 중" : dirty ? "저장" : "저장됨"}
    </Button>
  );

  return (
    <article className="space-y-6">
      {!wide ? (
        <button
          type="button"
          onClick={onBack}
          className="-ml-1 inline-flex min-h-9 items-center gap-0.5 rounded-md pr-2 text-[13px] text-muted hover:text-fg"
        >
          <ChevronLeft size={16} /> 질문 목록
        </button>
      ) : null}

      <header className="space-y-2">
        <div className="flex items-start gap-2">
          <p className="min-w-0 flex-1 text-xs leading-5 text-muted">
            {sourceLine(q.source)}
            {q.origin === "model" ? " · 모델이 다듬은 문구" : ""}
          </p>
          <MoreMenu
            label="질문 메뉴"
            items={[
              {
                label: "질문 삭제",
                danger: true,
                disabled: remove.isPending,
                onSelect: () => setConfirmDelete(true),
              },
            ]}
          />
        </div>
        <h2 className="text-base leading-7 font-semibold">{q.question}</h2>
        <SourceQuote q={q} />
        {q.stale ? (
          <p className="text-xs font-medium">출처가 바뀌었습니다: {q.stale_reasons.map((r) => STALE_TEXT[r]).join(" · ")}</p>
        ) : null}
      </header>
      <ConfirmDialog
        open={confirmDelete}
        onOpenChange={setConfirmDelete}
        title="질문을 삭제할까요?"
        description="답 메모도 함께 삭제합니다. 이은 공통 답변은 그대로 둡니다."
        confirmLabel="삭제"
        tone="danger"
        busy={remove.isPending}
        onConfirm={() => remove.mutate()}
      >
        <ErrorText error={remove.error} />
      </ConfirmDialog>

      <section aria-labelledby="answer-heading" className="space-y-3">
        <h3 id="answer-heading" className="text-sm font-semibold">
          내 답
        </h3>
        {q.answer_asset ? (
          <div className="space-y-2 border-y border-line py-3">
            <p className="flex flex-wrap items-center gap-x-1.5 text-xs text-muted">
              <span>이은 공통 답변</span>
              <span aria-hidden>·</span>
              <Link
                to="/brain/answers/$answerId"
                params={{ answerId: q.answer_asset.id }}
                className="inline-flex min-h-9 items-center font-medium text-fg underline underline-offset-2 sm:min-h-6"
              >
                {q.answer_asset.title}
              </Link>
              <span className="tabular-nums">
                · {int(q.answer_asset.chars)}자 · 공백 제외 {int(q.answer_asset.chars_without_spaces)}자
              </span>
            </p>
            <p className="text-sm leading-6 whitespace-pre-wrap">{q.answer_asset.body}</p>
            <div className="flex flex-wrap gap-2">
              <Button size="sm" variant="ghost" onClick={() => unlink.mutate()} disabled={unlink.isPending}>
                연결 해제
              </Button>
            </div>
            <ErrorText error={unlink.error} />
          </div>
        ) : null}

        <div className="space-y-1.5">
          <label htmlFor="answer-memo" className="text-xs font-medium text-muted">
            {q.answer_asset ? "이 회사에 맞춘 메모" : "답 메모"}
          </label>
          <Textarea
            id="answer-memo"
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={wide ? 10 : 8}
            maxLength={5000}
            placeholder="말할 순서대로 짧게 적습니다. 어떤 경험을 꺼낼지, 숫자 하나, 맺는 말."
          />
          <p className="text-xs text-muted tabular-nums">
            {int(count.chars)}자 · 공백 제외 {int(count.noSpace)}자{dirty ? " · 저장하지 않음" : ""}
          </p>
          <ErrorText error={save.error} />
        </div>

        <div className="flex flex-wrap gap-2">
          {!q.answer_asset ? (
            <Button size="sm" onClick={() => setPicking(true)}>
              공통 답변에서 가져오기
            </Button>
          ) : null}
          {saved && !q.answer_asset ? (
            <Button size="sm" variant="ghost" onClick={() => saveAsset.mutate()} disabled={saveAsset.isPending}>
              공통 답변으로 저장
            </Button>
          ) : null}
          {canFeedback ? (
            <Button size="sm" variant="ghost" onClick={() => askFeedback.mutate()} disabled={askFeedback.isPending}>
              {askFeedback.isPending ? "의견 받는 중" : "답 의견 받기"}
            </Button>
          ) : null}
          {wide ? saveButton : null}
        </div>
        <ErrorText error={saveAsset.error ?? askFeedback.error} />
        {feedback ? <FeedbackList feedback={feedback} onClose={() => setFeedback(null)} /> : null}
      </section>

      <Suggestions q={q} />

      {!wide ? (
        <div className={BOTTOM_BAR}>
          <Button onClick={onBack}>목록</Button>
          {saveButton}
        </div>
      ) : null}

      <AnswerPicker appId={appId} q={q} open={picking} onClose={() => setPicking(false)} onLinked={refresh} />
    </article>
  );
}

function FeedbackList({ feedback, onClose }: { feedback: InterviewFeedback; onClose: () => void }) {
  return (
    <div className="space-y-2 border-y border-line py-3">
      <div className="flex items-center gap-2">
        <h4 className="flex-1 text-xs font-medium">답 의견</h4>
        <Button size="sm" variant="ghost" onClick={onClose}>
          닫기
        </Button>
      </div>
      {feedback.points.length ? (
        <ul className="space-y-1.5 text-[13px] leading-5">
          {feedback.points.map((p, i) => (
            <li key={i}>
              <span className="font-medium">{POINT_KIND[p.kind]}:</span> {p.text}
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-[13px] text-muted">덧붙일 의견이 없습니다.</p>
      )}
      <p className="text-xs text-muted">의견은 저장하지 않습니다. 답은 직접 수정합니다.</p>
    </div>
  );
}

/** 쓸 경험: 확인한 사실과 그 원문 근거 */
function Suggestions({ q }: { q: InterviewPrepQuestion }) {
  if (q.source_kind === "user" && !q.suggestions.length) return null;
  return (
    <section aria-labelledby="suggest-heading" className="space-y-2">
      <h3 id="suggest-heading" className="text-sm font-semibold">
        쓸 경험
      </h3>
      {q.suggestions.length ? (
        <ul className="divide-y divide-line border-y border-line">
          {q.suggestions.map((c) => {
            const meta = [c.organization, c.role, c.period].filter(Boolean).join(" · ");
            return (
              <li key={c.experience_id} className="space-y-2 py-3">
                <div>
                  <Link
                    to="/brain/$experienceId"
                    params={{ experienceId: c.experience_id }}
                    className="text-sm leading-5 font-semibold underline-offset-2 hover:underline"
                  >
                    {c.title}
                  </Link>
                  {meta ? <p className="text-xs text-muted">{meta}</p> : null}
                </div>
                <ul className="space-y-2">
                  {c.evidence.map((e) => (
                    <li key={e.claim_id} className="text-[13px] leading-5">
                      <p>{e.statement}</p>
                      {e.quote ? (
                        <blockquote className="mt-1 border-l-2 border-line pl-2 text-xs leading-5 text-muted">
                          “{e.quote}”
                          <span className="mt-0.5 block">
                            {e.source.kind === "span"
                              ? `원문: ${e.source.title ?? "자료"}${e.source.page ? ` ${e.source.page}쪽` : ""}`
                              : "인터뷰 답"}
                            {e.restricted ? " · 이 기기 밖으로 보내지 않음" : ""}
                          </span>
                        </blockquote>
                      ) : null}
                    </li>
                  ))}
                </ul>
                {c.gaps.length ? (
                  <p className="text-xs">
                    <span className="font-medium">빈 곳:</span> {c.gaps.map((g) => GAP[g] ?? g).join(" · ")}
                  </p>
                ) : null}
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="border-t border-line py-3 text-[13px] leading-5 text-muted">
          맞는 경험을 찾지 못했습니다. 원문 근거가 있는 확인한 사실이 있어야 후보가 됩니다.
        </p>
      )}
    </section>
  );
}

/** 공통 답변에서 가져오기: 글을 복사하지 않고 잇는다. 같은 종류를 먼저 보인다. */
function AnswerPicker({
  appId,
  q,
  open,
  onClose,
  onLinked,
}: {
  appId: string;
  q: InterviewPrepQuestion;
  open: boolean;
  onClose: () => void;
  onLinked: () => Promise<unknown>;
}) {
  const list = useQuery({ queryKey: ["answer-assets"], queryFn: api.answerAssets, enabled: open });
  const link = useMutation({
    mutationFn: (answerId: string) => api.linkInterviewAnswer(appId, q.id, answerId),
    onSuccess: async () => {
      await onLinked();
      onClose();
    },
  });
  const rows = useMemo(() => {
    const all = list.data ?? [];
    if (!q.common_kind) return all;
    return [...all.filter((a) => a.kind === q.common_kind), ...all.filter((a) => a.kind !== q.common_kind)];
  }, [list.data, q.common_kind]);
  return (
    <Sheet
      open={open}
      onOpenChange={(o) => !o && onClose()}
      title="공통 답변에서 가져오기"
      description="고른 공통 답변을 이 질문에 잇습니다. 글은 공통 답변 쪽에 그대로 있습니다."
    >
      <Loaded query={list}>
        {() =>
          rows.length ? (
            <ul className="divide-y divide-line border-y border-line">
              {rows.map((a) => (
                <li key={a.id}>
                  <button
                    type="button"
                    onClick={() => link.mutate(a.id)}
                    disabled={link.isPending}
                    className="-mx-2 block min-h-9 w-[calc(100%+1rem)] rounded-md px-2 py-3 text-left hover:bg-subtle/60"
                  >
                    <span className="block text-sm leading-5 font-medium">{a.title}</span>
                    <span className="block text-xs text-muted tabular-nums">
                      {ANSWER_KIND_LABEL[a.kind]} · {int(a.chars)}자
                    </span>
                    {a.excerpt ? (
                      <span className="mt-1 line-clamp-2 block text-[13px] leading-5 text-muted">{a.excerpt}</span>
                    ) : null}
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState>
              공통 답변이 아직 없습니다. 답 메모를 쓰고 "공통 답변으로 저장"을 누르거나{" "}
              <Link to="/brain/answers" className="underline underline-offset-2 hover:text-fg">
                공통 답변
              </Link>
              에서 씁니다.
            </EmptyState>
          )
        }
      </Loaded>
      <ErrorText error={link.error} />
    </Sheet>
  );
}

/** 연습: 질문 하나씩, 메모는 눌러야 보인다 */
function Practice({
  questions,
  start,
  onDone,
}: {
  questions: InterviewPrepQuestion[];
  start: string | undefined;
  onDone: (id: string | undefined) => void;
}) {
  const [at, setAt] = useState(() => Math.max(0, questions.findIndex((q) => q.id === start)));
  const [show, setShow] = useState(false);
  const i = Math.min(at, questions.length - 1);
  const q = questions[i];
  if (!q) return null;
  const go = (n: number) => {
    setAt(n);
    setShow(false);
  };
  const memo = [q.answer_asset?.body, q.answer].filter((t): t is string => !!t?.trim());
  return (
    <section aria-label="연습" className="space-y-6">
      <p className="text-xs text-muted tabular-nums">
        {int(i + 1)} / {int(questions.length)} · {sourceLine(q.source)}
      </p>
      <h2 className="text-lg leading-8 font-semibold">{q.question}</h2>
      <p className="text-[13px] leading-6 text-muted">소리 내어 답해 봅니다. 끝나면 메모와 견주어 봅니다.</p>
      <div className="border-y border-line py-3">
        <Button size="sm" variant="ghost" onClick={() => setShow((s) => !s)} aria-expanded={show}>
          {show ? "메모 숨기기" : "메모 보기"}
        </Button>
        {show ? (
          memo.length ? (
            <div className="mt-2 space-y-3">
              {memo.map((t, k) => (
                <p key={k} className="text-sm leading-6 whitespace-pre-wrap">
                  {t}
                </p>
              ))}
            </div>
          ) : (
            <p className="mt-2 text-[13px] text-muted">아직 쓴 메모가 없습니다.</p>
          )
        ) : null}
      </div>
      <div className={BOTTOM_BAR}>
        <Button onClick={() => onDone(q.id)}>연습 끝</Button>
        <Button className="ml-auto" onClick={() => go(i - 1)} disabled={i === 0} aria-label="이전 질문">
          <ChevronLeft size={14} /> 이전
        </Button>
        <Button variant="primary" onClick={() => go(i + 1)} disabled={i >= questions.length - 1} aria-label="다음 질문">
          다음 <ChevronRight size={14} />
        </Button>
      </div>
    </section>
  );
}
