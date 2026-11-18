// 지원 작업대의 "쓸 경험" 칸 (O15, plan §19.3). 문항마다 쓸 경험 후보와 그 근거·이유·빈 곳을 보인다.
// "이 경험으로 쓰기"는 고른 것을 기록한다. 초안 쓰기(§22)가 고른 경험을 먼저 쓴다.
import type {
  ApplicationPrep,
  DocumentSummary,
  PrepCandidate,
  PrepCompanyFact,
  PrepCoverage,
  PrepGap,
  PrepQuestion,
  PrepStaleReason,
} from "@career-brain/client";
import { Button, cn, Disclosure, EmptyState } from "@career-brain/ui";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { Check, RefreshCw } from "lucide-react";
import { ErrorText } from "../common";
import { api } from "../../lib/api";
import { FACT_KIND_LABEL } from "../../lib/companies";
import { formatDateTime } from "../../lib/dates";

export const prepKey = (appId: string) => ["application-prep", appId] as const;

const GAP: Record<PrepGap, string> = {
  no_metric: "수치 없음",
  role_unclear: "본인 역할 불분명",
  period_unknown: "기간 모름",
};

const STALE: Record<PrepStaleReason, string> = {
  posting_changed: "공고를 다시 저장했습니다",
  questions_changed: "문항이 바뀌었습니다",
  experience_changed: "후보 경험의 내용이 바뀌었습니다",
};

const COVERAGE: Record<PrepCoverage["level"], { label: string; cls: string }> = {
  strong: { label: "근거 있음", cls: "text-muted" },
  weak: { label: "근거 약함", cls: "font-medium text-fg" },
  none: { label: "근거 없음", cls: "font-medium text-danger" },
};

/** 만들 때 남긴 메모 → 사람 말. 모르는 것은 보이지 않는다. */
function noteText(note: string): string | null {
  const [code, arg] = note.split(":", 2);
  switch (code) {
    case "model_not_allowed":
      return "모델 없이 검색 순위로 찾았습니다.";
    case "model_denied":
      return "모델 연결이 없어 검색 순위로 찾았습니다.";
    case "model_error":
      return "모델이 답하지 않아 검색 순위로 찾았습니다.";
    case "model_invalid_output":
      return "모델 답을 읽지 못해 검색 순위로 찾았습니다.";
    case "model_dropped":
      return `모델이 후보 밖에서 고른 ${arg}개는 뺐습니다.`;
    case "restricted_withheld":
      return `이 기기 밖으로 보내지 않는 사실 ${arg}개는 모델에 보내지 않았습니다.`;
    case "search":
      return "의미 검색 없이 낱말로만 찾았습니다.";
    default:
      return null;
  }
}

/** 문서 이름 (문항이면 "문항 2") */
export function docLabel(d: Pick<DocumentSummary, "question_index" | "title"> | undefined) {
  if (!d) return "다른 문서";
  return d.question_index !== null ? `문항 ${d.question_index + 1}` : d.title;
}

/** 이 경험을 쓰는(고른·연결한) 다른 문서 */
export function usedElsewhere(prep: ApplicationPrep, experienceId: string, docId: string): string[] {
  const ids = new Set<string>();
  for (const [d, cs] of Object.entries(prep.choices))
    if (d !== docId && cs.some((c) => c.experience_id === experienceId)) ids.add(d);
  for (const o of prep.overlaps)
    if (o.experience_id === experienceId) for (const d of o.document_ids) if (d !== docId) ids.add(d);
  return [...ids];
}

function useGenerate(appId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (useModel: boolean) => api.generateApplicationPrep(appId, { use_model: useModel }),
    onSuccess: (data) => qc.setQueryData(prepKey(appId), data),
  });
}

export function PrepPanel({
  appId,
  postingId,
  prep,
  doc,
  docs,
  editable,
  onChosen,
}: {
  appId: string;
  postingId: string;
  prep: ApplicationPrep;
  doc: DocumentSummary | undefined;
  docs: DocumentSummary[];
  editable: boolean;
  /** 고른 뒤 (좁은 화면: 쓰기 단계로) */
  onChosen?: () => void;
}) {
  const generate = useGenerate(appId);

  if (prep.status === "none")
    return (
      <div className="space-y-3">
        <p className="text-[13px] leading-6">
          문항마다 쓸 경험을 2~3개 찾습니다. 고른 경험은 초안을 쓸 때 먼저 씁니다.
        </p>
        {editable ? (
          <Button variant="primary" onClick={() => generate.mutate(true)} disabled={generate.isPending}>
            {generate.isPending ? "찾는 중" : "쓸 경험 찾기"}
          </Button>
        ) : null}
        <SendNote />
        <ErrorText error={generate.error} />
      </div>
    );

  const question = doc ? prep.questions.find((q) => q.document_id === doc.id) : undefined;
  const notes = [...new Set(prep.notes.map(noteText).filter((n): n is string => !!n))];
  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted tabular-nums">
          <span className="font-medium text-fg">
            {prep.mode === "model" ? "모델이 고른 후보" : "검색으로 찾은 후보"}
          </span>
          {prep.generated_at ? <span>· {formatDateTime(prep.generated_at)}</span> : null}
          {editable ? (
            <Button
              size="sm"
              variant="ghost"
              className="-my-1 ml-auto"
              onClick={() => generate.mutate(true)}
              disabled={generate.isPending}
            >
              <RefreshCw size={13} /> {generate.isPending ? "찾는 중" : "다시 찾기"}
            </Button>
          ) : null}
        </div>
        {prep.stale ? (
          <p role="status" className="rounded-md border border-line px-3 py-2 text-[13px]">
            <span className="font-medium">
              {prep.stale_reasons.map((r) => STALE[r]).join(", ")}.
            </span>{" "}
            후보가 지금과 다를 수 있습니다. 다시 찾으면 고른 것은 그대로 둡니다.
          </p>
        ) : null}
        {notes.length ? <p className="text-xs text-muted">{notes.join(" ")}</p> : null}
        <ErrorText error={generate.error} />
      </div>

      {question ? (
        <QuestionCandidates
          appId={appId}
          prep={prep}
          q={question}
          docs={docs}
          editable={editable}
          onChosen={onChosen}
        />
      ) : doc && prep.new_questions.includes(doc.id) ? (
        <EmptyState>준비 문서를 만든 뒤 생긴 문항입니다. 다시 찾으면 이 문항의 후보도 찾습니다.</EmptyState>
      ) : (
        <p className="text-[13px] text-muted">
          문항이 아닌 문서는 후보를 따로 찾지 않습니다. 아래 요건별 근거를 참고합니다.
        </p>
      )}

      <CoverageList rows={prep.requirements} postingId={postingId} />
    </div>
  );
}

function SendNote() {
  return (
    <Disclosure title="무엇을 보내나요" className="text-xs text-muted">
      <p className="mt-1 leading-5">
        먼저 내 브레인을 검색해 후보를 좁힙니다. 모델을 연결했으면 문항·공고 요건·후보 경험의 확인한 사실을 보내 그 안에서만
        고르고 이유를 쓰게 합니다. 이 기기 밖으로 보내지 않는 사실은 보내지 않습니다. 모델이 없으면 검색 순위로 보입니다.
      </p>
    </Disclosure>
  );
}

function QuestionCandidates({
  appId,
  prep,
  q,
  docs,
  editable,
  onChosen,
}: {
  appId: string;
  prep: ApplicationPrep;
  q: PrepQuestion;
  docs: DocumentSummary[];
  editable: boolean;
  onChosen?: () => void;
}) {
  const facts = new Map((prep.company?.facts ?? []).map((f) => [f.id, f]));
  const chosen = new Set((prep.choices[q.document_id] ?? []).map((c) => c.experience_id));
  return (
    <section className="space-y-3">
      <h3 className="text-[13px] font-semibold">
        쓸 경험 <span className="font-normal text-muted tabular-nums">{q.candidates.length}</span>
      </h3>
      {q.focus ? <p className="text-[13px] text-muted">이 문항이 묻는 것: {q.focus}</p> : null}
      {q.candidates.length ? (
        <ol className="divide-y divide-line border-y border-line">
          {q.candidates.map((c) => (
            <CandidateItem
              key={c.experience_id}
              appId={appId}
              docId={q.document_id}
              c={c}
              facts={facts}
              chosen={chosen.has(c.experience_id)}
              elsewhere={usedElsewhere(prep, c.experience_id, q.document_id).map((d) =>
                docLabel(docs.find((x) => x.id === d)),
              )}
              stale={prep.stale_experiences.includes(c.experience_id)}
              editable={editable}
              onChosen={onChosen}
            />
          ))}
        </ol>
      ) : (
        <EmptyState>
          이 문항에 맞는 경험을 찾지 못했습니다. 원문 근거가 있는 확인한 사실이 있어야 후보가 됩니다.{" "}
          <Link to="/brain" className="text-fg underline underline-offset-2">
            브레인으로
          </Link>
        </EmptyState>
      )}
      {q.references.length ? (
        <Disclosure title={<>참고 구성 <span className="tabular-nums">{q.references.length}</span></>} className="text-[13px]">
          <ul className="mt-2 space-y-3">
            {q.references.map((r) => (
              <li key={r.note_id} className="border-l border-line pl-3">
                <p className="text-xs text-muted">
                  {r.reference_title} · {r.same_company ? "같은 회사" : "같은 직무"}
                </p>
                {r.structure.length ? (
                  <ol className="mt-1 list-decimal space-y-0.5 pl-4 text-[13px]">
                    {r.structure.map((s, i) => (
                      <li key={i}>{s}</li>
                    ))}
                  </ol>
                ) : null}
              </li>
            ))}
          </ul>
          <p className="mt-2 text-xs text-muted">참고 글의 짜임만 보입니다. 그 글의 문장은 싣지 않습니다.</p>
        </Disclosure>
      ) : null}
    </section>
  );
}

function CandidateItem({
  appId,
  docId,
  c,
  facts,
  chosen,
  elsewhere,
  stale,
  editable,
  onChosen,
}: {
  appId: string;
  docId: string;
  c: PrepCandidate;
  facts: Map<string, PrepCompanyFact>;
  chosen: boolean;
  elsewhere: string[];
  stale: boolean;
  editable: boolean;
  onChosen?: () => void;
}) {
  const qc = useQueryClient();
  const done = () => qc.invalidateQueries({ queryKey: prepKey(appId) });
  const choose = useMutation({
    mutationFn: () =>
      api.choosePrepExperience(appId, docId, {
        experience_id: c.experience_id,
        claim_ids: c.evidence.map((e) => e.claim_id),
      }),
    onSuccess: async () => {
      await done();
      onChosen?.();
    },
  });
  const unchoose = useMutation({
    mutationFn: () => api.unchoosePrepExperience(appId, docId, c.experience_id),
    onSuccess: done,
  });
  const meta = [c.organization, c.role, c.period].filter(Boolean).join(" · ");
  const terms = [...new Set(c.matched.flatMap((m) => m.terms))];
  const linked = c.fact_ids.map((id) => facts.get(id)).filter((f): f is PrepCompanyFact => !!f);
  return (
    <li className="space-y-2 py-4">
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <h4 className="text-sm leading-5 font-semibold">
            <Link
              to="/brain/$experienceId"
              params={{ experienceId: c.experience_id }}
              className="underline-offset-2 hover:underline"
            >
              {c.title}
            </Link>
          </h4>
          {meta ? <p className="text-xs text-muted">{meta}</p> : null}
        </div>
        {chosen ? (
          <span className="inline-flex shrink-0 items-center gap-1 pt-0.5 text-xs font-medium">
            <Check size={13} /> 고름
          </span>
        ) : null}
      </div>

      {c.reason ? (
        <p className="text-[13px] leading-5">{c.reason}</p>
      ) : (
        <p className="text-[13px] leading-5 text-muted">
          모델 없이 찾았습니다.{" "}
          {terms.length ? `공고 요건 낱말 ${terms.slice(0, 5).join(", ")}이(가) 이 경험에 보입니다.` : "문항과 낱말이 겹칩니다."}
        </p>
      )}

      {linked.length ? (
        <ul className="space-y-0.5 text-xs text-muted">
          {linked.map((f) => (
            <li key={f.id}>
              회사 {FACT_KIND_LABEL[f.kind as keyof typeof FACT_KIND_LABEL] ?? f.kind}(참고): {f.body}
            </li>
          ))}
        </ul>
      ) : null}

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

      {c.gaps.length || stale ? (
        <p className="flex flex-wrap items-center gap-x-2 text-xs">
          {c.gaps.length ? (
            <span>
              <span className="font-medium">빈 곳:</span> {c.gaps.map((g) => GAP[g]).join(" · ")}
            </span>
          ) : null}
          {stale ? <span className="text-muted">· 찾은 뒤 내용이 바뀜</span> : null}
          {c.gaps.length ? (
            <Link
              to="/brain/$experienceId"
              params={{ experienceId: c.experience_id }}
              search={{ tab: "interview" }}
              className="inline-flex min-h-9 items-center text-muted underline underline-offset-2 hover:text-fg sm:min-h-6"
            >
              질문 받으며 채우기
            </Link>
          ) : null}
        </p>
      ) : null}

      {elsewhere.length ? (
        <p className="text-xs font-medium">다른 문서에도 씁니다: {elsewhere.join(", ")}</p>
      ) : null}

      {editable ? (
        <div className="flex flex-wrap gap-2 pt-1">
          {chosen ? (
            <Button size="sm" variant="ghost" onClick={() => unchoose.mutate()} disabled={unchoose.isPending}>
              고른 것 취소
            </Button>
          ) : (
            <Button size="sm" onClick={() => choose.mutate()} disabled={choose.isPending}>
              이 경험으로 쓰기
            </Button>
          )}
        </div>
      ) : null}
      <ErrorText error={choose.error ?? unchoose.error} />
    </li>
  );
}

/** 요건 충족표: 요건마다 내 브레인에 근거가 있는지 (글이 아니라 경험 쪽). */
export function CoverageList({ rows, postingId }: { rows: PrepCoverage[]; postingId: string }) {
  if (!rows.length) return null;
  const n = (l: PrepCoverage["level"]) => rows.filter((r) => r.level === l).length;
  return (
    <Disclosure
      tone="strong"
      title={
        <>
          요건별 근거
          <span className="ml-1 font-normal text-muted tabular-nums">
            · 있음 {n("strong")} · 약함 {n("weak")} · 없음 {n("none")}
          </span>
        </>
      }
    >
      <ul className="mt-2 divide-y divide-line border-y border-line">
        {rows.map((r, i) => {
          const lv = COVERAGE[r.level];
          return (
            <li key={`${r.requirement}-${i}`} className="py-2.5">
              <div className="flex items-start gap-3">
                <p className="min-w-0 flex-1 leading-5">{r.requirement}</p>
                <span className={cn("shrink-0 text-xs", lv.cls)}>{lv.label}</span>
              </div>
              {r.claims.length ? (
                <p className="mt-0.5 line-clamp-2 text-xs text-muted">
                  {r.claims[0]!.experience_title}: {r.claims[0]!.statement}
                </p>
              ) : null}
              {r.level === "weak" && r.experience_ids[0] ? (
                <Link
                  to="/brain/$experienceId"
                  params={{ experienceId: r.experience_ids[0] }}
                  search={{ tab: "interview" }}
                  className="inline-flex min-h-9 items-center text-xs text-muted underline underline-offset-2 hover:text-fg sm:min-h-6"
                >
                  질문 받으며 채우기
                </Link>
              ) : null}
              {r.level === "none" ? (
                <Link
                  to="/study"
                  search={{ tab: "coverage", posting: postingId }}
                  className="inline-flex min-h-9 items-center text-xs text-muted underline underline-offset-2 hover:text-fg sm:min-h-6"
                >
                  학습에서 채우기
                </Link>
              ) : null}
            </li>
          );
        })}
      </ul>
      <p className="mt-2 text-xs text-muted">낱말로 본 것입니다. 근거 있음은 원문 근거가 있는 확인한 사실이 요건 낱말을 덮을 때입니다.</p>
    </Disclosure>
  );
}
