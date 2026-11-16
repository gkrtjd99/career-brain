// 공고 화면의 c0025 추가분: 스캔 PDF 비전 비용 확인, 지원서 선택형 문항(구조화 데이터에서만).
import type { ChoiceQuestion, FetchRun, Posting } from "@career-brain/client";
import { Badge, Button, Card, Section } from "@career-brain/ui";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ErrorText } from "../common";
import { api } from "../../lib/api";
import { usd } from "../../lib/format";

/** 예상 비용이 상한을 넘어 멈춘 가져오기: 쪽·이미지·토큰·금액을 보여 주고 확인/거절을 받는다. */
export function CostConfirmation({ run, postingId }: { run: FetchRun; postingId: string }) {
  const qc = useQueryClient();
  const done = () => {
    void qc.invalidateQueries({ queryKey: ["posting", postingId] });
    void qc.invalidateQueries({ queryKey: ["postings"] });
  };
  const confirm = useMutation({ mutationFn: () => api.confirmFetchCost(postingId, run.id), onSuccess: done });
  const decline = useMutation({ mutationFn: () => api.declineFetchCost(postingId, run.id), onSuccess: done });
  const c = run.confirmation;
  if (!c) return null;
  const pending = c.status === "pending";
  return (
    <Card className="space-y-2 border-warn/40 p-3 text-[13px]">
      <p className="font-medium">스캔 PDF를 AI로 읽기 전에 비용을 확인해 주세요</p>
      <p className="text-muted">
        글자가 없는 PDF라 쪽 이미지를 이미지 읽기 모델({c.model})로 보냅니다. 예상 비용이 확인 기준(
        {usd(c.threshold_usd, 2)})을 넘었습니다. 토큰 수와 금액은 추정값입니다.
      </p>
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
        <dt className="text-muted">읽을 쪽</dt>
        <dd>
          {c.pages.join(", ")}쪽 (전체 {c.pages_total}쪽 중 {c.images}장)
          {c.pages_total > c.images ? " · 빈 쪽을 빼고 앞쪽과 마지막 쪽을 골랐습니다" : ""}
        </dd>
        <dt className="text-muted">토큰(추정)</dt>
        <dd>
          입력 {c.input_tokens.toLocaleString()} · 출력 약 {c.output_tokens.toLocaleString()}
        </dd>
        <dt className="text-muted">예상 비용</dt>
        <dd>
          {c.estimated_usd ? usd(c.estimated_usd) : "단가 모름"}
          {c.max_usd ? ` (최대 ${usd(c.max_usd)})` : ""}
        </dd>
      </dl>
      {pending ? (
        <div className="flex flex-wrap gap-2">
          <Button
            size="sm"
            variant="primary"
            disabled={confirm.isPending || decline.isPending}
            onClick={() => confirm.mutate()}
          >
            확인하고 읽기
          </Button>
          <Button size="sm" variant="ghost" disabled={confirm.isPending || decline.isPending} onClick={() => decline.mutate()}>
            읽지 않기 (직접 붙여넣기)
          </Button>
        </div>
      ) : (
        <p className="text-muted">
          {c.status === "confirmed" || c.status === "used" ? "확인했습니다. 다시 가져오는 중입니다." : "결정한 요청입니다."}
        </p>
      )}
      <ErrorText error={confirm.error ?? decline.error} />
    </Card>
  );
}

/** 지원서 선택형 문항 (채용 시스템이 준 보기 고르기). 자기소개서 문항과 따로, 읽기 전용으로 보여 준다. */
export function ChoiceQuestions({ p }: { p: Posting }) {
  const value = (p.fields.application_questions ?? p.latest?.extracted.application_questions?.value) as
    | ChoiceQuestion[]
    | undefined;
  const evidence = p.evidence.application_questions ?? p.latest?.extracted.application_questions?.evidence;
  if (!Array.isArray(value) || !value.length) return null;
  const ref = evidence && typeof evidence.ref === "string" && evidence.kind === "structured" ? evidence.ref : null;
  return (
    <Section title="지원서 선택형 문항">
      <Card className="space-y-3 p-3 text-[13px]">
        <ul className="space-y-2">
          {value.map((q, i) => (
            <li key={`${i}-${q.prompt}`}>
              <div className="flex flex-wrap items-center gap-1.5">
                <span className="font-medium">{q.prompt}</span>
                {q.required ? <Badge tone="warn">필수</Badge> : null}
              </div>
              <div className="mt-1 flex flex-wrap gap-1">
                {q.options.map((o) => (
                  <Badge key={o} tone="none">
                    {o}
                  </Badge>
                ))}
              </div>
            </li>
          ))}
        </ul>
        <p className="text-xs text-muted">
          보기를 고르는 문항이라 자기소개서 문항에는 넣지 않았습니다.
          {ref ? ` 근거: 채용 시스템 데이터 ${ref}` : evidence?.kind === "user" ? " 직접 고친 값입니다." : ""}
        </p>
      </Card>
    </Section>
  );
}
