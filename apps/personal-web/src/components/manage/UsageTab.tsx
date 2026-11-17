import type { UsageGroup, UsageRow } from "@career-brain/client";
import { Badge, Button, cn, Input, Select } from "@career-brain/ui";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { ErrorText, Loaded } from "../common";
import { api } from "../../lib/api";
import { formatDate } from "../../lib/dates";
import { int, usd } from "../../lib/format";

const GROUPS: [UsageGroup, string][] = [
  ["day", "날짜별"],
  ["model", "모델별"],
  ["purpose", "작업별"],
  ["endpoint", "엔드포인트별"],
  ["status", "결과별"],
];

const PURPOSE_LABEL: Record<string, string> = {
  brain_extract: "경험 추출",
  interview_questions: "인터뷰 질문",
  job_extract: "공고 추출",
  company_facts: "회사 항목 추출",
  reference_notes: "참고 글 메모",
  embedding: "검색 색인",
  search: "검색",
  ping: "연결 확인",
};

/** 종량제 환산. 모든 호출의 단가를 모르면 0 대신 "미집계". */
function equivalent(r: UsageRow) {
  return r.calls > 0 && r.unknown_cost_calls === r.calls ? "미집계" : usd(r.equivalent_usd);
}

export function UsageTab({ canEdit }: { canEdit: boolean }) {
  const [days, setDays] = useState(30);
  const [groupBy, setGroupBy] = useState<UsageGroup>("day");
  const q = useQuery({
    queryKey: ["usage", days, groupBy],
    queryFn: () => api.usage({ days, groupBy }),
    placeholderData: keepPreviousData,
  });

  return (
    <div className="max-w-4xl">
      <p className="mb-4 text-[13px] text-muted">모델 호출마다 토큰과 비용을 기록합니다.</p>
      <div className="mb-6 flex flex-wrap gap-2">
        <Select value={days} onChange={(e) => setDays(Number(e.target.value))} className="w-auto" aria-label="기간">
          <option value={7}>최근 7일</option>
          <option value={30}>최근 30일</option>
          <option value={90}>최근 90일</option>
          <option value={365}>최근 1년</option>
        </Select>
        <Select
          value={groupBy}
          onChange={(e) => setGroupBy(e.target.value as UsageGroup)}
          className="w-auto"
          aria-label="묶는 기준"
        >
          {GROUPS.map(([v, l]) => (
            <option key={v} value={v}>
              {l}
            </option>
          ))}
        </Select>
      </div>
      <Loaded query={q}>
        {(u) => {
          const budget = u.monthly_budget_usd ? Number(u.monthly_budget_usd) : null;
          const spent = Number(u.month_spent_usd);
          const ratio = budget ? Math.min(1, spent / budget) : 0;
          return (
            <div className={cn("space-y-10", q.isFetching && "opacity-70")}>
              <section>
                <h2 className="mb-2 text-[15px] font-semibold">요약</h2>
                <dl className="divide-y divide-line border-y border-line">
                  <Row label="이번 달 실제 청구">
                    <span className="font-medium">{usd(spent, 4)}</span>
                    {budget !== null ? <span className="text-muted"> / 예산 {usd(budget, 2)}</span> : null}
                    {budget !== null ? (
                      <div
                        className="mt-1.5 ml-auto h-1 w-32 overflow-hidden rounded-full bg-subtle"
                        role="meter"
                        aria-valuemin={0}
                        aria-valuemax={budget}
                        aria-valuenow={spent}
                        aria-label="이번 달 예산 사용"
                      >
                        <div
                          className={ratio >= 0.8 ? "h-full bg-danger" : "h-full bg-fg"}
                          style={{ width: `${ratio * 100}%` }}
                        />
                      </div>
                    ) : null}
                  </Row>
                  <BudgetRow canEdit={canEdit} />
                  <Row label="기간 호출">{int(u.total.calls)}회</Row>
                  <Row label="기간 청구">{usd(u.total.spent_usd)}</Row>
                  <Row label="종량제 환산">
                    {equivalent(u.total)}
                    {u.total.unknown_cost_calls ? (
                      <span className="text-muted"> · 단가 미상 {int(u.total.unknown_cost_calls)}회</span>
                    ) : null}
                  </Row>
                </dl>
                {u.notes.length ? (
                  <ul className="mt-3 space-y-1">
                    {u.notes.map((n) => (
                      <li key={n} className="text-[13px] font-medium">
                        {n}
                      </li>
                    ))}
                  </ul>
                ) : null}
              </section>

              <section>
                <h2 className="mb-2 text-[15px] font-semibold">
                  {GROUPS.find((g) => g[0] === u.group_by)?.[1] ?? "내역"}
                </h2>
                {u.rows.length ? (
                  <>
                    {/* 넓은 화면: 표 */}
                    <div className="hidden overflow-x-auto md:block">
                      <table className="w-full border-t border-line text-[13px] tabular-nums">
                        <thead className="border-b border-line text-left text-xs text-muted">
                          <tr>
                            <th className="py-2 pr-3 font-medium">
                              {GROUPS.find((g) => g[0] === u.group_by)?.[1].replace("별", "")}
                            </th>
                            <th className="px-3 py-2 text-right font-medium">호출</th>
                            <th className="px-3 py-2 text-right font-medium">입력</th>
                            <th className="px-3 py-2 text-right font-medium">캐시 입력</th>
                            <th className="px-3 py-2 text-right font-medium">출력</th>
                            <th className="px-3 py-2 text-right font-medium">청구</th>
                            <th className="py-2 pl-3 text-right font-medium">환산</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-line">
                          {[...u.rows, u.total].map((r) => (
                            <tr key={r.key} className={r === u.total ? "font-medium" : undefined}>
                              <td className="py-2.5 pr-3">
                                <RowKey row={r} group={u.group_by} total={r === u.total} />
                              </td>
                              <td className="px-3 py-2.5 text-right">{int(r.calls)}</td>
                              <td className="px-3 py-2.5 text-right">{int(r.input_tokens)}</td>
                              <td className="px-3 py-2.5 text-right">{int(r.cached_input_tokens)}</td>
                              <td className="px-3 py-2.5 text-right">{int(r.output_tokens + r.reasoning_tokens)}</td>
                              <td className="px-3 py-2.5 text-right">{usd(r.spent_usd)}</td>
                              <td className="py-2.5 pl-3 text-right text-muted">{equivalent(r)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                    {/* 좁은 화면: 목록 */}
                    <ul className="divide-y divide-line border-t border-line md:hidden">
                      {u.rows.map((r) => (
                        <li key={r.key} className="py-3 text-[13px] tabular-nums">
                          <div className="flex items-center justify-between gap-3">
                            <span className="min-w-0 text-sm font-medium">
                              <RowKey row={r} group={u.group_by} />
                            </span>
                            <span className="shrink-0 font-medium">{usd(r.spent_usd)}</span>
                          </div>
                          <div className="text-muted">
                            {int(r.calls)}회 · 입력 {int(r.input_tokens)} · 출력{" "}
                            {int(r.output_tokens + r.reasoning_tokens)} · 환산 {equivalent(r)}
                          </div>
                        </li>
                      ))}
                    </ul>
                    <p className="mt-3 text-[13px] text-muted">
                      청구는 실제로 낸 금액, 환산은 같은 토큰을 종량제 단가로 계산한 값입니다 (구독·무료 크레딧이면
                      청구가 0).
                    </p>
                  </>
                ) : (
                  <p className="border-t border-line pt-3 text-sm text-muted">이 기간에 모델 호출이 없습니다.</p>
                )}
              </section>
            </div>
          );
        }}
      </Loaded>
    </div>
  );
}

function Row({ label, children }: { label: ReactNode; children: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 py-2.5 text-sm">
      <dt className="shrink-0 text-muted">{label}</dt>
      <dd className="min-w-0 text-right tabular-nums">{children}</dd>
    </div>
  );
}

function RowKey({ row, group, total }: { row: UsageRow; group: UsageGroup; total?: boolean }) {
  if (total) return <>합계</>;
  const problems = row.errors + row.denied;
  return (
    <span className="inline-flex flex-wrap items-center gap-x-1.5 gap-y-0.5">
      {group === "day" ? formatDate(row.key) : group === "purpose" ? (PURPOSE_LABEL[row.key] ?? row.key) : row.key}
      {problems ? <Badge tone="danger">오류·거부 {problems}</Badge> : null}
      {row.unknown_cost_calls ? (
        <span className="text-xs font-normal text-muted">단가 미상 {row.unknown_cost_calls}</span>
      ) : null}
    </span>
  );
}

const BUDGET_SOURCE: Record<string, string> = { file: "models.toml에서 옴", ui: "화면에서 정함 (파일 값을 덮음)" };

/** 월 예산 한 줄: 값·출처, 고치기(PUT /core/models/budget). 화면 값을 지우면 파일 값으로 돌아간다. */
function BudgetRow({ canEdit }: { canEdit: boolean }) {
  const qc = useQueryClient();
  const status = useQuery({ queryKey: ["models-status"], queryFn: api.modelsStatus, retry: false });
  const [draft, setDraft] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: (value: string | null) => api.putModelBudget(value),
    onSuccess: () => {
      setDraft(null);
      void qc.invalidateQueries({ queryKey: ["models-status"] });
      void qc.invalidateQueries({ queryKey: ["usage"] });
    },
  });
  const b = status.data?.budget;
  const value = b?.monthly_usd ? Number(b.monthly_usd) : null;
  const valid = draft !== null && (draft.trim() === "" || /^\d{1,6}(\.\d{1,2})?$/.test(draft.trim()));
  return (
    <div className="py-2.5 text-sm">
      <div className="flex items-baseline justify-between gap-4">
        <dt className="shrink-0 text-muted">월 예산</dt>
        <dd className="min-w-0 text-right tabular-nums">
          {status.isError ? (
            <span className="text-muted">models.toml [budget]에서 정합니다</span>
          ) : !b ? (
            <span className="text-muted">확인 중</span>
          ) : (
            <>
              {value !== null ? usd(value, 2) : <span className="text-muted">정하지 않음 (제한 없음)</span>}
              {b.source ? <span className="text-muted"> · {BUDGET_SOURCE[b.source]}</span> : null}
            </>
          )}
          {canEdit && b && draft === null ? (
            <button
              type="button"
              onClick={() => setDraft(b.monthly_usd ?? "")}
              className="ml-2 inline-flex min-h-9 items-center text-[13px] text-muted underline underline-offset-2 hover:text-fg sm:min-h-6"
            >
              수정
            </button>
          ) : null}
        </dd>
      </div>
      {draft !== null ? (
        <form
          className="mt-2 flex flex-wrap items-center justify-end gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (valid && draft.trim()) save.mutate(draft.trim());
          }}
        >
          <label htmlFor="budget-usd" className="text-[13px] text-muted">
            한 달 USD
          </label>
          <Input
            id="budget-usd"
            inputMode="decimal"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="20.00"
            className="w-28 text-right tabular-nums"
          />
          <Button type="submit" size="sm" variant="primary" disabled={!valid || !draft.trim() || save.isPending}>
            저장
          </Button>
          {b?.source === "ui" ? (
            <Button size="sm" disabled={save.isPending} onClick={() => save.mutate(null)}>
              초기화
            </Button>
          ) : null}
          <Button size="sm" variant="ghost" onClick={() => setDraft(null)}>
            취소
          </Button>
          <p className="w-full text-right text-[13px] text-muted">
            종량제 지출이 이번 달(UTC) 이 값에 닿으면 호출을 막습니다. 구독·무료 연결은 세지 않습니다.
          </p>
          <ErrorText error={save.error} className="w-full text-right" />
        </form>
      ) : null}
    </div>
  );
}
